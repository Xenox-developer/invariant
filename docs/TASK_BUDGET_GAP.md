# Specific gap: one task allowance across agents, tools and restarts

**Reviewed October 18, 2026.** This replaces generic agent authorization as the candidate being investigated. It is a documented difference in control semantics, not proof of an underserved paying market.

> **Rejected as INVARIANT's current product priority after the demand check.** Some operator evidence exists, but the narrower cross-tool task-lifetime product lacks sufficient independently corroborated recurring demand, and existing controls provide substantial substitutes. See the [opportunity validation decision](/Users/nikchin/Desktop/invariant/docs/OPPORTUNITY_VALIDATION.md). Technical observations below remain research history, not a current implementation recommendation.

## The operational problem

An operator launches a research or product-automation job and wants one bounded allowance for the entire job, including parallel workers, alternate tools, retries and continuation tomorrow. The allowance must not multiply when an agent creates a session or delegates work.

Example, deliberately hypothetical: “This investigation may make **300 paid search attempts total**, using either provider, across all its agents, until the job ends.” This is not 300 calls per tool, 300 per agent, or 300 per calendar day. This first example bounds known operation units, not an opaque final provider bill. (B-C008.)

Parallel agent workflows are real: a vendor-hosted Wayfair account describes applied researchers running many agents and experiments against a shared benchmark. It does **not** report that Wayfair has this budget problem. The source supports the workflow's existence only. Actual recurrence, current workaround cost and willingness to pay for conserved task allowances remain unmeasured. [Workflow account](https://cursor.com/blog/wayfair). (B-C007.)

## What the current products document

| Product/control | Established behavior | Difference from a conserved task allowance |
|---|---|---|
| **AgentCore temporal policies** | History/counters are scoped to a caller-supplied session. A new session starts a new count. Maximum temporal look-back is 24 hours. | A stable job-wide allowance spanning separate sessions/principals is not what that temporal counter represents. [Official docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal.html). B-C001. |
| **AgentCore sample FAQ** | Its temporal policies cannot span sessions or aggregate across principals. It recommends task/conversation scope rather than an application-wide ID. | Simply forcing unrelated sessions into one ID is not a general solution to job/delegation accounting. Same-session controlled workflows may already work adequately. [Official sample FAQ](https://github.com/awslabs/agentcore-samples/blob/main/01-features/07-centralize-and-govern-your-ai-infrastructure/02-policy/03-temporal-policies/docs/faq.md). B-C002. |
| **Arcade native rate limits** | Count before execution; share counters among callers in an organization/project; apply independently to each matched tool; use fixed clock windows. | A wildcard does not produce one combined cross-tool counter, and the window is not the job lifetime. Do not claim Arcade lacks shared counters or reliable pre-execution enforcement. [Official docs](https://docs.arcade.dev/en/operate/governance/contextual-access/rate-limiting). B-C003. |
| **LiteLLM** | Already supports shared LLM budgets and pre-request cost reservations, with stricter fail-closed configuration available. | Ordinary parallel LLM spend is already addressed. The docs explicitly exclude full batch-job cost from submission reservation; some non-token-priced routes use recorded spend. [Budget docs](https://docs.litellm.ai/docs/proxy/users). B-C004, B-C005. |
| **Portkey** | Documents budget controls for parallel coding-agent sessions. | “Set an LLM budget” is not a new INVARIANT product. A demonstrated remaining cross-tool/task integration problem is required. [Integration account](https://portkey.ai/blog/conductor-x-portkey-is-now-live/). B-C006. |

These are built-in documented-control boundaries. They are not claims that AWS or Arcade cannot be extended with a database, Lambda, webhook or application-owned ledger. Other policy/rate-limit facilities are not disproven by the cited limits on temporal policies. A customer-specific extension may be perfectly sufficient. Undocumented coverage is unknown rather than absent.

## What INVARIANT would have to add

The proposed core is a durable authority for the task's allowance. It binds each supported request and child allocation to the same operator-issued job identity. A new model thread or process gets no new allowance merely by existing. Supported tools must be reached through the enforcement boundary; bypass credentials or direct alternative access invalidate the guarantee. (B-C008, B-C009.)

Illustrative behavior:

1. The operator issues a job 300 paid-attempt units.
2. Parallel workers reserve units atomically before dispatch, rather than each checking an outdated total.
3. A child can use only an allocated share; allocating to children cannot create extra units.
4. Provider switching and session restart retain the job's original balance.
5. A timed-out attempt remains charged/reserved according to the declared attempt/outcome semantics; uncertain work is not treated as free capacity.
6. A restarted controller recovers the same balance or refuses admission until authoritative state is known.

The accounting invariant would be explicit: committed consumption plus outstanding reservations plus unallocated available allowance cannot exceed what the operator issued. Delegated units must be counted once, not again in both parent allocation records and child balances. This is a proposed requirement, not an implemented or proven INVARIANT guarantee. Simultaneous calls, crashes, duplicated messages, lost replies and late completion must be tested against it. (B-C008.)

This is narrower than a general policy platform. It is also more substantive than a dashboard: INVARIANT would own admission accounting and delegation semantics. A prototype could still turn out to be a small library or an upstream contribution rather than a standalone product. (B-C009.)

## Important limits

- A count of admitted attempts is not the same as a count of successful external effects. Request retries and billing semantics must be explicit.
- A dollar ceiling requires a trustworthy maximum charge before dispatch, enforced request limits, known pricing and controlled paths. If cost is unbounded or unknown, refuse that operation under a hard ceiling or label the limit as estimated. Do not claim arbitrary cloud bills are capped.
- Provider batch submissions, asynchronous GPU jobs and downstream fan-out need their own cost/quantity contracts and reservations. Merely attaching the same job ID does not bound them.
- Timing out, cancelling or revoking access cannot recall an already accepted remote action. Refunds/reconciliation require authoritative evidence.
- If a shared LiteLLM key and ordinary project configuration already bound the customer's entire workload adequately, this candidate adds little. Fixing one transient vendor bug is not a durable market position.

These are engineering constraints and proposed semantics, not observations of customer dissatisfaction. (B-C005, B-C008, B-C009.)

## Decision

**The documented gap is a conserved task-lifetime allowance across independently executing agents and heterogeneous effect paths.** AWS temporal-session counters and Arcade's native per-tool clock-window counters are different controls. Basic LLM budget reservations are already supplied elsewhere. (B-C001–B-C006, B-C008.)

**The market gap remains conditional.** Before implementation, obtain recent examples from teams that launch parallel jobs: how they bound a job across model calls and paid tools, how restarts/delegation affect the allowance, what they reserve for uncertain operations, and whether current gateways already solve it cheaply. A qualifying case must have recurring operator work or observable failures despite a correctly configured existing budget product. Otherwise recommend the existing product. (B-C009.)

No enterprise functionality has been added to INVARIANT in this follow-up. Its current narrow broker contract is not evidence of complete cross-provider budget support. No outreach, purchases or paid API tests occurred.

## Evidence

[Sources](/Users/nikchin/Desktop/invariant/docs/market-research/task-budget-sources.csv) and [claims](/Users/nikchin/Desktop/invariant/docs/market-research/task-budget-claims.csv) distinguish documented capabilities, inference and recommendation. This is a focused desk comparison, not a representative customer study. Dates without a published value are marked not stated.

Methodological credit: Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*. [Current record](https://arxiv.org/abs/2609.00065), checked October 18, 2026; September 2 revision. This credit is not market evidence.
