"""Render a probe's recomp config overlay from profile templates, run the oracle, freeze outputs."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.files import atomic_bytes, atomic_json, sha256_file

from .runner import run_command

KINDS = ("P1", "P2", "P3", "P4", "P5", "P6", "P7")


def _functions(node: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "functions" and isinstance(value, list):
                names.update(item["name"] for item in value if isinstance(item, dict) and "name" in item)
            names |= _functions(value)
    elif isinstance(node, list):
        for item in node:
            names |= _functions(item)
    return names


def check_function(symbols: Path, function: str) -> None:
    try:
        known = _functions(tomllib.loads(symbols.read_text()))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TasteError("repro.symbols", f"{symbols}: {exc}") from exc
    if function not in known:
        raise TasteError("repro.function", f"{function} is not in the symbols TOML {symbols}")


def render_overlay(config: dict[str, Any], kind: str, values: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Return overlay text and extra environment for this probe kind; every fact comes from the profile."""
    if kind not in KINDS:
        raise TasteError("repro.kind", f"unknown probe kind {kind}; expected one of {', '.join(KINDS)}")
    template = config.get("templates", {}).get(kind)
    if template is None:
        raise TasteError("repro.template", f"profile repro.probe.templates has no {kind}")
    env: dict[str, str] = {}
    if kind == "P3":
        if "range" not in values:
            raise TasteError("repro.range", "P3 needs --range A:B")
        env[config["range_env"]] = values["range"]
    try:
        return template.format(**values) + "\n", env
    except KeyError as exc:
        raise TasteError("repro.template", f"template {kind} needs {exc}, which was not given") from exc


def probe(
    profile: Any,
    *,
    kind: str,
    function: str,
    script: Path,
    frames: int | None = None,
    span: str | None = None,
) -> dict[str, Any]:
    config = profile.section("repro").get("probe")
    if not config:
        raise TasteError("config.schema", "profile repro.probe is absent")
    check_function(profile.resolve_path(config["symbols"]), function)
    values: dict[str, Any] = {"function": function, "frames": frames or 0, "input": str(script)}
    if span:
        start, _, stop = span.partition(":")
        if not start or not stop:
            raise TasteError("repro.range", "--range must look like A:B")
        values.update(range=span, range_start=start, range_stop=stop)
    text, extra_env = render_overlay(config, kind, values)
    overlay = profile.resolve_path(config["overlay_path"])
    atomic_bytes(overlay, text.encode())
    values["overlay"] = str(overlay)
    runs = []
    for step in ("build", "run"):
        command = [part.format(**values) for part in config[step]["command"]]
        outputs = [Path(item.format(**values)) for item in config[step].get("outputs", [])]
        result = run_command(
            profile, name=f"probe {kind} {step}", function=function, command=command, artifacts=outputs, env=extra_env
        )
        if result["status"]["phase"] != "passed":
            raise TasteError("repro.failed", f"probe {step} failed; receipt: {result['receipt']}")
        runs.append(result)
    final = runs[-1]
    record = {
        "schema": 1,
        "kind": kind,
        "function": function,
        "frames": frames,
        "range": span,
        "input_sha256": sha256_file(script),
        "overlay": text,
        "env": extra_env,
        "build_receipt": runs[0]["receipt"],
        "outputs": [{"path": item["path"], "sha256": item["sha256"]} for item in final["status"]["artifacts"]],
    }
    run_dir = Path(final["status"]["run_dir"])
    atomic_json(run_dir / "probe.json", record, sort_keys=True)
    return {"probe": record, "run_id": run_dir.name, "receipt": final["receipt"]}
