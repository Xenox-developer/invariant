# INVARIANT: daily infrastructure needs of production agent teams

**Decision date: October 18, 2026.** Global, publicly available English-language desk research. This study supersedes the earlier product-direction recommendation in `MARKET_RESEARCH.md`; that report remains an evidence archive. No interviews, outreach, paid research, product experiments or purchases were conducted.

> **Direction status:** the user rejected run management as insufficiently distinct from existing tools. The ranked proposal below is retained as research history, not an accepted roadmap. See the [focused follow-up on task authorization](/Users/nikchin/Desktop/invariant/docs/DAILY_PRODUCTION_PROBLEM.md), which identifies concrete production work and competing solutions without declaring a validated product opening.

## 1. Decision brief

**Do not expand INVARIANT into another test runner, permission dashboard or general agent platform on the strength of this research.** Those are important capabilities, but the reviewed alternatives already provide substantial coverage. The previous recommendation confused evidence that teams need trustworthy results with evidence that they need our proposed verification product. The user's objection—Codex already runs tests—is valid. (P-C030–P-C034, P-C038.)

The strongest cross-segment research lead is **managing the working state of long agent tasks so a team can understand a failure, recover useful work and try a change without reconstructing everything manually**. The recurring job is closer to debugging and managing experiments than checking whether generated code passes tests. Accounts from deployed research agents, internal product development and agent-training infrastructure make this a credible problem to investigate. They do **not** establish its frequency across all companies, an unserved market or willingness to buy INVARIANT. Confidence is **medium in the problem, low in the proposed entry point**. (P-C002–P-C009, P-C038, P-C046.)

**Conditional direction:** investigate a portable record of an agent run, linked to its workspace, inputs, tool interactions and artifacts, with explicit capture limits and controlled branches for experiments. Start with teams already operating code/tool-using agents and losing engineering time reconstructing failures. Support their current agent, tracing and execution systems. Do not require a framework migration. This is a hypothesis to compare against AgentFS, LangGraph/LangSmith, MLflow, Braintrust, DVC and ordinary scripts—not a discovered empty category. (P-C039–P-C041.)

Three opportunities deserve comparison:

| Rank for INVARIANT discovery | Daily job | Why investigate | Main reason it may fail |
|---|---|---|---|
| **1. Reconstruct and branch failed or expensive agent work** | “Show me what happened, give me the relevant starting state, and let me try a fix without repeating real actions.” | Shared relevance to agent-product debugging and research iteration; observable human reconstruction work. | Existing state, experiment and observability systems may be sufficient; integration could be harder than the original problem. |
| **2. Provision a complete workspace for each agent task** | “Give this task its code, services, data, dependencies, compute and scoped access.” | Concrete infrastructure investment in product and scientific teams. | Crowded execution market; GPUs, private deployment and data gravity make a complete offering expensive. |
| **3. Maintain the project knowledge agents actually need** | “Use the current definitions, constraints, decisions and procedures without making me repeat them.” | Strong internal-workflow examples; can affect every task. | Much of the work is domain-specific semantic curation; an infrastructure wrapper cannot infer the correct meaning. |

This is a qualitative ranking of entry hypotheses, **not a measured demand score**. Durable execution is also a serious need, but it has strong direct substitutes; building a competing workflow engine is not the recommended entry. No research here can establish a path to Git/Docker-scale popularity. It can identify a useful primitive, test whether it saves work and reject a direction before a large build. (P-C015–P-C019, P-C039.)

## 2. What market and which teams?

“Teams using agents” is too broad to be one customer. This study separates four workflows:

| Segment | Work being done | Primary user | Plausible budget owner—hypothesis, not an interviewed buyer | Trigger for evaluating infrastructure |
|---|---|---|---|---|
| Teams building agent products | Operate assistants that use tools, code and external systems for customers or employees. | Agent/application engineer; on-call engineer. | Engineering or platform lead. | A costly failed run, customer incident, deployment regression or growth in concurrent tasks. |
| Product teams developing with agents | Delegate software changes, investigations and maintenance. | Developer; reviewer; developer-experience engineer. | Engineering productivity/platform owner. | Multiple simultaneous tasks become difficult to provision, steer or diagnose. |
| Model and applied-ML research teams | Generate rollouts, compare harnesses, train/evaluate models and run many experiments. | Research engineer; ML researcher. | Research lead or ML infrastructure owner. | Infrastructure failures contaminate comparisons, or reproducing a result consumes researcher time. |
| Scientific research teams | Run code over proprietary datasets and specialized tools, sometimes on private compute. | Computational scientist; research software engineer. | Research computing, data platform or research director. | A scientific agent must work with real data, large resources and restricted deployment. |

The roles and triggers above are recruitment hypotheses derived from the cases below. Team size, number of agents and choice of model are not established segmentation boundaries. Individual chat users are relevant to distribution, but evidence that they will buy team infrastructure is absent. (P-C003–P-C009, P-C035, P-C037, P-C040.)

We distinguish **using an agent to develop a product** from **developing a product that contains an agent**. The first often ends in a patch; the second may modify business records or interact with users continuously. A product that handles only repository files cannot claim to solve both. Similarly, research experimentation is not automatically a production deployment, and a scientific result is not evidence of infrastructure purchasing demand. These distinctions govern the evidence labels in the case ledger.

## 3. Method and strength of evidence

The source ledger retains earlier sources under `S-...` and adds production-focused sources under `P-S...`. The claims ledger carries the earlier 15 coding-agent issue claims plus the new study's factual claims and recommendations. The case ledger distinguishes 12 organization/workflow accounts, nine additional runtime-SDK issue threads and the 15 retained CLI issue threads. Related accounts from one organization are not independent corroboration. These are **purposive samples**, not a census or representative survey.

Primary engineering accounts were preferred for what teams actually built and maintained. Official documentation establishes described capabilities; it does not establish satisfaction or prove reliable performance. Named customer stories establish narrower attributable behavior, with vendor selection bias. Issue reports show concrete failure mechanisms or requests, but do not establish prevalence, independent organizations, continuing defects or budgets. A closed issue is not proof of a shipped fix. Search results were used for discovery; original pages and tracker metadata were used for the new SDK cases.

The additional survey is LangChain's **State of Agent Engineering**, published June 12, 2026, but fielded **November 18–December 2, 2025**. It reports 1,340 responses from a public survey, with technology representing 63% and organizations below 100 employees 49%. Quality is its leading reported production barrier. Recruitment is non-probability/self-selected; full instrument, response rate, weighting and item-level denominators were not established. The sponsor sells relevant infrastructure. We therefore use it as directional context, not a current population estimate or a trend against other surveys. “Quality” includes problems an environment manager cannot fix. [Report and methodology](https://www.langchain.com/state-of-agent-engineering). (P-C001.)

The earlier report's developer surveys retain their own different questions, field periods and populations; their percentages are not pooled with this one. There is no defensible industry-wide failure frequency, total addressable market, market-share estimate or willingness-to-pay estimate in this study. Public English sources also underrepresent private deployments, non-English teams, failed internal projects and organizations that have solved their problem without publishing.

## 4. Evidence from actual workflows

| Case and evidence class | Concrete work or difficulty described | Existing response | What it establishes—and what it does not |
|---|---|---|---|
| **Anthropic Research**, first-party production account, June 2025. | Long-running failures made restarts costly; dynamic behavior complicated diagnosis and deployment changes. | Recovery checkpoints, tracing and coordinated version rollout. | State and lifecycle work existed in a shipped product; not evidence that its current solution is inadequate. [Engineering account](https://www.anthropic.com/engineering/multi-agent-research-system). P-C002. |
| **Edison Kosmos**, first-party deployment account, May 2026. | Partner deployments required private data, substantial compute and restricted egress. | On-premise sandboxes, GPU/resource pools, shared persistent storage, hosted packages and credential proxying. | Serious scientific execution requirements; not evidence that a small local container launcher can meet them. [Engineering account](https://advances.edisonscientific.com/research/how-we-built-kosmos/). P-C003. |
| **OpenAI internal data agent**, first-party internal use, January 2026. | Correct answers required company-specific meanings and refreshed information. | Code-derived context, retained corrections, refresh pipelines and live data inspection. | Context maintenance is operational work; not merely installing a memory database. [Engineering account](https://openai.com/index/inside-our-in-house-data-agent/). P-C004. |
| **OpenAI agent-built product**, internal beta account, February 2026. | Agents needed an understandable application, development setup and persistent project knowledge. | Isolated application/observability per worktree; maintained repository documentation. | Teams invest beyond model calls; existing repositories and tools can provide the solution. [Harness engineering](https://openai.com/index/harness-engineering/). P-C005. |
| **OpenAI Symphony**, first-party operational account, April 2026. | Managing many sessions and stalled tasks created coordination overhead. | Ticket-based orchestration with an open-source specification. | Agent coordination is real work, with an integrated native substitute already available. [Symphony](https://openai.com/index/open-source-codex-orchestration-symphony/). P-C006. |
| **Prime Intellect**, supplier's own production training/evaluation account, July 2026. | Each rollout needed runtime lifecycle handling and infrastructure-error treatment. | Separate tasksets, harnesses and runtimes. | Infrastructure-versus-model failure classification matters to research; this is already a framework feature. [verifiers v1](https://www.primeintellect.ai/blog/verifiers-v1). P-C007. |
| **Extropic**, vendor-hosted research experiment account, publication date not stated. | Agent training needed coordinated GPUs, inference and sandboxed rollouts. | Used Prime Intellect's integrated stack. | A named team chose supplied infrastructure rather than building every component; contract value and ongoing frequency unknown. [Customer account](https://www.primeintellect.ai/case-study/extropic). P-C008. |
| **Wayfair**, vendor-hosted applied-research account, June 2026. | Researchers implemented and scored many model variants. | Parallel Cursor experiments on a standardized benchmark; a selected model reportedly entered production. | Research iteration can become agent-driven; this is also evidence native tools plus a team framework may be enough. [Customer account](https://cursor.com/blog/wayfair). P-C009. |
| **Google DeepMind AlphaEvolve**, first-party outcome account, May 2026. | Algorithm discovery required task-specific candidate evaluation. | A specialized search/evaluation system. | Research and infrastructure applications exist; it does not show demand for a generic agent environment product. [Outcome account](https://deepmind.google/blog/alphaevolve-impact/). P-C010. |
| **Grid Dynamics**, named practitioners on Temporal's site, September 2025. | A deep-research implementation incurred support work for inconsistent state, retries and debugging. | Migrated its Redis-based implementation to Temporal. | A concrete migration motivated by operational burden; not a general comparison proving LangGraph inadequate today. [Practitioner account](https://temporal.io/blog/prototype-to-prod-ready-agentic-ai-grid-dynamics). P-C046. |
| **Stripe**, first-party internal deployment, February 2026. | Coding agents needed company development infrastructure and deterministic tooling. | Built an internal integrated workflow. | Internal investment; no INVARIANT purchase signal. [Minions](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2). P-C035. |
| **Lark**, vendor-hosted customer account, September 2026. | Agent-generated applications used customer data. | Selected E2B for isolated execution. | Supplier selection for an execution need; no public contract amount or broad buy-versus-build conclusion. [Customer account](https://e2b.dev/customers/lark-case-study). P-C037. |

Ramp's background-agent account is retained as supplementary evidence, with the earlier study's incomplete direct-rendering limitation. It is not counted as another fully inspected case in the table above. [Ramp account](https://engineering.ramp.com/post/why-we-built-our-background-agent). (P-C036.)

**Synthesis:** the shared operational work concerns execution, durable state, usable context and iteration. The implementations and purchase decisions differ substantially. Some teams build internal platforms, some adopt an integrated supplier, and others extend native tools. None of these accounts reports a purchase of INVARIANT or demonstrates dissatisfaction with every substitute. (P-C038.)

## 5. Failure reports: repeatable themes, limited inference

Nine new public threads were selected around persistence, interruption and recovery in three runtime ecosystems. Bodies were inspected; current state and dates came from public GitHub REST metadata checked October 18, 2026. **They were not reproduced.** The table intentionally shows closures, including where discovery search results still described a thread as open.

| Thread | Report/request | State at review | Interpretation |
|---|---|---|---|
| [LangGraph #8039](https://github.com/langchain-ai/langgraph/issues/8039) | Checkpoint/write ordering during crash recovery. | Open. | Specific recovery-boundary report, not a population failure rate. P-C101. |
| [LangGraph #8115](https://github.com/langchain-ai/langgraph/issues/8115) | Concurrent pending-write loss. | Open. | Reported persistence race; possibly related mechanisms must not be counted as independent demand. P-C102. |
| [LangGraph #8382](https://github.com/langchain-ai/langgraph/issues/8382) | Beta DeltaChannel reconstruction order differed from live state. | Closed/completed, October 6. | Historical report; no released-version verification here. P-C103. |
| [OpenAI Agents SDK #4323](https://github.com/openai/openai-agents-python/issues/4323) | Durable pending input during a resumable run. | Closed/completed, August 9. | Narrow feature request acknowledging existing resume mechanisms. P-C104. |
| [OpenAI Agents SDK #4069](https://github.com/openai/openai-agents-python/issues/4069) | Callback-emitted history persisted again. | Closed/completed, August 5. | Historical session-handling report. P-C105. |
| [OpenAI Agents SDK #4646](https://github.com/openai/openai-agents-python/issues/4646) | Interrupted resume lost durable tool-guardrail results. | Closed/completed, August 26. | Historical boundary failure; no claim it remains present. P-C106. |
| [Google ADK #5064](https://github.com/google/adk-python/issues/5064) | Streaming long-running tool resume stopped early. | Closed/completed, July 23. | Historical report; closure does not prove our tested fix. P-C107. |
| [Google ADK #5048](https://github.com/google/adk-python/issues/5048) | Requested session backend and task lifecycle/recovery controls. | Closed/completed, October 13. | Feature interest; scope delivered unknown here. P-C108. |
| [Google ADK #5637](https://github.com/google/adk-python/issues/5637) | Authorization pause continued and overlapped later session writes. | Closed/completed, June 3; stale label also present. | Closure metadata has limited meaning; do not infer a released fix. P-C109. |

The retained sample adds five Claude Code, five Codex and five Gemini CLI threads. Their original qualified resolutions—including duplicates, stale closures, configuration errors and historical versions—remain in claims C-101 through C-115 and cases CI-01 through CI-15. They are **15 distinct threads, not 15 independent root causes or customers**, and not new observations in this study. See the [original issue discussion](/Users/nikchin/Desktop/invariant/docs/MARKET_RESEARCH.md) and [case ledger](/Users/nikchin/Desktop/invariant/docs/market-research/production-cases.csv).

Across these samples, recovery boundaries, lost/duplicated state, confusing setup and effective-policy visibility recur as themes. That establishes mechanisms worth investigating, not that INVARIANT should implement an alternative runtime. Tracker reports favor people encountering problems; missing denominators prevent “most teams” or “daily failure” claims. Framework improvements and closed reports are disconfirming evidence against monetizing a transient bug. (P-C101–P-C109; C-101–C-115.)

## 6. Needs by segment, consequences and limits

The following table is analyst synthesis. “Recurring” refers to the type of workflow in the case accounts; **daily incidence of failures has not been measured**. Severity is qualitative, not a score. (P-C038–P-C040.)

| Need | Frequency and consequence supported here | Workarounds and effort | Purchasing motivation and barriers | What infrastructure can actually address |
|---|---|---|---|---|
| Execution/environment setup | Required for each new task environment or rollout; scientific compute can require substantial platform work. | Images, worktrees, warm pools, scripts, K8s, hosted runtimes. | Platform/research owner may buy less operations work; existing infrastructure and data location increase switching cost. | Provisioning, isolation, dependency/data references and lifecycle. Cannot make arbitrary software compatible. |
| Failure reconstruction and debugging | Production and research accounts describe diagnosis/recovery work; per-team incidence unknown. | Trace viewers, logs, checkpoints, saved artifacts, notebooks, manually rebuilt inputs. | Agent engineer needs faster diagnosis; buyer must observe unrecovered human time after existing tooling. | Capture accessible inputs/state, link artifacts and identify missing evidence. Cannot expose unobservable provider state. |
| Durable execution and external actions | Long tasks can be interrupted; duplicate actions may have material consequences. | Durable runtimes, queues, idempotency keys and reconciliation. | Application/platform owner responsible for service reliability; runtime migration is expensive. | Durable control flow and action protocols. Cannot universally undo an email, payment or physical experiment. |
| Context and semantic correctness | Internal data and product-development accounts maintain knowledge continuously. | Repository docs, ingestion pipelines, memory, data catalogs and domain experts. | Users repeatedly explain project meaning; ownership can be split across engineering/data/product. | Freshness, provenance and access to knowledge. Cannot guarantee the knowledge or model's interpretation is correct. |
| Parallel coordination | Multi-session development and parallel research require allocation and result management. | Worktrees, task boards, orchestration frameworks and benchmark workflows. | Research/developer productivity owner; integrated native tools are a strong baseline. | Isolated workspaces, provenance and lifecycle. Cannot infer whether independently generated changes semantically agree. |
| Evaluation and review | Necessary to decide whether agent behavior improved, not just whether code compiles. | CI, task evaluators, human review, eval platforms and held-out datasets. | Product/research quality owner; relevance depends on the task. | Comparable fixtures and evidence collection. Cannot infer the correct business/scientific objective from test success. |
| Credentials and network access | Private-data deployment requires scoped connectivity and secret handling. | Proxies, vaults, native controls, package mirrors and policy tools. | Security/platform can own it; onboarding and trust reviews are substantial. | Identity, allowed destinations and enforced execution boundaries. A prompt is not enforcement. |

The common frustration is not simply “the model is bad.” It is that a failure may involve the model, stale context, a tool, a changed external system or runtime infrastructure, and the team must determine which. Yet improving observability does not itself eliminate model errors, and reproducible bad behavior is still bad behavior. An execution product must claim only the operational work it removes. (P-C002, P-C004, P-C007, P-C014, P-C038.)

## 7. Market map: what already exists

Capabilities below are documented/vendor-described, not independently benchmarked. “Unknown” means the reviewed material did not establish the capability or price; it does not mean absent. Products occupy different layers and are often combined. There is no normalized total-cost comparison without the same workload, resources, retention and deployment requirements.

| Layer and alternatives | Documented coverage relevant to this study | Commercial/public-cost evidence | Consequence for INVARIANT |
|---|---|---|---|
| **Local execution: Docker Sandboxes** | Agent execution with security controls; separate governance offering. [Docs](https://docs.docker.com/ai/sandboxes/), [governance](https://docs.docker.com/subscription-billing/plans/ai-governance/). | Local entitlement and compute/model costs depend on product; numeric governance quote unknown. P-C030. | A launcher and resource limits alone are weak differentiation. |
| **Hosted execution: E2B** | Hosted sandboxes. [Product](https://e2b.dev/), [pricing](https://e2b.dev/pricing). | Free Hobby / USD150 monthly Pro base in the reviewed offer, plus usage; eligibility/limits apply. P-C031, P-C048. | Compete against supplied operations, not only an API. |
| **Hosted execution: Daytona** | Managed sandbox lifecycle. [Docs](https://www.daytona.io/docs/en/sandboxes/), [pricing](https://www.daytona.io/pricing). | Allocated CPU, memory and storage metered by time. P-C031. | Hardware/backends/editions vary; no blanket security or cost comparison. |
| **Persistent machines: Sprites** | Persistent disk and filesystem checkpoints; checkpoint does not preserve process memory. [Lifecycle](https://docs.fly.io/sprites/working-with-sprites), [pricing](https://fly.io/sprites/). | Metered compute, memory and storage. P-C021, P-C031. | Persistence and filesystem recovery are already products. |
| **Native agent controls** | Claude checkpointing/sandboxing, Codex policies, Gemini sandboxes. [Claude](https://code.claude.com/docs/en/checkpointing), [Codex](https://openai.com/index/running-codex-safely/), [Gemini](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/sandbox.md). | Model/subscription entitlements vary; not a separate equivalent service. P-C032. | Must improve an end-to-end job beyond features already in the chosen agent. |
| **Environment/source primitives** | Dev Containers definitions; Git worktrees and source restoration. [Dev Containers](https://containers.dev/), [Git](https://git-scm.com/docs/git-worktree). | Existing runtime/hosting and maintenance costs; no like-for-like bundled price. P-C033. | If a devcontainer and scripts solve the job adequately, recommend them. |
| **Policy: NVIDIA OpenShell** | Declarative policy, credential/network controls; runtime source available. [Docs](https://docs.nvidia.com/openshell/latest/about/overview), [repository](https://github.com/NVIDIA/openshell). | Open source; comparable support price unknown here. P-C034. | “Policies outside the prompt” is necessary but already available. |
| **State filesystem: AgentFS** | SQLite-backed filesystem/KV/tool history; queryable snapshots, mount interfaces and experimental sandbox. [README](https://github.com/tursodatabase/agentfs), [design](https://turso.tech/blog/agentfs). | MIT software; hosted database offers are not the same as an AgentFS service price. P-C012, P-C013. | Direct substitute for much of a “Git for agent state” pitch. Evaluate reuse first. |
| **Framework state: LangGraph / LangSmith** | Checkpoint fork/replay, tracing and runtime/sandbox integration. Downstream calls re-execute. [Time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel), [runtime](https://www.langchain.com/blog/runtime-behind-production-deep-agents). | Free Developer single seat; Plus USD39/seat/month plus usage. [Pricing](https://www.langchain.com/pricing). P-C011, P-C016, P-C024. | Branching a run is not a new capability; demonstrate a concrete integration gap. |
| **Managed agent runtime: Anthropic** | Separate durable session events, harness and execution; recovery and credential proxying. [Design](https://www.anthropic.com/engineering/managed-agents). | Comparable price not established in this study. P-C017. | A competing general runtime would face a provider-integrated alternative. |
| **Durable workflows: Temporal / Restate** | Workflow/runtime approaches for recovery; fine-grained durability differs from saving state at boundaries. [Practitioner migration](https://temporal.io/blog/prototype-to-prod-ready-agentic-ai-grid-dynamics), [Restate analysis](https://restate.dev/blog/why-checkpointing-is-not-production-grade-durable-execution). | Temporal initially USD50/million actions plus storage, no base fee in reviewed offer; Restate numeric offer unknown in rendering. [Temporal pricing](https://temporal.io/pricing), [Restate pricing](https://restate.dev/pricing). P-C015, P-C025, P-C026, P-C046. | Integrate a durable engine when needed; do not invent universal exactly-once effects. |
| **Observability/evaluation: Braintrust** | Stateful evaluation and production-debugging workflow. [Stateful evals](https://www.braintrust.dev/blog/stateful-agent-evals). | Free tier; Pro USD249/month plus additional usage. [Pricing](https://www.braintrust.dev/pricing). P-C014, P-C023. | A trace viewer or report is not sufficient differentiation. |
| **Open-source tracing: MLflow** | Agent tracing, production monitoring, trace-derived datasets and OTel compatibility. [Docs](https://mlflow.org/docs/latest/genai/tracing/). | Open-source self-hosting; own infrastructure/operations still cost money. P-C018. | Free infrastructure is a serious substitute; integrate/export rather than creating a closed silo. |
| **Experiment lineage: DVC** | Git-linked experiment variants, run/save/compare/share. [Docs](https://doc.dvc.org/user-guide/experiment-management). | Local/open tooling plus storage/operations; equivalent managed price not established. P-C019. | Research teams may already have an adequate versioning system. |
| **Research environments: Prime / Harbor** | Task/harness/runtime separation; environment-driven benchmarks and trajectories. [Prime](https://www.primeintellect.ai/blog/verifiers-v1), [Harbor](https://hub.harborframework.com/). | Integrated infrastructure and open components; comparable end-to-end prices unknown here. P-C007, P-C008, P-C022. | A generic task package must interoperate with existing research formats. |
| **Coordination: Symphony** | Ticket-based agent orchestration and isolated issue workspaces. [Spec](https://openai.com/index/open-source-codex-orchestration-symphony/). | Open-source specification/reference; operating/model costs separate. P-C006. | A task board over coding agents is not an unoccupied entry point. |
| **Standards: OpenTelemetry** | Existing GenAI conventions work; reviewed page redirects to its dedicated repository. [Official pointer](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/). | Open specification; backend costs vary. P-C020. | Extend interoperable metadata; do not declare a new trace format necessary without a demonstrated gap. |

The existing research also records review vendors such as [CodeRabbit](https://www.coderabbit.ai/pricing). Their presence, alongside CI and native agents, further weakens the case for another generic review layer. No comparison here establishes all alternatives as feature-equivalent, or treats undocumented capability as absence. (P-C047; P-C038.)

## 8. Competing explanations and disconfirming evidence

**“Teams just need a better model.”** Sometimes true: quality includes reasoning, relevance and following task intent. Better state capture cannot fix those directly. A pilot must classify model/task-definition failures separately from missing inputs, unavailable tools, infrastructure failures and human reconstruction work. If model quality dominates and reconstruction is already cheap, stop this direction. (P-C001, P-C038.)

**“Another sandbox will make agents production-ready.”** The cases show important work outside execution: context, lifecycle, tools and organizational integration. Conversely, execution specialists already exist, and a named research team selected an integrated stack. A new general sandbox must beat an actual alternative on a concrete workload rather than advertise breadth. (P-C003–P-C008, P-C030–P-C037.)

**“A checkpoint makes the whole system reproducible.”** False as a general promise. Framework-state replay can reissue calls; filesystem snapshots omit remote services and process memory. Restoring captured state is different from reproducing the model's future choices. (P-C011, P-C015, P-C021, P-C045.)

**“Logs and a UI are the missing tool.”** MLflow, Braintrust and LangSmith are direct counterexamples. The opportunity, if any, must remove measured work left after those tools are in use. “Cross-framework” alone is not enough; a team using one framework may gain nothing from it. (P-C014, P-C016, P-C018.)

**“Research teams all need the same environment.”** Scientific data/compute requirements and model-training rollouts differ from ordinary coding tasks. A general interface can describe them, but someone still must implement and operate compatible backends. Do not claim GPU, on-premise or wet-lab support through a generic manifest. (P-C003, P-C007, P-C008, P-C045.)

**“Everything should move into INVARIANT.”** Existing deployments and task-specific systems may be adequate. A team may prefer one integrated supplier and deliberately accept its constraints. Integration and contribution to AgentFS, MLflow, a runtime or an existing research framework could be a better outcome than another standalone product. This study has not demonstrated that switching is worthwhile. (P-C008, P-C009, P-C011–P-C019, P-C039.)

## 9. Ranked shortlist in detail

### 1 — Failure reconstruction and controlled experiment branches

**Job:** take a real problematic run, understand the accessible evidence and starting conditions, then test a change from a named boundary. Users are agent engineers and research engineers; a plausible owner is their engineering/research infrastructure lead. Trigger: an expensive failure cannot be reconstructed from the current trace and workspace without manual repair. Current spending evidence is internal engineering/migration effort and use of supplier infrastructure, not disclosed budgets for this product. (P-C002, P-C007, P-C008, P-C046, P-C040.)

**Why it ranks first:** it has a concrete observable cost—human reconstruction and repeated setup—and can connect product debugging with research iteration. A small team could test a narrow integration before operating a cloud platform. Frequency, severity distributions, residual dissatisfaction and willingness to pay remain unknown. Its rank depends on finding a repeated gap after native checkpointing/tracing, not simply proving that agent failures exist. (P-C039; PA-001, PA-002.)

**Main disconfirmation:** AgentFS plus the team's trace store, or LangGraph plus its sandbox snapshot, already provides everything needed with little maintenance. DVC/MLflow may suffice for research. If so, recommend that combination; do not rename it as an INVARIANT innovation. Reachable channels are relevant OSS communities and teams publishing agent infrastructure—not an assumed ready sales pipeline. Integration friction and sensitive captured data are material switching costs. (P-C011–P-C019, P-C039.)

### 2 — Complete task workspaces across data and execution systems

**Job:** obtain a usable environment for a task, including dependencies, test services, required data references and bounded credentials. The stronger candidates are teams running many real code/data tasks, especially private scientific work. Potential owners are developer productivity or research computing. Trigger: existing sandbox APIs still leave substantial custom provisioning/integration work. (P-C003, P-C005, P-C007, P-C040.)

**Why second:** strong concrete infrastructure need, but completing the promise means backend, data and service integration—not merely persisting a Linux disk. Existing Docker/devcontainer/cloud offerings and private platforms are formidable. A small team should test one reusable integration with repeated demand, not promise all hardware, languages and deployment environments. Self-hosting/private data can be a requirement rather than a premium checkbox. (P-C003, P-C030–P-C034, P-C039.)

**Reject as the default expansion:** operating general GPU/cluster/private-deployment infrastructure would exceed the currently demonstrated product and multiply support burden before demand is known. Reopen if several teams independently show the same missing integration and sponsor a pilot. Package installation surviving resume is useful but does not prove this market. (P-C027–P-C029, P-C039.)

### 3 — Maintained, attributable project context

**Job:** keep the definitions, decisions and procedures an agent relies on accessible, current and attributable. User: developer, data analyst or researcher who repeatedly corrects an agent. Possible owner: product/data platform or engineering productivity; ownership may be fragmented. Trigger: stale or missing knowledge repeatedly produces expensive corrections. (P-C004, P-C005, P-C040.)

**Why third:** potentially frequent value with low compute demands, but the hard part is often deciding which knowledge is true. Repository documentation, catalogs, ingestion systems and native agent memory are existing responses. Distribution could occur through repository workflows; permissions, source freshness and entrenched knowledge systems create integration cost. WTP for another context product is unproven. (P-C004, P-C005, P-C039.)

**Reject as a universal memory layer:** retaining everything is not equivalent to providing relevant, correct knowledge. Pursue only if a narrow provenance/freshness workflow recurs across organizations and can be integrated without taking ownership of every company's semantics. (P-C038, P-C039.)

**Why not rank durable execution, generic agent orchestration or permissions higher?** They are serious engineering requirements, but Temporal/Restate, framework runtimes, Symphony, native controls and OpenShell directly address them. Replacing those systems would require substantial migration and trust without a demonstrated residual need. Use or extend them where appropriate. (P-C006, P-C015–P-C017, P-C025, P-C032, P-C034, P-C046.)

## 10. Conditional recommendation: a run as a shareable unit of work

This is a **product hypothesis**, not an implemented command set or established industry standard:

> A team should be able to point at an agent run, inspect the accessible state that produced it, and make a controlled experimental branch without assembling the evidence by hand.

The proposed unit is **a captured run**, not a chat transcript, a container or a test report. A run record would link the task and parent run; repository revision and workspace changes; environment identity; accessible model/tool inputs and outputs; external-data references; policy identity; artifacts; and a capture-coverage statement. It must distinguish what is copied, referenced, redacted, unavailable or externally mutable. Those fields are a design proposal, not a claim all can be automatically captured from every agent. (P-C041, P-C045; PA-002, PA-003.)

### Two everyday workflows to validate

**Product agent:** a customer reports a bad multi-step action. Today, the engineer assembles a trace, code revision, tool responses and a plausible service state, then tries to recreate the conditions. In the proposed workflow, an existing trace links to a local/private run record; the engineer opens the relevant captured state, inspects what is missing and tests a fix using recorded read responses and blocked external writes. Live verification is a separate explicitly authorized step. This example is hypothetical; it is not a customer interview finding.

**Research team:** a promising result appears in one of many agent experiments. Today, the researcher checks which code, environment, dataset, reward definition and inputs belonged to it, then prepares another experiment. In the proposed workflow, the researcher branches a named run while retaining input references, changes one declared component and compares results. Missing datasets or unavailable models are reported explicitly. This is also a hypothesis; not every experiment needs this extra layer. (P-C040, P-C041.)

The incremental value over Codex running tests would be **less reconstruction work and clearer experiment lineage**. It does not make the agent smarter, infer the correct tests or replace CI. If the team already does this adequately with its current tools, there is no incremental product value.

### Minimum useful experiment

Start with **one agent/framework and one existing execution backend selected from pilot teams**, not an INVARIANT-only execution requirement. Integrate its existing trace identifiers and checkpoint interfaces. The minimum experiment must:

1. Capture one agreed workflow with a manifest binding accessible execution evidence and artifact/environment references to a stable run ID.
2. Produce an explicit completeness report, including unsupported remote/proprietary state, mutable references and redactions.
3. Open an isolated captured workspace and inspect it without launching model calls or real external writes.
4. Create a named branch at a supported boundary, distinguishing recorded-read playback from fresh execution.
5. Export a documented, locally readable record that can be used alongside the team's trace/experiment store.
6. Demonstrate a measurable reduction in reconstruction work relative to the team's current stack.

These are proposed pilot requirements. They are not a commitment to build before interviews, nor a guarantee of universal interception. First test whether AgentFS can supply the storage portion and whether existing observability schemas already represent the events. Introduce custom fields only for a demonstrated gap. Do not build a new trace dashboard, durable execution engine, training scheduler or general cloud as part of the experiment. (P-C041; PA-002, PA-003.)

### Hard boundaries before making promises

| Operation | Defensible pilot meaning | Explicit limit |
|---|---|---|
| Inspect | Read captured evidence and artifacts. | A trace is not private model reasoning, complete kernel state or proof that every event was captured. |
| Playback recorded reads | Return previously captured tool/model responses in a supported fixture. | This examines a recorded path; it does not demonstrate a new model would choose the same actions. |
| Branch | Start an experimental continuation at a supported boundary with declared changes. | Framework serialization and compatible resources are required; arbitrary CLI internals may be inaccessible. |
| Re-execute | Make fresh calls/run code under declared current conditions. | Nondeterminism, provider revisions and changing external data can produce different outcomes. |
| Restore local state | Restore captured files and supported local fixtures. | Open transactions, live services, GPU/process memory and remote writes need separate treatment. |
| Recover a production task | Delegate to the application's runtime and reconciliation protocol. | Debugging a copy is not resuming an authoritative production workflow. |
| Undo | Reverse only operations with a validated, authorized inverse. | No universal rollback of emails, money movements, remote data mutations or physical work. |

These are engineering constraints and product-design inferences, not guarantees derived from vendor marketing. Persisting state does not make it trustworthy; untrusted artifacts require safe parsing and the original execution boundary. Captured secrets or customer data must not silently enter a shared registry. Exports require explicit inclusion/redaction rules, access checks and deletion policy. Privacy-driven missing evidence must remain visible. (P-C011, P-C015, P-C017, P-C021, P-C045.)

### Differentiation that must be demonstrated

The claim worth testing is: **“From a real failed run to an inspectable, safely branched working copy with less manual reconstruction, while keeping your current agent and trace system.”** That is a workflow claim to measure, not a new feature checklist. Evidence against it includes adequate existing combinations, low failure frequency, capture gaps at precisely the useful boundary, expensive fixture maintenance or mandatory framework rewrites. “We support multiple models” is insufficient; the team must save time on its own task. (P-C041.)

### Pricing hypothesis and economics

Test a **free local core** first. A possible paid product is a private team registry with shared run records, access controls, retention and supported integrations. An experimental **USD200–500 per team per month**, excluding compute and inference, can be tested after workflow value is observed. This is neither validated willingness to pay nor a market-clearing price. It also does not establish margins: storage volume, support and private deployments could make it uneconomic. Compare total maintenance cost and time saved with the customer's existing subscriptions and OSS stack; do not price by adding unrelated competitors' fees. (P-C042; PA-004.)

If value requires managed compute or an integrated full platform, that may favor an incumbent supplier instead. Do not force an independent registry business model onto contrary behavior.

## 11. What “as useful as Git/Docker/Conda” requires

Treat this ambition as a design constraint, not a forecast. A candidate foundational tool needs a stable object people already manipulate repeatedly, useful operations over that object, low adoption cost and compatibility with existing workflows. For the leading hypothesis the object would be a captured run; operations would be inspect, branch, compare and share. **Whether teams want this object is unproven.** (P-C039–P-C041.)

Proposed adoption sequence:

1. **Single-person utility:** one engineer resolves a real failure faster without changing the team's agent framework. No organization-wide rollout required.
2. **Team utility:** a second engineer can open the same record with declared missing state; sharing replaces a manual reconstruction handoff.
3. **Workflow integration:** the record links from existing traces, experiments and task systems rather than creating another place to monitor.
4. **Backend portability:** support another environment or agent only when real users need it. Preserve capture limitations instead of pretending native histories are interchangeable.
5. **Ecosystem utility:** publish a small readable manifest and compatible exports; contribute missing interfaces to existing projects where that is more useful than a competing standard.

Potential channels are maintainer collaboration, narrowly documented production-debugging examples and research infrastructure communities. Those are proposed recruitment/distribution channels, not proven conversion paths. Sustainable adoption must be observed as repeated unprompted use, teammate invitations and integrations maintained by users. Stars, downloads, a launch spike or association with “Git for agents” would not establish daily importance. (P-C039–P-C043.)

## 12. Fit with current INVARIANT

The [current architecture](/Users/nikchin/Desktop/invariant/docs/ENVIRONMENT_ARCHITECTURE.md) provides a persistent general Linux environment, a trusted host launcher, a Codex connection and enforced execution settings. Native chat, host-mounted project files and the saved Linux filesystem are separate forms of state. They are **not a joint portable checkpoint**. The narrow invoice broker is not an enterprise integration layer. (P-C027, P-C028.)

The saved [test-project migration report](/Users/nikchin/Desktop/invariant/.invariant/project-environment-checks/20261018T000517Z-589115d3/report.json) records 11 passing checks in one setup. It supports a limited implementation claim, not the proposed market direction; the checks were inspected, not rerun for this research. (P-C029.)

Reusable foundations could include the execution boundary, environment persistence and lifecycle supervision. Missing work for the leading hypothesis includes coordinated capture, trace/checkpoint integration, artifact addressing, external-I/O semantics, safe export/import and explicit coverage reporting. Scientific/private-compute work may require different backends. The architecture should adapt to validated demand; matching today's code is not evidence of a market. (P-C027, P-C041, P-C045.)

## 13. Interview and pilot protocol

**No outreach is authorized or performed.** The following protocol is ready for a later authorized validation phase. All sample sizes, thresholds and times are chosen decision rules, not research findings. (P-C043; PA-005.)

### Recruitment

Seek **eight organizations**, ideally four operating an agent product/internal agent and four running agent-driven research. Within research include both model/applied-ML and scientific work if reachable; do not replace absent scientific participants with coding demos and claim coverage. Recruit the person who debugged recent runs plus the person owning infrastructure or budget. Organization size is secondary to workflow eligibility.

Require a real multi-step code/tool workflow used in ongoing work and a concrete failure or result-reconstruction episode in the previous month. Include teams already satisfied with LangGraph/LangSmith, AgentFS, MLflow/DVC or an integrated platform; they are essential negative cases. Do not recruit only people posting bugs or interested in INVARIANT. Record refusals and reasons where voluntarily offered, without storing unnecessary identifiers.

### Questions about recent behavior

1. Walk through the last failed or surprising run you personally investigated. What was the task and what happened?
2. Show a redacted trace, artifact or incident timeline. What did you need that was no longer available?
3. Which code, environment, model settings, tool responses and data versions could you recover? Which did you reconstruct or guess?
4. How much human time was spent locating evidence, repairing the environment, rerunning work and deciding the cause? Separate waiting and inference/compute costs.
5. What did you actually use—native resume, checkpoint, CI, scripts, experiment tracker or a vendor? What worked well enough?
6. Was the cause model behavior, task ambiguity, stale context, tool/service behavior or infrastructure? What evidence supports that classification?
7. What happened after a tool call whose external result was uncertain? Who reconciled it? Was repeating it safe?
8. When did this occur previously? Show records if available; do not substitute a guess about frequency for observed history.
9. Who else needed the result or starting state? What did they do to use it?
10. Which data cannot leave your infrastructure? What may be retained, replayed, redacted or deleted? Who approves that?
11. What have you built, bought or abandoned to solve this? Who authorized the effort/spend, and what triggered that decision?
12. If your current tooling fixed the missing step, would you prefer that to a separate tool? What would make any new dependency unacceptable?

Do not open with “Would you use a Git for agents?” Ask for behavior before showing the concept. Feature enthusiasm is not evidence of budget or switching intent. Use redacted/synthetic artifacts if customer data cannot be shared; no request for confidential credentials, employer secrets or unauthorized production access.

### Interview gate

Proceed to a technical pilot only if **five of eight organizations** can document a related reconstruction/state-management problem in ongoing work and **at least three** identify a gap their current stack does not cheaply solve. At least **two organizations must agree to use real eligible workflows**, subject to their own approvals. To claim an initial cross-segment opportunity, the qualifying organizations must include both product-agent and research workflows. Otherwise narrow the initial customer honestly while retaining the broader study's negative result.

For the ambition of daily infrastructure, also require evidence that these five organizations manipulate the relevant run state or artifacts **at least weekly**, with examples across the previous month. Investigate useful operations on successful runs—experiment comparison, sharing and continuation—rather than relying on rare failures. If the value appears only during occasional incidents, it may justify a specialist debugging utility but does not validate the proposed everyday primitive.

Stop or change direction if reports are mostly rare one-off bugs, already fixed issues, model-quality complaints without reconstruction cost, or wishes unsupported by recent behavior. Do not count framework bugs as demand for replacement infrastructure automatically.

### Comparative pilot

Run a time-bounded pilot with **three organizations**, if eligible and later authorized. Each supplies at least **five real historical or naturally occurring cases** and uses its current stack as the baseline. Include successful runs as well as failures so capture overhead and routine usefulness are visible. Avoid creating artificial manual baseline work that the team does not normally perform.

For each case record task type, framework/agent version, environment, capture coverage and data restrictions. Compare the same saved incident under both workflows where possible; counterbalance order across cases to reduce learning effects. Historical cases cannot demonstrate capture of evidence that was never saved, so assess retrospective reconstruction and prospective capture separately. This small convenience pilot is not an industry-wide causal productivity study.

Measure:

- Human minutes to an agreed inspectable starting state and actionable diagnosis; document reviewer agreement on the cause rather than declaring every incident solved.
- Manual reconstruction steps and undisclosed assumptions required by a second engineer.
- Proportion of declared artifacts/fixtures actually available; unavailable state must be reported rather than silently substituted.
- Agent/framework changes required, ongoing fixture maintenance and integration/support time.
- Added execution latency, storage volume and capture failures, with a jointly agreed overhead budget per workload.
- Incidence of accidental external writes, secret exposure or misleading completeness/success claims during failure probes.
- Voluntary repeated use and requests from a second teammate after onboarding help stops.
- Active days and useful operations on successful runs, separating automatic background capture from deliberate user use.
- An actual paid renewal decision from the identified budget owner, separate from a feature request or letter of interest.

Predeclare **proceed** only if at least two organizations show a **30% reduction in median human reconstruction time**, at least **80% of selected eligible cases** open with their declared captured artifacts/fixtures and no undisclosed substitutions, and two organizations use the workflow voluntarily in **three consecutive weeks**. Require zero unauthorized external writes/secret disclosures in agreed probes, an acceptable workload-specific overhead budget, and a manageable support burden—initially no more than **one hour per organization per week after onboarding**. Test paid continuation with two budget owners at the stated quote range; record actual acceptance or refusal. Report each organization's results and task mix, not only a pooled median.

**Stop** if existing tools match the result after simple configuration, capture misses the useful boundary, privacy restrictions remove the value, state preparation costs exceed saved effort, recurring use does not materialize, or no buyer pays. If research needs full GPU/private-platform management while product teams need a different service, do not claim a unified product has been validated. Publish negative findings and recommend an existing alternative where appropriate.

## 14. Explicit assumptions and limitations

- **PA-001:** Teams with recent expensive reconstruction episodes are a promising initial segment. Optimal team size and recurrence are unknown.
- **PA-002:** A composable integration can save effort beyond the existing stack without forcing framework migration. This is the central unproven product assumption.
- **PA-003:** Enough useful state can be captured or referenced safely at supported boundaries. Opaque providers, remote systems and large datasets may invalidate it.
- **PA-004:** A free local core plus paid collaboration can support a business; USD200–500/team/month is an experimental range, not evidence.
- **PA-005:** Interview and pilot gates are management choices intended to prevent premature expansion, not statistically calibrated market thresholds.

No universal unmet market, representative failure rate, audited ROI, customer budget or mass-adoption forecast has been established. First-party success stories are selected; sources from one ecosystem are correlated; vendor critiques of competitors are not neutral comparative tests. Living documentation can change, and publication date is `not-stated` where unavailable. Price observations are nominal USD offers as reviewed, without tax/currency normalization or workload matching. The original source/claim files remain unchanged; the expanded files preserve their IDs and qualifications.

## 15. Evidence files and methodological credit

- [Production source ledger](/Users/nikchin/Desktop/invariant/docs/market-research/production-sources.csv): publication/retrieval dates, methods, samples, limitations, pricing units and API provenance; includes retained sources from the initial study.
- [Production claims ledger](/Users/nikchin/Desktop/invariant/docs/market-research/production-claims.csv): factual statements, synthesis, assumptions and recommendations linked to source IDs.
- [Production case ledger](/Users/nikchin/Desktop/invariant/docs/market-research/production-cases.csv): workflow evidence and issue cases with evidence class, consequences, resolution qualifiers and purchasing limits.
- [Earlier research](/Users/nikchin/Desktop/invariant/docs/MARKET_RESEARCH.md): original survey details and CLI issue appendix, retained as a dated record rather than the current recommendation.

The market-research-reports skill informed evidence structure and validation. Methodological attribution only: Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*. Current arXiv record checked, revision September 2, 2026. [DOI/current record](https://doi.org/10.48550/arXiv.2609.00065). This paper is not evidence of customer demand. (P-C044.)
