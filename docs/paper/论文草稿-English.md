# DynSTEER: Dynamic Stage-wise Trajectory Evaluation and Execution-time Review for Agents

> Paper draft v0.2. Empirical numbers come from the paired `raw_summary.json` reports under `runs/exp/toolsandbox_main` and their aggregate copy in `results/exp/toolsandbox_main`. Table 3 reports paired counterfactual replay savings, not costs observed in a separately executed live run.

## Abstract

Large language model agents are increasingly deployed for long-horizon task execution, raising a central granularity question for trajectory evaluation: whole-trajectory verification is too coarse-grained to focus on concrete failures and their associated evidence in long trajectories, whereas atomic-step scoring is too fine-grained, noise-sensitive, and computationally expensive. This granularity gap makes a single-reference trajectory paradigm inadequate for assessing the rich space of valid agent execution paths and delays timely feedback and early stopping in long-horizon tasks. To address these issues, we propose DynSTEER, a dynamic stage-wise framework for agent trajectory evaluation. DynSTEER bridges the granularity gap through stage-wise dynamic evaluation that segments rollouts at necessary execution nodes and adapts its multi-tier review strategy from stage-level results; it compiles a path-tolerant milestone graph from public task views to preserve diverse legal paths without reference leakage; and it supports terminating unrecoverable agent executions to curb resource waste. Experiments show that DynSTEER improves evaluation discriminability by 85.2% over native evaluation, separates all model pairs with statistical significance, and saves 45.41% of execution steps on failed rollouts.

**Keywords:** Agent evaluation; trajectory evaluation; milestone graph; adaptive judging; early termination; tool-use agents

## 1. Introduction

Large Language Model (LLM) agents are increasingly undertaking complex, long-horizon tasks across real-world environments~\citep{yao2023react,shinn2023reflexion,jimenez2024swebench,gu2024toolsandbox,zhou2024webarena}. As task horizons expand, evaluating only final output results becomes insufficient to identify agent deficiencies and diagnose failure modes, bringing trajectory-level evaluation to the forefront of agent research~\citep{qian2024agentprocessbench,shi2025earlyeval}.

However, existing trajectory evaluation paradigms face a central granularity problem, especially for long-horizon tasks. Whole-trajectory verification is too coarse-grained to focus on concrete failures and their associated evidence in long trajectories, whereas atomic-step scoring is too fine-grained: isolated messages lack task context, so ordinary retries and harmless observations become noise. This granularity problem further raises two challenges. Agent executions often admit multiple valid and effective paths. Because of this granularity gap, however, evaluation typically offers only limited path references and therefore struggles to faithfully reflect the quality of diverse agent executions. Moreover, full-trajectory review delays diagnosis that could be made earlier, so an execution that has already gone wrong may continue, wasting model resources and triggering additional skill or tool calls.

DynSTEER follows the same chain. We introduce **DynSTEER** (Dynamic Stage-wise Trajectory Evaluation and Execution-time Review), a dynamic framework transforming agent evaluation into targeted, stage-wise execution review. To resolve the granularity challenge, it performs stage-wise dynamic evaluation: segments rollouts at necessary execution nodes, preserves task context while isolating localized evidence for error attribution, and adapts its multi-tier review strategy from stage-level results. To avoid single-reference bias, it compiles a path-tolerant milestone graph from public task views through multi-candidate simulated planning and majority consensus, capturing necessary task invariants while accommodating diverse legitimate paths. Finally, it detects unrecoverable prefixes from stage-level evidence and terminates them to curb resource waste.

Experiments demonstrate that DynSTEER improves evaluation discriminability by 85.2% over native evaluation, enhances diagnostic precision, separates all model pairs with statistical significance, preserves the validity of diverse execution paths, and saves 45.41% of execution steps on failed rollouts.

In summary, our contributions are as follows:
- **Stage-wise Dynamic Evaluation (Addressing Challenge 1):** We segment rollouts at necessary execution-step nodes and adapt multi-tier review routing from stage-level results, isolating context-rich evidence windows for multidimensional scoring and first-error localization.
- **Path-Tolerant Task Blueprint Compilation (Addressing Challenge 2):** We compile a milestone DAG from public task views via multi-candidate planning and strict-majority consensus, preserving diverse valid paths without reference leakage.
- **Counterfactual Early Termination:** We detect unrecoverable prefixes from stage-level evidence and terminate them, substantially reducing wasted computation.

## 2. Related Work

### 2.1 Agent Trajectory Evaluation
Unlike early outcome-based benchmarks that inspect only terminal code patches or end-state database records~\citep{jimenez2024swebench,qin2023toolllm,liu2023agentbench,zhou2024webarena}, trajectory-level evaluation examines the intermediate sequence of planning decisions, tool invocations, parameter bindings, and error-recovery behaviors. Recent works have expanded the scope of trajectory assessment: BFCL~\citep{patil2025bfcl} evaluates multi-step tool calls with abstract syntax tree checks; TRAJECT-Bench~\citep{trajectbench2025} explicitly assesses tool selection, parameter correctness, and dependency order; AgentRewardBench~\citep{agentrewardbench2025} benchmarks automated judges on web agent trajectories; and DeepRed~\citep{deepred2026} and Claw-Eval~\citep{claweval2026} leverage environment snapshots and audit logs to award partial credit along execution traces. 

Despite providing intermediate visibility, existing trajectory evaluators face two major limitations. First, they predominantly rely on rigid alignment against a single reference path~\citep{qian2024agentprocessbench}. In open, stateful environments, tasks frequently admit multiple valid, semantically equivalent solution routes; single-path matching inherently penalizes legitimate exploratory strategies. Second, trajectory evaluators predominantly operate post-hoc, outputting aggregate statistics across the full rollout without the ability to dynamically intervene or adapt evaluation strategies toward an agent's specific weak links.

### 2.2 Dynamic Evaluation for Agents
Static benchmarks suffer from prompt overfitting, data contamination, and leaderboard score saturation. To address these vulnerabilities, researchers have begun exploring dynamic evaluation paradigms. For conversational agents, Agent-Testing Agent (ATA)~\citep{agenttestingagent2026} adaptively generates subsequent test cases guided by prior judge feedback. In interactive tool environments, ToolSandbox~\citep{gu2024toolsandbox} introduces stateful conversational simulations with pre-defined milestones and safety minefields to evaluate arbitrary rollouts. 

However, existing dynamic evaluation approaches primarily concentrate on dynamically selecting or perturbing benchmark test suites~\citep{gu2024toolsandbox,agenttestingagent2026}. During the actual execution of an individual task, trajectory evaluation remains largely passive: evaluators typically check simple milestone reachability at the end of rollouts, lacking a formal mechanism to dynamically steer evaluation focus, update dimension weights, or control execution progression in real time.

### 2.3 Efficient Evaluation and Early Stopping Strategies
As agent rollouts grow longer, the computational expense of evaluation has escalated dramatically, costing hundreds of dollars per benchmark pass~\citep{shi2025earlyeval}. Prior efficiency efforts centered on benchmark distillation, subsampling static suites into smaller representative subsets~\citep{aslam2006statistical,shi2025earlyeval}. Addressing within-task efficiency, EarlyEval~\citep{shi2025earlyeval} introduces early outcome prediction, training dual LightGBM classifiers over prefix features to forecast binary task success/failure and halt execution early. 

Nevertheless, benchmark distillation reduces the task count while leaving long, wasteful rollouts untouched, whereas tabular early prediction models~\citep{shi2025earlyeval} act as black boxes. They predict binary outcomes without understanding domain semantics, cannot provide causal failure attribution, and lack awareness of tool dependency constraints or hazardous minefields.

### 2.4 Bridging the Gaps: The DynSTEER Approach
In summary, current evaluation paradigms leave a critical gap. Existing trajectory evaluators either inspect isolated atomic steps that suffer from local noise, or evaluate full traces post-hoc against a single reference path that penalizes valid diversity. Meanwhile, early stopping methods lack domain semantics and failure attribution. DynSTEER bridges this divide through stage-wise dynamic evaluation that adapts review capacity to weak or ambiguous stages, path-tolerant milestone graphs compiled from public task views, and execution-time early stopping that eliminates wasteful computation.

## 3. Methodology

DynSTEER establishes an end-to-end framework for dynamic agent trajectory evaluation. Rather than treating rollouts as opaque black boxes or enforcing rigid single-path matching, DynSTEER coordinates three core components: (1) stage-wise dynamic evaluation to resolve the evidence-granularity problem (Sections 3.3 and 3.4); (2) path-tolerant milestone-graph generation to preserve execution diversity (Section 3.2); and (3) execution-time early termination to curb resource waste (Section 3.4).

### 3.1 Problem Formulation and Dual-Clock Observation Model
Following standard formulations of interactive tool-using agent environments~\citep{gu2024toolsandbox,yang2023intercode,liu2023agentbench}, an agent task is formalized as a tuple $T = (x, \mathcal{S}_0, \mathcal{A}, \mathcal{C})$, where $x$ represents the natural language instruction, $\mathcal{S}_0$ is the initial environment state, $\mathcal{A}$ denotes the catalog of accessible tool APIs with input/output parameter schemas, and $\mathcal{C}$ defines environment state invariants and safety contracts. To preserve evaluation integrity and prevent test contamination, DynSTEER operates strictly over the **public task view**:
$V_{\text{public}} = (x, \mathcal{A}, \mathcal{I}_0)$
where $\mathcal{I}_0$ denotes public metadata, strictly isolating any hidden ground-truth reference paths $\tau^*$ or terminal unit tests $\mathcal{V}_{\text{final}}$.

During execution, interaction traces are captured as sequential discrete events $\tau = \langle e_1, e_2, \dots, e_K \rangle$. To balance continuous safety screening with accurate semantic evaluation, DynSTEER introduces a **dual-clock** observation model:
- **Raw Event Clock ($k \in \{1, \dots, K\}$)**: Increments at every atomic event (agent thoughts, tool calls, API responses), dedicated to continuous safety monitoring for fatal minefield operations $e_k \in \mathcal{M}_{\text{fatal}}$.
- **Closed Action Clock ($t \in \{1, \dots, T\}$)**: Increments only when an action reaches semantic closure---specifically, when a tool call receives its execution return and the agent yields control. Milestone settlement and scoring occur strictly at closed action boundaries, guaranteeing tool outcomes are fully observed before grading.

### 3.2 Path-Tolerant Milestone Graph Generation (Addressing Challenge 2)
To overcome single-reference bias, DynSTEER compiles $V_{\text{public}}$ into a path-tolerant Milestone Directed Acyclic Graph (DAG) $G = (M, E, \mathcal{K}, \mathcal{M}_{\text{fatal}})$ prior to execution:
- **Multi-Candidate Simulated Rollouts**: A blueprint generator synthesizes $C$ candidate execution plans $\{G_1, \dots, G_C\}$ via temperature-sampled LLM prompting across diverse exploration heuristics (e.g., direct keyword query versus hierarchical temporal filtering). Each candidate $G_c = (M_c, E_c)$ represents an alternate valid decomposition of task requirements.
- **Deterministic Contract Validation**: Each candidate undergoes rule-based verification: $\text{Valid}(G_c) = \text{SchemaCheck}(M_c, \mathcal{A}) \land \text{Acyclic}(E_c) \land \text{BindingConsistency}(\mathcal{K}_c)$. Candidates with hallucinated APIs, type mismatches, or cyclic dependencies are pruned.
- **Strict-Majority Consensus and Transitive Reduction**: A milestone $m$ is retained in $M$ if and only if it appears across a strict majority of valid candidates: $M = \{ m \mid \sum_{c=1}^{C_{\text{valid}}} \mathbb{I}[m \in M_c] \ge \lceil (C_{\text{valid}} + 1) / 2 \rceil \}$. Precedence edges are aggregated analogously, and transitive reduction $E = \text{TR}(E_{\text{majority}})$~\citep{tarjan1972depth} eliminates redundant shortcut dependencies, yielding a minimal DAG that accommodates diverse legitimate execution strategies.

### 3.3 Stage-wise Dynamic Trajectory Evaluation (Addressing Challenge 1 Base)
- **Ready Frontier Matching**: The active search space is strictly bounded by the ready frontier $\mathcal{F}_t = \{ m \in M \setminus M_{<t} \mid \text{Pred}(m) \subseteq M_{<t} \}$, preventing out-of-order settlement or reward gaming.
- **Dominator-Anchored Stage Isolation**: To evaluate milestone $m^*$ without contamination from concurrent parallel branches, DynSTEER computes the immediate dominator $\text{idom}(m^*)$ in $G$~\citep{lengauer1979fast}, bounding the stage evidence window by $\mathcal{W}(m^*) = [t_{\text{idom}(m^*)}, t]$. Unlike whole-trajectory evaluation which obscures mistake locations and step-level evaluation which lacks context and suffers from noise, dominator-anchored stages provide sufficient trajectory context to evaluate intermediate quality and pinpoint the exact first-error step.
- **Multidimensional Quality Scoring**: Milestone $m^*$ is scored across four orthogonal dimensions: Correctness ($c$), Efficiency ($\eta$), Safety ($s$), and Tool Compliance ($\kappa$).
- **Adaptive Review as Part of Stage Evaluation**: Uncertainty-adaptive multi-tier review treats stage scores and uncertainty as control signals, allocating stronger review capacity to ambiguous or weak stages. It belongs to the stage-wise evaluator and completes its granularity solution, rather than being a separate early-stopping objective.

### 3.4 Uncertainty-Adaptive Review and Execution-Time Early Termination (Addressing Challenge 1 Review Layer and Challenge 3)

Uncertainty-adaptive review is the control layer of stage-wise evaluation: it adjusts dimension weights and judge tiers after each stage. Execution-time early termination is a separate downstream safeguard that reuses the same stage evidence to control resource waste.
- **Uncertainty-Adaptive Review Adjustment**: Unlike static evaluators applying uniform grading, DynSTEER dynamically adjusts its evaluation policy based on intermediate observations. When an agent exhibits marginal stage scores ($|S_t - \theta_{\text{pass}}| < \delta_{\text{margin}}$) or elevated uncertainty across specific dimensions (e.g., tool parameter retries or argument hallucinations), DynSTEER adaptively shifts dimension weights $\mathbf{w}_{t+1}$ and escalates judge inspection rigor to target the agent's weak execution segments.
- **Uncertainty-Adaptive Multi-Tier Judge Ladder**: Evaluators are organized into a three-tier hierarchy (prompt templates detailed in Appendix):
- **Tier 1: Cheap Judge ($J_{\text{cheap}}$)**: A deterministic structural heuristic evaluator that inspects tool status codes, parameter schema conformity, and interval evidence rules without invoking LLMs (incurring zero LLM inference calls).
- **Tier 2: Standard Judge ($J_{\text{std}}$)**: A multi-field unified LLM evaluator that scores all target dimensions simultaneously within a single structured JSON prompt pass, aggregating confidence across multiple sampling passes.
- **Tier 3: Expensive Judge ($J_{\text{exp}}$)**: A single-dimension specialist LLM evaluator. Instead of bundling dimensions together, $J_{\text{exp}}$ invokes dedicated expert rubrics for each individual dimension (e.g., tool quality, safety, or efficiency). It conducts in-depth forensic analysis on tool arguments, return values, side effects, and counterfactual reasoning across multiple deliberation passes, capturing subtle behavioral failures that unified multi-field judges overlook.
- **Execution-Time Early Termination**: To prevent runaway executions on unrecoverable trajectories, DynSTEER continuously evaluates four principled stopping criteria:
  1. *Fatal Minefield Interception* ($e_k \in \mathcal{M}_{\text{fatal}}$): Continuously screens raw event steps for irreversible or hazardous operations, halting the rollout immediately on the raw event clock.
  2. *Structural Invariant Failures*: Catches persistent execution breakdowns, such as unparseable tool payloads or schema violations across consecutive retries.
  3. *Persistent Low Stage Quality* ($S_t < \theta_{\text{fail}}$): Evaluates cumulative stage scores, halting the run if severe quality deficits indicate irreversible goal drift.
  4. *Frontier Stagnation and Deadlock*: Monitors progress along the ready frontier $\mathcal{F}_t$, detecting infinite loops and triggering a deadlock halt if $N_{\text{stag}}$ consecutive actions settle no milestones.
  When triggered, DynSTEER immediately halts the rollout, recording the precise step index, dominator anchor, and failed causal dimension into an auditable diagnostic report. *(See Algorithm~1 in Appendix for complete algorithmic execution details).*

## 4. Experiments

We evaluate DynSTEER on the ToolSandbox benchmark, addressing three central research questions:
- **RQ1 (Section~4.2):** Does stage-wise dynamic evaluation provide superior error focus and model discriminability over native terminal evaluation?
- **RQ2 (Section~4.3):** Can milestone graphs compiled from public task views reliably capture necessary goals while accommodating diverse solution paths?
- **RQ3 (Section~4.4):** How effectively does execution-time early stopping identify and remove wasteful execution prefixes?

### 4.1 Experimental Setup and Evaluation Metrics
**Benchmark Environment.** We evaluate on ToolSandbox~\citep{gu2024toolsandbox}, featuring state snapshots, dynamic argument types, and safety minefield invariants (detailed statistics in Appendix~A).

**Evaluated Agent Backbones.** We evaluate four instruction-tuned LLMs as agent backbones: DeepSeek-V4-Pro, DeepSeek-V4-Flash, Qwen3-Max-2026-01-23, and Qwen-Plus-2025-12-01, operating under standard ReAct prompting.

**Comparative Paradigms.** Each agent backbone is evaluated under two comparative paradigms: (1) **Default**, using the native benchmark verifier based on terminal environment state; and (2) **DynSTEER-Replay**, replaying the paired source trajectory under our stage-wise evaluation, dominator review, and adaptive judging ladder. Replay discovers and records a virtual stop without actually re-executing the remaining agent suffix. Thus Table 3 measures a counterfactual saving that would be realized by stopping during execution, not a separately executed live run.
The Virtual Policy-Stop Rate in Table 1 counts all replay-detected policy stops, including both agent underperformance and safety minefields; it is not a live execution rate.

**Evaluation Metrics.**
- **Metric Discriminability (DS):** Following evaluation meta-evaluation literature~\citep{sakai2006evaluating,sakai2014statistical,carterette2012multiple,zheng2023judging}, we measure **Discriminability Score (DS)** at margin $\epsilon$:
  $DS(\epsilon) = \left( \frac{\sigma_{\text{pop}}}{\mu} \right) \cdot \sqrt{\frac{N_{\text{sig}}(\epsilon)}{\binom{M}{2}}}, \quad N_{\text{sig}}(\epsilon) = \sum_{1 \le i < j \le M} \mathbb{I}\left( |\bar{s}_{m_i} - \bar{s}_{m_j}| > \epsilon \right)$
  where $\mu$ is the population mean score across models, $\sigma_{\text{pop}}$ is the standard deviation across model means (their ratio representing the Coefficient of Variation, penalizing metric compression), and $N_{\text{sig}}(\epsilon)$ is the number of statistically distinguishable model pairs at margin $\epsilon$.
- **Milestone Graph Quality:** We compute Tool Operation Micro-F1 and Macro-F1 to measure semantic tool coverage, Fatal Minefield F1 for safety-critical action detection, and Graph Edit Distance (GED) similarity for structural topology alignment.
- **Efficiency and Early Termination:** For stopped replay run $i$, we pair it with the complete Default trajectory from the same model, repeat, and case, align its virtual stop to the recorded execution-timing batches, and define stop progress $p_i=b_i^{\text{stop}}/b_i^{\text{full}}$ and saved progress $q_i=1-p_i$. For each model, the Table 1 replay stop rate divides replay-detected stops by the replay trajectories in that model's evaluation population and includes quality failures, frontier stagnation, and fatal minefields. Table 3 therefore conditions on the stopped-run population identified by this definition rather than a separately selected subset.

### 4.2 RQ1: Stage-wise Dynamic Evaluation, Error Focus, and Model Discriminability
Results are presented in Table~1. On Default, 22.63% of runs receive an exact 1.0 score, reflecting severe score saturation. At $\epsilon = 0.01$, Default achieves a DS of 0.0270, separating 5 of 6 pairwise model comparisons (83.33%). In contrast, DynSTEER achieves a DS of **0.0500** (**85.2% relative improvement**) and discriminates **all 6 out of 6 model pairs (100.0%)** with statistical significance ($p < 0.01$). DynSTEER yields a calibrated mean of 0.6892 with higher variance (0.0345 vs. 0.0224), penalizing trial-and-error flukes that stumble into a correct final state. In blind human auditing across 60 sampled trajectories with divergent labels, DynSTEER correctly pinpointed the first critical error with 91.7% agreement with expert annotators.

### 4.3 RQ2: Blueprint Compilation Reliability and Path Tolerance
Results are summarized in Table~2. DynSTEER's multi-candidate synthesis and strict-majority consensus achieve **86.0% Tool Operation Micro-F1** (Precision: 81.7%, Recall: 90.7%) and **86.7% Fatal Minefield F1** (Recall: 86.0%). Transitive reduction retains alternative parallel branches without penalizing valid exploratory variations, achieving a 100% valid DAG compilation rate with consistent topological alignment.

### 4.4 RQ3: Counterfactual Early-Stop Savings and Judge Efficiency

Table 3 separates stop causes instead of mixing them into one label. The stage low-score policy (**SCORE**) fires 234 times over 64 unique cases. Milestone/frontier stagnation (**DEADLOCK**) fires 285 times over 125 unique cases. Fatal minefields (**MINEFIELD**) fire 704 times over 97 unique cases. Their run counts sum to the same 1,223 policy stops counted in Table 1; unique-case counts overlap and therefore do not add.

These stops occur at 54.59% of the paired full trajectory on average and would remove 45.41% of the remaining execution steps. Minefield interception is earlier and saves a larger fraction of the remaining suffix than quality or deadlock stops, while SCORE stops are conservative on short trajectories. We report savings as normalized progress rather than absolute step counts or a wall-clock net benefit: replay judging latency is measured on a different clock from environment execution, and mixing them produced the unreliable "net greater than gross" comparison.

## 5. Discussion and Limitations

DynSTEER evaluates execution control rather than replacing the agent planner. The framework allows multiple legal paths, but the quality of the milestone graph still bounds the legal-path space represented by the evaluator. Multi-candidate generation and majority aggregation reduce single-path bias but cannot prove that all reasonable paths are covered. Graph reliability must therefore be reported as an independent result; later stage scores cannot be used to prove that the graph was correct.

Dynamic weights and thresholds are interpretable heuristics. The update rule increases later attention when a dimension has a low score or high uncertainty, but this does not guarantee that the next stage contains the same failure. Task types may require different initial weights. The paper should report sensitivity analysis and treat task type as an experimental factor rather than interpreting the default `\alpha=\beta=1` as theoretically optimal.

Repeated LLM-judge agreement is only internal agreement. A judge can be consistently wrong, so confidence cannot replace human labels, native state checks, or safety constraints. For high-risk tasks, hard constraints and minefields should take precedence over natural-language judging. Expensive judging provides additional diagnostic evidence; it should not override a structural fact failure.

The current implementation has the most complete preserve-state, snapshot, and minefield semantics for ToolSandbox. SWE-bench Pro and SkillsBench require additional artifact contracts, test-result scorers, and long-horizon code-state handling before equally granular cross-benchmark conclusions can be made. Replay also cannot recover real execution cost from a historical trajectory that lacks timing or token coverage.

Finally, the repository does not yet contain a production interface that injects stage diagnoses into the next agent context and automatically retries. The defensible current claim is that DynSTEER evaluates, stops, replays, and reports evidence during or after execution. It provides an early-stop and diagnosis substrate for an evaluation--optimization loop; the benefit of complete automatic correction requires a subsequent control experiment.

## 6. Conclusion

In this work, we presented DynSTEER, a dynamic stage-wise framework that advances LLM agent trajectory evaluation from passive, post-hoc terminal verification toward proactive, stage-wise execution review. To bridge the granularity gap, DynSTEER segments rollouts at key execution nodes and adapts its multi-tier review strategy based on stage-level results. It compiles a path-tolerant milestone graph from public task views to preserve diverse legal paths without reference leakage, and it supports terminating unrecoverable agent executions to curb resource waste.

Experiments show that DynSTEER improves evaluation discriminability by 85.2% over native evaluation, separates all model pairs with statistical significance, and saves 45.41% of execution steps on failed rollouts, establishing a scalable, diagnosable, and cost-aware foundation for evaluating and optimizing LLM agents. In our future works, we will investigate feeding stage-level diagnostic feedback back to agents and validating automatic recovery across broader long-horizon environments.

## 7. Working References (To Be Verified Before Submission)

The following entries originate from project documentation. Authors, versions, venues, page numbers, and final links should be checked individually before submission.

1. *ToolSandbox: A Stateful, Conversational, Interactive Evaluation Benchmark for LLM Tool Use Capabilities*. Findings of NAACL 2025. https://aclanthology.org/2025.findings-naacl.65.pdf
2. *AgentBench: Evaluating LLMs as Agents*. ICLR 2024. https://openreview.net/forum?id=zAdUB0aCTQ
3. *WebArena: A Realistic Web Environment for Building LLM Agents*. https://arxiv.org/abs/2307.13854
4. *Tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains*. https://arxiv.org/abs/2406.12045
5. *The Berkeley Function Calling Leaderboard (BFCL): From Tool Use to Agentic Evaluation of Large Language Models*. ICML 2025. https://proceedings.mlr.press/v267/patil25a.html
6. *AgentRewardBench: Evaluating Automatic Evaluations of Web Agent Trajectories*. https://arxiv.org/abs/2504.08942
7. *TRAJECT-Bench: A Trajectory-Aware Benchmark for Evaluating Agentic Tool Use*. https://arxiv.org/abs/2510.04550
8. *AURA: A Diagnostic Framework for Tracking User Satisfaction of Interactive Planning Agents*. https://arxiv.org/abs/2505.01592
9. *Do Agents Dream of Root Shells? Partial-Credit Evaluation of LLM Agents in Capture the Flag Challenges*. https://arxiv.org/abs/2604.19354
10. *Claw-Eval: Towards Trustworthy Evaluation of LLM Agents*. https://arxiv.org/abs/2604.06132
11. *Agent-Testing Agent: A Meta-Agent for Automated Testing and Evaluation of Conversational AI Agents*. EACL 2026. https://aclanthology.org/2026.eacl-long.339/
12. *Efficient Benchmarking of AI Agents*. https://arxiv.org/abs/2603.23749
13. *STeCa: Step-level Trajectory Calibration*. https://arxiv.org/abs/2502.14276
14. *AgentProcessBench*. https://arxiv.org/abs/2603.14465
