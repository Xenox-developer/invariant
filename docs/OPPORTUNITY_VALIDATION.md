# Demand check: reject shared budgets as the leading direction

**Decision date: October 18, 2026.** The question is whether production teams repeatedly report the proposed shared task-budget problem, and whether a stronger opportunity can be grounded in observed work. Public desk research only; no outreach, purchases or paid experiments.

> **Follow-up decision:** the environment-setup direction discussed below was also rejected as INVARIANT's next broad expansion. Real residual reports were found, but no convincing common unmet need across Signadot and Ona was established. See [environment gap validation](/Users/nikchin/Desktop/invariant/docs/ENVIRONMENT_GAP_VALIDATION.md). The observations below remain research history.

## 1. Decision

**Reject conserved cross-tool task budgets as INVARIANT's current product priority.** There is some operator evidence for hierarchical agent budgets, so “nobody has this problem” would be false. But the evidence found does not establish repeated unmet demand for the narrower proposed product: one conserved task allowance across heterogeneous tools, independent sessions, delegation and restarts. Existing implementations also weaken a new standalone product case. (V-C001–V-C006, V-C016.)

The better-supported opportunity area is **removing contention and preparation work when each parallel agent task needs a usable application environment**. The clearest observed problem is that developers/agents wait for a shared test environment or overwrite each other's changes. A Linux container with installed tools does not by itself provide the application's databases, queues, service routing, test identities, seed data and browser-visible application. (V-C007–V-C010, V-C017.)

This is a stronger **customer problem**, not yet a distinct INVARIANT market opening. Signadot and Ona are important existing alternatives. Do not announce another generic environment platform on the basis of this memo. (V-C011–V-C013, V-C017.)

## 2. What the budget search actually found

The search covered explicit shared/multi-agent budget reports, production overspend postmortems, framework issues, first-party operator accounts and a published incident catalog. Candidate sources were checked for first-hand provenance, named operators, exact problem fit, recurrence and existing workarounds. Repeated comments by one organization were counted once. A technical feature request, missing feature or research prototype was not automatically counted as a production customer.

| Evidence | What is supported | Why it does not validate the proposed priority |
|---|---|---|
| **KinthAI**, comments in OpenClaw #42475, April 28, 2026. | A named operator reports running agents with hierarchical quotas and reserving allowance when delegating. This is direct operator evidence, self-reported. | The several similar comments are one organization. It describes controls already built and emphasizes model spending; it does not establish demand for a new cross-tool task-lifetime product. [Thread](https://github.com/openclaw/openclaw/issues/42475). V-C001. |
| **Original OpenClaw request**, March 10, 2026. | Requests gateway-enforced per-agent daily/monthly spending limits. | It is not the full shared-task problem. The thread also identifies external budget-proxy workarounds. No buyer budget, incident frequency or independent organizational deployment was established. Same thread, V-C002. |
| **Signets procurement postmortem**, January 11, 2026. | Supplier describes repeated purchases passing per-transaction limits. | Customer is anonymized, supporting records unavailable and the mechanism differs from cross-agent allocation. Useful anecdote, insufficient independent recurrence evidence. [Account](https://www.signets.ai/blog/ai-agent-overspend-postmortem). V-C003. |
| **Token Budgets preprint**, June 2026. | Reports an incident catalog and a budget-integrity mitigation. | The linked artifact repository returned 404 during this review, preventing a row-by-row audit of the alleged delegation incidents. The paper also says correctly implemented runtime alternatives can achieve the cap outcome. Do not turn the headline incident total into independently verified customers. [Paper](https://arxiv.org/abs/2606.04056), [full text](https://arxiv.org/html/2606.04056v1). V-C004. |
| **LiteLLM and Portkey** | Current documentation describes shared/model spending controls, including pre-request reservations in LiteLLM. | Ordinary agent spending limits already have substitutes. Feature boundaries around certain routes do not prove customers need another product. [LiteLLM](https://docs.litellm.ai/docs/proxy/users), [Portkey](https://portkey.ai/blog/conductor-x-portkey-is-now-live/). V-C005. |
| **WE•DO operating account**, September 2026. | Describes its agent fleet's operating economics, emphasizing build and maintenance work over model usage. | One supplier/operator estimate, not representative. Nevertheless, it is disconfirming evidence against assuming token spend is the dominant operational pain. [Account](https://wedoworldwide.com/blog/ai-agent-roi-costs-savings). V-C006. |

The OpenClaw page was read directly. A separate unauthenticated REST metadata request returned 403; no successful fresh API status verification is claimed. The preprint artifact's 404 was observed through both the web reader and an independent public HTTP check. Neither failure proves that the underlying research or operator statements are false; it limits what could be verified here.

**Result:** keep budget conservation as a possible implementation property if a real customer needs it. Remove it from the leading product shortlist. This rejects a prioritization claim, not the existence of overspending or the engineering importance of atomic accounting. (V-C016.)

## 3. A stronger recurring problem: contested application environments

### Miro: named users, explicit contention and adoption

Miro practitioners describe contested integration environments and changes overwritten mid-test. The vendor-hosted account reports adopting Signadot, internal CLI/agent integration and daily use. This connects repeated friction to an actual response. [Case study](https://www.signadot.com/case-studies/how-miro-builds-and-tests-agentic-features-at-scale/). (V-C007.) Publication date is not stated. Marketing claims are not independently audited; no contract value or realized savings is inferred.

### Stripe: production engineering investment in a task primitive

Stripe describes giving agents standardized devboxes containing source and development services, preparing repositories/caches in advance and maintaining a warm pool. Its account explicitly identifies interference between concurrent tasks as wasted work and says the environment infrastructure predates its agents. This supports frequent task-environment use and internal investment, not dissatisfaction with Stripe's current solution. [Engineering account, February 19, 2026](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2). (V-C008.)

### OpenAI: application and observability per worktree

OpenAI's internal product-development account describes making the application runnable per worktree, with isolated logs/metrics and browser interaction available to Codex. It supports the need for a real application context alongside source isolation. It does not prove that every organization needs to buy the same infrastructure. [Engineering account, February 11, 2026](https://openai.com/index/harness-engineering/). (V-C009.)

### Research counterpart: Edison Kosmos

Edison describes scientific agents working continuously on partner data using prepared private sandboxes, shared persistent storage and substantial compute. That corroborates the importance of usable task environments in research, but its GPU/private-data requirements are different from Miro's integration-test contention. Do not present them as one validated product segment. [Engineering account, May 18, 2026](https://advances.edisonscientific.com/research/how-we-built-kosmos/). (V-C010.)

### Independent methodological cross-check

The peer-reviewed *Measuring Agents in Production* study combines interviews about 20 systems with survey analysis restricted to 86 production/pilot systems, from 306 initial responses. It identifies reliability as the leading challenge. Participation is non-probability, case studies are anonymized and item bases vary. It provides context, not specific validation for task budgets or an INVARIANT environment product. [Published paper](https://proceedings.mlr.press/v306/pan26a.html), [current preprint methods](https://arxiv.org/html/2512.04123v4). (V-C014.)

## 4. A concrete daily workflow to target

**Customer hypothesis:** a product/platform team already using multiple coding agents against an application whose dependent services cannot simply be started in another source-code directory.

**Job:** “Give each task a working version of our application without waiting for a staging slot or touching another task's data.”

Hypothetical example, not a reported Miro architecture: two agents change a checkout service while another changes a background worker. Each needs an application URL, database fixtures, a queue and scoped test credentials. Separate Git branches alone do not isolate a shared database or queue. A potential solution supplies the needed task-specific state/routing and ready signals, then tears down owned resources safely. (V-C017.)

The value is time and blocked concurrency removed before and during useful work. The metric is not “tests run” or “container starts.” Measure time from assignment to a usable application, time waiting for environments, cross-task collisions, preparation/repair effort and safe cleanup. An agent can perform the testing once this environment exists.

Potential buyer: the engineering productivity/platform owner responsible for developer waiting time and environment maintenance. Named adoption and internal engineering investment support plausible ownership; actual INVARIANT willingness to pay is absent. This is materially better problem evidence than a feature gap inferred from gateway documentation. (V-C007–V-C009, V-C017.)

## 5. What already solves it—and what has not been shown

- **Signadot** already provides request routing and stateful-resource isolation, with database partitioning, temporary resources or branches. The customer story also identifies native agent tooling. An agent-facing CLI alone would duplicate existing functionality. [Platform](https://www.signadot.com/product/platform/). (V-C011.)
- **Ona** markets complete connected environments and governed agent execution. It is a direct alternative to a generic agent-environment product. [Product](https://ona.com/). (V-C012.)
- **Dev Containers** is an existing open configuration standard. Existing environment definitions and application scripts must be the baseline; simple repositories may need no additional product. [Specification overview](https://containers.dev/). (V-C013.)

**No unserved INVARIANT segment is yet established.** “Teams without Kubernetes” or “teams wanting local execution” are possible recruitment criteria, not discovered dissatisfied buyers. Do not infer those segments from a Kubernetes vendor's successful case study. Likewise, complex private scientific work is not evidence that a small team can deliver a universal environment platform.

The proposed discovery target is the **remaining manual work after these alternatives are available**: recurring application preparation, state isolation, access setup or lifecycle work that a platform owner still maintains and wants to remove. If the best answer is Signadot, Ona, a devcontainer or a short script, recommend it. (V-C017.)

## 6. What this means for the next INVARIANT decision

1. **Stop expanding the budget idea.** Preserve its technical notes as rejected research history. (V-C016.)
2. **Prioritize problem discovery around environment contention/readiness**, using the named cases as recruitment leads only after outreach is authorized. Ask for a recent blocked task and its actual setup timeline, existing platform and repair work. Do not lead with INVARIANT. (V-C017.)
3. A qualifying opportunity needs repeated pain in several independent teams, a responsible owner, and a recurring step that existing platforms do not already remove cheaply. Also require at least one team outside the originating vendor case willing to test a real application. These are decision gates, not completed validation.
4. Do not implement a generic platform yet. The evidence supports the job; it does not support a unique INVARIANT solution, customer purchase or Git/Docker-scale adoption.

This is a negative decision on the budget direction and a better-evidenced problem area. It is not permission to replace one speculative feature roadmap with another. Current INVARIANT's isolated persistent Linux environment remains a foundation, not a complete per-task application platform.

## Evidence and limits

The [validation sources](/Users/nikchin/Desktop/invariant/docs/market-research/opportunity-validation-sources.csv) and [claims](/Users/nikchin/Desktop/invariant/docs/market-research/opportunity-validation-claims.csv) preserve the decisions, provenance and source limits. Search results were used as leads; primary pages were inspected for retained factual claims. Recycled incident headlines and anonymous community anecdotes were not counted as independent named production teams. This is a bounded public search; failure to locate stronger reports is not proof no private need exists.

Methodological credit: Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026), *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*. [Current record](https://arxiv.org/abs/2609.00065), verified October 18, 2026. Method attribution only, not demand evidence. (V-C015.)
