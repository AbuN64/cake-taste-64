"""Explicit JSON project profiles; component owners validate their own sections."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from cake_taste import TasteError

SECTIONS = frozenset({"build", "media", "panes", "stream", "logs", "repro", "target"})
_REQUIRED = frozenset({"schema_version", "name", "project_root", "runtime_root", "output_root"})
_ALLOWED = _REQUIRED | SECTIONS | {"tooling"}


class ConfigError(TasteError):
    def __init__(self, file: Path, key: str, reason: str) -> None:
        super().__init__("config.schema", f"{file}:{key}: {reason}")


@dataclass(frozen=True)
class Tooling:
    project: Path
    host: Path
    timeout_seconds: float


@dataclass(frozen=True)
class Profile:
    name: str
    project_root: Path
    runtime_root: Path
    output_root: Path
    tooling: Tooling | None
    source_file: Path
    digest: str
    _sections: Mapping[str, Mapping[str, Any]] = field(repr=False)

    def section(self, name: str) -> dict[str, Any]:
        """Return fresh component settings, so a component cannot mutate this profile."""
        if name not in SECTIONS:
            raise ConfigError(self.source_file, name, "unknown component section")
        if name not in self._sections:
            raise ConfigError(self.source_file, name, "required section is absent")
        return copy.deepcopy(dict(self._sections[name]))

    def resolve_path(self, value: str) -> Path:
        return _path(self.source_file, "path", value)

    def describe(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "name": self.name,
            "source_file": str(self.source_file),
            "profile_sha256": self.digest,
            "project_root": str(self.project_root),
            "runtime_root": str(self.runtime_root),
            "output_root": str(self.output_root),
            "tooling": None
            if self.tooling is None
            else {
                "project": str(self.tooling.project),
                "host": str(self.tooling.host),
                "timeout_seconds": self.tooling.timeout_seconds,
            },
            "sections": sorted(self._sections),
        }


def _path(file: Path, key: str, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ConfigError(file, key, "expected a nonempty path string")
    try:
        path = Path(value).expanduser()
        return (path if path.is_absolute() else file.parent / path).resolve()
    except (OSError, RuntimeError) as exc:
        raise ConfigError(file, key, str(exc)) from exc


def _keys(file: Path, key: str, table: Mapping[str, Any], required: frozenset[str], allowed: frozenset[str]) -> None:
    missing, extra = required - table.keys(), table.keys() - allowed
    if missing:
        raise ConfigError(file, key, f"missing keys: {', '.join(sorted(missing))}")
    if extra:
        raise ConfigError(file, key, f"unknown keys: {', '.join(sorted(extra))}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError(f"nonfinite JSON number: {value}")


def load_profile(path: Path) -> Profile:
    file = path.expanduser().resolve()
    try:
        content = file.read_bytes()
        value = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_constant)
    except (OSError, ValueError, UnicodeError) as exc:
        raise ConfigError(file, "profile", str(exc)) from exc
    if not isinstance(value, dict):
        raise ConfigError(file, "profile", "expected an object")
    _keys(file, "profile", value, _REQUIRED, _ALLOWED)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ConfigError(file, "schema_version", "only version 1 is supported")
    name = value["name"]
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(file, "name", "expected a nonempty string")
    project = _path(file, "project_root", value["project_root"])
    runtime = _path(file, "runtime_root", value["runtime_root"])
    output = _path(file, "output_root", value["output_root"])
    if not project.is_dir():
        raise ConfigError(file, "project_root", "directory does not exist")
    if runtime.is_relative_to(project) or output.is_relative_to(project):
        raise ConfigError(
            file, "runtime_root/output_root", "runtime and evidence must stay outside the target source tree"
        )
    tooling = None
    if "tooling" in value:
        table = value["tooling"]
        if not isinstance(table, dict):
            raise ConfigError(file, "tooling", "expected an object or omit this section")
        keys = frozenset({"project", "host", "timeout_seconds"})
        _keys(file, "tooling", table, keys, keys)
        tool_project = _path(file, "tooling.project", table["project"])
        host = _path(file, "tooling.host", table["host"])
        timeout = table["timeout_seconds"]
        try:
            finite_timeout = type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0
        except OverflowError:
            finite_timeout = False
        if not finite_timeout:
            raise ConfigError(file, "tooling.timeout_seconds", "expected a finite positive number")
        if not tool_project.is_dir() or not host.is_file():
            raise ConfigError(file, "tooling", "project directory and host file must exist")
        tooling = Tooling(tool_project, host, float(timeout))
    sections: dict[str, Mapping[str, Any]] = {}
    for name_ in SECTIONS & value.keys():
        if not isinstance(value[name_], dict):
            raise ConfigError(file, name_, "expected an object")
        sections[name_] = MappingProxyType(copy.deepcopy(value[name_]))
    return Profile(
        name, project, runtime, output, tooling, file, hashlib.sha256(content).hexdigest(), MappingProxyType(sections)
    )
