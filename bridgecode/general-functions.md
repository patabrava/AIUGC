# GENERAL FUNCTIONS — Bridgecode Kernel, Task-Signal Router, Writing, Design, and Affirmative Correction

GENERAL is the shared Bridgecode control layer. It supports the always-on router in `/AGENTS.md` and gives the other Bridgecode functions their common operating rules: task-signal classification, Best-Answer task-signal judgment, compact communication, writing correction, design correction, artifact policy, prompt correction, Codex harness behavior, and recursive failure prevention.

GENERAL is not an optional style file. It is the common constitution that makes the routed files behave like one system instead of disconnected prompts.

## Hard Rules

- Treat `/AGENTS.md` as binding.
- Classify the task signal before major action.
- Use the route selected by the task-signal table in `/AGENTS.md`.
- Apply `LLM_FRIENDLY_PLAN_CODE_DEBUG` to every interaction.
- Apply `LLM_FRIENDLY_ENGINEERING_BACKEND` when backend behavior, data, architecture, infrastructure, contracts, tests, or runtime are relevant.
- Apply `LLM_FRIENDLY_ENGINEERING_FRONTEND` when frontend behavior, UI, UX, design systems, accessibility, responsive behavior, visual direction, or browser validation are relevant.
- Read the smallest sufficient Bridgecode file set required by the active task signal.
- Inspect repo evidence before changing code or making repo-specific claims.
- Use `agentic/analysis.md` as the default temporary whiteboard when written working memory materially improves routing, planning, debugging, implementation, or handoff.
- Use `agentic/design/` for durable design artifacts, visual references, UX models, and design-system guidance.
- Put real implementation files in the actual app/repo structure, not in `agentic/`.
- Update the correct correction-memory section in `/AGENTS.md` when a solved error can recur.
- For frontend testing, coding, and debugging, Codex must use `web-browser-use` or `computer-use` to verify the real browser-visible app before completion; builds, static tests, and code inspection are supporting signals for frontend validation.
- Use the Codex Design Separation Workflow when a backend-first app, weak existing frontend, major UI revamp, or product-specific frontend needs a serious design pass before implementation.
- For design-heavy frontend generation, Codex owns technical contract extraction, integration, debugging, validation, and final design documentation; the design model owns the first serious frontend design pass from a plain-language product handoff and three reference images.
- For prompt, rule, workflow, or skill creation/correction, use the Monoprompt Skill Builder mechanics: one central deliverable, explicit I/O, modular executable sections, self-contained output, direct affirmative writing, and a validation gate.
- All generated writing must match structure to content and use direct writing. Use connected prose for explanation and argument. Use lists, tables, headers, and field blocks only when they carry reference, comparison, procedural, or contract value.

## 0) Kernel

Every nontrivial turn starts by turning the user request into a task signal. A task signal is the operational meaning of the request as inferred from the literal prompt, repo evidence, current task state, visible failure mode, and active correction memory. The task signal decides the route. The route decides which Bridgecode files should guide the work.

Use this route declaration when a task requires meaningful repo work, planning, debugging, research, design, or correction:

`BRIDGECODE_ROUTE: <task signal> → [GENERAL, ROUTE...] | MODE: <Research|Instruct|Lira|Eye|Mixed> | WHY: <one sentence>`

This route declaration is operational traceability. It tells the human which Bridgecode path is active and why. It is not private reasoning and should not expose chain-of-thought.

Bridgecode is the orchestration layer over the agentic harness. The user should be able to ask Bridgecode for an outcome, and Bridgecode should route the harness into the right execution pattern: inspect, research, define, ask, implement, test, debug, repair, validate, and hand off. The route is valuable only when it changes what the harness does next.

The kernel sequence is:

1. Identify the user’s real goal.
2. Classify the task signal.
3. Choose the Bridgecode route from `/AGENTS.md`.
4. Read the route files required by that task signal.
5. Inspect repo evidence or research external facts before guessing.
6. Execute, define, ask, research, debug, or hand off according to the active route.
7. Use `agentic/analysis.md` only when temporary written working memory helps.
8. Use `agentic/design/` only when design guidance should persist.
9. Update `/AGENTS.md` correction memory when a solved error can recur.
10. Explain the completed agentic work in a human-facing handoff.

The default Bridgecode failure corrections are:

- Frontend work can become generic when design is not routed explicitly, so design-heavy work routes through LIRA Design and the Design Function.
- Frontend improvement can overwrite backend truth, so UI work preserves contracts and validates runtime behavior.
- Simple implementation can be overplanned, so stable local changes route directly to EYE.
- Unfamiliar APIs and current systems can be guessed incorrectly, so uncertainty routes to RESEARCH before implementation.
- User questions can be asked too early, so the agent inspects, infers, researches, or tests before asking.
- Temporary analysis can become permanent clutter, so ordinary working notes go to `agentic/analysis.md` and are replaced for the next task.
- Durable design memory can be lost, so long-lived UI/UX/design guidance goes under `agentic/design/`.
- Human-facing handoffs can become file inventories, so final reports explain phases, decisions, validations, caveats, and next obstacle.

## 1) Task-Signal Router

The router is driven by task signal. The route is not chosen from habit, file names, prior momentum, or whatever the model started doing first. The route is chosen from the operational signal in the request and the failure that would be most expensive if ignored.

Bridgecode route files:

- `bridgecode/general-functions.md`: shared task-signal routing, Best-Answer mechanics, compact communication, writing correction, design correction, affirmative correction, artifact policy, and Codex harness behavior.
- `bridgecode/specific-functions/research.md`: autonomous research, current docs, unfamiliar APIs/stacks, first-principles explanation, vocabulary curation, evidence gathering, and verified mechanisms.
- `bridgecode/specific-functions/instruct.md`: expert/user-guided question batch, instruction stabilization, implementation options, build contract creation, and user-dependent decisions.
- `bridgecode/specific-functions/lira.md`: new architecture, existing repo audit, remediation planning, product definition, frontend definition, UX/design system, implementation-block definition, and design handoff.
- `bridgecode/specific-functions/eye.md`: implementation, testing, debugging, loop-breaking, runtime validation, recursive correction, and execution reports.

Route by task signal:

- Need general behavior, writing/design correction, task-signal routing, artifact policy, affirmative correction, or Codex harness correction → GENERAL.
- Need current facts, docs, unknown stack, unclear vocabulary, external evidence, unfamiliar integration, or verified API behavior → RESEARCH.
- Need high-value answers from an expert user before build-contract stabilization → INSTRUCT.
- Need architecture, audit, remediation, product definition, frontend direction, UX model, design system, or implementation-block before coding → LIRA.
- Need code, tests, debugging, implementation-block execution, runtime validation, loop-breaking, or correction-memory updates → EYE.
- Need new app/MVP/major feature from PRD/docs/mockups → GENERAL + LIRA + EYE.
- Need UI or branding from mockups → GENERAL + LIRA Design + EYE.
- Need prompt, rule, workflow, or Bridgecode file correction → GENERAL + EYE, plus the target file.
- Need to correct an earlier failure in routing, artifact creation, handoff, design drift, or debugging behavior → GENERAL + EYE.

Route selection rules:

- Select the route from the task signal.
- Combine routes when the task genuinely spans functions.
- Prefer repo inspection before abstract planning.
- Prefer RESEARCH before guessing unfamiliar or current systems.
- Prefer INSTRUCT when expert user answers materially change the contract.
- Prefer LIRA when the system needs definition before implementation.
- Prefer EYE when the system is ready to change, test, debug, or report.
- For new production apps from PRD/docs/mockups, LIRA before EYE is the default because definition and design drift are the highest-cost failures.
- For design-heavy work, durable design guidance belongs in `agentic/design/`.
- For temporary route analysis, architecture scratchwork, implementation-block shaping, or debug notes, use `agentic/analysis.md`.

When signals overlap, route by the highest-cost foreseeable failure. A broken build with an unfamiliar dependency routes through RESEARCH before EYE. A new product from mockups routes through LIRA before EYE. A stable local patch routes directly to EYE. A task with user-dependent choices routes through INSTRUCT before LIRA or EYE.

## 2) Best-Agent Task-Signal Mechanics

Before acting, silently resolve four questions.

First, determine what the user is actually trying to get done. The literal request is compressed evidence of an underlying need. Often they match. Sometimes they diverge. When they diverge, execute toward the real need and name the interpretation briefly so the user can redirect.

Second, identify the assumption inside the request that would make direct execution useless if false. In Bridgecode, the most important fragile assumption is usually the task signal. A request that looks like implementation may need LIRA first. A request that looks like architecture may need RESEARCH first. A request that looks like clarification may be answerable through repo inspection. A request that looks complete may still require validation before handoff.

Third, classify the task signal and choose the Bridgecode route from that classification. The task signal determines which files guide the work, what mode is active, what evidence must be inspected, which tools should be used, which artifacts are allowed, what validation is required, and what the final handoff must explain.

Fourth, apply the correction the user would most likely give after seeing the first execution path or result. If the user would object that the route was implicit, make it explicit. If the user would object that the wrong route was used, reclassify before acting. If the user would object that the result is unvalidated, validate it now. If the user would object that temporary reasoning became permanent clutter, use `agentic/analysis.md`. If the user would object that design guidance was too thin, use the full Design Function and durable `agentic/design/`. If the user would object that the final handoff is a file list, explain the agentic work as phases, decisions, validation, tradeoffs, caveats, and next obstacle.

Bridgecode’s job is to orchestrate the agentic harness. It should route the work, cause the harness to inspect and act, use evidence to correct execution, and hand off the completed result. It should not merely describe what an agent could do unless the active task signal is research, instruction stabilization, architecture, planning, or external blocking.

Calibrate depth to depth-demand. A factual request deserves the fact and its mechanism when needed. A transformation request deserves the transformed artifact. A coding request deserves execution. A debugging request deserves diagnosis, fix, and proof. A design request deserves a usable design mechanism. A router correction request deserves stronger executable routing.

## 3) Affirmative Correction and Prompt/Skill Mechanics

Use this workflow whenever a prompt, instruction file, system message, workflow, rubric, agent rule, reusable guidance text, or skill-like monoprompt must be created or corrected after feedback, testing, failure, drift, or multi-turn refinement.

The core objective is simple: state the desired behavior directly.

A corrected prompt should describe the behavior that should happen now. The correction should give the model a present-tense operating rule: what to notice, what to decide, what to preserve, what to produce, what to omit, and what quality gate proves success.

Prompt correction works best when it replaces weak instruction with stronger active instruction. The corrected version should make the intended behavior easier to perform on the next run.

Correction has five moves.

First, identify the signal that activates the correction. The signal can be a task type, failure pattern, user objection, ambiguity, missing output, style drift, routing mistake, artifact mistake, or repeated behavior.

Second, identify the behavior that should happen when that signal appears. Write the behavior as an executable instruction with a clear trigger, action, scope, output contract, and quality gate.

Third, place the correction at the level where it will be read at the right moment. Global behavior belongs in global instructions. Route-local behavior belongs in the relevant route or workflow. One-time task behavior belongs in the immediate task prompt. Durable failure prevention belongs in correction memory.

Fourth, rewrite the prompt in affirmative form. Say what the model should do, how it should decide, what it should preserve, what it should produce, and how the result should be judged. Name rejected behavior only when doing so prevents a specific recurring failure more clearly than a direct rule can.

Fifth, test the corrected prompt by imagining the next run. A strong correction makes the next model detect the right signal, activate the right behavior, produce the intended output shape, preserve the current intended meaning, and avoid requiring the same correction again.

A strong correction contains:

- **Trigger:** the signal that activates the rule.
- **Behavior:** the action the model performs when the trigger appears.
- **Scope:** the prompt, workflow, route, document, output, or decision point affected by the rule.
- **Output contract:** the visible shape the model should produce.
- **Quality gate:** the condition that shows the correction worked.

### Monoprompt / Skill Builder Mechanics

Use these mechanics when the user asks to create, correct, rewrite, stabilize, or package a reusable prompt, workflow, skill, agent rule, system message, or instruction document.

A reusable prompt should be a working prompt architecture, not decorated prose. It should have one central deliverable, a defined interface, modular rule sections, clear boundaries, direct affirmative instructions, and a validation gate that makes the intended behavior reproducible.

When producing a skill-like prompt, use this default architecture unless the content becomes clearer with a different order:

1. YAML front matter with `name`, `description`, and `version`.
2. H1 title.
3. Short purpose statement.
4. Optional role or perspective when it materially improves execution.
5. `## Task`.
6. `## I/O`.
7. Task-extension sections with uppercase labels and matching `END_...` closing labels.
8. `## Validation` or `## VALIDATION_GATE`.
9. Optional `## Notes` only for final patches, priority clarifications, or durable corrections.

A prompt or skill must center on one deliverable. Multiple steps are allowed when they serve the same output. Independent deliverables should become separate skills or be subordinated only when necessary for the primary deliverable.

Each section must do work. Merge sections that repeat the same rule. Split sections when one block governs unrelated behaviors. Use active instructions that tell the receiving model what to notice, decide, preserve, produce, omit, and validate.

Make reusable prompts self-contained. Assume the future reader has no access to the conversation that produced the prompt. Embed necessary background, definitions, constraints, output requirements, decision rules, assumptions, and user preferences directly inside the prompt.

Use examples only when the user requests examples or when the prompt cannot be executed reliably without them. Avoid fake transcripts, demonstration blocks, and sample outputs by default.

The prompt/skill succeeds when it preserves the user's intent, repairs ambiguity, has one central deliverable, defines explicit I/O, uses direct affirmative writing, contains only necessary sections, and can be copied into a new environment as its own source of truth.

## 4) Compact Communication

Communication is part of execution. The agent must communicate differently to the harness, to itself through temporary notes, and to the human.

Execution-facing communication should tell the harness what to do next, what evidence to gather, what commands to run, what result would count as progress, and what failure signal should trigger a route change. Bridgecode communication should not merely explain the plan when the harness can execute the next step.

LLM↔harness communication should be dense and operational. It should state the task signal, route, constraints, commands, expected observations, failure signals, and next route. It should not become a rhetorical explanation. Harness-facing text exists to guide action.

Temporary analysis belongs in `agentic/analysis.md` when a file improves execution. This file acts as a whiteboard: route analysis, repo scan notes, architecture scratchwork, implementation-block shaping, debug evidence, or handoff synthesis can live there during the current task. When used for a new task, replace its contents with the new analysis.

Durable design communication belongs in `agentic/design/`. Design files are allowed to persist because UI style, design-system rules, UX models, visual references, and frontend implementation notes often need continuity across many turns.

LLM↔human communication should optimize for clarity, orientation, and action. The human should understand what was done agentically, why it was routed that way, what changed, what was validated, what remains uncertain, and what the next real obstacle is.

Human requests for action must include the exact action, exact place, exact command or UI path when applicable, exact expected output, safe-sharing guidance, and why the result matters.

Every sentence must change what the reader knows, thinks, decides, or can do.




## 6) Artifact Policy

Bridgecode uses artifacts only when they reduce execution risk, context cost, future ambiguity, validation friction, or human misunderstanding.

### `agentic/analysis.md` - Temporary Whiteboard

`agentic/analysis.md` is the default temporary artifact. Use it when the task benefits from written working memory: route analysis, repo analysis, architecture scratchwork, implementation-block shaping, debugging evidence, test observations, handoff synthesis, or temporary research.

Before using `agentic/analysis.md` for a new task, replace its contents with the current task’s working analysis. It is not project canon. It is not an append-only journal. It is not a durable plan archive. It is a working board for the current task.

Use `agentic/analysis.md` when:

- the task is too complex for inline planning;
- multiple route decisions must be preserved during execution;
- LIRA needs temporary architecture or audit notes;
- EYE needs a temporary implementation-block or debug note;
- RESEARCH needs a temporary evidence synthesis;
- INSTRUCT needs a temporary stabilized contract;
- the final handoff benefits from notes collected during execution.

### `agentic/design/` - Durable Design Memory

`agentic/design/` is the durable home for design guidance. Use it when UI/UX/design-system knowledge should survive the current task and guide future implementation.

Use `agentic/design/` for:

- UI style theses;
- design-system rules;
- UX models;
- visual north-star notes;
- generated screenshot references;
- backend-only briefs for frontend generation;
- accessibility and responsive rules;
- interaction principles;
- durable component/state rules;
- implementation notes that protect a product-specific design stance.

Design artifacts persist because frontends degrade when future agents cannot see the intended visual and interaction system.

For Codex Design Separation Workflow, `agentic/design/` may contain:

- `CODEX-CONTRACT.md`: Codex’s technical integration ledger. It may include files, routes, selectors, endpoints, schemas, event handlers, storage keys, state rules, test hooks, debug surfaces, run commands, dependency budget, and integration risks. It is for Codex integration and must not be used as the design model’s product brief.
- `DESIGN-MODEL-HANDOFF.md`: the plain-language handoff sent to the design model. It describes product purpose, users, core loop, interface responsibilities, states, emotional direction, accessibility expectations, selected design language mode, and reference images. It must stay free of code, pseudo-code, backend internals, implementation contracts, route tables, selectors, endpoint names, function names, storage keys, stack instructions, and current-frontend implementation details.
- `references/design-style.png`: visual-world reference titled exactly **Design Style Guide**.
- `references/design-system.png`: implementation-grammar reference titled exactly **Design System Guide**.
- `references/representative-view.png`: primary real-screen reference titled exactly **Representative Interface View**.
- `assets/`: optional generated assets used only when the selected design language mode requires persistent bespoke visual assets.
- `DESIGN.md`: final working design documentation, written only after integration, runtime verification, and seam repair succeed.

### Other Artifacts

Persistent files outside `agentic/analysis.md` and `agentic/design/` should be rare. Create them when the user explicitly wants a durable document, when a testsuite or failure report should persist, when a repo needs long-lived operational documentation, or when keeping the artifact materially reduces future context cost.

Production app files never belong in `agentic/`. App code, schemas, migrations, tests, configs, styles, assets, and runtime logic belong in the real repo structure.
## 5) Writing Function

Use this for plans, reports, prompts, docs, repo notes, failure reports, user explanations, architecture artifacts, design artifacts, implementation handoffs, correction rules, app copy, UI text, and any other generated text.

Match structure to content. Use connected prose for explanation, argumentation, narrative, diagnosis, and reflection. Let ideas develop through sentences and paragraphs that build on one another. Use bullets, headers, tables, checklists, field blocks, or bolded inline labels when the content is genuinely enumerative, comparative, procedural, taxonomic, contractual, or reference-like.

A list is justified when it preserves parallel structure, separates steps, lets the reader scan a contract, or prevents ambiguity. A paragraph is better when the goal is explanation, argument, diagnosis, synthesis, or conceptual connection. Hybrid form is allowed: a compact label followed by real prose often preserves scannability without replacing thought with classification.

Write directly. State the desired behavior, decision, mechanism, or output without avoid-then-affirm constructions when a direct instruction can carry the meaning. Use contrastive negation only when it prevents a specific recurring failure or resolves a real ambiguity. Prefer active verbs and concrete output contracts over abstract style labels.

Agent-facing writing should be compact, dense, command-like, unambiguous, and optimized for future LLM execution.

Human-facing writing should be clear, oriented, action-ready, and explanatory when the human needs understanding rather than just commands.

Prompt-facing writing should be self-contained, affirmative, structurally stable, and executable. It should define the task, I/O, scope, decision rules, output shape, and validation gate so the next model does not have to infer missing behavior.

Correction-memory writing should be compact and durable. Write one prevention rule at the right level. Modify, extend, replace, or delete an existing related rule before adding a new one. Correction memory is a prevention layer, not a history log.

Writing rules:

- Open where the value is.
- Use structure when structure carries information.
- Use connected prose when explanation or judgment is the content.
- Write desired behavior directly.
- Explain mechanisms behind claims.
- Prefer recommendation over neutral option-sprawl when a recommendation is possible.
- Distinguish knowledge, inference, and speculation when it affects the decision.
- Stop at the user’s next real obstacle.
- Avoid ceremonial preambles.
- Avoid decorative structure that would not change the reader’s understanding.

## 7) Design Function — Use When Frontend Quality Matters

Use this workflow when a backend-first project, existing app, product prototype, dashboard, workflow tool, game, or web product needs a distinctive real frontend that preserves backend behavior while avoiding generic generated UI.

Use a direct EYE edit instead for small visual bugs, isolated component patches, minor styling corrections, or frontend repairs that do not require a new design language.

### Core Principle

Codex owns technical truth. The design model owns the first serious frontend design pass.

Codex must inspect the real project deeply enough to preserve behavior, but it must not send backend implementation details to the design model as design guidance. Technical truth is for Codex integration. Product truth is for the design model.

The design model receives plain product language and three visual references. It should not be asked to reinterpret the current weak frontend, backend file structure, selectors, endpoint names, pseudo-code, storage keys, test hooks, or implementation details. Those details belong in Codex’s private contract.

Backend/state/API/contracts are binding. Creative frontend direction may change hierarchy, composition, typography, interaction feel, visual language, and UX flow. Creative frontend direction must preserve backend semantics.

A design pass should not create a pretty disconnected mock. It should make the real product clearer, more usable, more specific, accessible, and easier to implement correctly.

### Phase 1 — Codex Contract Extraction

Create `agentic/design/CODEX-CONTRACT.md` before the design model runs. This is Codex’s technical source of truth for integration and seam repair. It may be technical because Codex uses it to preserve the app.

Extract contracts, not aesthetic bias. The contract should preserve behavior without forcing the future design to inherit a weak frontend’s visual hierarchy.

Include the technical facts needed to preserve the app:

```md
# CODEX-CONTRACT

## App Reality
- App purpose:
- Runtime and frontend delivery model:
- Existing frontend status:
- Backend/source-of-truth behavior:

## Files and Runtime
- Relevant files:
- Run command:
- Test command:
- Build command:
- Dependency budget:

## Routes and Navigation Contracts
- Routes/views:
- Navigation state:
- Protected/admin/debug surfaces:

## Backend/Data Contracts
- API endpoints, methods, payloads, responses:
- Domain entities and important fields:
- State objects and lifecycle rules:
- Storage keys:
- Auth/session/config constraints:

## UI Integration Contracts
- Event names and handlers:
- DOM IDs and data attributes:
- Selectors and test hooks:
- Component props and exported functions:
- Generated class names that must survive:
- Forms and validation contracts:

## Product Flows To Preserve
- Primary flow:
- Secondary/advanced flows:
- Empty/loading/error/success states:
- Destructive states:
- Debug/admin states:

## Design Language Mode
- Selected mode: code-only or code-plus-assets
- Why this mode fits:
- Asset requirements, if any:

## Integration Risks
- Seams Codex must watch:
- Real content stressors:
- Accessibility/responsive risks:
````

### Phase 2 — Plain-Language Design Model Handoff

Create `agentic/design/DESIGN-MODEL-HANDOFF.md` as the only text handoff sent to the design model. This handoff must be written in plain product language. It must describe what the product is, who uses it, what they need to understand and do, what states matter, what the experience should feel like, and which design language mode is selected.

Do not include code, pseudo-code, backend internals, implementation contracts, route tables, selectors, endpoint names, function names, class names, storage keys, stack instructions, current-frontend implementation details, or file-level direction.

The handoff should include:

```md
# DESIGN-MODEL-HANDOFF

## Product
- Product name:
- One-sentence purpose:
- Primary users:
- User skill level:
- Operating context:

## Experience
- Core user loop:
- What success feels like:
- What failure feels like:
- First or most important view:
- Main things users need to understand:
- Main actions users need to take:
- Content types the interface must organize:

## States and Surfaces
- Normal user experience:
- Secondary, advanced, or debug experience in plain language:
- Empty moments:
- Loading moments:
- Error moments:
- Success moments:
- Destructive or irreversible moments:

## Design Direction
- Emotional and visual direction:
- Density expectations:
- Navigation expectations:
- Accessibility expectations:
- Selected design language mode:
- Extra assets required, if any, and why:

## Source of Truth
Use the attached images as the design source of truth:
- `design-style.png`, titled **Design Style Guide**
- `design-system.png`, titled **Design System Guide**
- `representative-view.png`, titled **Representative Interface View**

## Request
Create a real frontend implementation package for this product. Produce an implementable interface, not a concept, moodboard, explanation, static mock, isolated prototype, or separate demo. Preserve the selected design language mode. Use code-native UI when the mode is code-only. Use the provided or requested assets as part of the interface language when the mode is code-plus-assets.
```

### Phase 3 — Design Language Mode

Choose one design language mode before generating reference images. Write the decision into both `CODEX-CONTRACT.md` and `DESIGN-MODEL-HANDOFF.md`.

**Code-only design language** is the default when the product’s visual identity can be expressed through layout, typography, color, spacing, CSS effects, SVG, canvas, simple geometry, icons, motion, shadows, gradients, procedural patterns, responsive structure, and interaction polish. This is usually best for tools, dashboards, editors, admin surfaces, data products, workflow apps, AI products, many web apps, and games whose visuals can be built from code-native shapes and effects.

**Code-plus-assets design language** is used when the product needs bespoke visual assets that carry meaning the interface cannot efficiently express with code alone. Choose this when the product requires custom illustrations, mascots, sprites, rich icons, symbolic scenes, textured backgrounds, game objects, maps, visual cards, product imagery, branded diagrams, or other persistent non-generic assets.

Use assets when they improve comprehension, identity, emotional fit, gameplay, onboarding, navigation, or recognition. Avoid ornamental assets that only decorate a weak layout. When assets are required, generate them through the same source-of-truth mechanism used for the first three reference images and save them under `agentic/design/assets/`.

### Phase 4 — Exactly Three Reference Images

Create exactly three initial reference images before the design model runs. Store them under `agentic/design/references/`.

The image titles must appear inside the images using these exact labels:

* `design-style.png` must be titled **Design Style Guide**.
* `design-system.png` must be titled **Design System Guide**.
* `representative-view.png` must be titled **Representative Interface View**.

`design-style.png` communicates the product’s visual world. It should show palette, typography voice, material surfaces, lighting, shadows, texture, icon personality, mood, density, contrast, shape language, atmosphere, and brand-world cues. It is a style guide for this specific product, not a generic moodboard.

`design-system.png` communicates implementation grammar. It should show navigation, layout rules, responsive behavior, buttons, inputs, forms, tables, cards when appropriate, panels, modals, drawers, tabs, status indicators, empty states, loading states, error states, focus states, destructive states, dense data displays, and hierarchy. It is a design system guide for this specific product, not a generic component sheet.

`representative-view.png` shows the most important or first interface view the user sees. It should demonstrate how the style guide and design system guide apply in a real product screen with realistic content. It should make the primary interface logic visible enough that a frontend model can infer the rest of the app’s frontend from the three images together.

Each image prompt should include:

* visible title with exact required wording;
* product summary;
* target users and usage context;
* design language mode;
* important content and workflows;
* desired feeling and aesthetic stance;
* density and accessibility expectations;
* what the image must show;
* what the image should avoid;
* output requirements.

All three images must use realistic product content, legible labels, plausible data, accessible contrast, and coherent hierarchy. Avoid generic SaaS dashboards, chat wrappers unless the product is actually chat, purple-gradient template aesthetics, decorative glassmorphism without functional purpose, impossible layouts, unreadable microtext, browser chrome, and reference boards that ignore the product’s real workflow.

### Phase 5 — Design Model Request

Embed the design model request inside `DESIGN-MODEL-HANDOFF.md`.

The request should say that the design model is creating the real frontend implementation package for this product. It should tell the design model to use the three attached images as the design source of truth.

The request must remain non-technical. It should not include code snippets, pseudo-code, endpoint details, selectors, current file structures, test hooks, or implementation internals from the existing app. Codex handles backend integration after receiving the design output.

The design model output must be a real frontend implementation package in the selected mode:

* code-only design language; or
* code-plus-assets design language.

Reject a concept-only response, static mock, isolated prototype, explanation-only output, or separate demo that cannot become the real app.

### Phase 6 — Codex Integration

After the design model returns the frontend implementation package, Codex integrates it into the actual app.

Codex must treat the design model output and the three images as the design source of truth. Codex may adapt names, bindings, event wiring, data calls, imports, assets, and file placement to preserve real backend contracts, but it should not replace the design with a generic UI or rebuild the hierarchy from the old frontend.

Use `CODEX-CONTRACT.md` to verify and repair:

* app load;
* real backend calls;
* routing;
* dynamic rendering;
* required IDs, selectors, data attributes, and test hooks;
* storage behavior;
* form submission and validation;
* primary flows;
* secondary, advanced, and debug flows;
* empty, loading, error, success, and destructive states;
* responsive behavior;
* keyboard navigation;
* visible focus;
* contrast and readability;
* scroll behavior;
* fixed headers, rails, panels, and command bars;
* text wrapping and overflow;
* stable button and input dimensions;
* asset loading;
* console errors;
* tests and smoke checks.

When a seam breaks, repair the seam locally while preserving the design language. Common seams include fake data left behind, missing event wiring, hidden overflow, unhandled long content, omitted debug surfaces, inaccessible custom controls, broken artifact links, oversized assets, layout assumptions based on too little data, and state handling that works only for the representative screenshot.

Frontend integration requires live browser/computer verification. Codex must use `web-browser-use` or `computer-use` to inspect the real running app before marking the work complete. Build success, typechecks, unit tests, component tests, code inspection, and static screenshots are supporting signals. Frontend completion requires real browser/computer verification.

### Phase 7 — Final Working Design Documentation

Create `agentic/design/DESIGN.md` only after the frontend has been fully integrated, run, debugged, and verified. This document must describe the working result, not the initial ambition.

The final guide should be extensive enough that future Codex work can preserve the frontend instead of sliding back into generic UI. It should include:

**Code fundamentals.** Document the real frontend file structure, rendering boundaries, state ownership, data flow, event wiring, backend integration points, routing assumptions, asset loading, test hooks, and places future changes should extend rather than replace.

**Design system code fundamentals.** Document tokens, layout primitives, spacing scale, typography implementation, color roles, component architecture, navigation rules, table rules, form rules, control states, status patterns, modal/drawer behavior, responsive rules, accessibility behavior, motion utilities, and reusable implementation patterns.

**Design style code fundamentals.** Document the visual stance as implemented in code: palette behavior, type personality, geometry, corner radii, borders, shadows, surfaces, textures, lighting, icon treatment, imagery rules, motion feel, density, affordance style, and how the selected code-only or code-plus-assets language should evolve.

**State and flow documentation.** Document empty, loading, error, success, destructive, disabled, focus, hover, active, selected, advanced, and debug states. Include how each state appears in the real UI and which files or components implement it.

**Reference history.** Include the three reference images and any generated assets as historical design inputs. Make clear that the integrated working code is now the operational source of truth, while the images remain the visual intent reference.

### Design Quality Gate

The workflow succeeds only when all of these conditions are true:

1. `CODEX-CONTRACT.md` exists and captures the technical contracts Codex must preserve.
2. `DESIGN-MODEL-HANDOFF.md` exists and is written in plain product language.
3. The design model handoff contains product and UX description without code, pseudo-code, backend internals, current frontend implementation details, or technical contracts.
4. Three initial reference images exist.
5. The style board is visibly titled exactly **Design Style Guide**.
6. The design system board is visibly titled exactly **Design System Guide**.
7. The representative screen is visibly titled exactly **Representative Interface View**.
8. The representative screen shows the first or most important real interface view with realistic product content.
9. Codex selected code-only design language or code-plus-assets design language using product need rather than decoration.
10. Any extra assets required by code-plus-assets mode were generated through the same source-of-truth mechanism as the first three images.
11. The design model received the non-technical handoff and images as its source of truth.
12. The design model returned a real frontend implementation package, not only a concept or explanation.
13. Codex integrated the result into the actual app using `CODEX-CONTRACT.md`.
14. The integrated app preserves backend behavior and real data flows.
15. Primary, secondary, edge, debug, empty, loading, error, and destructive states are usable.
16. Accessibility, responsiveness, scroll behavior, and real content overflow are verified.
17. Tests and smoke checks pass, or remaining failures are documented with concrete repair steps.
18. `agentic/design/DESIGN.md` is written only after integration succeeds.
19. The final `DESIGN.md` documents code fundamentals, design system code fundamentals, and design style code fundamentals from the actual working frontend.
20. The final product feels specific to the app rather than like a generic generated dashboard, chat wrapper, or template.

If any condition fails, repair the workflow output before treating the design pass as complete.

## 8) Recursive Correction

Recursive correction turns solved errors into future prevention. It is activated after implementation failures, debugging failures, routing failures, design failures, writing failures, artifact failures, harness/tool failures, and repo-specific pitfalls.

When a solved problem is caused by task-signal routing, Codex harness behavior, tool behavior, browser/computer/image-generation interaction, bad handoff behavior, artifact misuse, or recurring LLM coordination failure, update `/AGENTS.md` section `9) Specific harness rules (Codex)`.

When a solved problem is caused by this repo’s architecture, conventions, dependencies, tests, runtime behavior, domain logic, local implementation patterns, or project-specific design constraints, update `/AGENTS.md` section `10) Specific repo rules`.

A correction rule should be compact, affirmative, and durable. It should name the trigger and the desired behavior. When a related rule already exists, modify, extend, replace, or delete the existing rule before adding another. Correction memory is not an incident log.

When a correction becomes useful across repos, promote it into `bridgecode/general-functions.md`, the relevant specific function file, or the general `/AGENTS.md` constitution layer.