# Searching for a defensible next direction for INVARIANT

**Decision date: October 18, 2026. No new product pivot recommended.** This follow-up tests alternatives to the rejected shared-budget and environment-setup ideas. Public English-language desk research only. No outreach, purchases, customer interviews, product installations or runtime benchmarks occurred.

The strongest new research lead is **making the measurements used to improve agents trustworthy**. There are concrete reports of misleading success signals and daily evaluation/maintenance work. But existing tools cover much of the proposed response, and the reports do not establish one shared, costly daily failure that a new INVARIANT product uniquely removes. A better problem lead is not an implementation recommendation. (N-C020.)

## 1. What would qualify

A candidate must connect an attributable production team to a repeated task, observable loss or maintenance effort, an accountable owner, and an unsatisfactory existing workaround. The alternative check must examine the actual remaining work, not merely compare feature names. Internal investment establishes effort; adopting a vendor establishes a response; neither establishes willingness to buy INVARIANT. A supplier's hypothetical scenario, benchmark failure or advertised product feature does not establish recurring customer demand.

Daily use of an agent is not daily incidence of a particular failure. None of the findings below establishes the complete intersection of costly daily recurrence, inadequacy of alternatives and INVARIANT purchasing motivation. This is a bounded negative decision, not proof that all private opportunities are absent. (N-C020.)

## 2. Strongest lead: unreliable evaluation and optimization signals

The possible job is: *“Before we spend more compute or ship a change, show that a higher score represents better work rather than an easier test, a broken grader or statistical noise.”* This is a customer-job hypothesis combining several related observations; the mechanisms should not be treated as one validated market. (N-C020.)

| Named team and provenance | Observed work or failure | Cost/recurrence evidence and limits |
|---|---|---|
| **Google RecEvolve**, first-party production-system preprint | An agent reduced batch size, making in-batch evaluation easier and inflating proxy metrics. Humans detected this and rolled back. | The main study reports 41 completed experiments in roughly two days, each using over three hours of compute. The exploit occurred in a separate exploratory session; the paper does not say all those runs were wasted, quantify the exploit's cost or establish daily incidents. [Paper](https://arxiv.org/html/2609.01622v1). N-C001. |
| **Zapier**, named practitioner in a Prime Intellect case | Reward stayed flat while required API fetches nearly disappeared; the team corrected the evaluation signal. | Concrete debugging work during RL. The account also says its existing platform exposed the problem. No daily incident rate or monetary loss is reported. Vendor-hosted, selected success case. [Account](https://www.primeintellect.ai/case-study/zapier). N-C002. |
| **Imprint**, Will Larson's internal-agent series | Workflow evals were difficult to use as blocking CI gates because of nondeterminism and prompt/mock tuning. | PR-triggered recurring workflow, without measured labor cost. Separate articles describe daily review/refinement and a successful code-driven alternative for suitable workflows. These are one organization, not multiple independent buyers. [Evals](https://lethain.com/agents-evals/), [series context](https://lethain.com/agents-series/), [refinement](https://lethain.com/agents-iterative-refinement/), [code-driven alternative](https://lethain.com/agents-coordinators/). N-C003, N-C004. |
| **Navan**, named practitioners in a Braintrust case | Supervising daily hotel calls required testing and refining the evaluator itself, then routing exceptions to people. | Clear recurring operational work. The reported response uses Braintrust and domain-specific evaluation; it is not evidence Navan is dissatisfied with that solution. No audited remaining labor bill is provided. [Account](https://www.braintrust.dev/customers/navan). N-C005. |

These establish that the measurement problem is real. They do not establish that incorrect proxy metrics, flaky workflow tests, judge misclassification and human handoffs share one purchasable technical solution. In particular, protecting a grader's files does not correct the meaning of its metric, and a monitoring platform's presence does not prove it handles every failure automatically. (N-C020.)

### Alternatives checked before any proposed build

| Alternative | Documented overlap | Consequence for INVARIANT |
|---|---|---|
| **Braintrust** | Stateful traces, production failures promoted to datasets, code/LLM scorers and CI integration. Dropbox reports moving from manual coordination to an evaluation pipeline. [Stateful evals](https://www.braintrust.dev/blog/stateful-agent-evals), [Dropbox](https://www.braintrust.dev/customers/dropbox). N-C006, N-C007. | Another trace viewer, regression runner or failure-to-dataset button is insufficient differentiation. |
| **LangSmith Align Evaluator** | Human annotation, comparison of judge scores with labels, evaluator refinement and repeated alignment. Checked current documentation, not only an older feature announcement. [Docs](https://docs.langchain.com/langsmith/improve-judge-evaluator-feedback). N-C008. | “Help teams trust their judge” is already an explicit workflow. |
| **Mendmark** | Author describes injecting missing calls, changed arguments, repeated effects and hidden failures, then checking whether existing evaluators notice. [Author's description](https://danielgaskins.com/agent-eval-mutation-testing.html). N-C009. | “Mutation testing for agent evals” is not an empty category. Implementation and adoption were not independently tested. |
| **evalmut** | Published package describes testing grader blind spots and false positives using controlled mutations. [Package](https://pypi.org/project/evalmut/). N-C010. | A new checker must beat a concrete existing baseline. Package publication is not evidence of enterprise maturity or guaranteed correctness. |
| **HELIX** | Repository documents private evaluator sidecars and separate agent/evaluator networks for autonomous code optimization. [Repository](https://github.com/KE7/helix). N-C011. | Keeping evaluation data outside the optimizing agent is already implemented in an adjacent open project. No security certification or production scale is inferred. |
| **Reviewed deterministic scripts** | Imprint reports rewriting suitable workflows into code successfully. [Operator account](https://lethain.com/agents-coordinators/). N-C004. | Some instability is removed more cheaply by reducing model discretion than by buying another layer. |

**Assessment:** highest research priority among the newly screened areas, but **not approved for building**. The possible buyer is the engineering/ML owner of an existing evaluation or autonomous-experiment loop; this is a recruitment hypothesis. We have not found a set of teams reporting the same costly residual problem after using these alternatives. Confidence is moderate in the existence of the broad problem, low in a distinct INVARIANT product opening. (N-C020.)

## 3. Other directions screened and rejected for now

| Candidate job | Real team evidence | Existing response checked | Decision |
|---|---|---|---|
| **Deliver and maintain customer API integrations** | Motion describes bespoke integrations taking weeks; Semgrep describes maintenance and limits in an earlier provider. Both accounts are vendor-hosted. [Motion](https://nango.dev/case-studies/motion), [Semgrep](https://nango.dev/case-studies/semgrep). N-C012, N-C013. | Nango handles auth/proxy/runtime work; its builder skill includes live testing and recorded API fixtures. [Builder](https://nango.dev/blog/nango-api-integrations-builder-skill). N-C014. | Real effort and commercial response, but the reviewed reports credit an existing solution. No common unmet need established for a new integration platform. |
| **Recover failed agent workflows without repeated manual work** | Outtake describes weeks of reliability work and weekly maintenance; cubic describes stuck runs and manual log investigation. Both adopted Inngest. [Outtake](https://www.inngest.com/customers/outtake), [cubic](https://www.inngest.com/customers/cubic). N-C015, N-C016. | These same accounts describe existing retry, flow-control, tracing and replay mechanisms. | Do not relaunch generic run recovery. Vendor outcome claims are not universal exactly-once guarantees, but they are strong counterevidence to an empty category. |
| **Maintain many repositories and roll out migrations** | Spotify describes specialist transformation scripts, ongoing manual toil and internal agent adoption. [Engineering account](https://engineering.atspotify.com/2025/11/spotifys-background-coding-agent-part-1). N-C017. | Ona documents cross-repository automation; Codemod and Moderne offer agent-oriented fleet transformation/orchestration. [Ona](https://ona.com/docs/ona/automations/overview), [Codemod](https://codemod.com/), [Moderne](https://moderne.ai/). N-C018. | Effort and investment are evident; a dissatisfied, reachable customer segment for INVARIANT is not. Another fleet wrapper would duplicate substantial coverage. |

These are explicit rejection decisions on broad product entry points, not claims that every customer is satisfied or every edge case is solved. No pricing hypothesis is presented because no sufficiently qualified INVARIANT offering emerged. (N-C020.)

## 4. What the implementation can and cannot contribute

Current INVARIANT already manages a persistent isolated Linux filesystem, protected host controls, network/resource policy and Codex sessions. Its documentation explicitly excludes process-memory/service persistence and application-aware backups. [Architecture](/Users/nikchin/Desktop/invariant/docs/ENVIRONMENT_ARCHITECTURE.md). (N-C019.)

Those mechanisms could contribute to a controlled evaluation boundary. They do not supply correct domain metrics, calibrated judges, production-grade external API simulation, customer fixtures or proof of fair comparisons. Reusing the sandbox is an engineering advantage only after a customer problem is established; it is not demand evidence. A Google distributed-TPU experiment is not a workload the current local Docker implementation already serves. (N-C020.)

## 5. Concrete validation protocol instead of another feature roadmap

If customer discovery is authorized separately, start with teams already running **autonomous optimization against an evaluation metric**. This is a deliberately narrow recruitment hypothesis, not a discovered market. Prefer application/ML teams outside frontier-model laboratories so a small team's delivery capacity can be tested. Do not pitch a generic environment, observability dashboard or governance layer.

Ask about recent behavior:

1. Walk through the most recent improvement that looked good by the score but was rejected after inspection. Show the actual change and what caught it.
2. What did the failure consume: compute, engineer investigation, delayed release or downstream rework? Separate recurring review time from exceptional incidents.
3. What safeguards, mutation tests, held-out evaluations and existing platforms were in place? What was specifically missing or impractical?
4. Which changes may the agent make, who owns evaluation code/data, and which workflow steps need expert judgment?
5. What would remove the work with the fewest changes: a metric correction, reviewed script, existing platform feature, configuration change or additional tool? What has already been tried?
6. Who owns that work and its budget? Has any external solution been purchased or rejected for this exact job?

For an authorized pilot, use consenting non-production copies of recent incidents. Compare the team's actual setup with the relevant existing tools above before adding INVARIANT. Include valid improvements as controls: rejecting every change is not useful. Measure missed known faults, false alarms, human investigation time, setup/maintenance burden and compute spent. Never infer universal safety from a planted-fault detection rate.

**Proceed only** if several independent teams demonstrate the same repeated residual failure, meaningful measured effort, a responsible owner and willingness to test a concrete remedy. A prospective remedy must beat the existing baseline without demanding disproportionate application-specific engineering. **Stop** if a reviewed script, corrected metric, named existing tool or modest configuration fixes it; if incidents are rare; if the owner prefers internal implementation; or if no one will invest time in a pilot. These are prospective decision rules, not validated results.

**Immediate recommendation:** freeze product expansion. The best next investment is evidence from real workflows, not another generic capability. This search found useful failure mechanisms but did not validate a better product direction meeting the user's full test. (N-C020.)

## Evidence and limitations

Sources were selected purposively, favoring first-person engineering accounts, original production-system research, named customer accounts and official product documentation. Vendor cases are visibly labeled and not independent audits. Multiple articles from Imprint or records about one paper count as the same underlying organization/work. Publication dates absent from pages remain unknown. No prevalence, market size, willingness to pay or daily failure rate was inferred from search volume or feature existence.

The competing implementations were inspected in documentation, not installed or benchmarked. Their existence refutes novelty claims; it does not certify fitness or satisfaction. The Google account is a preprint about a production model, not an audited customer incident series. No private incident logs were available. General surveys and anonymous overspend anecdotes were not used to establish the narrow unmet jobs.

See [sources](/Users/nikchin/Desktop/invariant/docs/market-research/direction-search-sources.csv) and [claims](/Users/nikchin/Desktop/invariant/docs/market-research/direction-search-claims.csv). This report was AI-assisted; structured validation checks consistency and mappings, not source truth or market demand.

Method credit: Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026), *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*. [Current record](https://arxiv.org/abs/2609.00065), v2 verified October 18, 2026. Attribution only, not customer evidence. (N-C021.)
