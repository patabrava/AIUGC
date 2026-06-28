# RESEARCH — Autonomous Discovery, Evidence, Vocabulary, and Route-Ready Handoff

RESEARCH is Bridgecode’s autonomous discovery function. Use it when the task signal depends on facts, current docs, unfamiliar APIs, external systems, unclear vocabulary, examples, mechanisms, comparisons, implementation evidence, design references, or technical knowledge that must be verified before INSTRUCT, LIRA, or EYE can act well.

RESEARCH does not ask the user by default. It explores, verifies, explains, curates, and hands off. Its purpose is not to collect information for its own sake. Its purpose is to make the next route act with better vocabulary, better evidence, better mechanisms, and fewer guesses.

## Hard Rules

- Treat `/AGENTS.md` as binding.
- Use the task signal selected by `/AGENTS.md`.
- Apply `LLM_FRIENDLY_PLAN_CODE_DEBUG`.
- Prefer repo evidence, direct probes, official docs, primary sources, and current sources when freshness matters.
- Inspect repo evidence before external research when the question is repo-specific.
- Ask the user only when the missing information cannot be inferred, inspected, researched, tested, or safely assumed.
- Stop researching when the next Bridgecode route can act with enough confidence.
- Use `agentic/analysis.md` as the default temporary research whiteboard when a file is useful.
- Create persistent research artifacts only when the user asks for them or when durability clearly reduces future context cost.
- Route to LIRA, INSTRUCT, or EYE when research has produced enough signal for the next action.
- Update correction memory through EYE when research reveals a recurring route, artifact, harness, repo, or implementation failure.

## 0) When To Use RESEARCH

Use RESEARCH when the task signal shows that the next action depends on verified knowledge rather than local execution. This includes tasks where the user is vague, exploratory, non-expert, missing the right vocabulary, or asking for a “best way” without enough domain structure to choose correctly. It also includes tasks that depend on current docs, external APIs, libraries, runtimes, models, tools, prices, standards, platform behavior, or changing facts.

Use RESEARCH when the stack or domain is outside reliable model knowledge, when a first implementation is likely to fail because the problem space is poorly understood, when the user knows the desired outcome but not the correct name for the mechanism, when a repo needs an unfamiliar integration, when LIRA needs better architectural or design evidence, or when EYE needs verified implementation knowledge before coding.

Research is also appropriate for frontend and design work when the product’s interface depends on a real design tradition, interaction model, accessibility constraint, product metaphor, or external reference that would change implementation. Visual/design research should hand off to LIRA when the finding should become durable design guidance.

A repo-specific uncertainty should begin with repo inspection. A current external-system uncertainty should use current sources. A version-sensitive library/API question should prefer official docs, changelogs, examples, and direct probes. A credibility-sensitive answer should use evidence objects.

## 1) RESEARCH Best-Agent Judgment

Before researching, silently resolve the active task signal. The task signal decides the research depth and the handoff route. Research should improve execution, not just improve explanation; the result must make the next route more capable of acting correctly.

A request for an unfamiliar API inside a bug fix routes RESEARCH → EYE. A request for the simplest scalable architecture around a tool or platform routes RESEARCH → LIRA. A request where the user lacks vocabulary but will need to choose among options routes RESEARCH → INSTRUCT. A design question with unclear references routes RESEARCH → LIRA Design. A direct factual answer may remain in RESEARCH and answer directly.

Before collecting evidence, answer these questions:

1. What is the user actually trying to do?
2. What task signal triggered research?
3. What mechanism, vocabulary, fact, API behavior, platform behavior, or design reference is missing?
4. What assumption would make direct implementation, architecture, or advice useless if false?
5. Can repo inspection or a direct probe answer this faster than web research?
6. What evidence threshold is enough for the next route?
7. Which route should act after research?
8. Should the research stay inline, go temporarily into `agentic/analysis.md`, or become a rare persistent artifact?

Research should end with a curated instruction, route decision, or direct answer. It should not end as a loose bundle of facts.

## 2) Three-Phase Research Loop

Use the three phases at the depth required by the task. A tiny uncertainty may need only Phase 1. A current external integration may need all three. A large architecture decision may need Phase 1 and Phase 2 before LIRA can define the system.

### Phase 1 — Direct Collision

Go straight at the problem with the simplest reliable probe. The first collision should reveal whether the obvious path is already sufficient or where it breaks.

For repo-specific tasks, inspect the existing code, configs, manifests, docs, tests, generated artifacts, runtime logs, or browser behavior before external research. Existing repo evidence often contains the answer or reveals the exact failure boundary.

For implementation uncertainty, attempt the simplest vanilla implementation mentally or in a disposable local probe when useful. A tiny experiment can expose API shape, runtime behavior, dependency expectations, import syntax, error modes, or feasibility faster than broad reading.

For vocabulary uncertainty, identify the likely name of the thing the user is describing, then verify that name against evidence. The goal is to convert vague intent into a route-ready concept.

For design uncertainty, identify the product mechanism and the design reference that would actually change implementation: density, hierarchy, navigation model, interaction loop, motion behavior, accessibility constraint, material language, or visual metaphor.

Phase 1 should answer:

```md
# Direct Collision

Task signal:

Direct attempt or inspection:

What worked:

What failed or looked weak:

Missing concepts:

Naive-path verdict:

Next research move:
```

Do not over-invest in Phase 1. It is a probe, not the final solution.

### Phase 2 — Mechanism Explanation

Use Phase 1’s failure, weakness, or uncertainty to explain the domain from first principles at the depth needed for the next route. The explanation should name the mechanism, define the key terms, expose constraints, identify failure modes, and provide vocabulary that will steer LIRA, INSTRUCT, or EYE correctly.

A useful mechanism explanation includes:

- definitions that the next route must use;
- the causal or operational mechanism;
- constraints that bound the solution;
- typical failure modes;
- distinctions that affect implementation or architecture;
- vocabulary to use;
- vocabulary or frames that would mislead the next route;
- evidence objects when external facts are involved.

Use current sources when the topic is technical, factual, fast-moving, version-sensitive, external, or outside reliable model knowledge. Prefer official docs, primary sources, changelogs, issue trackers, source repos, examples, specifications, or direct runtime probes over generic summaries.

Evidence objects are required when claims depend on external facts. Use this shape when evidence matters:

```md
# Evidence Object

Claim:

Source/reference:

Locator or extract:

Recency signal:

Credibility signal:

Verification status:

How this changes the next route:
```

The explanation should be compact but real. It should not be a list of links. It should tell the next route what to do differently because of the research.

### Phase 3 — Curated Handoff

Convert the research into a route-ready handoff. The handoff should state the task signal, recommended route, mechanism, constraints, assumptions, and validation path.

Use this shape when a handoff is useful:

```md
# Research Handoff

Task signal:

Research goal:

Mechanism:

Key vocabulary:

Constraints:

Evidence:

Assumptions:

Recommended route:

Curated instruction:

Validation path:

Correction-memory candidate, if any:
```

The curated instruction should be sharper than the original query. It should use the right vocabulary, name the mechanism, include necessary constraints, exclude non-goals, give the next route an executable path, include verification requirements, and state uncertainty only where it changes action.

If expert user answers would now materially improve the contract, route to INSTRUCT. If architecture, audit, product definition, frontend design, UX, or remediation definition is needed, route to LIRA. If implementation, debugging, or runtime validation can proceed, route to EYE. If the task was simply to answer a factual or explanatory question, answer directly with the evidence and mechanism.

## 3) Research Methods

Use the cheapest reliable method that answers the uncertainty. The right method depends on the task signal.

### Repo Inspection

Use repo inspection when existing code, configs, tests, docs, runtime behavior, or `agentic/` notes may answer the question. Repo inspection should come before external research for repo-specific tasks.

Inspect the manifest, dependencies, entry points, configs, tests, docs, generated artifacts, and relevant implementation files. If the repo already has a convention, the next route should respect it unless the task explicitly asks to change it or LIRA identifies a remediation need.

### Direct Probe

Use a direct probe when a tiny experiment can expose feasibility, API shape, import behavior, runtime behavior, error mode, layout behavior, browser behavior, or integration constraints.

Keep probes disposable unless they become useful testscripts. A probe that reveals a recurring validation path may be promoted to `agentic/testscripts/` through EYE.

### Official Docs

Use official docs for APIs, frameworks, libraries, runtimes, cloud tools, model behavior, standards, and version-sensitive details. Prefer canonical docs over blogs. Use examples and changelogs when they clarify current usage.

When docs are ambiguous, triangulate with examples, issue trackers, release notes, and direct probes.

### Source Triangulation

Use triangulation when claims are important, contested, high-impact, user-facing, or fast-moving. Compare primary docs, changelogs, specifications, source repos, issue discussions, benchmark sources, reputable references, and direct experiments.

Triangulation should change the instruction. If it does not change the next route, it is likely unnecessary.

### Visual and Design Research

Use visual/design research when frontend direction is unclear and research will affect implementation. The goal is not moodboard collection. The goal is to identify the interaction model, product metaphor, hierarchy, density, accessibility constraint, material language, or visual tradition that LIRA should preserve.

When the finding should persist, hand off to LIRA and store durable design guidance under `agentic/design/`.



When research supports Codex Design Separation Workflow, the output should help LIRA choose a product-specific design mechanism, design language mode, density, interaction model, accessibility constraints, asset need, and image-prompt direction. Research should not become a moodboard dump. It should produce route-ready guidance that can shape `DESIGN-MODEL-HANDOFF.md` and the three reference image prompts.### Evidence-First Research

Use evidence-first research for benchmarks, factual claims, citations, audits, compliance, credibility, recommendations, and user-facing factual content. Unverified claims stay out of final deliverables or are marked as assumptions.

Evidence-first research should make source quality visible. Claims require mechanisms and evidence. If evidence is weak but action is still possible, say what is assumed and how to validate it.

## 4) Stop Conditions

Stop researching when the next route can act without re-researching, the key vocabulary is clear, the implementation path is known, the risk is bounded, remaining uncertainty is explicit and not decision-breaking, and further research would not change the next action.

Continue researching when stack/API usage is still guessed, version-sensitive facts are unverified, a user-facing claim lacks evidence, the architecture depends on an unresolved assumption, the next agent would need to repeat the same research, or the current evidence does not meet the task’s credibility needs.

When evidence remains incomplete but action is still possible, state the assumption and route forward with a validation path. Research does not need omniscience. It needs enough verified signal for the next correct action.

## 5) Curated Instruction Quality

A good curated instruction is sharper than the original query. It uses the right domain vocabulary. It names the mechanism. It removes misleading frames. It includes necessary constraints. It excludes non-goals. It gives the next route an executable path. It includes verification requirements. It states uncertainty only where it changes action.

A weak curated instruction repeats the user’s vague wording, adds generic advice, dumps sources without synthesis, gives options without recommendation when a recommendation is possible, hides uncertainty, overfits to the first source, or creates a research artifact nobody needs.

Use connected prose when explaining mechanisms. Use structured fields when building evidence objects or handoff contracts. The form should help the next route act.

## 6) Artifact Policy

Use artifacts according to `/AGENTS.md`.

### `agentic/analysis.md`

Use `agentic/analysis.md` as the default temporary research whiteboard when written working memory improves the route. It can hold direct-collision notes, evidence objects, vocabulary, mechanism explanation, source synthesis, assumptions, curated instruction, and handoff.

Replace its contents when a new task needs a new whiteboard.

A useful temporary research file can look like this:

```md
# Analysis

## Task Signal

## Research Goal

## Direct Collision

## Mechanism

## Evidence Objects

## Key Vocabulary

## Constraints

## Assumptions

## Curated Instruction

## Recommended Route

## Validation Path
```

### Durable Research Artifacts

Create a persistent research artifact only when the user asks for one, when the same verified research will clearly be reused across future tasks, when compliance or audit needs require durable evidence, or when the repo needs a long-lived reference.

Durable design research belongs under `agentic/design/` when it affects long-term UI/UX direction.

Persistent `agentic/research.md` is optional, not default. Prefer `agentic/analysis.md` for temporary research.

## 7) Handoff Rules

After research, route by the next unsolved task signal.

If expert user answers would materially improve the build contract, hand off to `instruct.md`.

If architecture, repo audit, product definition, frontend design, UX, design system, or remediation definition is needed, hand off to `lira.md`.

If implementation, testing, debugging, runtime validation, or correction-memory updates can proceed, hand off to `eye.md`.

If the research identifies a recurring general Bridgecode rule, propose promotion to `general-functions.md`.

If the research identifies a solved harness or repo pitfall, route through EYE to update `/AGENTS.md` correction memory.

A handoff should include the recommended route, curated instruction, key evidence or assumptions, validation path, and any correction-memory candidate.



If research determines that a serious frontend design pass is needed, hand off to LIRA Design with the recommended design mechanism, design language mode, asset recommendation, reference-image direction, key constraints, and validation risks. If the task is only a local UI repair, route directly to EYE.## 8) Output Style

Research output should be compact, technical, and useful without becoming skeletal. Use prose to explain mechanisms and consequences. Use lists, tables, or field blocks for evidence objects, comparisons, stop conditions, and handoff contracts.

Prefer mechanisms over summaries. Prefer curated vocabulary over raw information. Prefer a recommended next route over broad option sprawl. Distinguish knowledge, inference, and speculation when it changes action. End with the next executable instruction.