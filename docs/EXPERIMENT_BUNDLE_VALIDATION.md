# Do teams need portable agent experiment bundles?

Research cutoff and retrieval date: October 18, 2026. Public English-language desk research. No outreach, paid research, trial registrations, customer-data uploads or product implementation.

## Decision

**The search did not establish a recurring commercial problem that INVARIANT's proposed bundles would solve better than existing tooling.** Real work exists around agent replay and evaluation, but the strongest findings concern application-specific state reconstruction, infrastructure behavior and trace analysis. Missing portable environment archives were not established as the costly remaining cause. Do not expand the product around bundles on this evidence. This is a bounded negative finding, not proof that private demand does not exist. (BV-C001.)

The prior [competitive comparison](/Users/nikchin/Desktop/invariant/docs/AGENT_REPRODUCTION_COMPARISON.md) proposed easier executable handoff as a hypothesis. This validation does not substantiate it as a commercial opportunity.

## Qualification rule

A convincing case needs an identifiable commercial team, demonstrated adoption of MLflow/LangSmith/Harbor, recent repeated manual reproduction work, observable cost or staff time, and a causal explanation showing that captured workspace/environment/input state removes that work. A feature request, custom harness or generic reproducibility warning alone is insufficient. No reviewed case satisfied the full chain. (BV-C001, BV-C018.)

## What the strongest accounts show

| Account | First-hand evidence and current status | Would a portable bundle remove the work? |
| --- | --- | --- |
| **n8n using LangSmith** | The company's [evaluation README](https://github.com/n8n-io/n8n/blob/master/packages/@n8n/instance-ai/evaluations/README.md) documents a custom harness that reconstructs a real conversation from LangSmith, restores preceding messages/tool calls and workflow state, and sends the last user turn live. It also mocks external tools/services. Exact publication date is not stated. (BV-C002.) | **Partial supporting role.** Files and dependencies could travel in a bundle, but workflow compilation, conversation reconstruction and mocks require n8n-specific semantics. The harness already automates them. No reported remaining hours, invoice or dissatisfaction with that solution was found in this source. Engineering investment is observable; recurring costly manual handoff is not. (BV-C003, BV-C014.) |
| **Anthropic's coding-evaluation teams** | [February 5, 2026 engineering report](https://www.anthropic.com/engineering/infrastructure-noise): Terminal-Bench results on its GKE cluster differed from the leaderboard; the team investigated resource-enforcement differences and calibrated configurations. The report distinguishes guaranteed allocation from kill limits and discusses other runtime confounders. It establishes Terminal-Bench/GKE use, **not adoption of Harbor, MLflow or LangSmith**. (BV-C004.) | **Partial.** Preserve resource policy and environment identity, but archived files cannot equalize CPU contention, provider latency or scheduler enforcement across machines. This is adjacent evidence of real evaluation work, not a qualifying post-adoption bundle customer. No reproduction labor bill or recurring manual handoff cost is disclosed. (BV-C014.) |
| **Harbor/Daytona operator, employer unverified** | In [issue #2418](https://github.com/harbor-framework/harbor/issues/2418), opened July 21, 2026, the author reports roughly two days of lost batch throughput and extensive diagnosis. The follow-up retracts the upload-layer explanation: leaked running sandboxes exhausted a quota, with errors retried until timeout. [PR #2590](https://github.com/harbor-framework/harbor/pull/2590) proposes fail-fast handling and is open in the reviewed page; shipped resolution not established. (BV-C005, BV-C006.) | **No direct causal fit.** The failure is quota exhaustion, cleanup and misleading retry behavior. Restoring identical files does not free cloud quota or fix error classification. Local execution could avoid that provider but is a different workaround, already available outside INVARIANT. Two days of throughput is not two engineer-days, a dollar bill or proof of a recurring business-wide problem. (BV-C014, BV-C018.) |
| **LangChain's own team using LangSmith** | Its [May 19, 2026 Engine account](https://www.langchain.com/blog/how-we-built-langsmith-engine-our-agent-for-improving-agents) says granular trace inspection became laborious internally and describes a product built to group failures and propose evaluators/regression examples. (BV-C008.) | **Poor fit.** The residual task is identifying meaningful failure patterns and defining checks. A bundled filesystem does not perform that analysis. The supplier already describes a response; its effectiveness is not independently measured here. (BV-C014.) |

## Leads that do not establish unmet bundle demand

- **Mutable benchmark images:** [Terminal-Bench 2 RFC #66](https://github.com/harbor-framework/terminal-bench-2/issues/66), May 13, 2026, proposes digest verification and remains open in the reviewed page. It describes drift channels, not a named company's quantified recurring loss. Separately, an image-pinning change in the other Terminal-Bench repository [merged October 7](https://github.com/harbor-framework/terminal-bench/pull/1824). These are different corpora; that merge does not prove the TB2 RFC is resolved. Standard digest pinning and retained images directly address the technical issue without requiring a new experiment platform. (BV-C007.)
- **Harbor snapshot visibility:** the initial mechanism in [#2417](https://github.com/harbor-framework/harbor/issues/2417) was explicitly retracted after the author's diagnostic actions contaminated the test. It and #2418 are the same operator/incident, not independent customer evidence. (BV-C006.)
- **Eval portability request:** [LangSmith #3428](https://github.com/langchain-ai/langsmith-sdk/issues/3428) was submitted by the maintainer of the proposed interchange format, who says an external adapter already works. It shows interest in first-party integration, not verified commercial labor cost or a need to archive executable environments. The old link can redirect to the new Python SDK repository; do not treat archive-era issue status as proof of product abandonment. (BV-C011.)
- **Orange Business:** a [recent attributed customer account](https://www.langchain.com/blog/how-orange-business-built-live-intelligence-on-langchain-and-langsmith), October 13, 2026, describes LangSmith-based regression evaluation and historical trace replay. It reports benefits, not a residual manual reproduction problem. Vendor-hosted selection does not prove universal satisfaction. (BV-C009.)
- **Factory:** the [original customer story](https://www.langchain.com/blog/customers-factory) is June 19, 2024 and describes improvement after LangSmith adoption. Recent reposts cannot turn that into recent unresolved post-adoption pain. (BV-C010.)
- **MLflow:** an [agent-packaging issue found during the search](https://github.com/mlflow/mlflow/issues/13963) opened December 3, 2024 and is closed. It is not evidence of a current recurring experiment-reproduction burden. No qualifying recent MLflow team account emerged from this bounded search. (BV-C017.)

## Would INVARIANT actually solve the observed causes?

The following is a causal assessment, **not a customer benchmark**. No complainant's private experiment or complete failure environment was available for hands-on reproduction. (BV-C014.)

| Cause | Bundle contribution | Work it does not remove |
| --- | --- | --- |
| Original files, installed dependencies or image content lost | Potentially strong if actual bytes were captured beforehand and retained | Unavailable historical state cannot be recovered after the fact; platform compatibility still matters |
| Application state reconstructed from a trace | Can carry a tested reconstruction adapter and its fixtures | Someone still defines database/workflow/message semantics; n8n already has this logic |
| External service or live data changed | Can carry captured responses for matching calls | New calls need a fixture, simulator or live response; old recordings do not cover arbitrary new agent behavior |
| Resource enforcement or cloud quota differs | Can record intended configuration and observed failures | Cannot guarantee another scheduler's behavior, reclaim provider quota or normalize external latency |
| Trace analysis and evaluation design | Can preserve evidence and checks | Does not decide what an error means or supply the correct evaluation criteria |

These boundaries follow from the cases and the current architecture; the first row is the hypothetical strongest fit, **not a qualifying costed case found here**. (BV-C014.)

Current INVARIANT documents filesystem persistence, a separately persistent project directory and host-side Codex history. It does not restore process memory or running services. Portable export/import, immutable starting workspace versions and replay adapters remain proposed work. Resuming a chat is not proof of reproducing a study. (BV-C013.)

## Existing responses checked

[MLflow Projects](https://mlflow.org/docs/latest/ml/projects/) supports Docker environments; [LangSmith snapshots](https://docs.langchain.com/langsmith/sandbox-snapshots) preserve runtime files and optionally memory under documented conditions; [Inspect checkpointing](https://inspect.aisi.org.uk/checkpointing.html) restores supported agent context and configured sandbox paths. [LangSmith Preview Builds](https://www.langchain.com/blog/langsmith-preview-builds-test-agent-changes-before-production), August 20, 2026, gives reviewers a shared running deployment, an alternative to rebuilding a colleague's local setup. [ThalesGroup's AGILAB](https://github.com/ThalesGroup/agilab) advertises executable portable workflows and optional MLflow handoff. These establish overlap and available approaches, not that all workflows are solved or that AGILAB demonstrates quantified residual demand. (BV-C012.)

## Evidence that would change the decision

Require a recent real incident from a verified team already using one of the named tools: the original run, exact missing state, attempted native workaround, and recorded engineering time. Compare the team's best current process with a bundle on a second compatible machine. Measure total capture, review, handoff and rerun effort, including maintaining adapters and fixtures. Distinguish restored input fidelity from fresh-model outcome equality. (BV-C015.)

Proceed only if preserved executable state removes the dominant manual step and the team prefers paying for this workflow after trying credible existing approaches. Stop if the work is primarily writing application mocks, interpreting failures, fixing quota handling, improving evaluation validity or using a native feature already available. No pilot or product expansion is selected, and no outreach has occurred. (BV-C001, BV-C015, BV-C018.)

## Method and credit

The search began with first-person reports in issue trackers, team engineering accounts and public evaluation code. Current replies and remediation were checked before classifying a report as unresolved. Corporate tool adoption, custom engineering, feature interest and actual recurring costs were kept separate. This purposive English-language sample cannot estimate prevalence or prove absence of private demand. (BV-C018.)

Research-method credit: Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). [Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents](https://arxiv.org/abs/2609.00065). Current record retrieved October 18, 2026; attribution only. (BV-C016.)
