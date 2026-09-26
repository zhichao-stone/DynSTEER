# DynSTEER 评估看板 display/index.html 乱码与语法错误修复实施方案

- **方案日期**：2026-09-13
- **方案状态**：待评审 / 准备实施
- **方案路径**：`docs/plans/2026-09-13-fix-display-index-html-encoding-and-syntax-plan.md`
- **执行约束**：遵循 `docs/constraints/code.md`，严禁删除已有约束与方案文档，不主动执行 git commit。

---

## 1. 方案目标与问题根因深度分析

### 1.1 现状故障现象

当前打开 `display/index.html` 时看板完全无法正常运行或渲染，控制台报错，页面呈现白屏或残缺状态。主要表现为：
1. 页面标题及所有界面文案出现严重乱码（如 `<title>DynSTEER 璇勪及鐪嬫澘</title>`、`鎵ц杞ㄨ抗`、`鏃? 等）；
2. 浏览器直接抛出脚本语法错误（`SyntaxError`），导致整个内联 JavaScript 逻辑崩溃中断，无任何数据与交互可被触发；
3. 左右分栏拖拽条（`panel-splitter`）无法工作，鼠标拖动无效。

### 1.2 问题根因定位

通过对 Git 历史记录的全面回溯与逐 hunk 对比分析：
- 在 commit `25cf0ca`（提交信息为 `add method_id in results/runs dir`）中，开发者意图对 `display/index.html` 进行两处小幅功能更新（在顶部看板信息栏增加 `model_id` 显示，以及在 `runId` 中增加 `experiment_id` 与 `model_id` 前缀）。
- 但是在编辑保存过程中，使用了非 UTF-8 编码环境（系统默认 GBK/ANSI 编码）打开并重新写入该文件，触发了典型的 **UTF-8 字节流被误作为 GBK 解码再转存** 的级联破坏：
  1. **文本乱码（Mojibake）**：原文件中所有中文汉字、全角标点均被转换为对应的 GBK 乱码字符；
  2. **引号被吞噬导致致命语法错误**：由于 UTF-8 中文为 3 字节，当按 GBK 双字节切分时，部分中文字符结尾字节与紧随其后的 ASCII 双引号 `"` 或反引号 ``` 组合成了双字节序列（例如 `"无";` 被转为 `"鏃?;`），导致代码中出现数十处未闭合字符串、未闭合模板字面量、未闭合属性值。Node.js 语法检查器（`node --check`）在脚本解析初期即抛出 `SyntaxError: Unexpected identifier`，脚本执行彻底被阻断；
  3. **HTML DOM 属性破坏**：如 `<aside class="sidebar" aria-label="涓诲鑸?>` 以及分栏拖拽条 `<div class="panel-splitter trajectory-graph-splitter" ... aria-label="璋冩暣... data-left="trajectory" data-right="graph" ...>` 中的 `aria-label` 引号丢失，直接把后续的 `data-left`、`data-right` 等关键交互属性吞噬到了属性文本中，使得 DOM 节点的 `splitter.dataset.left` 为 `undefined`，分栏拖拽逻辑因此彻底失效；
  4. **下游数据字段演进的兼容缺失**：在 commit `67ff3e2` 中，`display/build.py` 的数据汇聚逻辑将 run 与 scenario 的得分字段由 `overall_score` 统一精简为 `score`。而看板目前仅读取 `summary.overall_score`，需要升级为 `summary.score ?? summary.overall_score` 保证向前与向后兼容；同时需补齐对多轮重复实验 `repeat_index`（如 `r0`, `r1`）的显示支持，防止同一实验下的多次重复运行在下拉框中重名。

---

## 2. 修改文件清单与职责划分

| 序号 | 文件路径 | 操作类型 | 核心修改职责 |
|---|---|---|---|
| 1 | `display/index.html` | 修复重构 | 基于 commit `25cf0ca~1` 干净基线，完整恢复所有中文文案、HTML 标签与属性闭合；正确融入 `model_id`、`repeat_index`、`score/overall_score` 兼容逻辑；保证 UTF-8 无 BOM 编码与 100% 语法正确。 |
| 2 | `docs/plans/2026-09-13-fix-display-index-html-encoding-and-syntax-plan.md` | 新建 | 记录问题根因、详细修改方案与附录，供代码评审与留档。 |

---

## 3. 具体修改设计与代码方案

### 3.1 干净基线恢复策略
直接以 commit `25cf0ca~1`（即 commit `f4c2abd`）无损、语法验证完全通过的 `display/index.html` 为基线进行修复。该版本拥有全部正确的中文注释、中文 UI 文案和严谨的 HTML/CSS/JS 结构。

### 3.2 增量功能与兼容性补丁

在干净基线的基础上，精准补充与完善以下几处功能逻辑：

#### 1) `runId` 函数增强
支持 `experiment_id`、`benchmark`、`model_id`、`repeat_index`、`method` 的完整层次化标识，解决重复实验名称碰撞问题：
```javascript
function runId(run) {
  if (!run) return "未命名 run";
  const parts = [];
  if (run.experiment_id) parts.push(run.experiment_id);
  parts.push(run.benchmark || "benchmark");
  if (run.model_id) parts.push(run.model_id);
  if (run.repeat_index !== null && run.repeat_index !== undefined && run.repeat_index >= 0) {
    parts.push(`r${run.repeat_index}`);
  }
  if (run.method) parts.push(run.method);
  return parts.join(" / ");
}
```

#### 2) `renderRunRow` 运行指标条展示增强
在展示顶部 run 指标时：
- 若当前 run 包含 `model_id`，在指标项前排增加 `metric("model", run.model_id)`；
- 得分展示支持兼容 `summary.score ?? summary.overall_score`，适配不同版本 `display/build.py` 生成的 `data.js`：
```javascript
const metrics = el("div", "metric-list");
const run = currentRun();
const summary = run ? run.summary || {} : {};
const metricNodes = [metric("benchmark", run ? run.benchmark : "")];
if (run && run.model_id) {
  metricNodes.push(metric("model", run.model_id));
}
metricNodes.push(
  metric("得分", formatScore(summary.score ?? summary.overall_score)),
  metric("覆盖", summary.milestone_coverage || "unknown"),
  metric("耗时", formatSeconds(summary.elapsed_seconds)),
  metric("步骤", formatValue(summary.step_count)),
  metric("场景", formatValue(summary.scenario_count))
);
metrics.append(...metricNodes);
container.append(selector, metrics);
```

#### 3) HTML 属性与分栏拖拽条（Splitter）完整性保证
确保 HTML 模板中以下关键结构绝对完好闭合，属性不丢失：
```html
<aside class="sidebar" aria-label="主导航">
  <div class="brand-mark">D</div>
</aside>
<main class="app">
  <header class="topbar">
    <section class="run-row" aria-label="运行记录切换"></section>
    <section class="scenario-row" aria-label="场景切换"></section>
  </header>
  <section class="workspace">
    <section class="panel trajectory-panel" aria-label="执行轨迹"></section>
    <div class="panel-splitter trajectory-graph-splitter" role="separator" aria-label="调整执行轨迹和里程碑图宽度" data-left="trajectory" data-right="graph" tabindex="0"></div>
    <section class="panel graph-panel" aria-label="里程碑图"></section>
    <div class="panel-splitter graph-stage-splitter" role="separator" aria-label="调整里程碑图和评估阶段宽度" data-left="graph" data-right="stage" tabindex="0"></div>
    <section class="panel stage-panel" aria-label="评估阶段"></section>
  </section>
</main>
```

---

## 4. 实施与验证步骤

### 4.1 实施步骤
1. **生成开发计划文档**：保存本方案至 `docs/plans/2026-09-13-fix-display-index-html-encoding-and-syntax-plan.md`；
2. **代码合成与写入**：基于 `25cf0ca~1` 的正确基线，注入上述两项明确的业务改动，并将生成的文件以 UTF-8 编码严格写入 `display/index.html`；
3. **语法检查验收**：通过 Node.js 提取 `display/index.html` 内联 JavaScript 脚本，运行 `node --check` 静态语法检查，确保无任何 SyntaxError；
4. **DOM 与交互逻辑验证**：
   - 验证 HTML 属性闭合情况，确保 `data-left`、`data-right`、`aria-label` 无吞噬缺失；
   - 构造模拟 `DYNSTEER_DATA` 数据包，利用无头环境或断言脚本验证看板主要函数（`init`、`renderAll`、`renderTrajectory`、`renderGraph`、`renderStages`）在各种边界输入（空数据、单用例数据、完整多轮用例数据）下的正确执行；
5. **代码冗余与约束检查**：按照 `docs/constraints/code.md` 第 3、4 节进行全项核对，确保代码整洁无冗余，中文日志/注释无编码异常。

---

## 附录A. 项目中没有把握实现的模块部分

依据 `docs/constraints/code.md` 规范要求，在此明确列出当前任务中**最没有把握实现的模块部分及其原因**：

### 1. 复杂浏览器环境下 Canvas/SVG 在极端极端窗口尺寸下的像素级自动重排适应
- **对应模块**：`display/index.html` 中的里程碑拓扑图渲染（`renderGraph`）及分栏宽度动态缩放（`updatePanelScales`）。
- **为什么没有把握**：
  在极其狭窄（如宽度小于 600px）或极端非标准比例的显示设备上，三个面板（执行轨迹、里程碑图、评估阶段）会受到 CSS 中 `minmax(300px, ...)` 与弹性伸缩比例（`--trajectory-col`, `--graph-col`, `--stage-col`）的联合约束。当用户强行将窗口收窄至极限宽度以下时，SVG 连线计算依赖的 DOM 节点坐标由于浏览器布局流的舍入可能产生细微的像素级偏移（例如 1~2px 的连线端点未完全居中）。这属于纯前端自适应布局在无外部 UI 框架时的固有几何容差边界，但不影响核心功能与日常桌面级 1080p/2K/4K 分辨率下的正常工作。
