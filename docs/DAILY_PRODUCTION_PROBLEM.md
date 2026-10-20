# A concrete production problem beyond session and environment management

**Evidence reviewed: October 18, 2026.** Follow-up to the user's objection that the proposed run-management product mostly combines existing capabilities. This note does not promote that previous proposal as INVARIANT's settled direction.

> **Scope correction after competitive review:** generic task authorization is not a distinct opening against AgentCore or Arcade. See the [narrower comparison of task-lifetime allowances](/Users/nikchin/Desktop/invariant/docs/TASK_BUDGET_GAP.md). That comparison identifies a specific documented control boundary, while leaving demand unvalidated.

## Finding

The strongest concrete problem in this focused follow-up is:

**Let an agent use real company systems for one task without giving that task all of the employee's authority or allowing it to mix customers' data.**

This is a recurring operational responsibility at each data access and action, not evidence that harmful incidents occur daily. Its clearest initial customer is a team building internal or customer-facing agents over multiple customers' data and business tools. This is not a demonstrated universal problem for every research group or individual coding-agent user. Confidence is medium in the problem and low in an unoccupied opening for INVARIANT. (D-C001, D-C002, D-C004, D-C011.)

## Concrete deployment evidence

**Sierra, July 22, 2026:** engineers describe building an internal MCP gateway across company systems. Preventing cross-customer information use required additional controls even though production systems were partitioned by customer. They added access history, sensitivity/customer classification, exceptional-access approval and separate identities for interactive versus recurring work. This is an account of implemented internal engineering, not a hypothetical threat or an independent performance test. [Engineering account](https://sierra.ai/blog/building-sierras-mcp-gateway-an-engineering-iceberg). (D-C001.)

**Sierra, July 9, 2026:** a companion account describes agents investigating customer issues across internal systems and the gateway enforcing permissions and customer isolation. These two publications are one organization's experience, not independent corroboration. [Deployment context](https://sierra.ai/blog/ai-pilling-our-company-lessons-learned). (D-C002.)

**Arcade, February 12, 2026:** its own internal assistant is described as having narrower email capabilities than the human. The supplier also documents runtime hooks before execution and after tool results. That supports the distinction between user access and agent-task authority, but is also a direct competing solution. [Contextual Access](https://www.arcade.dev/blog/contextual-access-ai-agent-security/). (D-C003.)

**Anthropic's March 9, 2026 NIST submission** identifies cross-agent delegation and permission propagation as unresolved architectural questions and reports customer interest. This is a supplier's policy submission, not a customer census, measured failure rate or purchase evidence. [Submission, pp.7–8](https://www-cdn.anthropic.com/43ec7e770925deabc3f0bc1dbf0133769fd03812.pdf). (D-C004.)

These sources support recurring control work. They do not establish how many teams remain dissatisfied, the incidence of data mixing, spending on INVARIANT or the efficacy of any proposed control system.

## A hypothetical ordinary workday

An engineer has legitimate access to customers Alpha and Beta. They ask an agent:

> Investigate Alpha's failed deployment and prepare a fix.

The agent needs logs, tickets, internal discussions and a repository. A company-wide credential can be valid for both customers. A prompt saying “only Alpha” is an instruction; it does not itself prevent a connector from returning Beta's sensitive documents. A tool allowlist allowing search does not distinguish which customer's records a search result contains.

The job is to make a narrower task authority enforceable:

| Requested operation | Desired decision for this example |
|---|---|
| Read Alpha's authorized logs and tickets | Allow. |
| Search an unclassified internal source | Restrict, classify or route for review before exposing sensitive results. |
| Read Beta's private operating procedure | Deny, despite the human's broader rights. |
| Create a draft fix | Allow in the designated workspace. |
| Deploy to production | Require the separately defined approval and target restrictions. |
| Delegate investigation to another agent | Pass only the task's restricted authority. |
| Reuse an approval after it has expired or for another target | Deny under the agreed authorization protocol. |

This is a design example, not an observed Sierra incident or a promise that arbitrary documents can be classified perfectly. Trusted resource metadata and authoritative target-side checks are preferable to asking another model to guess data ownership. When labels are missing, classification introduces uncertainty and requires an explicit fail/review policy. A local sandbox cannot enforce these rules if credentials or alternate network paths bypass the control boundary. (D-C011, D-C012.)

## What the named tools already do

| Tool | Existing capabilities to acknowledge | Responsibility still belonging to the application/operator |
|---|---|---|
| **Codex** | External MCP connections, OAuth and tool allow/deny configuration. | Define and enforce the business meaning of the current task: permitted customer, records, action arguments and exceptional access. This is not a claim Codex lacks authentication. [Official configuration documentation](https://learn.chatgpt.com/docs/extend/mcp). D-C007. |
| **AgentFS** | Filesystem, key-value state and tool-call history. | Decide whether a business action or returned record is authorized before the effect/exposure. Recorded history can support a decision but is not the organization's policy by itself. [Repository](https://github.com/tursodatabase/agentfs). D-C008, D-C011. |
| **LangGraph / LangChain ecosystem** | Framework integration points and authorization patterns; managed offerings now handle identity-scoped credentials and memory. | Supply the application's task/customer rules and trusted resource context. Do not claim the ecosystem lacks auth or cannot implement such rules. [Auth explanation](https://www.langchain.com/blog/agent-authorization-explainer), [managed offering](https://www.langchain.com/blog/langsmith-managed-deep-agents-whats-new). D-C006, D-C009, D-C011. |

The defensible distinction is **a recurring application responsibility that these tools do not eliminate**, not a claim that developers cannot solve it using them. This is a different job from saving/replaying a run. It still does not prove demand for a standalone product. (D-C011.)

## Disconfirmation: specialist products already compete

**AWS AgentCore, August 6, 2026:** documents temporal policies covering call sequencing, output-to-input binding, freshness, cumulative limits and approvals consumed once. Its worked example is hypothetical. This directly contradicts a claim that multi-step enforcement is an invented or empty category. [Official walkthrough](https://aws.amazon.com/blogs/machine-learning/securing-ai-agents-with-temporal-policies-in-amazon-bedrock-agentcore/). (D-C005.)

**Arcade** already supplies scoped access and runtime policy hooks; **Auth0 Agent Gateway** announced a beta for customer-facing, multi-tenant agent identity and delegation on September 18, 2026. [Arcade](https://www.arcade.dev/blog/contextual-access-ai-agent-security/), [Auth0](https://auth0.com/blog/auth0-agent-gateway-beta/). These are vendor-described capabilities, not independent tests. (D-C003, D-C010.)

**LangSmith Managed Deep Agents**, September 24, 2026, describes identity-scoped credentials and memory. Basic per-user authentication and a token vault are therefore particularly weak standalone differentiation. [Announcement](https://www.langchain.com/blog/langsmith-managed-deep-agents-whats-new). (D-C006.)

An existing policy engine, application authorization checks or a managed platform may already be sufficient. Company investment in custom infrastructure shows a problem and a workaround, not necessarily a preference to buy a separate enforcement service. No current price or comparative efficacy claim is made in this focused note.

## Implication for INVARIANT

If investigated, the product would have to own a useful enforcement mechanism: bind a trusted task identity to permitted resources/actions; enforce it at all supported effect boundaries; constrain delegation; apply explicit revocation/approval semantics; and explain denials. A dashboard or configuration wrapper would not establish the value. The earlier narrow broker is conceptually closer to this job than the environment launcher, but it is not evidence that enterprise integrations, task identity or complete mediation have been implemented. (D-C012.)

**Do not announce a new direction yet.** The residual question is narrow: after using native auth and the available specialist products, do teams still maintain substantial custom code to keep a task within one customer's data and authority? Interview people who own that code, ask for a recent redacted workflow, and compare the current implementation with AgentCore/Arcade/Auth0 and ordinary application checks. Ask what happens when resource labels are missing, a user is authorized for several customers, work is delegated or permission changes mid-task. Feature interest is not a purchase commitment. (D-C012.)

If the remaining pain is simply OAuth setup, adding integrations or displaying approvals, existing offerings are the default recommendation. If the gap is real but requires bespoke business logic for each customer, that weakens the ambition of a broadly adopted general tool. A deeper need does not automatically yield a viable product.

## Scope, provenance and credit

This is a focused public-source follow-up, not representative research or an assertion that no other daily problems exist. Facts, vendor descriptions, engineering inferences and proposals are separated in the [claims ledger](/Users/nikchin/Desktop/invariant/docs/market-research/daily-problem-claims.csv); source dates and limits are in the [source ledger](/Users/nikchin/Desktop/invariant/docs/market-research/daily-problem-sources.csv). No outreach, purchases or product changes occurred.

Methodological credit for the market-research-reports skill: Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026), *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*. Current record checked October 18, 2026; revision September 2, 2026. [DOI/current record](https://doi.org/10.48550/arXiv.2609.00065). This is method attribution, not market evidence.
