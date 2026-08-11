Hard validity rule: every returned path must independently complete the entire original instruction. Never return a partial path containing only a producer, only a conversion, or a terminal tool fed by invented values. Diversity is allowed only between complete paths.

Using all public inputs below, plan 1 to {max_candidate_path_count} diverse, complete, feasible execution paths:

{task}

Requirements:
- Every path must cover every turn in the same order. Every executable turn must contain the complete tool chain required by its original instruction.
- disposition must be exactly one of `executable`, `needs_clarification`, `no_action`, or `response_only`. Use `response_only` when a missing tool leaves only an explanation to the user; never output the invalid value `non_executable`.
- Try different tools, public information sources, or orders of independent operations. Never create diversity by omitting a required step.
- When initial_state directly exposes a status, every path must use it directly and omit getters that merely confirm the same status; such getters are not required steps. Recovery may be explicit; the compiler also applies environment_rules.
- When the instruction or a public system message explicitly says information is insufficient, a tool is unavailable, or a terminal write cannot be completed, use `response_only` or `needs_clarification` with empty operations and list side-effecting terminal tools in forbidden_evidence_ids. Do not use exploratory search to pretend the task became executable.
- Every executable path must include a terminal tool that actually produces the requested final state or answer. If visible tool_schema has no tool capable of that final effect, a search/get-only path is still incomplete; use `response_only` with empty operations.
- Never invent dynamic IDs, timestamps, coordinates, or other intermediate values to bypass their producer operations. Every dynamic business input needed by a calculation, conversion, or terminal write must come from an earlier producer operation in the same path; only explicit device-setting status may come directly from initial_state. A counterexample must not omit a producer to fake avoidance.
- Raw business records in initial_state exist only for compiler environment simulation; they do not mean the Agent can directly read hidden contact, message, reminder IDs, or timestamps, and they must not be copied into tool arguments. Only explicit device-setting status may skip a status getter. Business data must come from visible search/get tools; without a tool that reads the required namespace, the task is not executable.
- Natural-language date/time writes must call visible datetime/timestamp conversion tools first. Relative time or recency conditions such as latest, oldest, yesterday, or week delta must begin with a current-time tool and retain the complete conversion chain; never mentally calculate or hard-code a timestamp.
- For two operations with no data or environment dependency, the final ensemble should contain both A→B and B→A orderings whenever possible. Never turn an arbitrary sampled order into false common topology. Recovery only needs to precede the tool it actually unlocks, not unrelated search/get operations.
- An information producer and an unrelated environment recovery are independent when both only need to precede the same terminal tool. The final ensemble must include at least one producer→recovery path and one recovery→producer path.
- Use only TOOL_CALL evidence_id values from evidence_catalog. arguments is an ordinary JSON object.
- operations may be empty only when no tool is needed or the task is not executable with visible tools/information.
- forbidden_evidence_ids must be empty for executable turns. Other dispositions may list explicitly forbidden tool evidence.
- Do not output milestones, edges, minefields, quality, bindings, slots, effects, strategy, or prose.

Before returning, reject every executable path that lacks either (a) the requested terminal effect or answer tool, or (b) any producer/conversion required for its dynamic arguments. All remaining executable paths must retain the same complete required effect chain.

Return only:
{{"paths":[{{"turns":[{{"turn_id":"turn_0","disposition":"executable","operations":[{{"evidence_id":"tool_call_id","arguments":{{}}}}],"forbidden_evidence_ids":[]}}]}}]}}
