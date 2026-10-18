# Cake Taste 64

Cake Taste runs bounded N64 experiments and records native-output evidence. Cake Unbake supplies objective facts and checks; Recipe Maker supplies candidate recipe work. Both are direct, required dependencies of this package. A generated candidate is not a verified result.

The public command is `taste`; the Python package is `cake_taste`. Project-specific behavior belongs in profiles and target adapters, not the core package. The [coordination contract](COORDINATION.md) records ownership and handoff rules.

## Install in an isolated environment

Python 3.13 or newer is required. Do not install into an existing Unbake environment.

```sh
python3.13 -m venv /path/to/isolated-env
/path/to/isolated-env/bin/python -m pip install -e '.[dev]'
/path/to/isolated-env/bin/taste dependencies
```

Direct pins are Unbake `b25437c884c2803537d17f62fdc729ee205896db` and Recipe Maker `ba2f659f027e7eba653d2bbfd94636a87ec45dcb`. The Unbake requirement exactly matches this Recipe Maker revision's requirement so the ordinary dependency resolver can resolve both. `taste dependencies` verifies each installed distribution's `direct_url.json`, Git commit and upstream URL, and Recipe Maker's matching Unbake requirement. There is no resolver bypass or source-tree import fallback.

## Project profile

Profiles are explicit JSON. Relative paths resolve against the profile file. Runtime and output roots must be outside the target source tree; loading a profile does not create directories. Missing or unknown top-level keys fail with a file and key location.

```json
{
  "schema_version": 1,
  "name": "example-project",
  "project_root": "/work/native-project",
  "runtime_root": "/state/taste/example-project",
  "output_root": "/evidence/taste/example-project",
  "tooling": {
    "project": "/work/decomp-project",
    "host": "/configuration/unbake-host.toml",
    "timeout_seconds": 300
  },
  "target": {
    "adapter": "/configuration/project-adapter",
    "asset_ledger": "/evidence/taste/example-project/asset-ledger.json",
    "asset_observations": "/evidence/taste/example-project/asset-observations.jsonl"
  }
}
```

`tooling` is optional for build/display/media use. Direct Unbake or Recipe Maker commands require it; neither tool silently chooses a project, host, or timeout. `target.asset_ledger` identifies confirmed asset facts; `target.asset_observations` identifies rough steering and observations in JSONL. Neither is an invented Unbake runtime catalog API. Verified catalog changes cross to the owning source lane as evidence or reviewed patches.

The known component sections are `build`, `media`, `panes`, `stream`, `logs`, `repro`, `recomp`, `trace`, `handoff`, and `target`. Component owners validate their own section fields. Paths, binaries, service identities, ports, titles, timezone and game-specific fatal markers belong in those sections. Secrets, runtime state, media, ROMs and dependency binaries stay outside Git.

## Commands and public boundaries

```sh
taste --profile /configuration/project.json profile
taste dependencies
taste --profile /configuration/project.json unbake report
taste --profile /configuration/project.json recipe status
```

Unbake and Recipe Maker are invoked through their public `main(argv)` entry points in an isolated interpreter using the installed packages. Unbake's full JSON envelope is preserved. This pinned Recipe Maker emits a flat `--json` document, so Cake Taste preserves that document rather than pretending it has Unbake's envelope. A nonzero process exit or `ok: false` is a failure; malformed output fails immediately. The configured host is supplied through the documented `UNBAKE_HOST` environment channel only, replacing any ambient value; the project is supplied through `--project`. Tool host/project overrides are rejected because those values belong in the explicit profile.

Every machine command emits one JSON object with `ok`, `result`, `findings`, `stages`, and `invocation`; human tool logs go to stderr. Help prints plain text. CLI execution waits for process completion rather than polling. Long-running domain services own their normal event loops and write streamed logs outside the code tree.

Owned components implement `cake_taste.<domain>.cli.run(profile, argv) -> dict` for `build`, `media`, `panes`, `stream`, `logs`, or `repro`. `argv` excludes the domain token. The core uses explicit lazy dispatch; there is no plugin-discovery framework, and a missing component is a named failure. `Profile.section(name)` returns a fresh dictionary and `Profile.resolve_path(value)` resolves a project path. Components can raise `TasteError(key, detail)` for named refusals. Their public behavior is accepted only through their actual integration proofs.

## Development and evidence

```sh
python -m unittest discover -s tests -p 'test_*.py'
ruff check src/cake_taste tests
```

Core tests use mocked dependency processes and small temporary profiles. Those tests do not establish gameplay, rendering, streaming or recompilation correctness. Real integration receipts must identify inputs, exact revisions, public commands, outputs and remaining gaps. Only the receiving source owner lands changes; tools do not commit or alter unrelated lanes.

The core uses a profile byte digest and verified dependency commits for durable identity, isolates process imports and runtime namespaces, and keeps project facts out of generic code. Completion signals replace polling; timeouts and schema failures are explicit. Stages report measured command duration. There is one CLI and no retired compatibility route. Domain-specific output behavior and resource limits remain the domain owner's proof obligation.

Asset ledger dashboards: `taste catalog render` validates a project ledger and publishes a searchable metadata snapshot. See [catalog contract](src/cake_taste/catalog/README.md).
