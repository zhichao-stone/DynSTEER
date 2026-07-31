# Experiment API

## 鐩爣

`dynsteer.experiment` 璐熻矗缁熶竴缂栨帓 ToolSandbox銆丼WE-bench Pro 绛?benchmark 鐨勫疄楠岀煩闃碉紝閬垮厤鎶?`{benchmark, model, method, repeat}` 缁勫悎閫昏緫濉炶繘 harness 鎴?evaluator銆?
## 閰嶇疆鍏ュ彛

```bash
python main.py --exp data/experiments/double_benchmark_initial.json
```

閰嶇疆瀛楁锛?
- `experiment_id`: 瀹為獙 ID銆?- `benchmarks`: benchmark 鏁扮粍锛屾瘡椤瑰寘鍚?`benchmark`銆乣data_root`锛屽彲閫?`case_ids/scenarios` 鍜?`metadata`銆?- `models`: 妯″瀷鏁扮粍锛屾瘡椤瑰寘鍚?`model_id`锛屽彲閫?`metadata`銆乣harness_metadata`銆?- `methods`: 鏂规硶鏁扮粍锛屾敮鎸?`default`銆乣dynsteer_evaluate`銆乣dynsteer_replay`銆乣dynsteer_replay_static`銆?- `judge_profiles`: Judge 鐨?provider銆乵odel銆乥ase_url銆乼emperature銆乵ax_tokens銆乵ax_retries 绛夐厤缃紱API key 鍙粠鐜鍙橀噺璇诲彇銆?- `threshold_profiles`: `ThresholdConfig` 瀛楁闆嗗悎銆?- `threshold_matrix`: 闇€瑕佸睍寮€鐨勯槇鍊兼。浣嶃€?
鍚姩鑴氭湰 `scripts/start_experiment.sh` 涓?`scripts/start_experiment_no_docker.sh` 浼氭牴鎹?`benchmarks[*].data_root` 璇诲彇瀵瑰簲 `benchmark.json`锛岃嚜鍔ㄤ娇鐢?`source_root` 瀹夎鎴栨寕杞?benchmark 婧愮爜锛屽苟浣跨敤 `max_workers` 浣滀负榛樿 worker 鏁帮紱`--source`銆乣--workers` 浠呬綔涓鸿鐩栭」銆俙--force_adapt` 浼氬湪璇勪及鍓嶅己鍒堕噸寤?`data/<benchmark>/adapted_cases`锛屽苟鑷姩绛変环浜?`--force_eval`锛沗--force_eval` 鍙己鍒堕噸璺戣瘎浼?case 浜х墿锛沗--no_sum` 鍙烦杩囧疄楠岀骇姹囨€绘枃浠躲€?鐩存帴璋冪敤 `main.py --exp ...` 鏃讹紝benchmark 婧愮爜浠嶉渶瑕佸凡鍦ㄥ綋鍓嶇幆澧冧腑鍙鍏ワ紱鑷姩 bootstrap 閫昏緫鍙湪 wrapper 鑴氭湰涓墽琛屻€?
ToolSandbox 鐨?agent/user 杩炴帴鍙傛暟搴旀斁鍦?`models[*].harness_metadata` 涓紝涓?`agent`銆乣user` 瑙掕壊绫诲瀷鍚岀骇锛?
```json
{
    "model_id": "GPT_4_o_2024_05_13",
    "harness_metadata": {
        "agent": "GPT_4_o_2024_05_13",
        "user": "GPT_4_o_2024_05_13",
        "agent_client": {
            "api_key_env": "DYNSTEER_AGENT_API_KEY",
            "base_url_env": "DYNSTEER_AGENT_BASE_URL"
        },
        "user_client": {
            "api_key_env": "DYNSTEER_USER_API_KEY",
            "base_url_env": "DYNSTEER_USER_BASE_URL"
        }
    }
}
```

`judge_profiles` 鍙厤缃?DynSTEER Judge锛屼笉浼氬奖鍝?ToolSandbox 鍘熺敓 agent/user role锛涗笉瑕佹妸 agent/user 鐨?client 閰嶇疆鍐欏埌 `judge_profiles`銆?
## 鎵ц娴佺▼

`run_experiment(config_path, workers=1, force_adapt=False, force_eval=False, no_sum=False)` 浼氬厛灞曞紑瀹為獙鐭╅樀锛屽啀鎸?benchmark 鍚嶇О鍑嗗 `TaskCase` 妯℃澘銆傛瘡涓?benchmark 鍦ㄥ悓涓€娆″疄楠屼腑鍙皟鐢ㄤ竴娆?`load_task_case(...)`锛沗model`銆乣method`銆乣judge_profile`銆乣threshold_profile` 鍜?`repeat` 涓嶄細瑙﹀彂 adapted case 閲嶆柊鍔犺浇鎴栭噸寤恒€?
`force_adapt=True` 鏄?adapted case 閲嶅缓鐨勫敮涓€鏄惧紡寮€鍏筹紝骞朵笖鍙綔鐢ㄤ簬涓婅堪缁熶竴鍑嗗闃舵锛涚敱浜庡熀纭€杈撳叆宸插彉鍖栵紝瀹冧細鑷姩寮哄埗 `force_eval=True`銆傚悗缁?`default`銆乣dynsteer_replay`銆乣dynsteer_replay_static`銆乣dynsteer_evaluate` 浼氫粠鍚屼竴鎵规ā鏉挎繁鎷疯礉寰楀埌鍗?case 杈撳叆锛岃繍琛屾湡瀵?`TaskCase.initial_state` 鎴?metadata 鐨勫啓鍏ヤ笉浼氭薄鏌撳叾浠?method銆俙force_eval=False` 鏃讹紝鑻?case 绾?`runs/results` 浜х墿瀹屾暣瀛樺湪锛屼細鐩存帴澶嶇敤缂撳瓨锛沗no_sum=True` 鏃朵粛浼氬啓 case 绾т骇鐗╋紝浣嗕笉鍐?`index.json`銆乣scores.json` 鍜?`metrics.json`銆?
Default 杈撳嚭鐨?`trajectory.json` 浼氭惡甯︽湰娆?session 鐨?`runtime_initial_state`銆俁eplay 璇诲彇 default trajectory 鍚庯紝浼氫紭鍏堟妸杩欎釜 runtime initial state 娉ㄥ叆褰撳墠 `TaskCase.initial_state`锛岀‘淇?`preserve_state`銆乣reference_milestone_node_index=-1` 绛夌姸鎬佺害鏉熶娇鐢?default 鐪熷疄鍒濆鐘舵€侊紝鑰屼笉鏄?adapted JSON 涓彲鑳借繃鏈熺殑闈欐€佸崰浣嶃€?
## 杈撳嚭

瀹為獙灞傝緭鍑哄埌 `results/exp/<experiment_id>/` 鎴栭厤缃寚瀹氱殑 `results_dir`锛沜ase 浜х墿鎸?`<benchmark>/<model_id>/<method>/<case_id>/` 鍒嗗眰鍐欏叆锛岄潪瀹為獙 harness 浠嶇戶缁繚鎸?`<benchmark>/<method>/<case_id>/`銆傜粺涓€瀹為獙榛樿鐩綍涓猴細

- `runs/exp/<experiment_id>/<benchmark>/<model_id>/<method>/<case_id>/`
- `results/exp/<experiment_id>/<benchmark>/<model_id>/<method>/<case_id>/`

- `index.json`: 鍒嗗眰 case 绱㈠紩锛屾寜 `benchmark -> method -> model_id -> repeats -> repeat_index -> cases -> case_id` 缁勭粐銆?- `scores.json`: `method -> benchmark -> model_id -> average_score`銆?- `metrics.json`: PSEP銆乣rank_tau`銆佽€楁椂銆佹楠ゆ暟銆丄gent/Judge token 姹囨€汇€?
`index.json` 鐨?repeat 鑺傜偣鍙繚鐣?`cases`锛宑ase 鍙跺瓙鍙繚鐣欑粨鏋滄湰韬紝涓嶅啀閲嶅鍐?`experiment_id`銆乣benchmark`銆乣case_id`銆乣model_id`銆?
```json
{
    "experiment_id": "double_benchmark_initial",
    "case_count": 4,
    "results": {
        "toolsandbox": {
            "default": {
                "toolsandbox_gpt4o": {
                    "repeats": {
                        "0": {
                            "cases": {
                                "add_contact_with_birthday": {
                                    "score": 1.0,
                                    "default_score": 1.0,
                                    "dynsteer_score": null,
                                    "resolved": true
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
```

case 浜х墿浠嶅鐢?harness 鐩綍锛?
- Default: `default_report.json`銆乣summary.json`銆乣raw_summary.json`銆乣trajectory.json`銆?- Replay/Evaluate: `report.json`銆乣summary.json`銆乣raw_summary.json`銆乣trajectory.json`銆?
`scores.json` 涓?`metrics.json` 淇濇寔绾?JSON 缁撴瀯锛屼笉鍐呭祵娉ㄩ噴瀛楁锛涘瓧娈佃涔夐€氳繃鏈枃妗ｈ鏄庯細

- `scores.json`: 姣忎釜鍙跺瓙鍊奸兘鏄悓涓€ `method / benchmark / model_id` 涓嬬殑 case 骞冲潎鍒嗐€?- `metrics.json`: `efficiency` 姹囨€昏€楁椂涓庢楠ゆ暟锛宍cost` 姹囨€?token锛宍psep` 涓?`rank_tau` 鏄法妯″瀷瀵规瘮鎸囨爣銆?
## 鎸囨爣

- `case_score(value)`: 灏?bool銆佹暟瀛楁垨鍚?`score/resolved/similarity` 鐨勫璞″綊涓€鍒?`[0, 1]`銆?- `model_scores(results)`: 璁＄畻 `S_{m,e,b}`銆?- `psep(scores)`: 璁＄畻妯″瀷瀵瑰湪骞冲潎寰楀垎涓婄殑闂磋窛銆?- `aggregate_efficiency(results)`: 姹囨€昏€楁椂鍜?Agent 姝ラ鏁般€?- `aggregate_cost(results)`: 姹囨€?Agent trajectory token 鍜?Judge LLM token锛屽苟淇濈暀鍙敤鎬ф爣璁般€?
## 闄勫姞璇存槑

`run_experiment(config_path, workers=1, force_adapt=False, force_eval=False, no_sum=False)` 鐜板湪浼氭帴鏀?CLI 鐨?`--workers`銆傚疄闄呭苟鍙戞暟鎸夋瘡涓?spec 鐨?`workers` 涓庡搴?benchmark 鐨?`benchmark_max_workers` 鍙栬緝灏忓€硷紱褰?`benchmark.json` 娌℃湁鎻愪緵 `max_workers` 鏃讹紝瀹為獙灞備細鐩存帴浣跨敤 CLI 鐨?`workers`銆?

## Replay 消融方法

统一实验配置的 `methods` 字段支持以下 replay 消融方法：

- `dynsteer_replay`: 完整 DynSTEER replay，默认 `dynamic_routing=True`、`dynamic_weighting=True`。
- `dynsteer_replay_static`: 静态路由 + 静态权重，强制 `dynamic_routing=False`、`dynamic_weighting=False`。
- `dynsteer_replay_static_weighting`: 动态路由 + 静态权重，强制 `dynamic_routing=True`、`dynamic_weighting=False`。
- `dynsteer_replay_static_routing`: 静态路由 + 动态权重，强制 `dynamic_routing=False`、`dynamic_weighting=True`。

上述 replay 方法都会先准备同一组 `default` 轨迹，再基于该轨迹执行离线评估；`policy_stop`、`fixed_judge_level`、`replay_continue_after_virtual_stop` 和 `strategy.metadata` 仍可通过 method 级 `strategy` 配置覆盖。
