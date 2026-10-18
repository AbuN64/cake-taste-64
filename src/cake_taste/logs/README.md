# Shared evidence console

## Events

`logs/events.py` is the one writer of the event feed (flock append, one JSON line per event). New lines are schema 2, described by [event.schema.json](event.schema.json). Schema 2 is additive over schema 1: it adds the kinds `RUN-FAIL`, `PROBE`, `MATCH`, `HANDOFF`, `LAND`, `LANE`, `DECISION` (closed set), the optional `subject` and `lane` objects, and an evidence object `{path, sha256}` beside the path string. Schema 1 lines stay valid and are never rewritten; readers show their `PORT-FAIL`, `DECOMP` and `COMMIT` as `RUN-FAIL`, `MATCH` and `LAND`. The writer and `--kinds` filters accept schema 2 names only. `taste logs validate [--path FEED]` checks a feed line by line.
