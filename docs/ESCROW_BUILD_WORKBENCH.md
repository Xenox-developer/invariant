# Conditional niche: independent source-deposit build verification

> **Superseded, October 18, 2026:** [gap validation](/Users/nikchin/Desktop/invariant/docs/ESCROW_GAP_VALIDATION.md) rejects this niche as the next expansion. No convincing repeated post-tool gap was established among commercial verification firms. The conditional recommendation and pilot below are withdrawn and retained only as research history.

**October 18, 2026: investigate as a narrowly scoped workbench; do not select a product pivot yet.** The candidate customer is an independent build-verification consultancy rebuilding suppliers' Linux-compatible software deliveries for escrow or handover acceptance. The job has commercial service adoption, priced offerings and a plausible use for isolation and persistence. An advantage over providers' existing internal tools remains unproven. (W-C001)

## Evidence of an existing commercial job

| Evidence | What it establishes | Limits |
|---|---|---|
| Bhavesh Narsai's account, updated **August 1, 2026**, describes nine years of NCC Group verification/build work, through 2023. Environment reconstruction was a major time sink; a service team grew from two to five as delivery improved. | A practitioner reports a selling service and recurring delivery work: compiler versions, dependencies and undocumented configuration, followed by independent rebuilding. | Retrospective personal account, not audited current labor data. Scoping and relationships also improved delivery; the bottleneck was not solely compute. [Account](https://bhaveshnarsai.com/software-escrow). W-C002 |
| **Al Salam Bank** implemented Escode source-code verification, including reviewing materials, rebuild testing and documented build procedures. | Named commercial customer adoption of the specific service. | Supplier-hosted case; contract amount, software stack and recurring staff time undisclosed. [Case](https://www.escode.com/resources/case-study-al-salam-bank/). W-C003 |
| **Transavia** describes escrow and verification supporting continuity of a mission-critical cloud application. | A second named commercial adopter. | Same provider's case selection, not independent effectiveness evidence or proof the application fits our Linux backend. [Case](https://www.escode.com/transavia/). W-C004 |
| An anonymous financial institution's Escode pilot found **three unbuildable deposits**, involving missing code, passwords, dependencies and instructions. After vendor clarification and independent testing, all three passed. | Concrete failure modes and corrective service work. | One customer, not three customers; resolved by the incumbent. Missing source/passwords cannot be manufactured by an execution environment. [Case](https://www.escode.com/resources/case-study-verification-and-risk-mitigation/). W-C005 |
| EscrowTech publishes **$1,595/year plus $995 setup** for its starting single-beneficiary escrow arrangement. Full build verification is separately quoted; **$295** is the file-listing entry level. | A current priced service category. | List prices, not those customers' bills. Do not describe $295 as full build verification or derive our savings from the annual storage/administration fee. [Pricing](https://www.escrowtech.com/software-escrow-fees/). W-C006 |

Together these support commercial adoption and service-budget existence, not a disclosed transaction amount for any named customer. No direct willingness to pay for INVARIANT has been established. Daily provider workload, per-deposit labor and present dissatisfaction are unknown. (W-C001)

## The precise workflow to test

**Illustrative scenario, not a reported customer incident:** a verifier receives a Linux application deposit. It requires an old compiler, a native library and an undocumented build setting. The verifier investigates, asks the supplier for clarification, and resumes several days later. A colleague may finish the assignment.

An INVARIANT workbench could keep one isolated project per deposit and preserve the installed tools and configuration between investigation sessions. A second, fresh environment would then test the finalized instructions and declared inputs. A review bundle would connect the original deposit, approved additions, commands, logs, outputs and limitations. This workflow design is a hypothesis. (W-C007)

**The value mechanism:** less manual environment reconstruction, agent-session setup and evidence assembly between clarification rounds. Isolation limits ordinary project access to the assigned deposit workspace; persistence retains native dependencies as well as language packages. These are a plausible convenience over manually coordinated sessions. They are not capabilities unavailable in VMs or Docker. (W-C007, W-C010)

**A successful resumed investigation is not proof of a complete deposit.** The final assessment needs a fresh rebuild from the declared materials and authorized dependency sources. The preserved workspace may contain undocumented repairs and hidden state. The outcome can correctly be “blocked: required input missing”; an agent must not quietly replace missing vendor code and report a pass. Matching output hashes is appropriate only when the relevant build is expected to be deterministic. (W-C007)

## Check the closest alternatives

| Alternative | Existing scope | Implication |
|---|---|---|
| **Escode** | Independent build verification already appears in its current offering and the case above. | INVARIANT should not claim to invent the service or outperform its engineers. Internal workflow tooling and appetite to buy remain unknown. [Verification](https://www.escode.com/software-escrow/testing-and-verification/). W-C008 |
| **Codekeeper** | Advertises automated reviews and human analyses; its Certified tier lists complete build runs and deployment validation, **from $349/month**. | A direct incumbent response. List price is not an unlimited arbitrary-build guarantee; cadence, scope and internal workbench details are unverified. [Verification and tiers](https://codekeeper.co/verification). W-C009 |
| **EscrowTech** | Offers dependency analysis, compiling, binary comparison and test-plan verification at separately scoped levels. | Existing service buyer needs are substantially covered; potential tool savings must be shown inside delivery. W-C006 |
| **Existing VM/Docker setup plus a coding agent** | This is the essential experimental baseline, with the same model and equivalent access. | If existing snapshots, templates and runbooks already remove the overhead, there is no demonstrated reason to switch. This comparison is proposed, not performed. W-C007 |

The possible business is a tool sold to verification practitioners to improve their delivery economics. It is not a replacement for an escrow agent, independent professional judgment, or legal custody. No evidence shows that established providers currently lack adequate internal tooling. (W-C001, W-C007)

## Fit and missing implementation

Our [documented architecture](/Users/nikchin/Desktop/invariant/docs/ENVIRONMENT_ARCHITECTURE.md) already provides isolated Linux execution and filesystem persistence with policy reapplied on resume. It does not preserve process memory, provide application-aware database backups, or implement environment export/import. (W-C010)

A useful prototype would add immutable deposit manifests, explicit separation of investigation and fresh verification, recording approved dependencies, and an exportable evidence bundle with an accessible environment archive. These are **proposed additions**, not implemented features. Start with Linux-compatible local builds on supported architectures. Windows-specific tools, hardware, specialized kernels and full cloud recovery need other backends or services. Adoption also depends on permitted handling of proprietary source and use of the chosen model; local execution alone does not make model processing local. (W-C007, W-C010)

User: build-verification engineer. Proposed buyer: verification consultancy owner or technical delivery lead. Trigger: a new deposit, a new software release requiring re-verification, or an interrupted handover. Distribution hypothesis: specialist build consultants and escrow-service technical partners. Switching costs include accepted reporting procedures, existing VM templates, access controls and client approval. These buyer/channel assessments are hypotheses, not interviews. (W-C011)

## Recommendation and explicit stop test

**Conditional recommendation:** this is a better technical fit to investigate than SaaS-automation maintenance, because the work actually includes installing and reconstructing software environments. Test it as a practitioner workbench before adding a vertical product. Pricing should follow demonstrated labor savings per verification job; no price or cost advantage is established yet. (W-C011)

An authorized evaluation should use 5–10 representative Linux-compatible deposits from one consultancy, followed by a second firm's cases if results justify it. Include interrupted work and updates to previously reviewed deposits. Compare the firm's current VM/Docker workflow plus the same coding agent against the prototype. Use matched or counterbalanced assignments to reduce learning effects. Count onboarding, setup, supplier clarification, evidence preparation and reviewer time separately.

Proposed proceed conditions: at least **20% lower hands-on effort**, no false completeness claims, successful fresh verification where inputs are sufficient, and a consultancy willing to pay for continued use. These thresholds are predeclared product judgments, not research results. Stop if VM templates already deliver the same benefit, most effort is missing inputs/permissions rather than environment work, client restrictions prevent use, or supported Linux cases form too little of the actual workload. No outreach, purchase, trial or implementation occurred. (W-C011)

## Confidence and records

**Medium confidence** that commercial build-verification work exists. **Low confidence** in current unmet tool demand; **unmeasured** INVARIANT advantage. This is public English-language, purposive desk research through October 18, 2026. Provider-selected cases and retrospective testimony cannot establish market prevalence or current delivery cost. No regulatory-compliance conclusion is made.

[Sources](/Users/nikchin/Desktop/invariant/docs/market-research/escrow-workbench-sources.csv) · [Claims](/Users/nikchin/Desktop/invariant/docs/market-research/escrow-workbench-claims.csv). Structural checks are not market validation.

Method attribution: Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*. [Current record](https://arxiv.org/abs/2609.00065), revised September 2, 2026. Procedural credit only. (W-C012)
