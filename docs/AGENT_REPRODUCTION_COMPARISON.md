# Agent experiment reproduction: competitive comparison

> **Subsequent demand validation, October 18, 2026:** [first-hand team evidence](/Users/nikchin/Desktop/invariant/docs/EXPERIMENT_BUNDLE_VALIDATION.md) did not substantiate portable experiment bundles as a recurring costly commercial gap. The possible workflow advantage below remains a hypothesis; no pilot or expansion is selected.

Public documentation checked October 18, 2026. Technical capability comparison, not a customer-demand study or hands-on benchmark. No subscriptions, trials or outreach. Source dates, evidence types and limitations are in `market-research/agent-reproduction-sources.csv`; claim mappings are in `market-research/agent-reproduction-claims.csv`.

## Decision

**The broad idea is already substantially served.** Saving configurations, traces, artifacts and scores, running repeated evaluations and comparing versions are established capabilities. Environment restoration and recorded-response replay also have existing implementations. Do not position INVARIANT as the first reproducible agent experiment system or assume that competitors only provide dashboards. (RPR-C001.)

The most plausible improvement is a **simpler local workflow for turning an existing, interactive agent workspace into a portable, executable experiment**. That is a proposed advantage in packaging, capture completeness and effort, not an established missing capability. Reprova advertises an especially close concept, so even the full experiment bundle is not an uncontested idea. (RPR-C016.)

## What existing tools cover

Capabilities below mean documented or advertised scope, not independently tested effectiveness. An unverified capability is unknown, not absent. No prices or total-cost comparisons were verified in this screen. (RPR-C019.)

| Alternative | Relevant existing capabilities | Implication for INVARIANT |
| --- | --- | --- |
| **MLflow** | [Agent evaluation](https://mlflow.org/docs/latest/genai/eval-monitor/), [Docker-backed Projects](https://mlflow.org/docs/latest/ml/projects/) and [prompt versions with model configuration](https://mlflow.org/docs/latest/genai/prompt-registry/). | “Compare experiments plus a container” already overlaps. Automatic capture of arbitrary mid-session changes and reconstruction of external services was not established from these pages. (RPR-C002.) |
| **LangSmith** | [Version benchmarking and regression evaluation](https://docs.langchain.com/langsmith/evaluation-types); [sandbox filesystem snapshots](https://docs.langchain.com/langsmith/sandbox-snapshots), with optional memory capture through REST subject to runtime limitations. | It already extends beyond traces. Default filesystem capture does not preserve processes; memory snapshots have additional requirements. Neither claim entails restoring external systems. (RPR-C003.) |
| **Harbor + LangSmith** | [Repeated trials in fresh environments, verifiers, traces and experiment results](https://www.langchain.com/blog/unified-stack-for-evaluating-agents). Harbor has [Codex, Claude Code, LangGraph and other integrations](https://docs.harborframework.com/agents/pre-integrated-agents), native trajectory loading for Codex/Claude, and local completed-session handoff for Claude Code. | The closest documented stack for the user's “N agent configurations, compare outcomes” workflow. Framework choice, CLI agents and clean environments are not unique advantages. June 30, 2026 integration announcement; current documentation also checked. (RPR-C004.) |
| **Inspect AI** | [Checkpointing](https://inspect.aisi.org.uk/checkpointing.html) saves supported agent context, configured sandbox filesystem paths, sample store and events, restoring these on retry. Custom agents need explicit support; arbitrary process RAM and external side effects are excluded. | Joint restoration of agent context and environment files already exists. Full arbitrary-process capture would be substantially harder than current INVARIANT persistence. (RPR-C005.) |
| **LangGraph** | [Replay and fork from graph checkpoints](https://docs.langchain.com/oss/python/langgraph/use-time-travel). Nodes after the checkpoint execute again, including model and API calls. | “Branch an agent run” already exists. Graph-state restoration should not be confused with deterministic execution or restoration of a filesystem/database. (RPR-C006.) |
| **Braintrust** | [Immutable evaluation records](https://www.braintrust.dev/docs/evaluate/run-evaluations), comparison and sharing; evaluations in code, UI and CI. | Saving and comparing research outcomes is already covered. Whole arbitrary runtime capture was not established here. (RPR-C007.) |
| **W&B Weave** | Its [official repository](https://github.com/wandb/weave) documents agent conversations, turns, model/tool-call tracing and evaluation. | Another existing trace/evaluation layer. The reviewed repository does not establish complete operating-system restoration; that remains unknown rather than a negative feature claim. (RPR-C008.) |
| **Langfuse** | [Dataset item versioning and experiments against specific versions](https://langfuse.com/docs/evaluation/experiments/datasets). Schema changes are explicitly outside that versioning scope. | A stable evaluation dataset is not unique. Dataset versioning is distinct from capturing the runtime. (RPR-C009.) |
| **E2B / Sprites** | [E2B persistence](https://docs.e2b.dev/sandbox/persistence) documents filesystem and memory preservation; [Sprites](https://fly.io/sprites/) documents filesystem checkpoints and restoration. | Persistent or restorable agent computers are available components. These pages alone do not establish an experiment registry and fair comparison workflow. Do not equate the two providers' checkpoint semantics. (RPR-C010.) |
| **Runback** | [Recorded-context inspection, single-call model/prompt replay and whole-agent replay](https://runback.dev/docs). Documentation distinguishes recorded hits from new live calls and supports blocking misses. | Frozen tool results, divergence tracking and explicit replay boundaries are not new. Whole-agent replay executes caller-supplied agent code and wrappers; this is not proof of restoring an arbitrary original OS environment. (RPR-C011.) |
| **Reprova** | [Advertises process wrapping, environment/code/model/input/output capture, reconstruction and differences](https://www.reprovalabs.com/). Public page directs visitors to a waitlist. | Direct conceptual competitor. Public claims establish overlap, not shipping status, capture completeness, usable SDK availability or customer satisfaction. (RPR-C012.) |

## What “reproduce” should mean

For a proposed INVARIANT product, define separate operations rather than one deterministic-reproduction promise. These are product requirements inferred from the documented differences above, not features implemented in INVARIANT. (RPR-C013.)

1. **Inspect the record:** view captured messages, calls, outputs and artifacts. This explains what was recorded; it does not execute the system.
2. **Replay recorded responses:** execute supported code while serving recorded model/external responses for matching calls. A pass establishes behavior under those recordings, not model quality on fresh generations.
3. **Run a controlled variation:** restore the same starting files/environment and evaluation cases, change a named agent parameter, and generate fresh model responses. Record which external interactions are replayed, simulated or live. If a changed query has no recording, stop or explicitly mark the departure from controlled conditions.
4. **Run against today's world:** reuse the saved configuration but access current models/services. Useful for detecting drift, with a weaker reproducibility claim.

Changed prompts can lead to different tool arguments and different data requests. A cache of old responses is not a simulation of all possible future interactions. Filesystem snapshots also do not undo remote database writes or preserve unavailable hosted-model versions. Treat these as limits of the proposed claim, not something Docker can automatically solve. (RPR-C013.)

## Where we could offer something better

**Candidate user:** a small team experimenting with file- and shell-using agents in an existing local project, currently assembling Docker images, configuration files, transcripts and evaluation scripts themselves. This target and its purchasing motivation are hypotheses; this screen did not verify its prevalence or willingness to pay. (RPR-C016.)

**Proposed job:** “Send a colleague experiment 42; they open its actual starting workspace, rerun its checks, and create experiment 43 changing only the planner prompt.” The agent may have installed command-line tools, produced files and changed configuration during preparation. The bundle must contain the actual captured files and environment contents, not just names, hashes or a Dockerfile that fetches today's packages. Record the starting snapshot separately from the final state. (RPR-C014.)

The useful output would be one versioned bundle linking:

- Original code and uncommitted files; selected input bytes and their identifiers.
- Pinned executable environment plus a manifest of platform constraints.
- Agent configuration, visible message/tool records and supported harness state.
- Versioned evaluation code/data and resulting artifacts.
- A capture-coverage report: what was recorded, omitted, mutable, unavailable or substituted during rerun.

These are proposed requirements, not a claim competitors cannot assemble the same bundle. Capture cannot promise hidden model reasoning, inaccessible provider state, arbitrary native-process memory or automatic capture of every possible tool. Initially support explicit adapters and start-of-run restoration. (RPR-C014.)

**Potential advantages to test, in order:** less manual setup for an existing project; faster handoff to another machine; clearer diagnosis of whether a difference came from agent configuration, environment or external data. Local execution, framework neutrality, checkpoints and recording by themselves are already represented in alternatives. Runback already documents replay provenance, and Reprova claims broad capture. Superiority must be demonstrated on the entire workflow. (RPR-C016.)

Keep MLflow or LangSmith as an optional destination for scores and traces. Rebuilding their experiment dashboard is not the strongest use of a small team's effort. INVARIANT's proposed role would be the executable artifact underneath those results. This is an integration strategy, not evidence of defensibility. (RPR-C017.)

## Fit with the current project

The [current architecture](/Users/nikchin/Desktop/invariant/docs/ENVIRONMENT_ARCHITECTURE.md) documents project-specific Linux filesystem persistence and a fresh executor on resume. The project directory persists independently, while host Codex history remains outside the environment. Process RAM and services are not restored. (RPR-C015.)

That provides a starting component, not experiment reproduction. We would still need immutable starting snapshots, versioned workspace copies, run manifests, artifact export/import, harness/model/tool capture adapters, controlled response playback and evaluator integration. Do not reuse a mutable project directory across supposedly independent trials or describe ordinary chat resume as reproducing an experiment. These are proposed implementation requirements. (RPR-C014, RPR-C015.)

## Test the advantage before expanding

Use a single representative workflow: a local agent installs a tool, processes fixed files, creates an output and is evaluated by a saved script. Compare the same job using MLflow Projects, Harbor/Inspect, and a proposed INVARIANT bundle. Use supported-agent adapters in each baseline rather than deliberately handicapping alternatives. (RPR-C018.)

Measure operator setup time, manual steps, handoff time on another compatible machine, omitted dependencies, restored input fidelity and ability to identify intentional drift. Use a clean machine that has the documented runtime prerequisites; the bundle is not a promise to run across arbitrary architectures. Repeat trials for model/effort comparisons, hold the evaluator and cases fixed, and assess on separate held-out tasks before selecting the winner. These are proposed methodological safeguards, not measured outcomes. (RPR-C018.)

Suggested proceed condition: independently observed users complete the capture–share–rerun–vary workflow with materially less effort than their best existing setup, without losing required fidelity, and prefer retaining INVARIANT. Ask for recent failed handoffs and existing manual steps before proposing features. Stop or use the existing stack if the difference is a thin command alias, if the work is already straightforward with Harbor/Inspect, or if nobody needs executable handoff beyond their existing dashboard. No market-size, demand or pricing conclusion is supported by this documentation review. (RPR-C018, RPR-C019.)

## Method credit

Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). [Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents](https://arxiv.org/abs/2609.00065). Current record retrieved October 18, 2026. Research-method attribution only. (RPR-C020.)
