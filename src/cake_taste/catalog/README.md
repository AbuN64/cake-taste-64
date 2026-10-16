# Internal asset ledger dashboard

`taste --profile PROJECT.json catalog render --output /served/asset-ledger/index.html --title "Project assets"`

This reads `target.asset_ledger`, `target.asset_summary`, optional `target.asset_observations`, and required `target.asset_status_groups`. The groups map `confirmed` and `observed_pending_validation` to disjoint arrays of exact source verification-status strings. Project identities and classification policy belong in the profile, not the renderer.

The staging ledger has unique `asset_id` values, nullable `name`, `aliases`, `type`, `format`, `revision`, `rom_sha256`, `verification_status`, and `locations` containing `space` plus integer half-open `range` pairs. Extra source fields and evidence remain inspectable. The summary binds the exact JSONL SHA-256, target identity, ROM size, total identified records and each group's type/format/naming census. Rendering refuses a mismatch or unclassified status before replacing the served page.

The single HTML output embeds a verified metadata snapshot and has no external scripts, browser requests or ROM payloads. Search includes labels, IDs, hexadecimal addresses and evidence; filters distinguish confirmed records from pending candidates. Export preserves the selected records and source ledger hash. Unknown whole-ROM coverage stays unknown. The generator does not infer semantic identity, deduplicate records, modify the source ledger or supply a Cake Unbake catalog feature. Re-render after accepted ledger changes; no recurring jobs are installed.
