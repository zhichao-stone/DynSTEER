import json
import os



with open(os.path.join("docs", "plans", "toolsandbox_rapid_api_key_scenarios.json"), "r", encoding="utf-8") as fr:
    datas: dict = json.load(fr)


scenarios_without_rapid: dict[str, list[str]] = datas["scenarios_without_rapid"]
scenarios: list[str] = []
for s in scenarios_without_rapid.values():
    scenarios.extend(s)

with open(os.path.join("docs", "plans", "toolsandbox_scenarios_without_rapid"), "w", encoding="utf-8") as fw:
    json.dump(scenarios, fw, ensure_ascii=False, indent=4)