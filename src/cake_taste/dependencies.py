"""Direct pinned dependency provenance and isolated calls to each public CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.config import Profile


@dataclass(frozen=True)
class Dependency:
    distribution: str
    module: str
    url: str
    revision: str


DEPENDENCIES = {
    "unbake": Dependency(
        "unbake", "unbake.cli", "https://github.com/AbuN64/cake-unbake-64", "b25437c884c2803537d17f62fdc729ee205896db"
    ),
    "recipe": Dependency(
        "recipemaker",
        "recipemaker.cli",
        "https://github.com/AbuN64/recipe-maker-64",
        "ba2f659f027e7eba653d2bbfd94636a87ec45dcb",
    ),
}


class DependencyError(TasteError):
    pass


@dataclass(frozen=True)
class ToolResult:
    tool: str
    argv: tuple[str, ...]
    returncode: int
    document: dict[str, Any]
    stderr: str
    provenance: dict[str, dict[str, str]]
    context: dict[str, str]

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.document["ok"] is True

    def describe(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "argv": list(self.argv),
            "exit_code": self.returncode,
            "document": self.document,
            "provenance": self.provenance,
            "context": self.context,
        }


def verify_dependencies() -> dict[str, dict[str, str]]:
    """Require both installed direct VCS pins; never accept a transitive-only claim."""
    found: dict[str, dict[str, str]] = {}
    for name, dependency in DEPENDENCIES.items():
        try:
            distribution = metadata.distribution(dependency.distribution)
        except metadata.PackageNotFoundError as exc:
            raise DependencyError(
                "dependency.missing", f"required direct dependency {dependency.distribution} is not installed"
            ) from exc
        raw = distribution.read_text("direct_url.json")
        if raw is None:
            raise DependencyError("dependency.provenance", f"{name} has no direct_url.json VCS provenance")
        try:
            origin = json.loads(raw)
            vcs = origin["vcs_info"]
            url = origin["url"]
            valid = (
                isinstance(url, str)
                and url.removesuffix(".git") == dependency.url
                and vcs["vcs"] == "git"
                and vcs["commit_id"] == dependency.revision
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise DependencyError("dependency.provenance", f"{name} has invalid VCS provenance") from exc
        if not valid:
            raise DependencyError("dependency.pin", f"{name} must come from {dependency.url} at {dependency.revision}")
        if name == "recipe":
            requirements = distribution.requires or []
            expected = f"unbake@git+{DEPENDENCIES['unbake'].url}@{DEPENDENCIES['unbake'].revision}"
            if expected not in ["".join(row.split()) for row in requirements]:
                raise DependencyError(
                    "dependency.compatibility", "Recipe Maker's direct Unbake requirement differs from Cake Taste's pin"
                )
        found[name] = {
            "distribution": dependency.distribution,
            "version": distribution.version,
            "url": dependency.url,
            "revision": dependency.revision,
        }
    return found


def invoke(tool: str, profile: Profile, args: Sequence[str], *, stdin: bytes = b"") -> ToolResult:
    """Call the installed public main(argv) in a clean interpreter; JSON stdout is mandatory."""
    if tool not in DEPENDENCIES:
        raise DependencyError("dependency.unknown", f"unknown direct tool: {tool}")
    if profile.tooling is None:
        raise DependencyError("dependency.configuration", "this profile has no explicit tooling project/host/timeout")
    given = tuple(args)
    if given and given[0] == "--":
        given = given[1:]
    if not given or given[0].startswith("-") or any(not isinstance(arg, str) or "\0" in arg for arg in given):
        raise DependencyError("dependency.arguments", "provide a public command followed by its arguments")
    if any(arg in ("--host", "--project") or arg.startswith(("--host=", "--project=")) for arg in given):
        raise DependencyError("dependency.arguments", "tool project and host come only from the explicit profile")
    provenance = verify_dependencies()
    dependency = DEPENDENCIES[tool]
    command = ["--project", str(profile.tooling.project), given[0]]
    if tool == "recipe":
        command.append("--json")
    command.extend(given[1:])
    bootstrap = f"from {dependency.module} import main; raise SystemExit(main())"
    executable = str(Path(sys.executable).absolute())
    environment = dict(os.environ)
    # Both public CLIs support UNBAKE_HOST; one explicit channel replaces ambient host selection.
    environment["UNBAKE_HOST"] = str(profile.tooling.host)
    # Recipe's own documented CLI calls must resolve the same isolated environment.
    environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
    try:
        completed = subprocess.run(
            [executable, "-I", "-c", bootstrap, *command],
            cwd=profile.tooling.project,
            input=stdin,
            capture_output=True,
            timeout=profile.tooling.timeout_seconds,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise DependencyError(
            "dependency.timeout", f"{tool} exceeded explicit timeout {profile.tooling.timeout_seconds:g}s"
        ) from exc
    except OSError as exc:
        raise DependencyError("dependency.process", f"cannot execute {tool}: {exc}") from exc
    stderr = completed.stderr.decode("utf-8", errors="replace")
    try:
        document = json.loads(completed.stdout.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise DependencyError(
            "dependency.output",
            f"{tool} exited {completed.returncode} without one valid JSON document; stderr: {stderr.strip()}",
        ) from exc
    if not isinstance(document, dict) or type(document.get("ok")) is not bool:
        raise DependencyError("dependency.output", f"{tool} JSON requires an explicit boolean ok")
    if "findings" in document and not isinstance(document["findings"], list):
        raise DependencyError("dependency.output", f"{tool} JSON findings must be a list")
    if tool == "unbake" and not {"ok", "result", "findings", "stages", "invocation"}.issubset(document):
        raise DependencyError("dependency.output", "Unbake omitted fields of its public result envelope")
    return ToolResult(
        tool,
        tuple([tool, *command]),
        completed.returncode,
        document,
        stderr,
        provenance,
        {"project": str(profile.tooling.project), "host": str(profile.tooling.host), "profile_sha256": profile.digest},
    )
