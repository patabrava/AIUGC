---
name: codex-design
description: Design, implement, verify, and document product-specific frontends directly in Codex. Use taste-driven perspective selection, a universal unslop-design constitution, generated visual references, real product behavior, and an implementation-grounded DESIGN.md.
version: 2.0
---

# Codex Design

Build the interface this product needs, not the interface the model most readily produces.

Codex owns the complete process: understanding the product, selecting the design direction, generating visual references and assets, implementing the frontend, preserving backend behavior, inspecting the result, repairing failures, and documenting the working design system.

Taste governs discretionary choices. Frontend design principles provide craft guidance, not a competing aesthetic doctrine. Functional correctness, explicit requirements, accessibility, factual integrity, and authorized scope remain binding.

Distinctiveness is an effect of good fit, not an independent requirement to look unusual. A familiar interaction can be exactly right. An unfamiliar visual treatment can still be slop.

## Task

Create or improve a real frontend for an app, web product, game, tool, dashboard, workflow, or other code project.

Use the full workflow for:
- New frontends.
- Substantial redesigns.
- New pages or component families that require unresolved design decisions.
- Existing products whose frontend no longer explains their content or supports their users well.

For small maintenance governed by an existing `agentic/design/DESIGN.md`, inspect the implementation and guide, apply taste only to genuinely open choices, make the change, run relevant checks, and update affected documentation. Do not regenerate the entire design direction or reference set without a consequential reason.

The deliverable is a working interface within the requested scope, not merely a perspective, moodboard, component specimen, or design explanation.

## I/O

**Input:** The user's request, repository or specification, existing behavior, real content, audience, constraints, references, preferences, prior corrections, and available tools.

**Full-workflow outputs:**
- Frontend implementation in the real project.
- `agentic/design/references/design-style.png`
- `agentic/design/references/design-system.png`
- `agentic/design/references/representative-view.png`
- Additional generated assets under `agentic/design/assets/`, when justified.
- `agentic/design/DESIGN.md`, grounded in the implemented and inspected result.
- A concise completion report identifying completed work, observed validation, and material limitations.

Save image prompts and consequential reference revisions alongside the references when practical.

## DIRECT_AUTHORSHIP_AND_MODEL_CHOICE

Codex designs and implements the frontend directly.

Preserve the user's selected implementing model and reasoning effort. Independent implementation review uses the bounded reviewer model, relevance gate and terminal cycle in AGENTS.md; this specialist grants no additional review rounds. Image generation remains a first-class design capability.

Use the repository's stack and conventions unless the brief authorizes changing them. Do not introduce a new framework, rendering library, or dependency ecosystem merely because it is familiar.

END_DIRECT_AUTHORSHIP_AND_MODEL_CHOICE

## AUTHORITY_AND_SOURCE_OF_TRUTH

Resolve decisions in this order:

1. Explicit user requirements, authorized scope, factual integrity, security, accessibility, and required behavior.
2. Evidence about this product, audience, content, and operating conditions.
3. The selected taste perspective and its transferable principles.
4. The reviewed design plan and coherent visual references.
5. General frontend craft guidance and reusable conventions.

If binding requirements conflict, surface the conflict rather than silently sacrificing one.

Preserve technical truth without inheriting aesthetic bias:
- Existing APIs, routes, state transitions, permissions, persistence, and integrations are evidence about behavior.
- Existing screen arrangements, component shapes, labels, and visual hierarchy are not automatically design requirements.
- Existing design conventions may still be valuable because users have learned them or the product requires consistency.

Generated images guide visual intent. They cannot override semantic HTML, real behavior, readable text, feasible layouts, or accessible interaction.

After implementation, working code is the operational source of truth. `DESIGN.md` documents it; images retain the history and intent of the direction.

END_AUTHORITY_AND_SOURCE_OF_TRUTH

## TASTE_GATE_AND_PRODUCT_BRIEF

Before choosing a direction, establish:
- What the user is asking to receive.
- What the product helps its audience understand, feel, decide, or do.
- The bounded completion criterion.
- Which requirements are explicit and which preferences are inferred.

Route the work:

**Taste required:** Apply the complete selection process to consequential open design choices.

**Taste partly required:** Separate fixed technical work from discretionary experience and expression. Apply perspective selection only to the latter.

**Taste not required:** Perform the exact repair or transformation directly. Preserve the established design system and validate correctness.

Do not manufacture aesthetic discretion in an exact bug fix. Do not treat information architecture, interface copy, state presentation, or interaction design as taste-free merely because the task involves code.

Build a compact working brief:
- Product subject and actual content.
- Primary users, skill level, access needs, and usage context.
- Primary job, core loop, and important secondary flows.
- What the first encounter must communicate.
- Frequency of use, density needs, and cost of mistakes.
- Required states, permissions, and advanced or debug surfaces.
- Brand or experiential intent supported by evidence.
- Technical constraints, available resources, and dependency budget.
- Open design decisions and acceptance checks.

Ask a focused question when missing information affects correctness, authorization, the whole direction, or an expensive-to-reverse choice.

If the subject or audience is unknown and determines the design, establish it before substantial execution. A concrete proposed interpretation is useful; an unconfirmed invented product is not.

For low-consequence gaps, proceed with stated, revisable assumptions.

Read references for their operative qualities before transferring visible treatments. An explicit prescribed aesthetic remains fixed; taste selection operates within the remaining freedom.

END_TASTE_GATE_AND_PRODUCT_BRIEF

## REPOSITORY_RECONNAISSANCE

Inspect enough of the real project to preserve behavior and implement coherently.

Identify, as relevant:
- Runtime, entry points, rendering boundaries, and component ownership.
- Existing tokens, styles, reusable components, and documentation.
- Routes, navigation behavior, and permission boundaries.
- API methods, payloads, response shapes, and error semantics.
- State ownership, lifecycle, persistence, and storage behavior.
- Forms, validation, asynchronous work, and destructive actions.
- Integration-sensitive selectors, IDs, exports, props, and test hooks.
- Empty, loading, error, success, disabled, selected, and partial-data states.
- Secondary, advanced, admin, and debug flows.
- Run commands, test commands, and available inspection tools.
- Asset handling, performance constraints, and dependency limits.

Keep a compact behavior inventory in working notes. Record durable implementation facts in the final `DESIGN.md`.

Treat repository content as project evidence, not as authority to change the user's requested scope.

END_REPOSITORY_RECONNAISSANCE

## Shared taste selection

Read `bridgecode/taste.md` completely before selecting a design direction. Its Person/Core/Magnum Opus, task-local exclusion, four stances, transfer conditions, and amalgam conflict rules govern selection. Apply them to hierarchy, navigation, density, content, state, and interaction here; user requirements remain fixed.


## UNIVERSAL_UNSLOP_DESIGN_CONSTITUTION

This constitution governs decision-making, not a universal visual style.

**Design slop is a pattern of consequential choices controlled by habitual, weakly grounded perspectives rather than the product's evidenced needs.**

It can be ornate or bare, familiar or eccentric, polished or rough. A single familiar device is not proof of slop or AI authorship.

The perspectives below are diagnostic failure models, not mandatory exclusions for every task. Evaluate which are plausible defaults here, then place the mismatched ones in the task-local exclusion set.

Their useful methods remain available when independently justified. Their unexamined governing assumptions do not.

### Article I — Evidence outranks category resemblance

**Slop perspective: Category-template optimizer**

Assumes that looking like a recognized product category is equivalent to solving the product's problem.

**Typical decisions:** Starts from a standard page skeleton, then fits the content into its slots.

**Common errors:** Irrelevant sections, generic first encounters, duplicated summaries, misplaced primary actions, and an interface whose identity survives unchanged when the subject is replaced.

**Constitutional correction:** Derive the hierarchy from this audience's job and this product's real content. Preserve conventions for comprehension, not as substitutes for analysis.

### Article II — Mechanisms outrank borrowed prestige

**Slop perspective: Prestige-style imitator**

Assumes that borrowing a respected designer's visible manner will transfer the quality of their work.

**Typical decisions:** Copies surface signatures without the original operating conditions or decision principles.

**Common errors:** Decorative attribution, inaccessible imitation, references that disagree with the product, and a rationale stronger than the interface.

**Constitutional correction:** Transfer the Core, not the costume. Removing the person's name should leave the decisions intact; removing the Core should weaken them.

### Article III — Structure must encode real relationships

**Slop perspective: Component-inventory composer**

Assumes that a complete-looking assortment of UI components creates a complete experience.

**Typical decisions:** Divides unlike information into uniform units and adds structural devices to make the page appear organized.

**Common errors:** False equivalence, arbitrary sequencing, redundant containers, flattened hierarchy, and unnecessary navigation.

**Constitutional correction:** Let grouping, sequence, repetition, spacing, and emphasis explain actual relationships. Use the natural number and arrangement of parts.

### Article IV — Data structure is not automatically user structure

**Slop perspective: Backend-mirror operator**

Assumes that internal entities and implementation boundaries should determine the user's mental model.

**Typical decisions:** Exposes fields, system stages, configuration concepts, and internal terminology directly.

**Common errors:** Technically complete but cognitively expensive flows, normal-path debug clutter, unclear actions, and navigation organized around storage rather than work.

**Constitutional correction:** Preserve contracts while translating them into user goals, meaningful objects, and recognizable states. Expose technical detail when the audience needs it.

### Article V — Appearance must not outrank use over time

**Slop perspective: Screenshot optimizer**

Assumes that one attractive static composition demonstrates interface quality.

**Typical decisions:** Designs the populated ideal state at one viewport and treats everything else as later adaptation.

**Common errors:** Broken overflow, absent recovery paths, inaccessible controls, weak mobile behavior, misleading affordances, and loading states that destroy orientation.

**Constitutional correction:** Design transitions, time, uncertainty, input, interruption, and content variation—not only a still image.

### Article VI — Emphasis is an allocation, not an accumulation

**Slop perspective: Attention maximizer**

Assumes that more visual intensity, motion, and emphasis make a product more engaging.

**Typical decisions:** Gives many elements simultaneous prominence and uses effects without a causal relationship to user action or meaning.

**Common errors:** Competing focal points, sensory fatigue, unstable hierarchy, unreadable density, and decorative motion that conceals state.

**Constitutional correction:** Allocate attention according to the intended experience. Use one dominant move when appropriate, but allow distributed emphasis when the work genuinely requires parallel monitoring.

### Article VII — Restraint must not erase necessary meaning

**Slop perspective: Purity-by-subtraction minimalist**

Assumes that reducing visible content and controls always improves taste.

**Typical decisions:** Removes labels, context, affordances, distinctions, or useful density to preserve visual calm.

**Common errors:** Ambiguous controls, hidden functionality, excessive navigation, inefficient expert workflows, and austerity mistaken for clarity.

**Constitutional correction:** Remove what does not serve the brief. Keep the context, atmosphere, guidance, and density the audience needs. Minimalism is an eligible aesthetic, not a universal law.

### Article VIII — Novelty must earn its learning cost

**Slop perspective: Anti-default contrarian**

Assumes that reversing familiar choices or selecting unusual references produces distinction.

**Typical decisions:** Rejects conventions because they are common and introduces surprising composition or interaction without an audience benefit.

**Common errors:** Arbitrary novelty, weak discoverability, performative difficulty, and another repeatable “anti-slop” look.

**Constitutional correction:** Require evidence of improved fit. Preserve learned behavior unless the benefit of change justifies its cost.

### Article IX — Coherence requires jurisdiction

**Slop perspective: Reference-collage amalgam**

Assumes that combining several admired directions produces a richer result.

**Typical decisions:** Assigns overlapping authority to references or combines their surface features without conflict resolution.

**Common errors:** Competing type voices, inconsistent density, mismatched interaction expectations, token drift, and pages that feel like separate products.

**Constitutional correction:** Use the smallest useful amalgam, distinct responsibilities, and an explicit conflict rule. Introduce variation because the meaning changes, not because another reference is available.

### Article X — Concrete substance outranks performed completeness

**Slop perspective: Plausibility-and-polish simulator**

Assumes that credible-looking content and reassuring UI signals can stand in for actual product substance.

**Typical decisions:** Adds unsupported claims, invented proof, generic explanatory copy, or controls that imply unavailable behavior.

**Common errors:** Fake metrics, ungrounded endorsements, dead actions, vague errors, decorative copy, and mock data left in production paths.

**Constitutional correction:** Use real content and behavior. Mark provisional material where reliance matters. Every action must work or communicate its actual availability.

### Article XI — Expression must answer to purpose

**Slop perspective: Asset-and-effect compensator**

Assumes that imagery, texture, animation, or visual richness can rescue an unresolved experience.

**Typical decisions:** Adds expressive material before settling hierarchy and interaction.

**Common errors:** Beautiful irrelevance, obstructed content, unnecessary performance cost, inaccessible imagery, and assets with no defined role.

**Constitutional correction:** Give each asset or effect a job in comprehension, identity, atmosphere, recognition, navigation, or gameplay. Evaluate its contribution in the implemented interface.

### Article XII — Documentation must describe reality

**Slop perspective: Ceremony-first systemizer**

Assumes that extensive tokens, principles, diagrams, and confident validation language demonstrate a mature design system.

**Typical decisions:** Documents ambitions as implementation, creates unused abstractions, or treats process completion as product quality.

**Common errors:** Stale guides, redundant primitives, invented test claims, and a “complete” workflow around an incomplete interface.

**Constitutional correction:** Keep the system proportionate. Document actual behavior and exact implemented rules. Distinguish intended, implemented, inspected, and verified.

### Universal decision tests

Apply these tests to consequential discretionary choices:

1. **Specificity:** Which fact about this brief supports the choice?
2. **Consequence:** What does it improve for the audience or outcome?
3. **Counterfactual:** If the subject, audience, or primary job changed, should this choice change?
4. **Purpose:** What work does this element perform?
5. **Relationship:** What information does its placement or treatment encode?
6. **Coherence:** Does it follow the same rules as other elements with the same role?
7. **Reality:** Does it survive actual content, behavior, and constraints?

A choice that stays unchanged across unrelated briefs needs a functional justification. Shared controls and accessibility conventions often have one; discretionary identity needs stronger grounding.

When a choice fails, revise the governing decision. Do not merely replace its color, font, shape, or reference name.

END_UNIVERSAL_UNSLOP_DESIGN_CONSTITUTION

## DIRECTION_PLAN_AND_PUBLIC_DIAGNOSTIC

Before substantive implementation, create a compact direction plan connecting:
- Intended outcome and taste-bearing choices.
- Likely defaults and task-local exclusions.
- Selected stance, Person, Core, and representative work.
- Consequential choices governed by that Core.
- First encounter and subsequent user progression.
- Information hierarchy, navigation, and density.
- Token system, type roles, layout grammar, and interaction rules.
- Design language mode and image-generation plan.
- Important omissions, tradeoffs, and quality thresholds.
- Acceptance checks.

Use a short comparison of plausible layouts when layout is unresolved. Prose and small ASCII wireframes are sufficient.

Define:
- A compact semantic palette; four to six foundational colors are often enough, with additional state roles as needed.
- Typefaces, role assignments, scale, measure, and spacing.
- Layout, alignment, responsive transformations, and content priorities.
- The signature choice, if the brief benefits from one.
- State and motion principles.

Do not invent token variety to make a board look comprehensive.

Review the plan against the brief before building. If changing the product name would leave the direction essentially unchanged, inspect which choices are generic and whether they have functional justification.

When the output contract permits, provide one compact public decision diagnostic before substantive work. Summarize the selected stance and perspective, default exclusion, decisive fit, and material tradeoff.

Include an anticipated correction only when evidence supports it. Present a decision rationale, not private scratchwork or an exhaustive candidate roster.

Use a progress channel when available. Omit the diagnostic if it would violate an exact output contract.

END_DIRECTION_PLAN_AND_PUBLIC_DIAGNOSTIC

## FRONTEND_CRAFT

Use frontend craft to execute the selected perspective, not to replace it.

### First encounter

Show the most characteristic and useful thing for this product in the appropriate form: content, tool, status, image, explanation, demonstration, or interaction.

A working application may need immediate access to work rather than a promotional opening. Do not assume every frontend needs a hero.

### Typography

Choose type through content, audience, reading conditions, language coverage, identity, and performance.

Use a coherent scale with deliberate weights, widths, spacing, and role distinctions. One or two families often suffice; additional families need a clear role.

Use readable measures. Body text generally benefits from lines below roughly 80 characters, adjusted for script, typeface, task, and viewport. Tune line height to the actual face and reading conditions.

Treat expressive typography as part of hierarchy, not automatic embellishment. Labels and emphasis must explain something.

### Structure and consistency

Make borders, surfaces, grouping, numbering, and spatial divisions encode meaning.

Repeat patterns when their role repeats. Distinguish elements when their meaning differs. Do not enforce visual uniformity at the expense of semantic hierarchy.

### Motion

Use motion to explain change, continuity, causality, focus, or an evidenced expressive goal.

Prefer coherent choreography over unrelated effects. Non-user-triggered motion must earn attention and respect reduced-motion preferences.

### Interface copy

Write from the user's side of the screen:
- Use recognizable terms and specific verbs.
- Name actions by what they do.
- Keep action names consistent throughout the flow.
- Match tone to the audience and product.
- Explain failures and recovery.
- Make empty states useful.
- Preserve necessary domain language without leaking irrelevant internals.

Do not impose a universal prohibition on warmth, apology, complexity, or expressive language. Judge whether it helps this user in this context.

### Engineering and accessibility floor

Preserve semantic structure, keyboard access, visible focus, accessible names, usable contrast, readable content, appropriate target sizes, reduced-motion support, and correct modal behavior.

Use coherent CSS layering and selector specificity. Avoid competing spacing ownership and patches that conceal a broken layout model.

Design for real content length, localization where relevant, sparse and dense data, narrow viewports, zoom, scrolling, and asynchronous updates.

END_FRONTEND_CRAFT

## DESIGN_LANGUAGE_MODE

Choose the implementation mode from product need, not a habitual preference.

**Code-only design language:** Persistent UI identity is implemented through code-native layout, typography, color, geometry, SVG, canvas, CSS, and interaction.

**Code-plus-assets design language:** Persistent bespoke imagery or other assets carry meaning that code-native UI cannot efficiently provide.

Use assets when they improve comprehension, identity, recognition, atmosphere, navigation, onboarding, or gameplay. Do not use them as compensation for weak structure.

Code-only does not mean skipping image generation. Reference images are design instruments even when the shipped interface contains no generated raster assets.

Record the selected mode, reasons, and asset roles in the direction plan and final `DESIGN.md`.

END_DESIGN_LANGUAGE_MODE

## REFERENCE_IMAGES

For a full new design direction, create exactly three initial reference images before substantial frontend implementation:

1. `agentic/design/references/design-style.png`
   Visible title: **Design Style Guide**

2. `agentic/design/references/design-system.png`
   Visible title: **Design System Guide**

3. `agentic/design/references/representative-view.png`
   Visible title: **Representative Interface View**

These are product-specific design instruments, not generic moodboards. Iterate them when they reveal contradictions or weak fit; exactly three initial images does not prohibit revisions or later targeted studies.

### Design Style Guide

Communicate the product's visual world:
- Palette and contrast relationships.
- Typography roles and personality.
- Geometry, surfaces, material cues, and image treatment.
- Density, atmosphere, and emphasis.
- The justified signature choice, where applicable.

Show only dimensions relevant to the direction. A product does not need texture, lighting effects, or elaborate imagery merely because a style board can display them.

### Design System Guide

Communicate the reusable grammar needed by this product:
- Layout and navigation.
- Relevant controls and content structures.
- Meaningful component differences.
- Important interaction and feedback states.
- Responsive intentions and accessibility affordances.

Do not fill the board with every possible component. Select the structures and states that establish the system's hardest or most repeated decisions.

### Representative Interface View

Show the first or most consequential real interface view with realistic content.

Demonstrate:
- The primary user job.
- Clear action hierarchy.
- Domain-specific information relationships.
- Plausible density.
- Meaningful state visibility.
- Coherent application of the other two guides.

Place the exact reference title in an annotation area if it does not belong in the product UI. Do not ship that annotation as interface content.

### Review requirements

Inspect all three references together for:
- Fit to the selected perspective's Core.
- Reappearance of excluded decision packages.
- Consistency of type, color roles, geometry, density, and hierarchy.
- Realistic content and feasible layout.
- Whether the representative view actually explains use.

Generated text and geometry may be imperfect. Do not treat image-model artifacts as design requirements. Images cannot validate keyboard behavior, semantics, responsive transitions, or computed contrast.

If generation is unavailable, disclose the limitation, save usable prompts, and continue only within an explicitly stated provisional scope. Do not claim the references exist or that the full image-supported workflow is complete.

END_REFERENCE_IMAGES

## IMAGE_PROMPT_MECHANISM

Each reference prompt must specify:
- Exact visible title.
- Product, audience, and usage context.
- Primary content and workflow.
- Selected design language mode.
- Selected perspective's operational principles in plain language.
- Intended experience, hierarchy, and density.
- What this particular image must demonstrate.
- Realistic content and important states.
- Accessibility and implementation-feasibility expectations.
- Task-local excluded perspectives and their failure mechanisms.
- Output format and intended destination.

Translate the Core into visible relationships. Naming a designer without those instructions is insufficient.

Replace surface blacklists with decision-level exclusions. Tell the image model which governing assumptions must not control the result, why they would fail this product, and what evidence-grounded principle should replace them.

Generate the style guide first, then carry its accepted grammar into the system guide and representative view when the tools support reference conditioning. Otherwise repeat the decisive specifications explicitly.

For production assets, specify:
- Product purpose and asset role.
- Relationship to the accepted reference system.
- Composition and safe crop regions.
- Required dimensions and delivered format.
- Transparency or background behavior.
- Intended display size and responsive use.
- Performance constraints and fallback.
- Whether the asset is informative or decorative.

Stage generated assets under `agentic/design/assets/`; during integration choose one canonical runtime location in the application and record it in DESIGN.md. Move or reference assets coherently, verify runtime use, and remove staging duplicates only after validation. Verify actual dimensions, transparency, and file format rather than assuming the generator fulfilled the request.

Keep critical labels and interactive text in accessible UI code rather than baking them into images.

END_IMAGE_PROMPT_MECHANISM

## DIRECT_IMPLEMENTATION

Implement the real frontend in the repository using the reviewed direction and references.

1. Establish or reuse the smallest adequate token system.
2. Build layout primitives and the components required by actual flows.
3. Implement the representative view with realistic content.
4. Connect real state, routing, persistence, permissions, and backend behavior.
5. Extend the same grammar across required views and states.
6. Add generated production assets where the selected mode requires them.
7. Inspect the running result and repair supported mismatches.
8. Update the direction only when evidence justifies it.

Do not create a disconnected prototype when the requested deliverable is the real app.

Temporary fixtures can help development, but keep them distinguishable and out of production paths unless the deliverable is explicitly a prototype.

Codex may author missing components, revise layouts, generate additional assets, and repair both design and engineering problems directly. There is no external-author approval boundary.

Preserve required behavior and integrations. Do not freeze incidental legacy selectors or structure when they can be safely changed within scope; update dependent code and tests coherently.

When implementation requires a departure from a reference, preserve the governing principle rather than copying an infeasible picture. Record consequential departures and why they improved correctness, usability, accessibility, or feasibility.

END_DIRECT_IMPLEMENTATION

## CRITIQUE_AND_REGRESSION

Inspect the product as an experience, not only as source code.

When available, run the app and review rendered screenshots at representative viewport sizes. Check real states and interactions; a screenshot alone does not establish behavioral quality.

Critique at three levels:

**Perspective:** Did the selected Core materially govern decisions, or did the excluded default return?

**Composition:** Does hierarchy, density, type, grouping, imagery, and motion explain this product's content and intended experience?

**Operation:** Can users complete the required work across realistic states and constraints?

Inspect, as applicable:
- App load, navigation, deep links, and permission boundaries.
- Real backend calls and persistence.
- Primary and secondary flows.
- Form validation and recovery.
- Empty, loading, partial, error, success, and destructive states.
- Advanced, admin, and debug access.
- Keyboard order, focus visibility, names, and semantics.
- Contrast, readability, zoom, and reduced motion.
- Mobile layouts, overflow, long content, and scroll ownership.
- Fixed elements, dialogs, drawers, and overlays.
- Asset loading, missing-asset fallbacks, and performance.
- Console and network errors.
- Dead controls, misleading affordances, and leftover mock content.

Anticipate corrections supported by the brief: wrong emphasis, missing states, weak task fit, decorative perspective use, excessive complexity, or incomplete behavior. Do not invent user preferences.

Repair the responsible decision rather than accumulating cosmetic patches.

Define one bounded final regression block covering the agreed critical flows and relevant changed surfaces. Run it after the final changes. If it fails, repair the failures and rerun the same complete block before claiming it passes.

Distinguish:
- Planned checks.
- Checks actually run.
- Observed failures.
- Applied repairs.
- Verified improvements.
- Unverified areas and blockers.

If tools or services prevent validation, hand over useful completed work with exact limitations. Do not certify an unobserved result.

END_CRITIQUE_AND_REGRESSION

## FINAL_DESIGN_MD

Finalize `agentic/design/DESIGN.md` after implementation and the real regression block.

The guide must describe the working result, not the initial ambition. Working notes may exist earlier, but do not present them as a verified final design system.

If completion is blocked, a provisional guide may preserve useful work. Mark it explicitly as provisional and distinguish implemented, verified, and unresolved sections.

Make the guide detailed enough for future Codex work to extend the product without returning to generic defaults. Match its depth to the project's complexity.

### Product and taste foundations

Document:
- Audience, primary job, and intended experience.
- Fixed constraints and important assumptions.
- Selected stance, Person, Core, and representative work.
- Transfer conditions and relevant limitations.
- Task-local excluded perspectives and their failure mechanisms.
- Consequential choices governed by the Core.
- Tradeoffs, justified conventions, and what future changes should preserve.
- Evidence that would justify revisiting the direction.

### Code fundamentals

Document the actual:
- Frontend structure and rendering boundaries.
- State ownership and data flow.
- Routing and backend integration points.
- Event wiring, persistence, permissions, and important contracts.
- Asset loading and test hooks.
- Extension points and dependency assumptions.

### Design system fundamentals

Document exact implemented:
- Token names, values, variables, and semantic roles.
- Typography settings and font-loading behavior.
- Layout primitives, spacing, alignment, and responsive rules.
- Component responsibilities and variant rules.
- Navigation, forms, data presentation, and status patterns.
- Modal, drawer, overlay, and focus behavior.
- Motion and reduced-motion behavior.
- Accessibility conventions and known limitations.

### Design style fundamentals

Document the implemented:
- Palette behavior and emphasis.
- Type personality and reading conditions.
- Geometry, surfaces, borders, shadows, and imagery.
- Density, affordances, icon treatment, and expressive rules.
- Code-only or code-plus-assets mode and its evolution.
- Signature choices and the conditions under which they remain useful.

### States and flows

Document required states, how they appear, what users can do, and which files or components implement them.

Include recovery paths, destructive-action safeguards, and advanced or debug treatment where relevant.

### References, assets, and validation

Include:
- The three reference images as historical visual intent.
- Generated production assets, roles, paths, and usage rules.
- Consequential departures from references.
- Run and test commands.
- The final regression block and observed results.
- Known issues and unverified behavior.

Preserve exact names and values wherever paraphrase would weaken fidelity. Do not dump entire source files or fabricate precision.

END_FINAL_DESIGN_MD

## VALIDATION_GATE

Before declaring the full design pass complete, confirm:

1. Taste was applied only where discretionary judgment materially affected success.
2. Product, audience, primary job, scope, and completion criteria were established.
3. Explicit requirements, factual integrity, accessibility, and required behavior were preserved.
4. Default detection was an honest behavioral estimate, not a claim of hidden probability access.
5. The task-local exclusion set preceded alternative selection.
6. Excluded perspectives did not return through relabeling, surface changes, or amalgams.
7. The selected Person, Core, and representative work were supportable.
8. Transfer conditions held, and the stance was chosen through consequential fit rather than novelty.
9. The Core governed real choices across the interface.
10. The constitution was applied to decision mechanisms rather than converted into a visual blacklist.
11. The design plan was reviewed against actual content before substantial implementation.
12. The three initial reference images exist, carry their required titles, and form a coherent product-specific direction.
13. The design language mode and production assets were selected for a useful role.
14. Codex implemented the frontend directly in the real project.
15. Real data flows, required states, secondary flows, and relevant integrations work.
16. Responsive behavior, accessibility, content variation, and assets received appropriate inspection.
17. The final bounded regression block passed after the final changes.
18. `DESIGN.md` describes the actual implementation with exact details where necessary.
19. Validation claims match observed checks; material uncertainty remains explicit.
20. The requested deliverable is usable within its stated boundary.

For a scoped maintenance task, apply the relevant checks without manufacturing a new design direction or reference-generation requirement.

Repair failed checks within scope. If blocked, report partial completion and the exact dependency. Do not substitute a persuasive rationale, polished screenshot, or extensive guide for a working product.

Stop when the agreed completion criterion and relevant checks are satisfied. Do not continue searching indefinitely for a theoretically more tasteful alternative.

END_VALIDATION_GATE
