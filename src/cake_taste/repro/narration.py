"""Describe a probe's question, original owner and explicit decomp evidence."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cake_taste import TasteError
from cake_taste.logs.events import append_event

MATCH_STATES = ("matched-c", "stand-in", "unmatched", "missing", "unknown")


def probe_story(script: Path, function: str | None, overrides: dict[str, Any], profile: Any = None) -> dict[str, Any]:
    try:
        request = json.loads(script.read_text())
    except (ValueError, UnicodeError):
        request = {}
    request = request if isinstance(request, dict) else {}
    story = dict(request.get("story", {}))
    story.update({key: value for key, value in overrides.items() if value is not None})
    story["function"] = function or request.get("function", "unknown")
    if profile is not None and request.get("version"):
        from cake_taste.recomp.settings import load as load_recomp

        from .closure import function_vrams, resolve_target

        settings = load_recomp(profile)
        symbols = settings.out(request["version"]) / "symbols.toml"
        if symbols.exists():
            vrams = function_vrams(symbols)
            name = resolve_target(story["function"], vrams)
            story["function"] = name
            story.setdefault("owner_address", f"{vrams[name]:#010x}")
            source = settings.asm_path(request["version"]) / (name + ".s")
            if source.exists():
                story.setdefault("decomp_path", str(source))
    story.setdefault("question", "Observe the original behavior of " + story["function"])
    story.setdefault("owner_address", "unknown")
    story.setdefault("decomp_path", "unknown")
    story.setdefault("match_state", "unknown")
    if story["match_state"] not in MATCH_STATES:
        raise ValueError("unknown decomp match state: " + str(story["match_state"]))
    # A C file's existence does not prove that it matches. Match state is supplied
    # with the question; absent provenance remains explicitly unknown.
    return story


def narrated_probe(profile: Any, story: dict[str, Any], execute: Callable[[], dict]) -> dict:
    append_event(profile, "repro", "started", story["question"], kind="PROBE", payload=story)
    try:
        result = execute()
    except Exception as exc:
        append_event(profile, "repro", "failed", story["question"], kind="PROBE", payload={**story, "result": str(exc)})
        raise
    probe = result["probe"]
    checked, differences = probe.get("checked"), probe.get("differences")
    outcome = (
        f"{checked} checks, {differences} differences"
        if checked is not None
        else f"{probe['cases']} cases: " + ", ".join(f"{n} {k}" for k, n in sorted(probe["statuses"].items()))
        if probe.get("kind") == "cases"
        else "probe returned; inspect the frozen outputs"
    )
    if probe.get("stubs_hit"):
        outcome += "; boundaries: " + ", ".join(probe["stubs_hit"])
    payload = {
        **story,
        "owner_address": probe.get("vram", story["owner_address"]),
        "function": probe.get("function", story["function"]),
        "result": outcome,
        "run_id": result["run_id"],
        "checks": checked,
        "differences": differences,
    }
    evidence = str(Path(result["receipt"]).parent / "probe.json")
    append_event(
        profile,
        "repro",
        "failed" if differences else "passed",
        story["question"],
        evidence,
        kind="PROBE",
        payload=payload,
    )
    finding = story.get("decomp_finding")
    if not finding and checked and differences == 0 and story["match_state"] in {"unmatched", "missing", "stand-in"}:
        finding = "Original behavior characterized for decomp matching; " + outcome
    if finding:
        append_event(profile, "repro", "info", finding, evidence, kind="MATCH", payload=payload)
    if differences:
        raise TasteError(
            "repro.differences",
            f"{outcome}; run: {result['run_id']}; receipt: {result['receipt']}",
        )
    return result
