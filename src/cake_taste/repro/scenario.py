"""Timed input scenarios over an existing role, with immutable trace and capture evidence."""

from __future__ import annotations

import json
import math
import os
import re
import select
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.files import sha256_file

from .runner import run_command


def validate(spec: dict[str, Any]) -> list[dict[str, Any]]:
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("scenario needs a nonempty steps list")
    previous = -1.0
    checkpoints = set()
    for step in steps:
        at = step.get("at")
        if not isinstance(at, (int, float)) or not math.isfinite(at) or not 0 <= at <= 7200 or at < previous:
            raise ValueError("scenario times must be finite, ordered and within 0..7200 seconds")
        previous = at
        if not step.get("keys") and not step.get("checkpoint"):
            raise ValueError("each scenario step needs keys or a checkpoint")
        if "keys" in step and (
            not isinstance(step["keys"], list)
            or not step["keys"]
            or any(not isinstance(k, str) or not k for k in step["keys"])
        ):
            raise ValueError("scenario keys must be a nonempty list of strings")
        if "checkpoint" in step:
            name = step["checkpoint"]
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name) or name in checkpoints:
                raise ValueError("checkpoint names must be unique safe lowercase names")
            checkpoints.add(name)
    return steps


def expand(command: list[str], values: dict[str, Any]) -> list[str]:
    if not isinstance(command, list) or not command or any(not isinstance(p, str) for p in command):
        raise ValueError("scenario transport commands must be nonempty argument lists")
    return [part.format(**values) for part in command]


def scenario(profile: Any, name: str) -> dict[str, Any]:
    config = profile.section("repro").get("scenario")
    if not config:
        raise TasteError("repro.scenario", "profile repro.scenario is absent")
    facts_path = profile.resolve_path(config["facts"])
    facts = json.loads(facts_path.read_text())
    try:
        spec = facts["scenarios"][name]
    except KeyError as exc:
        raise TasteError("repro.scenario", f"unknown scenario {name}") from exc
    steps = validate(spec)
    work = profile.resolve_path(config["work_root"]) / uuid.uuid4().hex
    work.mkdir(parents=True)
    request = work / "request.json"
    request.write_text(
        json.dumps({"name": name, "spec": spec, "transport": config, "facts_sha256": sha256_file(facts_path)}, indent=2)
        + "\n"
    )
    outputs = [request, work / "trace.jsonl", work / "host.log"]
    outputs += [work / (step["checkpoint"] + ".png") for step in steps if "checkpoint" in step]
    result = run_command(
        profile,
        name="scenario " + name,
        command=[sys.executable, "-m", __name__, str(request)],
        cwd=work,
        artifacts=outputs,
        env=config.get("env", {}),
    )
    if result["status"]["phase"] != "passed":
        raise TasteError("repro.scenario", f"scenario failed; receipt: {result['receipt']}")
    return result


def execute(request: Path) -> None:
    data = json.loads(request.read_text())
    spec, config = data["spec"], data["transport"]
    steps = validate(spec)
    root = request.parent
    pid = int(Path(config["pid_file"]).read_text())
    fd = os.pidfd_open(pid)
    log = Path(config["log_template"].format(pid=pid))
    started = time.monotonic()
    env = {**os.environ, **config.get("env", {})}
    values = {"role": spec["role"], "scenario": data["name"]}
    with (root / "trace.jsonl").open("w", buffering=1) as trace:

        def record(event: dict[str, Any]) -> None:
            line = json.dumps({"elapsed": time.monotonic() - started, "pid": pid, **event})
            trace.write(line + "\n")
            print(line, flush=True)

        record({"event": "start", "facts_sha256": data["facts_sha256"]})
        try:
            for step in steps:
                if select.select([fd], [], [], max(0, step["at"] - (time.monotonic() - started)))[0]:
                    raise RuntimeError("oracle process exited during scenario")
                if "keys" in step:
                    command = expand(config["send_command"], values) + step["keys"]
                    subprocess.run(command, env=env, check=True, capture_output=True, text=True, timeout=10)
                    record({"event": "input", "keys": step["keys"]})
                if "checkpoint" in step:
                    output = root / (step["checkpoint"] + ".png")
                    current = {**values, "output": str(output), "message": step.get("message", step["checkpoint"])}
                    subprocess.run(expand(config["capture_command"], current), env=env, check=True, timeout=10)
                    record(
                        {
                            "event": "checkpoint",
                            "name": step["checkpoint"],
                            "image_sha256": sha256_file(output),
                            "message": current["message"],
                        }
                    )
                    subprocess.run(
                        expand(config["narrate_command"], current),
                        env=env,
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
            if select.select([fd], [], [], 0)[0]:
                raise RuntimeError("oracle exited at final checkpoint")
            record({"event": "complete"})
        except Exception as exc:
            record({"event": "failed", "error": str(exc)})
            raise
        finally:
            os.close(fd)
            (root / "host.log").write_text(log.read_text() if log.exists() else "host log missing\n")


if __name__ == "__main__":
    execute(Path(sys.argv[1]))
