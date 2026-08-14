You generate candidate milestone goal graphs, not step-by-step trajectories. A node belongs in one candidate graph only when, under that candidate's complete and feasible interpretation, every valid completion must reach that operation or goal. One candidate is a hypothesis about necessity; necessity across candidates is decided later by code aggregation.

This is candidate batch {batch_index}. Focus code: `{focus_code}`.
Batch focus: {focus_instruction}

Return exactly 2 complete, standalone candidate graphs for the current task.

Independence and diversity rules:
- Each graph must define its own dispositions, nodes, edges, and minefields. Local IDs have graph-local scope only.
- Do not share a node table, refer to a node in the other graph, or encode graph 2 as a base/delta/patch of graph 1.
- Seek genuine diversity between two complete judgments: a different visible tool, direct use of public information, a valid bulk state goal, a different necessary dependency, or a justified executability judgment.
- Never create diversity by omitting a required producer, conversion, recovery, terminal goal, or user-facing answer; by adding irrelevant tools; or by inventing arguments.
- If no genuine alternative is supported, the two graphs may be equivalent. Completeness and correctness take priority over artificial difference.

Compact structural examples

The example evidence IDs, selectors, and source references below are illustrative only. For the current task, use only evidence IDs and public source references present in the current task JSON.

Example 1
Task: "What's my relationship with +10000000000"
Meaning: the contact search produces the dynamic answer, then the Agent must answer the user.

{{
  "dispositions": {{"turn_0": "executable"}},
  "nodes": [
    {{
      "local_id": "n0",
      "turn_id": "turn_0",
      "kind": "tool_call",
      "evidence_id": "example_search_contacts",
      "arguments": {{
        "phone_number": {{
          "source": "public_literal",
          "source_ref": "instruction:0",
          "value": "+10000000000"
        }}
      }}
    }},
    {{
      "local_id": "n1",
      "turn_id": "turn_0",
      "kind": "emit_message",
      "sender": "AGENT",
      "recipient": "USER",
      "content_requirement": "Answer with the relationship returned for the requested phone number"
    }}
  ],
  "edges": [["n0", "n1"]],
  "minefields": []
}}

Example 2
Task: "Remove my upcoming reminder."
Meaning: current time and reminder search are independent producers needed to identify the upcoming reminder; both support the removal state goal. There is no edge between the two producers.

{{
  "dispositions": {{"turn_0": "executable"}},
  "nodes": [
    {{
      "local_id": "n0",
      "turn_id": "turn_0",
      "kind": "tool_call",
      "evidence_id": "example_get_current_timestamp",
      "arguments": {{}}
    }},
    {{
      "local_id": "n1",
      "turn_id": "turn_0",
      "kind": "tool_call",
      "evidence_id": "example_search_reminder",
      "arguments": {{}}
    }},
    {{
      "local_id": "n2",
      "turn_id": "turn_0",
      "kind": "set_state",
      "namespace": "REMINDER",
      "operation": "remove",
      "cardinality": "one",
      "match": {{
        "reminder_id": {{
          "source": "node_output",
          "producer_local_id": "n1",
          "selector": "$.reminders[0].reminder_id",
          "cardinality": "one"
        }}
      }},
      "values": {{}},
      "executor_evidence_id": "example_remove_reminder"
    }}
  ],
  "edges": [["n0", "n2"], ["n1", "n2"]],
  "minefields": []
}}

In this example, current time is indispensable to target selection but is not itself a REMINDER record field, so it must not be invented as a `match` key. Its edge to the state goal records the target-selection dependency; the selected reminder ID is dynamically bound from the search output. The shown selector remains illustrative and must be replaced by one supported by the current producer contract.

Current public task JSON

{task}

Required semantics

1. Turns and dispositions
- Every graph's `dispositions` object must contain exactly every turn ID from `turns`, with no missing or extra key.
- Each value must be exactly one of `executable`, `needs_clarification`, `no_action`, or `response_only`.
- Cover all turns in the task's original order. Assign every node and minefield to an existing turn ID.
- A non-`executable` turn must not contain `tool_call` or `set_state`. It may contain an `emit_message` only when a response to the user is itself required.
- Every `executable` turn must contain a final goal: a `set_state`, an `emit_message`, or a terminal `tool_call` that directly and completely performs the requested result when neither state-goal nor message-goal representation applies.
- A graph containing only search/getter/producer/conversion/recovery nodes is incomplete and invalid.

2. Necessity, feasibility, and goals
- Keep only milestones that are necessary under this graph's complete judgment. Do not include a useful, conventional, defensive, or confirmatory operation unless the task cannot be completed correctly without its output or effect.
- A producer is necessary only when a retained consumer needs its dynamic output or when its observed value is indispensable to the final answer or target selection.
- Represent a requested business-state change as one `set_state` goal. Put the real terminal tool's evidence ID in `executor_evidence_id`; do not also emit a duplicate terminal `tool_call` for the same effect.
- Keep search, getter, conversion, and recovery calls as separate `tool_call` nodes only when they are genuinely necessary producers or prerequisites.
- Represent a required user-facing answer with `emit_message`. `content_requirement` states what the answer must communicate; it must not fabricate the unknown runtime answer.
- Use visible public data directly when sufficient. Do not add a getter merely to reconfirm the same public value.
- If visible tools or public inputs cannot safely complete the task, do not invent a completion. Use an appropriate non-executable disposition and a legal empty or response-only graph.

3. Node schemas and provenance
- The only allowed node kinds are `tool_call`, `set_state`, and `emit_message`. Never output `preserve_state`; it is derived by code.
- A `tool_call` node has exactly `local_id`, `turn_id`, `kind`, `evidence_id`, and `arguments`.
- `evidence_id` must be a TOOL_CALL evidence ID from the current `evidence_catalog`. Never use example IDs or invent an ID.
- `arguments` contains only argument names accepted by that tool. Every argument value must use exactly one of the two binding forms below; do not put a raw scalar directly in `arguments`.

Public literal binding:
{{"source":"public_literal","source_ref":"an exact current public source reference","value":"the literal value from that source"}}

Node-output binding:
{{"source":"node_output","producer_local_id":"a producer in this same graph","selector":"$.field","cardinality":"one"}}

- `public_literal.source_ref` must point to the exact current instruction/turn or Agent-visible public asset that exposes the value. Public state is usable only when the current task JSON exposes it to the Agent.
- A `node_output` producer must be an earlier reachable `tool_call` in the same graph. Its selector and `one`/`all` cardinality must match the producer's real output.
- Every node-output binding requires a producer-to-consumer edge.
- Do not use a `derived` source. Any required lookup, calculation, date/time conversion, coordinate conversion, or recovery must be represented by a real visible `tool_call`, then referenced through `node_output`.
- Never hard-code or copy a dynamic UUID, row ID, person/message/reminder ID, timestamp, coordinate, token, or tool-produced value as `public_literal` unless that exact value is explicitly visible in a permitted current source.

A `set_state` node has exactly:
{{
  "local_id": "n_goal",
  "turn_id": "turn_0",
  "kind": "set_state",
  "namespace": "CONTACT|MESSAGING|REMINDER|SETTING or another current supported namespace",
  "operation": "add|update|remove|set",
  "cardinality": "one|all",
  "match": {{}},
  "values": {{}},
  "executor_evidence_id": "a current terminal TOOL_CALL evidence ID"
}}

- Each value inside `match` and `values` must use one of the same two binding forms.
- `match` selects target records; `values` describes fields to add or change.
- For `add`, `values` is non-empty and `match` may be empty. For `update` or `set`, `values` is non-empty. For `remove`, `match` is non-empty. Use only `one` or `all` for cardinality.
- `executor_evidence_id` must identify a current visible terminal tool that can actually perform this namespace and operation with the represented inputs.

An `emit_message` node has exactly:
{{
  "local_id": "n_answer",
  "turn_id": "turn_0",
  "kind": "emit_message",
  "sender": "AGENT",
  "recipient": "USER",
  "content_requirement": "a non-empty requirement for the answer"
}}

4. Edges
- `edges` is an array of `[source_local_id, target_local_id]` pairs whose endpoints are nodes in the same graph.
- Add an edge only for a true non-commutable data, prerequisite, or goal dependency. Array order is not an edge.
- Do not order two independent producers merely because one is commonly called first. If both independently feed one consumer, connect each producer to the consumer and do not connect the producers to each other.
- Do not add cross-turn edges only to restate conversation order; code adds deterministic turn-order dependencies.
- No self-loop, duplicate edge, missing endpoint, or cycle is allowed.

5. Minefields and legal empty graphs
- A minefield marks a fatal side-effecting tool call that must not occur for the stated turn. It has exactly:
{{
  "turn_id": "turn_0",
  "evidence_id": "a current terminal TOOL_CALL evidence ID",
  "severity": "fatal",
  "reason_code": "missing_required_input|tool_unavailable|unsafe_side_effect",
  "missing_inputs": []
}}
- Use only severity `fatal` and the three listed reason codes. Fill `missing_inputs` only for truly unavailable required inputs; otherwise use an empty array.
- The evidence must identify a real current side-effecting terminal tool, and the reason must be supported by the current public task. Do not infer danger from a tool name alone.
- Do not put the same terminal effect in both an executable goal and a fatal minefield for the same turn.
- A candidate may legally have empty `nodes` and `edges`. If no safe tool action or answer milestone is required, `minefields` may also be empty. If the task is blocked and attempting a terminal side effect would be fatal, include the justified minefield.

6. Exact response shape
- Return one JSON object with exactly one top-level key, `graphs`.
- `graphs` must contain exactly 2 graph objects.
- Every graph object has exactly `dispositions`, `nodes`, `edges`, and `minefields`.
- Do not add description, rationale, reasoning, confidence, quality, strategy, shared nodes, metadata, comments, or any other field.
- Return raw valid JSON only. Do not use Markdown fences and do not write any prose before or after the JSON.

Final checklist before returning
- Exactly two standalone complete graphs?
- Exact disposition keys for all current turns?
- Every executable turn has its final state/action/answer goal?
- No producer-only or search-only executable graph?
- All evidence IDs and public source references come from the current task?
- Every dynamic value has a reachable in-graph producer and edge?
- Only true dependencies are edges, with independent producers unordered?
- No invented ID, timestamp, coordinate, selector, argument, tool capability, or extra field?
- Empty/non-executable graph used instead of a fabricated completion when necessary?

Return only this shape, populated for the current turns and task:
{{"graphs":[{{"dispositions":{{}},"nodes":[],"edges":[],"minefields":[]}},{{"dispositions":{{}},"nodes":[],"edges":[],"minefields":[]}}]}}
