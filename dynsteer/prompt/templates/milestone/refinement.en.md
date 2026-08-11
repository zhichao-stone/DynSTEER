Hard validity rule: an avoidance target is a hypothesis to test, never a command to remove a required operation. Every returned path must independently complete the full instruction. Reject partial producer-only, conversion-only, or invented-argument paths before output.

Review and rewrite a complete final ensemble of 1 to {max_candidate_path_count} paths using exactly the generation schema.

Public task input: {task}
Original response: {original_response}
Deterministic violations: {validation_violations}
Simulatable normalized draft paths: {simulated_paths}
Preliminary common operations to challenge: {common_operations}

In one pass:
1. Remove or repair structural errors, unknown evidence, and environmentally infeasible paths.
2. Recheck that every path fully completes the original instruction. Omitted-step paths must not enter the final ensemble.
3. For every preliminary common operation, try a complete feasible path that avoids it. Keep the operation when it truly cannot be avoided.

Status directly exposed by initial_state needs no getter; remove getters that merely confirm that status from the final ensemble. When public input explicitly says information is insufficient or a terminal write cannot be completed, do not fabricate an executable path through exploratory search. Return empty operations and consistently forbid side-effecting terminal tools.

Review every operation pair without a data or environment dependency. If the current ensemble shows only one order, add a complete feasible path with the pair reversed. If no visible terminal tool can produce the requested final effect, delete search/get-only paths and replace them with a `response_only` empty path.

Use only `executable`, `needs_clarification`, `no_action`, or `response_only` as disposition; use `response_only` when a tool is unavailable and never emit `non_executable`. Do not invent dynamic arguments to avoid a candidate operation. Business IDs, timestamps, and similar inputs needed by calculations or writes must be produced earlier in the same path; only device-setting status may come from initial_state.

Raw initial_state business rows are not directly readable Agent argument sources. Contact, message, reminder IDs, and business timestamps must come from visible search/get producers. Preserve datetime/timestamp conversion for natural-language date writes, and preserve current-time plus the complete conversion chain for latest/oldest/yesterday/week-delta tasks. Cover both relative orders of an information producer and an unrelated recovery in the final ensemble.

Return only the reviewed complete final paths JSON, not only new counterexamples and no prose.

Final checklist: every executable path contains the requested terminal effect/answer and every required producer/conversion; every response-only path has empty operations and consistently forbids visible terminal tools that would cause the invalid side effect.
