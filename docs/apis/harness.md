# Harness API

> 当前结果 schema 为 v3：default/evaluate/replay 均生成 `report.json`；`summary.json` 使用单一 `score`、嵌套 `runtime_metrics` 与结构化 `termination={should_stop, code, reason, detail}`。运行循环只以 `HarnessAdvanceResult.continue_running` 为准，不再提供 `case_finished()` 或 `BaseBenchmarkConstraintScorer`。

## ToolSandbox history 完整性

`ToolSandboxHarness.advance_case()` 每次只调用一次原生 role `respond()`，随后读取 `get_all_history_snapshots=True` 的完整 SANDBOX history，按 `last_sandbox_message_index` 过滤本批新增 rows，并按原生 index 升序生成 step 和 snapshot。一次 respond 产生的并行 tool calls、tool results 与最终消息都会被保留；重复 index、缺失 index 或乱序 steps 会明确报错。只有整批 step/snapshot 构造成功后才推进 session 的 last index。

同一批新增 rows 可以包含多个连续的 Agent tool outbound。harness 不丢弃前面的调用、不把批次重排为伪串行序列，也不负责闭包配对；row 转换会把 tool call 与 tool result 的 `openai_tool_call_id` 保留在 `TrajectoryStep.raw`，交由公共 `AgentStepTracker` 精确配对。该字段在 `trajectory.json` 序列化和公共 loader 往返后保持不变。

实验 runner 会进一步校验 ToolSandbox native `milestone_mapping[*].snapshot_index` 全部存在于 DEFAULT `trajectory.json` 的 `snapshots[*].raw.sandbox_message_index`，缺失时拒绝把该轨迹用于 replay。

## 运行期动态 target

`prepare_task_cases(config, force_adapt=False, refresh_dynamic_targets=True)` 在每次 experiment preparation 中调用 adapter 的 `refresh_task_case_for_experiment()`。ToolSandbox 使用与 harness session 相同的共享 scenario dictionary，把当前 scenario `target_dataframe` 转换得到的值写入 `Constraint.expected`，同步 `stage_goal_semantics["expected"]`，再实例化 `stage_goals`。

`TaskCase.stage_goal_templates` 是 stage goal 的 canonical source，模板中的 expected 使用 `[[<constraint_id>.expected]]`。`TaskCase.stage_goals` 只是当前 expected 的实例化结果。adapted JSON 的 `initial_state` 固定写为 `null`；真实初始状态只在 `start_case()` 后由 harness session 注入，不参与 expected 或 stage goal 计算。

## 鐩爣

Harness API 鐢ㄤ簬鎶?benchmark 鍘熺敓鎵ц杩囩▼鎺ュ叆 DynSTEER銆傚綋鍓嶈亴璐ｈ竟鐣屾槸锛?
- `BaseBenchmarkAdapter`: 璐熻矗鎸?case 閫傞厤 `TaskCase`銆佸啓鍏?澶嶇敤 adapted JSON 缂撳瓨銆?- `BaseBenchmarkHarness`: 鍙礋璐?benchmark 鍘熺敓 session 鐢熷懡鍛ㄦ湡銆佸閲忚娴嬫壒娆￠噰闆嗐€佸師鐢熸憳瑕佸拰璧勬簮娓呯悊锛屼笉鍐嶆瀯閫?`TaskCase`銆?- `DynSTEEREvaluator`: 璐熻矗鎵ц缂栨帓銆乵ilestone checkpoint銆侀樁娈靛紡鍔ㄦ€佽瘎浼般€丩LMJudge 璋冨害鍜?fail-fast銆?
Harness 涓嶅啀鎷ユ湁 `run_case()` 涓荤紪鎺掑叆鍙ｏ紝涔熶笉璐熻矗闃舵璇勫垎銆佸姩鎬佹潈閲嶆洿鏂版垨 minefield 绛栫暐缁堟銆?
## 鏍稿績鏁版嵁缁撴瀯

- `HarnessRunConfig`: 鍗曟 harness 杩愯閰嶇疆锛屽寘鍚?benchmark銆乨ata root銆乧ase_ids銆乺uns_dir銆乺esults_dir銆乫ail-fast 绛栫暐鍜?metadata銆俙case_ids` 鍙敤浜?adapter/loader 闃舵閫夋嫨鏈瑕佸姞杞界殑 case锛涜繘鍏ュ崟 case 鎵ц鍚庯紝褰撳墠 case 韬唤缁熶竴鏉ヨ嚜 `TaskCase.case_id`銆?- `BenchmarkCase`: benchmark 鍐呭崟涓彲杩愯娴嬭瘯浠诲姟銆?- `HarnessAdvanceResult`: `advance_case()` 鐨勭粨鏋勫寲杩斿洖鍊硷紝鍖呭惈 raw `steps`銆乣snapshots`銆乣continue_running` 鍜屽彲閫?`reason`銆俙snapshots` 鏄繀濉瓧娈碉紝琛ㄧず鏈壒鎺ㄨ繘鍚庡彲瑙佺殑鐘舵€佸揩鐓э紝蹇呴』涓?`steps` 浣跨敤鍚屼竴鏃堕棿鍧愭爣銆?- `HarnessStageSettlement`: evaluator 鍦ㄨ繍琛屾湡鐢熸垚鐨?start/milestone/finish 闃舵缁撶畻鑺傜偣銆?- `HarnessRunResult`: evaluator 杩斿洖鐨?benchmark 杩愯缁撴灉锛屽寘鍚?`TaskCase`銆乣Trajectory`銆侀樁娈电粨绠椼€佺瓥鐣ョ粓姝㈠瓧娈靛拰 `evaluation_report`銆?
## BaseBenchmarkAdapter 鎺ュ彛

```python
def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase: ...
```

adapter 璐熻矗鎶婂師鐢?benchmark case 杞崲涓?DynSTEER `TaskCase`銆俽unner 閫氳繃 `dynsteer.adapter.loader.load_task_case(config, adapter, force_adapt=False)` 鎸?`data/{benchmark}/adapted_cases/<case_id>.json` 璇诲彇缂撳瓨锛涚己澶辨垨缁撴瀯涓嶅畬鏁存椂鍙Е鍙戝綋鍓?case 鐨?`adapt_task_case()` 骞朵繚瀛樺崟 case JSON銆俙force_adapt=True` 鏄?adapted case 閲嶅缓鐨勫敮涓€鏄惧紡寮€鍏筹紝浼氬湪鍔犺浇闃舵蹇界暐缂撳瓨骞堕噸寤哄搴?case銆?
杩愯鏈?harness 鐢?`dynsteer.adapter.registry.get_harness(benchmark)` 鐩存帴鍒涘缓锛屼笉鍐嶉€氳繃 adapter 闂存帴鍒涘缓銆傝繖鏍峰崟 case 杩愯鏈熸墽琛岀瓑鍙渶瑕?harness 鐨勮矾寰勪笉浼氬疄渚嬪寲 adapter锛宎dapter 涔熶笉鍐嶆壙鎷?harness 宸ュ巶鑱岃矗銆?
## Adapter 涓?Stage Goal 璇箟杈圭晫

Adapter 鍙互鐞嗚В benchmark 绉佹湁鏍煎紡锛屽苟鎶婄鏈夌害鏉熻В閲婁负 DynSTEER 閫氱敤 `Constraint.stage_goal_semantics`銆備緥濡傛煇 benchmark 鐨勨€滀繚鎸佸弬鑰冪姸鎬佷笉鍙樷€濈害鏉熷簲鍦?Python 浠ｇ爜涓槧灏勪负 `{"kind": StageGoalSemanticKind.PRESERVE_STATE.value}`锛岃惤鐩樺悗琛ㄧ幇涓?`{"kind":"preserve_state"}`銆?
Adapter 涓嶅簲鐩存帴鐢熸垚 `TaskCase.stage_goals`锛屼篃涓嶅簲鎻愪緵 benchmark 涓撶敤 stage_goal hook銆俙TaskCase.stage_goals` 鐢?`dynsteer.stage.generate_stage_goals(...)` 缁熶竴鐢熸垚銆?
绉佹湁璇勫垎瀛楁浠嶄繚鐣欏湪 benchmark 鑷繁鐨?metadata key 涓嬶紝渚涗笓鐢?scorer 浣跨敤锛涘叕鍏?stage_goal 鍜?judge prompt 涓嶈鍙栬繖浜涚鏈夊瓧娈点€?
## BaseBenchmarkHarness 鎺ュ彛

瀛愮被闇€瑕佸疄鐜颁互涓嬪叕寮€鎺ュ彛锛?
```python
def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]: ...
def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object: ...
def advance_case(self, session: object) -> HarnessAdvanceResult: ...
def case_finished(self, session: object) -> bool: ...
```

鍩虹被鎻愪緵浠ヤ笅榛樿鎺ュ彛锛?
```python
def prepare_config(self, config: HarnessRunConfig) -> None: ...
def metrics_from_session(self, session: object) -> JsonObject: ...
def initial_state_from_session(self, session: object) -> JsonObject | None: ...
def final_state_from_session(self, session: object) -> JsonObject | None: ...
def raw_summary_from_session(self, session: object) -> JsonObject: ...
def default_result_from_session(self, session: object) -> BenchmarkDefaultResult: ...
def stop_case(self, session: object, reason: str) -> None: ...
def teardown_case(self, session: object) -> None: ...
def constraint_scorer(self) -> BaseBenchmarkConstraintScorer: ...
```

### `BaseBenchmarkHarness.constraint_scorer()`

杩斿洖褰撳墠 benchmark 鐨勭害鏉熻瘎鍒嗗櫒銆傞粯璁よ繑鍥?`BaseBenchmarkConstraintScorer()`锛屽叾琛屼负绛夊悓 `GeneralScorer`銆?闇€瑕佽В閲?`Operator.CUSTOM` 鎴?benchmark 鍘熺敓绾︽潫鐨?harness 搴旇鍐欒鏂规硶銆?
```python
class MyHarness(BaseBenchmarkHarness):
    def constraint_scorer(self) -> BaseBenchmarkConstraintScorer:
        return MyBenchmarkConstraintScorer()
```

### `BaseBenchmarkHarness.default_result_from_session()`

瀹屾暣 Default 瀹為獙浼氬湪 benchmark 鑷劧缁撴潫鍚庤皟鐢ㄨ鎺ュ彛锛屾彁鍙栧師鐢?benchmark 鍒嗘暟銆傛寮忓疄楠?benchmark 蹇呴』鏄惧紡瀹炵幇锛岃繑鍥烇細

- `score`: `[0, 1]` 连续主分数；`raw` 保存原生 similarity、milestone/minefield mapping；`metrics` 保存 turn count 等原生统计。结果不派生或输出 `resolved`、`success_basis` 和成功阈值。

ToolSandbox 会话未显式配置 `max_messages` 时继续使用 DynSTEER harness 默认值 100，显式配置时按 override 执行。原生结束原因结构化为 `natural_end_conversation`、`max_messages` 或 `role_error`；不增加重复话术、重复空查询或 `simulator_stall` detector。
ToolSandbox 褰撳墠閫氳繃 `scenario.evaluation.evaluate(execution_context=session.context, max_turn_count=session.max_messages)` 鎻愬彇 `similarity`锛屽苟鎶?`milestone_mapping`銆乣minefield_mapping` 鍜?`turn_count` 鍐欏叆 raw銆?
## 杩斿洖濂戠害

- `TaskCase` 蹇呴』鍦?adapter/loader 闃舵瀹屾垚閫傞厤锛宧arness 杩愯鏈熶笉鎻愪緵 `task_case_from_session()`銆?- `advance_case()` 蹇呴』杩斿洖 `HarnessAdvanceResult`锛屼笉鑳借繑鍥?`None`銆?- `advance_case()` 璐熻矗鍒ゆ柇绌烘楠ゆ槸鍚﹀悎鐞嗐€傝嚜鐒跺畬鎴愭椂杩斿洖 `HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False, reason="benchmark 宸茶嚜鐒跺畬鎴?)`銆?- 濡傛灉 session 鏈畬鎴愪絾娌℃湁鏂板姝ラ锛宍advance_case()` 搴斿湪 harness 鍐呴儴鎶涘嚭寮傚父銆?- 鎵€鏈?harness 蹇呴』閫氳繃 `HarnessAdvanceResult.snapshots` 杩斿洖鏈壒鎺ㄨ繘鍚庡彲瑙佸揩鐓э紱娌℃湁鐘舵€佸揩鐓х殑 benchmark 蹇呴』鏄惧紡杩斿洖绌烘暟缁勩€?- `snapshots_from_session()` 涓嶅啀灞炰簬 `BaseBenchmarkHarness` 鍏紑杩愯鏈熸帴鍙ｃ€?- 瀵瑰甫鍘熺敓鍏ㄥ眬娑堟伅绱㈠紩鐨?benchmark锛宍steps[].index` 涓?`snapshots[].after_step_index` 蹇呴』浣跨敤鍚屼竴鍧愭爣绯汇€?- `steps[]` 蹇呴』鎻愪緵瑙勮寖鍖?`actor` 涓?`recipient`銆侱ynSTEER 鐢ㄥ畠浠粍瑁呬覆琛?agent step 闂寘锛歚Agent -> X` 鍚庡繀椤荤敱瀵瑰簲 `X -> Agent` feedback 闂悎锛涙湭闂悎 outbound 涓嶄細瑙﹀彂 milestone matching 鎴?no-progress 瑙傚療銆?- `metrics_from_session()`銆乣raw_summary_from_session()` 搴旀妸鍙己鐪佺粨鏋滃綊涓€涓虹┖瀛楀吀銆?- `initial_state_from_session()` 杩斿洖褰撳墠 session 鐨勭湡瀹炲垵濮嬬姸鎬侊紱榛樿杩斿洖 `None`銆傚甫鍔ㄦ€佸垵濮嬬姸鎬佺殑 benchmark 搴斿湪 `start_case()` 鍚庡浐鍖栬鐘舵€侊紝渚?`reference_milestone_node_index=-1` 绛?guardrail 寮曠敤銆?- `final_state_from_session()` 杩斿洖褰撳墠鎴栨渶缁堢姸鎬侊紱榛樿杩斿洖 `None`銆傜姸鎬佷笉鍙敤鏃朵笉瑕佷吉閫犵┖瀵硅薄銆?- `case_finished()` 鍙綔涓烘煡璇㈡帴鍙ｆ垨瀛愮被鍐呴儴杈呭姪鑳藉姏锛沗DynSTEEREvaluator.evaluate()` 涓嶇敤瀹冩帶鍒朵富寰幆銆?
## 璧勬簮閲婃斁涓庡紓甯稿鐞嗗绾?
- `DynSTEEREvaluator.evaluate()` 鍦ㄦ垚鍔熴€佺瓥鐣ョ粓姝㈠拰寮傚父璺緞涓兘浼氳皟鐢?`harness.teardown_case(session)`銆?- 鑻ヤ富鎵ц杩囩▼宸茬粡鎶涘嚭寮傚父锛宼eardown 澶辫触鍙褰曠粨鏋勫寲閿欒鏃ュ織锛屼笉閬斀涓诲紓甯搞€?- 鑻ヤ富鎵ц杩囩▼鎴愬姛浣?teardown 澶辫触锛宔valuator 鎶涘嚭 `HarnessTeardownError`锛岄伩鍏嶈祫婧愰噴鏀惧け璐ヨ闈欓粯鍚炴帀銆?- harness 瀛愮被鐨?`teardown_case()` 搴斿敖鍔涢噴鏀惧叏閮ㄥ閮ㄨ祫婧愶紝骞舵柇寮€ session 涓澶у瀷涓婁笅鏂囥€佽建杩规楠ゃ€佸揩鐓с€丼DK client 鎴栧師鐢?role 鐨勫紩鐢ㄣ€?- runner 瀵瑰崟涓?case 鐨勬墽琛屽け璐ョ粺涓€鍖呰涓?`HarnessCaseExecutionError`锛岄敊璇俊鎭寘鍚?benchmark 鍜?case_id銆?
## Runner

`dynsteer.harness.runner` 鎻愪緵锛?
- `run_harness_configs(configs, max_workers=1, force_adapt=False, force_eval=False)`: 鍞竴鍏紑杩愯鍏ュ彛锛屾寜 `run_configs.json` 涓殑閰嶇疆椤哄簭閫愮粍鍔犺浇 `TaskCase` 鍒楄〃锛涘悓涓€閰嶇疆鍐呮寜 case 椤哄簭涓茶鎴栧苟琛屾墽琛岋紝杩斿洖鍊兼寜閰嶇疆鍜?case 鐨勫師濮嬮『搴忔帓鍒椼€傝嫢 `benchmark.json` 閰嶇疆浜?`max_workers`锛屽疄闄?worker 鏁板彇鍛戒护琛?workers 涓庤瀛楁鐨勮緝灏忓€硷紝涓旀渶灏忎负 1銆俙force_adapt=True` 鏃朵細鍦ㄨ繍琛屽墠閲嶅缓缂撳瓨鐨?adapted case锛屽苟鑷姩寮哄埗 `force_eval=True`锛沗force_eval=True` 鏃跺拷鐣ュ凡鏈?case 绾?`runs/results` 浜х墿骞惰鐩栧啓鍑恒€?
Runner 鍙礋璐ｉ€夋嫨 case銆佸姞杞?adapted `TaskCase`銆佽皟鐢?`evaluator.evaluate(harness, config, task_case)` 鍜屽啓鍑烘枃浠躲€傚畠涓嶈皟鐢?`harness.run_case()`锛屼篃涓嶈皟鐢ㄦ暣杞ㄨ抗璇勪及浣滀负涓诲疄楠屾祦绋嬨€傚崟 case 杈撳嚭璺緞銆乻ummary 鍜?report 閮戒互 `task_case.case_id` 涓哄噯锛屼笉瑕佹眰鎶?`HarnessRunConfig.case_ids` 鏀瑰啓鎴愬崟鍏冪礌鍏冪粍銆?
褰?`run_configs.json` 宸查€氳繃 `scenarios` 鏄惧紡鎸囧畾 case 鏃讹紝Runner 鐩存帴浣跨敤璇ラ『搴忥紝涓嶅啀璋冪敤 `list_cases()` 鍏ㄩ噺鏋氫妇 benchmark锛涙湭鎸囧畾 `scenarios` 鏃舵墠閫氳繃 `list_cases()` 灞曞紑鍙繍琛?case銆傝繖鏍峰凡鏈?adapted cache 鐨勬寚瀹?case 杩愯涓嶄細琚?benchmark 鍘熺敓鍏ㄩ噺鍦烘櫙鏋勯€犳嫋鎱€?
Runner 瀵规瘡涓?config 浼氬厛璋冪敤涓€娆?`load_task_case(run_config, adapter)` 鍔犺浇鏈粍 `TaskCase` 鍒楄〃锛岀劧鍚庤緭鍑烘棩蹇楋細`鍩轰簬閰嶇疆XXX锛屽紑濮嬪熀浜?{benchmark} 灞曞紑璇勪及锛孋ases鏁伴噺: N`銆傚崟 case 鎵ц鍙礋璐?evaluator 璋冪敤鍜岀粨鏋滄枃浠跺啓鍏ワ紝閬垮厤澶氬満鏅繍琛屾椂鍙嶅鍒濆鍖栨棩蹇楁垨鍒峰睆銆?
`run_harness_configs(...)` 浼氭妸鍗曚釜 case 鐨勫紓甯稿寘瑁呬负 `HarnessCaseExecutionError`锛岄敊璇俊鎭寘鍚?benchmark 鍜?case_id锛屼究浜庝覆琛屾垨骞惰杩愯鏃跺畾浣嶅け璐ユ牱鏈€傚苟琛屾ā寮忎笅鏃ュ織缂撳啿鍜?logger 鍒濆鍖栦娇鐢ㄩ攣淇濇姢锛沺rovider client 涓嶅湪 worker 涔嬮棿鍏变韩锛岀敱姣忔 `BaseLLM.chat(...)` 璋冪敤鍒涘缓涓€娆★紝骞跺湪璇ユ璋冪敤鐨勯噸璇曞惊鐜腑澶嶇敤銆?
Runner 浣跨敤 `dynsteer.progress.TqdmCaseProgressManager` 鏄剧ず浼扮畻鎬绘鏁拌繘搴︽潯銆傝繘搴︽潯鐨?`steps` 鍗曚綅鏄畬鏁撮棴鍚堢殑 agent step锛屼笉鏄?raw step锛涘疄闄呭悓鏃惰繍琛岀殑 case 鏁伴噺浠嶄笉瓒呰繃 `max_workers`銆傛瘡涓椿鍔?case 浣跨敤涓€鏉?tqdm 杩涘害鏉★紝鍚庣紑鍖呭惈 `elapsed`銆乣steps` 鍜?`avg_step`銆俢ase 瀹屾垚鍚庡搴旇繘搴︽潯鍏抽棴锛岃繍琛屾湡闂寸粓绔棩蹇?handler 浼氫复鏃堕潤榛橈紝鏂囦欢鏃ュ織鍜屽唴瀛樻棩蹇椾粛淇濈暀 INFO 缁撴瀯鍖栧唴瀹广€?
Harness 妯″紡杈撳嚭锛?
- `runs/<benchmark>/<method>/<case_id>/raw/`
- `runs/<benchmark>/<method>/<case_id>/raw_summary.json`
- `runs/<benchmark>/<method>/<case_id>/trajectory.json`
- `results/<benchmark>/<method>/summary.json`
- `results/<benchmark>/<method>/<case_id>/report.json`
- `results/<benchmark>/<method>/<case_id>/summary.json`

`results/<benchmark>/<method>/summary.json` 鏄柟娉曠骇姹囨€绘憳瑕侊紝鑱氬悎鍚屼竴 `benchmark/method` 涓嬫墍鏈?case 鐨勫崟鍦烘櫙 `summary.json`銆傛眹鎬诲瓧娈靛寘鍚?`benchmark`銆乣method`銆乣case_count`銆乣average_overall_score`銆乣milestone_coverage_counts`銆乣total_step_count`銆乣total_llm_tokens`銆乣total_trajectory_tokens`銆乣average_elapsed_seconds` 鍜?`cases`銆俙cases[]` 淇濈暀姣忎釜鍦烘櫙鐨?`case_id`銆佺浉瀵?`summary_path`銆佺浉瀵?`report_path` 浠ュ強鍗曞満鏅憳瑕佸瓧娈碉紝渚夸簬浠庢€昏杩芥函鍒板叿浣撳満鏅粨鏋溿€?
`trajectory.json` 鍖呭惈瀹屾暣 raw `Trajectory` 搴忓垪鍖栫粨鏋滐紝step 涓殑 `recipient` 鏄?DynSTEER 瑙勮寖鍖栬鑹诧紱benchmark 鍘熺敓 sender/recipient 鍙繚鐣欎负 raw 璇婃柇瀛楁锛屼緥濡?ToolSandbox 鐨?`raw_sender`銆乣raw_recipient`銆侱efault 杞ㄨ抗浼氬湪椤跺眰 raw 瀛楁涓啓鍏?`runtime_initial_state`锛屼緵 replay 澶嶇敤鏈 session 鐨勭湡瀹炲垵濮嬬姸鎬併€俙raw_summary.json.trajectory_output.path` 鍥哄畾鎸囧悜 `trajectory.json`锛屽苟璁板綍 agent `step_count`銆乣raw_step_count`銆乣snapshot_count` 鍜?`final_state_present`锛涘畬鏁?steps 涓嶅祵鍏?`raw_summary.json`锛岄伩鍏嶅崟涓憳瑕佹枃浠惰繃澶с€?
`raw_summary.json` 浼氬湪 benchmark 鍘熺敓鎽樿鍩虹涓婅拷鍔?DynSTEER 杩愯鏈熷瓧娈碉細`runtime_metrics`銆乣trajectory_output`銆乣terminated_by_policy`銆乣termination_code`銆乣termination_reason` 鍜?`stage_settlements`銆侱efault raw summary 杩樹細鍐欏叆 `runtime_initial_state_source=harness_session` 涓?`runtime_initial_state_summary`銆俙runtime_metrics.step_count` 璁板綍瀹屾暣闂悎 agent step 鏁帮紝`runtime_metrics.raw_step_count` 璁板綍瀹屾暣 raw 杞ㄨ抗娑堟伅鏁帮紝姝ゅ杩樿褰?case 璇勪及鑰楁椂銆乼ool call 鏁般€佽建杩?step cost 鑱氬悎銆乧ost 鍙敤鎬у瓧娈靛拰 LLM judge token usage 鑱氬悎銆俙trajectory_cost_available=false` 鎴?`trajectory_latency_available=false` 琛ㄧず瀵瑰簲 `0` 鍊煎彧鏄暟鎹笉鍙敤鍏滃簳锛屼笉鏄湡瀹為浂鎴愭湰銆俙task_case_snapshot.runtime_initial_state_source` 璁板綍杩愯鏈熻瘎鍒嗕娇鐢ㄧ殑鍒濆鐘舵€佹潵婧愶紝`runtime_initial_state_summary` 鍙繚鐣?namespace 琛屾暟鎽樿銆俙stage_settlements[].metadata` 涓殑 `stage_trace` 涓?`milestone_matching` 鐢?`DynSTEEREvaluator` 鐢熸垚锛孯unner 鍙礋璐ｅ簭鍒楀寲钀界洏銆俙stage_trace` 鐢ㄤ簬鏌ョ湅鏈樁娈佃建杩规楠わ紝`milestone_matching` 鐢ㄤ簬鏌ョ湅 milestone 鍛戒腑杈圭晫銆佺害鏉熻瘎鍒嗗拰 finish 闃舵鏈懡涓?milestone銆?
Default 杈撳嚭浣跨敤鍚屼竴鐩綍缁撴瀯锛屼絾 case 鐩綍涓嬬殑鎶ュ憡鏂囦欢涓?`default_report.json`锛宻ummary 涓殑 `overall_score` 涓?`default_score` 鍧囨潵鑷?benchmark 鍘熺敓 `BenchmarkDefaultResult.score`銆俁eplay 杈撳嚭浣跨敤 `report.json`锛屽苟鍦?summary/report metadata 涓褰?`method`銆乣strategy`銆乣model_id`銆乣repeat_index` 鍜屽彲閫?`default_reference`銆?
`raw_summary.json` 杩樺寘鍚疄鏃?milestone 鍖归厤璇婃柇瀛楁锛?
- `milestone_graph_summary`: 褰撳墠 case 杞崲鍚庣殑 milestone DAG 鎽樿锛屽寘鍚妭鐐广€佽竟銆乵andatory milestone ID 鍜岀害鏉熸憳瑕併€?- `milestone_match_attempts`: 杩愯鏈熸瘡涓瓨鍦?ready milestone 鐨勫€欓€?step 鍖归厤灏濊瘯锛屽寘鍚懡涓墠 matched/ready 闆嗗悎銆佸€欓€夎竟鐣屻€佸€欓€?milestone 璇勫垎銆佹槸鍚﹁閫変腑鍜屾嫆缁濆師鍥犮€?- `milestone_final_diagnostics`: 姣忎釜 milestone 鐨勬渶缁堢姸鎬佹憳瑕侊紝鍖呭惈 `matched`/`pending`銆佹槸鍚︽浘缁?ready銆佸皾璇曟鏁般€佹渶浣冲垎鏁般€佹渶浣宠竟鐣屻€侀樆濉炲師鍥犲拰鏈弧瓒冲墠椹便€?
## ToolSandbox 閫傞厤璇存槑

### 批次级 execution timing

`BaseBenchmarkHarness.timed_advance_case(session)` 包装一次 `advance_case()` 调用，返回的 `HarnessAdvanceResult.execution_latency_ms` 是该批次的墙钟耗时；异常会原样传播。DEFAULT 和在线 evaluate 会将该耗时按 raw step 数均匀分摊到 `StepCost.latency_ms`，并在 `trajectory.raw.execution_timing` 写入 batch 索引、step 范围、总耗时和分摊规则。历史轨迹没有该字段时，耗时可用性必须标记为 false，不能将缺失值解释为真实 0 秒。

DEFAULT `runtime_metrics` 额外记录 `native_evaluation_seconds`。benchmark 若在 `BenchmarkDefaultResult.metrics` 明确提供 `prompt_tokens`、`completion_tokens`、`total_tokens`，则映射为 `native_evaluation_*` 字段并标记可用；字段缺失时保持 `null/false`。

ToolSandbox 澶栭儴鍙€変緷璧栫敱涓撻棬渚濊禆杈圭晫宸ュ叿鍑芥暟鍔犺浇锛屼笉閫氳繃 DynSTEER 鍖呯骇 `__getattr__` 鎳掑姞杞介殣钘忛」鐩嚜韬緷璧栥€傝繍琛屾椂闇€瑕佷繚璇?ToolSandbox 鍙婂叾渚濊禆宸插畨瑁咃紝鎴栧湪 `data/toolsandbox/benchmark.json` 涓厤缃彲瀵煎叆鐨勫閮?`source_root`銆?
`data/{benchmark}/benchmark.json` 鏀寔 `language` 瀛楁锛岄粯璁ゅ€间负 `en`銆俙load_harness_run_configs(...)` 浼氭牎楠岃瀛楁涓洪潪绌哄瓧绗︿覆锛屽苟鍐欏叆 `HarnessRunConfig.metadata["language"]`锛屼緵 prompt 妯℃澘閫夋嫨璇█鐗堟湰銆俙benchmark.json` 杩樻敮鎸佸彲閫?`max_workers` 鏁存暟瀛楁锛岀敤浜庝负涓嶆敮鎸佸苟琛岀殑 benchmark 璁剧疆 case 骞跺彂涓婇檺銆?
`data/toolsandbox/run_configs.json` 姣忛」閰嶇疆涓紝`agent` 涓?`user` 鍙〃绀?ToolSandbox 瑙掕壊瀹炵幇绫诲瀷锛涜鑹?SDK client 鐨勮繛鎺ュ弬鏁扮敱鍚岀骇鐨?`agent_client` 涓?`user_client` 鎺у埗锛屽苟鍘熸牱鍐欏叆 `HarnessRunConfig.metadata`銆傛敮鎸佸瓧娈靛寘鎷?`api_key`銆乣api_key_env`銆乣base_url`銆乣base_url_env`銆乣timeout_seconds`锛屽叾涓┖瀛楃涓蹭細琚涓烘湭閰嶇疆銆傚疄闄?client 鍒濆鍖栦紭鍏堢骇涓烘樉寮忓€笺€佹樉寮忕幆澧冨彉閲忋€佹棫鍏ㄥ眬鐜鍙橀噺锛涙湭璁剧疆鏂板瓧娈垫椂缁х画璇诲彇 `OPENAI_API_KEY`銆乣OPENAI_BASE_URL`銆乣ANTHROPIC_API_KEY`銆乣ANTHROPIC_BASE_URL`銆?
```json
{
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
```

ToolSandbox adapter 璐熻矗璇诲彇 scenario銆佸垵濮?SANDBOX 琛屻€佸垵濮嬫暟鎹簱鐘舵€佸拰 evaluation matcher锛岀敓鎴愬甫 `case_id` 涓庡凡 enrich milestone graph 鐨?`TaskCase`銆傞€傞厤闃舵涓嶅緱璋冪敤 `scenario.play()`锛屼篃涓嶅緱璋冪敤 agent/user `respond()`銆?
ToolSandbox harness 涓嶈皟鐢ㄥ師鐢?`play_and_evaluate()` 鎴栨暣鍦?`Scenario.play()`銆俙start_case()` 鍙繁鎷疯礉涓€娆?`Scenario.starting_context`锛屽噯澶?system -> execution environment 鍒濆鍖栨秷鎭紝骞跺湪鍒濆鍖栧悗鎶婂綋鍓?context 鐨勭湡瀹炲垵濮嬬姸鎬佸浐鍖栧埌 session銆傛瘡娆?`_advance_native_session()` 鎭㈠ `session.context`锛岃鍙栧綋鍓?SANDBOX recipient锛屽苟鍙皟鐢ㄨ role 鐨勪竴娆?`respond()`銆傛瘡娆?respond 鍚庨兘浼氬啓鍥?`session.context = get_current_context()`锛岄伩鍏嶅悗缁帹杩涢噸缃洖 starting context銆?
ToolSandbox 鐨?`initial_state_from_session()` 杩斿洖鍥哄寲鐨?runtime initial state锛沗final_state_from_session()` 杩斿洖褰撳墠 context 鐨?namespace 鐘舵€併€俰nitial銆乺untime snapshots 鍜?final state 閮戒繚鐣?`sandbox_message_index` 鍒楋紝閬垮厤绂荤嚎 adapted JSON 涓殑鍔ㄦ€?timestamp 鎴?schema 缂哄垪褰卞搷杩愯鏈?guardrail銆?
ToolSandbox harness 浼氭寜 `data_root + tool_backend` 鍦ㄨ繘绋嬪唴缂撳瓨鍘熺敓 `named_scenarios()` 缁撴灉锛岄伩鍏嶅悓涓€娆¤繍琛屼腑 `list_cases()`銆乣start_case()` 鍙嶅鏋勯€犲叏閮?ToolSandbox 鍦烘櫙銆傚崟 case 鍚姩浠嶄細娣辨嫹璐?`starting_context`锛屼笉鍏变韩杩愯鏈?context銆?
ToolSandbox 鐨勫師鐢熷伐鍏枫€乺ole 鍜?execution environment 閫氳繃妯″潡绾?`_global_execution_context` 璇诲啓褰撳墠娑堟伅涓婁笅鏂囷紝绾跨▼骞惰鎵ц涓嶅悓 case 浼氫簰鐩歌鐩?context銆傚洜姝?`data/toolsandbox/benchmark.json` 閰嶇疆 `max_workers: 1`锛岀‘淇?ToolSandbox case 涓茶鎵ц銆?
## 闄勫姞璇存槑

`data/{benchmark}/benchmark.json` 閲岀殑 `max_workers` 鐜板湪鏄彲閫夊瓧娈碉紱缂哄け鏃朵笉浼氬啓鍏?`benchmark_max_workers`銆俆oolSandbox 鐨?`agent_client` / `user_client` 杩樻敮鎸?`max_retries`銆乣retry_base_seconds` 鍜?`retry_max_seconds`锛岀敤浜庡師鐢?`respond()` 鐨勯噸璇曟帶鍒躲€?
# AgentCompass Default harness

| benchmark | 执行方式 | snapshots | 首版方法 |
|---|---|---|---|
| `swebench_pro` | 单次 `advance_case()` 完成 AgentCompass 整任务执行与原生评分 | `[]` | Default |
| `skillsbench` | 单次 `advance_case()` 完成 AgentCompass 整任务执行与原生评分 | `[]` | Default |

完整配置、安全边界和 ACTF 映射参见 `docs/apis/agentcompass.md`。

## Guided harness 接口

`BaseBenchmarkHarness.send_guidance(session, message)` 是执行期引导契约：空 session、空消息直接报错；不支持的 benchmark 抛出 `NotImplementedError`，不静默降级。`ToolSandboxHarness` 使用 ToolSandbox `BaseRole.add_messages()` 写入 `USER -> AGENT` 消息，并把 `visible_to` 收窄为 `[AGENT]`；该消息进入 agent 的 user prompt，但 user simulator 不会读取。写入后 harness 校验 SANDBOX index 前进并更新 `ToolSandboxSession.last_sandbox_message_index`，避免 guidance row 被重复转换。

## Online native result

`HarnessRunConfig.use_milestone_graph` 控制静态适配是否生成完整图。在线 `dynsteer_evaluate` 显式设置 `collect_online_native_score=true` 时，raw summary 保存 `native_default_result`，summary/report 顶层输出 `native_score` 与 `native_task_completed`。native score 缺失时 completion 保持 `null`；replay 不把 DynSTEER overall score 回填为 native score。

三个 benchmark 的 native verifier 是确定性容器/本地评估，`BenchmarkDefaultResult.metrics` 中 `prompt_tokens=0`、`completion_tokens=0`、`total_tokens=0` 均为真实 0，并用 `native_evaluation_token_source="deterministic_native_verifier"` 标记来源。

## ToolSandbox agent usage recorder

仅当 harness metadata 显式设置 `capture_agent_usage=true` 时，`start_case()` 才创建 `ProviderUsageRecorder` 并把它传给 agent role factory。recorder 挂在 OpenAI/Anthropic SDK 的 `DefaultHttpxClient` response hook 上，是纯观察者；不保存请求/响应正文、URL、header 或 provider cost/cache 字段。user simulator token 不计入 agent ledger。

一个批次的所有完整 usage 汇总到首条 `Agent -> User/Environment` raw step 的 `cost.tokens`；同批其他 raw step 记 0。usage 缺失时首条 agent outbound 保持 `null`，不按半个 usage 或 0 填充。`trajectory.metrics.agent_usage` 记录 request、usage、missing counts 和 `available`；`build_runtime_metrics()` 只在 `available=true` 时输出累计 agent token，旧配置不新增该对象并保持原 step-cost 汇总路径。
