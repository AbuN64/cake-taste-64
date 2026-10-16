# Asset accounting now

Cake Unbake does not yet provide the asset-catalog feature needed here. Do not wait for it and do not claim that it does. Keep portable observations now; a future catalog can import them.

Each project profile identifies one `target.asset_notes` JSONL file. The initial Rage Wars observations are in `catalogs/rage_wars/assets.jsonl`; their README and provenance record the bounded source inventory. These are metadata only, not ROM or decoded asset payloads.

- When a lane identifies an asset, table, packed stream, model, image, font, page or resource relationship, it MUST record the useful observation or hand the evidence to the designated catalog writer. A rough, clearly qualified record is preferable to losing the finding.
- Records MUST retain the source identifier/name when known, target revision and ROM hash, original coordinate space, address or range, evidence location, and verification scope. Unknown fields MUST remain unknown; guessed names or meanings MUST be marked as inferred.
- Physical ROM offsets, virtual addresses and offsets inside decompressed resources MUST be distinguished. A compressed stream, a catalog node and a playable asset are different accounting units. Their counts MUST NOT be presented as an asset-completion denominator.
- A new finding MUST NOT silently overwrite contradictory history. Preserve the prior observation and identify the correction or superseding evidence. Byte matching alone does not prove a resource's gameplay meaning.
- ROM bytes, decoded images/audio/video, credentials and mutable build artifacts MUST NOT enter this repository. Asset observations MAY reference externally retained, hash-identified evidence.
- Verified names, addresses, relationships and remaining questions SHOULD be handed back to the decomp owner using COORDINATION.md. Private candidates and accepted published results MUST remain distinct.

This is intentionally a lightweight staging format and steering file. Keep the existing evidence available by hash, append observations, and convert through a reviewed migration when the objective catalog contract exists. Do not build a parallel ROM parser or pretend that these notes are a completed Cake Unbake catalog.
