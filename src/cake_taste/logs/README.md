# Shared evidence console

## Events

`logs/events.py` is the one writer of the event feed (flock append, one JSON line per event). New lines are schema 2, described by [event.schema.json](event.schema.json). Schema 2 is additive over schema 1: it adds the kinds `RUN-FAIL`, `PROBE`, `MATCH`, `HANDOFF`, `LAND`, `LANE`, `DECISION` (closed set), the optional `subject` and `lane` objects, and an evidence object `{path, sha256}` beside the path string. Schema 1 lines stay valid and are never rewritten; readers show their `PORT-FAIL`, `DECOMP` and `COMMIT` as `RUN-FAIL`, `MATCH` and `LAND`. The writer and `--kinds` filters accept schema 2 names only. `taste logs validate [--path FEED]` checks a feed line by line.

## Console

`logs console` and `panes supervise` use the same Textual view. Click a row or focus it and press Enter/Space to expand. Up/Down or j/k selects rows; Home/End reaches the ends. Supervisor views retain F5 restart and F7 retry pause. Raw output stays collapsed.

Optional `logs.console_sources` profile fields:

- `events`: additional JSONL narration paths, merged by event time.
- `host_log`: host output path, parsed incrementally for frame/task counters.
- `run_status`: reproduction status JSON; `run_dir` identifies the run.
- `build_profile`: canonical build profile, loaded again when it changes. This selects the boot-chain model.
- `supervisor_status`, `release_current`: supervisor status and immutable release symlink. Supervised panes supply these from their role config.
- `boot_plan`: Markdown path or glob; newest file by modification time wins.
- `plan_after`: string or ordered list of markers delimiting the planned portion; `plan_before` optionally ends it. Addresses already in the configured gate are excluded from planned rows.
- `build_step`, `test_step`: receipt step names, default `build` and `ctest`.

The build workspace's status points to the latest attempt receipt; step logs establish compile/test results and observed gate assertions. An expected error can pass a public gate while remaining the highlighted boot stop. Narration supplies row provenance. Missing probes or match qualifications stay explicit unknowns; a decomp filename alone never proves matching. Gate rows use configured order, including distinct checkpoints sharing one owner.

File changes wake inotify readers; a short event debounce batches bursts. No polling timer or per-VI event rows. Open rows and focus survive source refreshes. Textual is pinned in project dependencies. The previous curses console/header and supervisor drawing routes are retired.
