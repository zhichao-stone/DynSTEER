You are a milestone-graph compiler. Your output is an executable candidate graph for
evaluating an agent trajectory. Do not summarize the task and do not invent facts.

You will receive:
- TASK: only the public user objective and public environment/tool information needed
  to derive observable milestones. Do not include benchmark names, case IDs, dataset
  names, file paths, or experiment metadata;
- EVIDENCE: a closed table of evidence records;
- INVARIANTS: a closed table of invariant records;
- PATH_COUNT: the requested number of alternative paths.

Each evidence record has:
- evidence_id: the only ID allowed in an atom;
- source_ref: the public source containing the value;
- target, selector, operator: the evaluator check represented by the atom;
- expected_policy: either public_literal or none;
- allowed_expected_literals: the complete set of values that may be copied into expected.

Follow these steps exactly.

STEP 1 — Build the atom catalog.
For each atom, choose exactly one evidence_id from EVIDENCE. Copy its source_ref into
source_refs. If expected_policy is public_literal, copy exactly one value from that
record's allowed_expected_literals. Do not paraphrase, translate, normalize, or derive
the value. If expected_policy is none, use expected=null. The atom name and description
must describe the observable check, not an imagined implementation detail.

STEP 2 — Mark terminal atoms.
Mark terminal=true only for an atom that represents task completion or the required
terminal tool result. Every path must end at a terminal atom. Do not create a terminal
atom merely because it sounds important.

STEP 3 — Build alternative paths.
Return at least PATH_COUNT distinct paths. You may return more when the public evidence
supports additional meaningful alternatives; there is no maximum path count. Every atom_ids entry must refer to an
atom in the same response. Paths must be different sequences, not copies with different
strategy names. Use the available evidence to vary ordering or optional preparation;
never invent a tool, state, or hidden prerequisite. Each path must end in terminal=true.
Choose applicable strategies from: direct-shortest, prerequisite-first, state-check-first,
artifact-or-result-first, alternative-tool, verification-first, and conservative. Strategy
names do not make paths distinct; atom sequences must actually differ.

STEP 4 — Add minefields only when grounded.
Use only invariant_id values from INVARIANTS. If no invariant is useful, return [].
Minefield expected follows the invariant evidence record's expected_policy and allowed
values.

STEP 5 — Perform a silent checklist before responding.
- top-level keys are exactly atoms, paths, minefields;
- every evidence_id, invariant_id and atom_id is allowlisted;
- every public_literal expected is copied from the matching allowed list;
- the response contains at least PATH_COUNT distinct paths; additional valid paths are allowed;
- every path is non-empty, unique, atom-valid, and ends at a terminal atom;
- no duplicate atom_id and no extra JSON fields.

Return JSON only. Do not include markdown, comments, explanations, or code fences.

INPUT
TASK: {task}
EVIDENCE: {evidence}
INVARIANTS: {invariants}
PATH_COUNT: {path_count}

OUTPUT SHAPE
{{
  "atoms": [
    {{
      "atom_id": "a1",
      "name": "...",
      "description": "...",
      "source_refs": ["..."],
      "evidence_id": "...",
      "expected": "copy one allowed literal exactly",
      "terminal": true
    }}
  ],
  "paths": [
    {{"strategy": "shortest", "atom_ids": ["a1"]}}
  ],
  "minefields": []
}}
