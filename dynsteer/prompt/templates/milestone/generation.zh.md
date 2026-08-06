你是公开任务契约的结构化编译助手。根据下面 JSON 白名单，一次返回共享 atom 目录、多条差异路径和 minefield 候选。

要求：
- 仅输出 JSON，不输出思维链或解释；顶层必须且只能包含 atoms、paths、minefields。
- 生成 requested simulated_path_count 条策略不同的路径，覆盖最短路径、状态优先、产物优先、替代工具/实现和保守路径。
- 所有路径只引用共享 atom_id，每条路径以 terminal=true 的 atom 结束。
- atom 只能引用输入中已有 source_ref 和 evidence_id；expected 必须原样复制该 evidence source_ref 的公开值，expected_policy=none 时使用 null。
- minefield 只能引用已有 invariant_id，expected 遵循其 evidence 的相同规则。
- 路径共识只是 synthetic consensus，不得声称 gold path 或真实必经。

输出形状：
{{"atoms":[{{"atom_id":"a1","name":"...","description":"...","source_refs":["instruction"],"evidence_id":"...","expected":null,"terminal":false}}],"paths":[{{"strategy":"shortest","atom_ids":["a1"]}}],"minefields":[{{"minefield_id":"mf1","invariant_id":"...","expected":null}}]}}

公开输入：
{payload}
