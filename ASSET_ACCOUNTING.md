# Asset accounting now

Cake Unbake does not yet provide the asset-catalog feature needed here. Do not wait for it and do not claim that it does. Keep portable observations now; a future catalog can import them.

Each project profile identifies a primary `target.asset_ledger` and separate `target.asset_observations`. For Rage Wars these are `catalogs/rage_wars/distinct-assets.jsonl` and `assets.jsonl`; `summary.json` separates confirmed, observed and unresolved counts. Their README and provenance define the bounded source inventory. These are metadata only, not ROM or decoded asset payloads.

The headline is distinct identified assets, grouped by type and format, with known names/aliases and exact locations. Generic containers, references and coverage intervals are not assets. Semantic composites and their distinct image components must be labeled separately and linked, not collapsed into a misleading texture or sound count. Audio samples, sequences, definitions and cue/control relationships must not be summed as an unlabeled count of sounds. Unknown whole-game totals remain unknown.

- When a lane identifies an asset, table, packed stream, model, image, font, page or resource relationship, it MUST record the useful observation or hand the evidence to the designated catalog writer. A rough, clearly qualified record is preferable to losing the finding.
- Records MUST retain the source identifier/name when known, target revision and ROM hash, original coordinate space, address or range, evidence location, and verification scope. Unknown fields MUST remain unknown; guessed names or meanings MUST be marked as inferred.
- Physical ROM offsets, virtual addresses and offsets inside decompressed resources MUST be distinguished. A compressed stream, a catalog node and a playable asset are different accounting units. Their counts MUST NOT be presented as an asset-completion denominator.
- A new finding MUST NOT silently overwrite contradictory history. Preserve the prior observation and identify the correction or superseding evidence. Byte matching alone does not prove a resource's gameplay meaning.
- ROM bytes, decoded images/audio/video, credentials and mutable build artifacts MUST NOT enter this repository. Asset observations MAY reference externally retained, hash-identified evidence.
- Verified names, addresses, relationships and remaining questions SHOULD be handed back to the decomp owner using COORDINATION.md. Private candidates and accepted published results MUST remain distinct.

This is intentionally a lightweight staging format and steering file. Keep the existing evidence available by hash, append observations, and convert through a reviewed migration when the objective catalog contract exists. Do not build a parallel ROM parser or pretend that these notes are a completed Cake Unbake catalog.
