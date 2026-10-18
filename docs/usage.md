# Usage

Cake Taste runs bounded N64 experiments and records native-output evidence. It is standalone: it depends on no decomp tool, and its evidence crosses to other tools only as files (events, receipts, handoff packets). A generated candidate is not a verified result.

The public command is `taste`; the Python package is `cake_taste`. Project-specific behavior belongs in profiles and target adapters, not the core package. The [coordination contract](../COORDINATION.md) records ownership and handoff rules.

## Install in an isolated environment

Python 3.13 or newer is required.

```sh
python3.13 -m venv /path/to/isolated-env
/path/to/isolated-env/bin/python -m pip install -e '.[dev]'
```

## Project profile

Profiles are explicit JSON. Relative paths resolve against the profile file. Runtime and output roots must be outside the target source tree; loading a profile does not create directories. Missing or unknown top-level keys fail with a file and key location.

```json
{
  "schema_version": 1,
  "name": "example-project",
  "project_root": "/work/native-project",
  "runtime_root": "/state/taste/example-project",
  "output_root": "/evidence/taste/example-project",
  "target": {
    "adapter": "/configuration/project-adapter",
    "asset_ledger": "/evidence/taste/example-project/asset-ledger.json",
    "asset_observations": "/evidence/taste/example-project/asset-observations.jsonl"
  }
}
```

`target.asset_ledger` identifies confirmed asset facts; `target.asset_observations` identifies rough observations in JSONL. Verified catalog changes cross to the owning source repository as evidence or reviewed patches.

The known component sections are `media`, `logs`, `repro`, `recomp`, `trace`, `handoff`, and `target`. Component owners validate their own section fields. Paths, binaries, service identities, ports, titles, timezone and game-specific fatal markers belong in those sections. Secrets, runtime state, media, ROMs and dependency binaries stay outside Git.

## Commands and public boundaries

```sh
taste --profile /configuration/project.json profile
```

Every machine command emits one JSON object with `ok`, `result`, `findings`, `stages`, and `invocation`; human tool logs go to stderr. Help prints plain text. CLI execution waits for process completion rather than polling. Long-running domain services own their normal event loops and write streamed logs outside the code tree.

Owned components implement `cake_taste.<domain>.cli.run(profile, argv) -> dict` for `media`, `logs`, or `repro`. `argv` excludes the domain token. The core uses explicit lazy dispatch; there is no plugin-discovery framework, and a missing component is a named failure. `Profile.section(name)` returns a fresh dictionary and `Profile.resolve_path(value)` resolves a project path. Components can raise `TasteError(key, detail)` for named refusals. Their public behavior is accepted only through their actual integration proofs.

## Development and evidence

```sh
python -m unittest discover -s tests -p 'test_*.py'
ruff check src/cake_taste tests
```

Core tests use mocked processes and small temporary profiles. Those tests do not establish gameplay, rendering, streaming or recompilation correctness. Real integration receipts must identify inputs, exact revisions, public commands, outputs and remaining gaps.

The core uses a profile byte digest for durable identity, isolates process imports and runtime namespaces, and keeps project facts out of generic code. Completion signals replace polling; timeouts and schema failures are explicit. Stages report measured command duration. There is one CLI and no retired compatibility route. Domain-specific output behavior and resource limits remain the domain owner's proof obligation.

Asset ledger dashboards: `taste catalog render` validates a project ledger and publishes a searchable metadata snapshot. See [catalog contract](../src/cake_taste/catalog/README.md).
