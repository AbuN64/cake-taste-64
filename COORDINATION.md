# Coordination contract
The uppercase requirement words MUST, MUST NOT, SHOULD and MAY follow [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

1. **One committer per repository.** Each repository has exactly one committer. Everyone else leaves changes uncommitted or proposes them as evidence packets.
2. **Evidence packets cross owners.** Changes cross owners only as evidence packets. The receiving owner decides integration; a packet MUST NOT edit another owner's paths.
3. **Game facts live in profiles.** Addresses, asset names, ROM hashes, markers and timings belong in project profiles, never in generic code.
4. **Quirks live in symmetric adapters.** A project quirk is handled by an adapter with the same interface for every project. Generic tools get no opt-in flags and no project names.
5. **Evidence is hash-identified and immutable.** Evidence MUST carry the sha256 of its bytes and the pinned tool revisions. Evidence is never rewritten; a correction is a new packet.
6. **Narration reports actual actions and results.** Reports state what was run and what it returned. They MUST NOT claim a step that did not happen or a result that was not measured.
