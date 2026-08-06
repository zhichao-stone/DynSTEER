You compile public task contracts into structured candidates. From the JSON allowlist below, return one shared atom catalog, diverse paths, and minefield candidates in one response.

Requirements:
- Output JSON only, without chain of thought or commentary. Top-level keys must be exactly atoms, paths, and minefields.
- Produce the requested simulated_path_count strategies covering shortest, state-first, artifact-first, alternative tool/implementation, and conservative paths.
- Paths reference shared atom_id values only and end in an atom with terminal=true.
- Atoms may reference only supplied source_ref and evidence_id values. Copy expected verbatim from that evidence source_ref; use null for expected_policy=none.
- Minefields may reference supplied invariant_id values only and follow the same expected rule.
- Path consensus is synthetic consensus, not a gold path or proof of real necessity.

Output shape:
{{"atoms":[{{"atom_id":"a1","name":"...","description":"...","source_refs":["instruction"],"evidence_id":"...","expected":null,"terminal":false}}],"paths":[{{"strategy":"shortest","atom_ids":["a1"]}}],"minefields":[{{"minefield_id":"mf1","invariant_id":"...","expected":null}}]}}

Public input:
{payload}
