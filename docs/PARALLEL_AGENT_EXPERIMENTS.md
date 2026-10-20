# Parallel agent variants from one project

Public primary documentation checked October 20, 2026. This is a technical comparison, not a hands-on performance benchmark or a customer-demand validation. No paid services or model calls were used.

## Answer

**Yes: existing tools already run multiple configurations from one codebase without requiring a separate ordinary Git branch for every run.** Harbor and Promptfoo are close agent-specific alternatives; Inspect handles evaluation sets; DVC handles queued workspace variants and selection; Ray Tune handles parameter sweeps. LangSmith and MLflow also provide relevant configuration/evaluation or parameterized execution primitives. The absence of per-run branches does not mean there is no per-run directory, checkout, Git reference or execution context. (PX-C001.)

The potential INVARIANT advantage is a simpler complete local workflow around an existing project, especially when an agent modifies both project files and installed tools. This is a product-experience hypothesis. It is not a new parallel-execution or storage primitive, nor a demonstrated unmet commercial need. (PX-C015.)

## First correct the storage premise

Docker already lets containers share read-only image layers while retaining their own writable layers. Ten containers based on one image do not imply ten complete copies of that image. Copy-on-write is a standard storage mechanism, so “one base plus per-run changes” is not INVARIANT-specific. Writable data can still grow substantially; image sharing does not eliminate each run's processes, compute or memory needs. Project bind mounts and other external state need their own separation. [Docker storage documentation](https://docs.docker.com/engine/storage/drivers/). (PX-C002.)

## Closest alternatives

| Tool | Relevant documented workflow | Boundary for the user's idea |
| --- | --- | --- |
| **Harbor** | One [JSON/YAML job](https://docs.harborframework.com/jobs/configs) can contain multiple agents, models and per-agent kwargs, with attempts and concurrency controls. Cached environments can be reused; environment deletion is configurable. | Strong baseline for running coding-agent variants on prepared tasks. Per-trial environments still exist. Retaining a completed environment is already supported; an effortless general workspace-to-winner workflow was not established here. (PX-C003.) |
| **Promptfoo** | [Coding-agent evaluation](https://www.promptfoo.dev/docs/guides/evaluate-coding-agents/) supports Codex, Claude and other providers, fresh workspaces and file-level assertions. [Concurrency](https://www.promptfoo.dev/docs/configuration/reference/) is configurable. | Particularly close to testing variants without changing the source repository. Workspaces can be temporary clones or directory copies, rather than branches in the original repo. They are deleted after assertions; clone-based diffs are available but truncated above the documented limit. The workspace feature is not whole-environment isolation. (PX-C004.) |
| **Inspect AI** | [Eval sets](https://inspect.aisi.org.uk/eval-sets.html) run tasks/models in parallel, with retries and resumption. [Model specs/run configs](https://inspect.aisi.org.uk/reference/inspect_eval-set.html) configure model arguments and evaluation settings. | Good for systematic evaluations of agents exposed through its interfaces. No separate Git branch is a prerequisite of the documented workflow. Stateful shell work still needs per-sample setup and appropriate sandboxing. (PX-C005.) |
| **DVC Experiments** | [Experiment records](https://doc.dvc.org/user-guide/experiment-management) stay outside the ordinary Git tree. [Queued runs](https://doc.dvc.org/user-guide/experiment-management/running-experiments) execute in temporary workspace copies, share the project data cache and can run in parallel. [Apply](https://doc.dvc.org/command-reference/exp/apply) restores a selected experiment into the workspace. | Already covers much of “try variants, inspect results, keep one” without ordinary branch clutter. Requires a configured pipeline/tracked parameters and does not, from these pages, automatically snapshot all mutable Linux packages/services or agent conversation state. (PX-C006, PX-C007.) |
| **Ray Tune** | [Grid search](https://docs.ray.io/en/latest/tune/api/doc/ray.tune.grid_search.html) explores combinations; [TuneConfig](https://docs.ray.io/en/latest/tune/api/doc/ray.tune.TuneConfig.html) controls repeated sampling and trial concurrency. | General-purpose baseline when the agent system is a function accepting configuration and reporting metrics. Integrating that function and separating application-side effects remains the developer's responsibility; trial concurrency is not an automatic filesystem snapshot. (PX-C008.) |
| **LangSmith / LangGraph** | [Assistant configuration](https://docs.langchain.com/langsmith/configuration-cloud) varies model, prompts and tools without modifying an already-configurable graph. [Experiment settings](https://docs.langchain.com/langsmith/experiment-configuration) control repetitions and evaluation concurrency. | Directly supports keeping configuration outside graph code, provided the graph exposes those choices. Concurrency within an evaluation is distinct from scheduling a configuration sweep; orchestration and workspace handling still need to be supplied or combined with a runner. (PX-C009.) |
| **MLflow Projects** | [Parameterized runs and asynchronous execution](https://mlflow.org/docs/latest/ml/projects/), including Docker environments. | Existing code can be launched with different parameter dictionaries without per-variant branches. It provides primitives for a sweep; meaningful agent-specific grading and independent mutable workspaces require setup. (PX-C010.) |
| **ServiceNow AgentLab** | [Official repository](https://github.com/ServiceNow/AgentLab) documents parallel agent experiments using Ray and analysis of results. | Another agent-specific implementation, focused on BrowserGym/web-agent research. Its own README says it is a research framework rather than a consumer product. That warning is not evidence every user finds it difficult. (PX-C011.) |

These are documented scopes, not proof of comparable ease, memory use, disk use or runtime. Unknown integration details are not negative feature claims. Pricing and customer satisfaction were not evaluated. (PX-C017.)

## Where INVARIANT could be better

**Proposed differentiator:** make the entire project-to-comparison-to-selection flow convenient for local, file- and shell-using agents, with clear run ownership of files, dependencies, agent context and results. Each ingredient has alternatives; the claim to test is less preparation and fewer manual joins between them. (PX-C015.)

Illustrative future workflow:

1. Select the current project and the input task or evaluation set, including explicitly selected uncommitted inputs.
2. Define variants in a form or one experiment file: model, effort, prompt, tools and exposed agent-graph settings.
3. Capture a common starting workspace/environment and run independent variants under a total concurrency/resource budget.
4. Inspect results, full artifacts and file differences alongside the evaluation outcomes.
5. Retain a candidate's workspace and installed tools for further work; separately review which configuration or project-file changes to apply to the main project.

This is a proposed integrated experience, not evidence competitors cannot provide it. Harbor already has environment retention and DVC already has selected-experiment application. Promptfoo's documented default workspace lifecycle makes persistent post-run exploration an area worth comparing, but users can also save artifacts through their own code. No speed, storage or usability advantage has been measured. (PX-C015.)

Changing model/effort/prompt can be external configuration when the agent program exposes those options. Changing a one-agent implementation into a planner/executor/reviewer system may require implementation code. Keep those code variations as explicit private patches or named entry points if needed; do not promise that any arbitrary architecture can be changed without code. This is a design requirement and programming constraint, not a unique product feature. (PX-C012.)

## Fit with current INVARIANT

The [current architecture](/Users/nikchin/Desktop/invariant/docs/ENVIRONMENT_ARCHITECTURE.md) documents a persistent Linux filesystem, a separately mounted host project, and host-side Codex history. It restores a fresh executor on resume rather than process RAM or open connections. (PX-C013.)

A parallel experiment runner remains new work. In particular, pointing ten executors at the same writable project mount would not create ten independent experiments. We would need isolated project views, per-run agent state, immutable starting-state identities, configuration/patch capture, scheduling, outcome collection and reviewed selection. Docker image sharing alone does not supply these project and application semantics. (PX-C014.)

## Recommendation

Use **Harbor** as the first technical baseline for coding-agent task evaluation, **Promptfoo** for a configurable agent evaluation matrix, and **DVC** for the branch-free queued-workspace-and-selection workflow. This routing is analyst judgment about documented fit, not a claim they are interchangeable or always sufficient. (PX-C016.)

If pursuing INVARIANT, compare the whole workflow on the same real project: prepare variants, run repeated trials, inspect an unexpected result, keep a candidate and apply selected changes. Measure operator time, required adapters/configuration, retained-state completeness, shared versus per-run disk use, peak memory and successful isolation of file writes. Hold evaluation cases/checks fixed; a single lucky run is not sufficient to select a generally better agent. (PX-C016.)

Proceed only if INVARIANT produces a clear end-to-end improvement over the best appropriate configured baseline. Stop claiming differentiation if the improvement is just a thin launch alias or ordinary Docker layer sharing. This screen supports technical feasibility and strong competition, not validated customer demand. No implementation or benchmark was performed. (PX-C015, PX-C016, PX-C017.)

## Method credit

Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). [Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents](https://arxiv.org/abs/2609.00065). Current record retrieved October 20, 2026. Research-method attribution only. (PX-C018.)
