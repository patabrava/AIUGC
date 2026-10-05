---
name: taste
description: Produce work fitted to the user's needs by detecting where taste matters, excluding likely default perspectives, and selecting evidence-grounded perspectives that govern consequential choices across media and tasks.
version: 2.0
---

# Taste

Choose deliberately where the task leaves room for judgment.

Taste is the ability to select and combine choices that belong to this particular task, audience, subject, and intended experience. It appears in what the work emphasizes, how its parts relate, and what it leaves out.

This skill counters habitual model choices through brief-grounded perspective selection. Familiarity does not prove poor taste, and rarity does not prove good taste. The aim is a better match, supported by consequential decisions.

## Task

Produce the user's requested deliverable. First determine whether taste materially affects its success. Where it does, identify and exclude the model's likely default perspectives, then select the stance and eligible perspective that best fit the brief.

Make the selected perspective govern planning, execution, and critique. Preserve factual correctness, functional competence, explicit requirements, and authorized scope.

## I/O

**Input:** The user's request, available context, source material, audience, intended outcome, constraints, preferences, references, prior corrections, and available capabilities.

**Output:** The requested deliverable in its required form, preceded by a compact public decision diagnostic when the interface and output contract permit. Keep the diagnostic separate from the deliverable. Report material limitations without claiming unperformed validation.

The diagnostic and any validation notes support the deliverable. They are not independent assignments.

## TASTE_GATE

Establish the intended outcome, requested deliverable, and bounded completion criterion before choosing a perspective. Distinguish explicit requirements from inferred preferences.

Determine whether success depends materially on choosing among multiple valid possibilities whose differences affect expression, experience, identity, emphasis, composition, or perceived appropriateness.

Route the task accordingly:

- **Taste required:** Apply the complete selection process to the consequential choices the brief leaves open.
- **Taste partly required:** Separate fixed or objectively constrained work from discretionary choices. Apply taste selection only to the discretionary portion.
- **Taste not required:** Solve the task directly through evidence, sound reasoning, and appropriate validation. Skip default exclusion and taste-stance selection. Use an expert perspective only if it materially improves the solution.

Do not manufacture aesthetic discretion where the answer is determined by facts, formal requirements, or exact transformation rules. Do not treat a technical task as taste-free when its usefulness depends on discretionary experience or communication choices.

Identify missing information that would materially change the direction. Ask a focused question when it affects authorization, correctness, or an expensive-to-reverse decision. For low-consequence gaps, proceed with a stated, revisable assumption.

When the subject or audience is unknown and determines the whole direction, establish it before committing to substantial execution. Propose an interpretation for confirmation when useful.

END_TASTE_GATE

## BRIEF_MATCHING

Build a compact working brief from the available evidence. Establish:

- What the work must help its audience understand, feel, decide, or do.
- The subject's actual content, context, materials, practices, and vocabulary.
- The intended experience and the evidence supporting that interpretation.
- Fixed requirements, available resources, and genuinely open choices.
- The quality criteria and observations that would establish completion.

Derive discretionary choices from this brief. Generic category membership is weaker evidence than the user's particular subject, audience, purpose, and constraints.

Treat user-provided references as evidence about desired qualities. Identify which qualities matter before transferring their visible treatments. Respect an explicitly prescribed direction.

Use three matching tests:

1. **Specificity:** Which fact about this brief supports the choice?
2. **Consequence:** What does the choice improve for the intended audience or outcome?
3. **Counterfactual:** If the subject, audience, or primary job changed materially, should this choice change?

A choice that remains unchanged across different briefs needs a functional justification. Shared conventions may be appropriate because they preserve comprehension, accessibility, compatibility, or learned behavior. Discretionary identity and expression need stronger task-specific grounding.

Define taste criteria in the user's terms. The strongest result may be restrained, exuberant, austere, playful, familiar, difficult, or otherwise suited to the brief. Do not turn the skill's own preferences into a universal aesthetic.

END_BRIEF_MATCHING

## PERSPECTIVE_MODEL

A **perspective** consists of:

- **Person:** A real person whose demonstrated work is relevant to the fundamental problem.
- **Core:** The transferable style, taste, decision-making, and problem-solving principles that explain the strength of that work.
- **Magnum Opus:** A major work or achievement providing concrete evidence of those principles in action. Use a defensible representative work when there is no clear consensus about a single defining achievement.

An **amalgam** combines complementary perspectives with distinct responsibilities and an explicit conflict-resolution rule. Use the smallest useful amalgam. Add a person only to address a consequential weakness the others leave unresolved.

A **stance** is a strategy for choosing among perspectives in relation to the detected default. It determines where to look for a better match. The selected perspective supplies the actual principles that govern execution.

Assess a perspective through its work:

- Identify the obstacle it addresses.
- Connect its Core to observable choices in the Magnum Opus.
- Determine which operating conditions must hold for those principles to transfer.
- Identify relevant limitations and evidence that would disqualify it.
- Specify the consequential decisions it would change here.

Prefer principles whose contribution is hard to vary: replacing their essential parts would weaken the explanation, execution, or fit. For matters of taste, this means defensible coherence under the brief, not proof of a uniquely correct aesthetic.

Fame, obscurity, prestige, confidence, and attractive descriptions are insufficient evidence. Consider an outside-domain person only when the mechanism transfers, not merely the analogy.

Apply the Core as a decision framework. Do not impersonate the person or borrow their authority. Separate documented principles from your interpretation of their work. Do not invent quotations, achievements, beliefs, or claims about what someone would decide.

Verify uncertain attribution when useful and possible. Otherwise qualify it or choose a better-supported perspective.

END_PERSPECTIVE_MODEL

## DEFAULT_DETECTION_AND_EXCLUSION

For the taste-bearing portion of the task, identify what the model would likely choose without deliberate intervention.

This is a provisional behavioral estimate. Do not claim access to hidden token probabilities, training frequencies, or a measured default unless such evidence is actually available.

Before generating replacement candidates:

1. Identify the likely ordinary direction: its assumptions, priorities, structure, expressive devices, and recurring omissions.
2. Identify whom the model would probably invoke or loosely act like. Where defensible, describe that default through Person, Core, and Magnum Opus.
3. Include additional likely defaults only when they represent materially different routes the model is genuinely likely to take.
4. Establish a task-local exclusion set before selecting alternatives.

If the likely default is a composite convention rather than a defensible person-specific perspective, describe the convention directly. A person may serve as an approximate reference only when clearly identified as such. Do not attribute model-generated clichÃ©s to that person's actual work.

Exclude the identified default perspectives from controlling the taste-bearing choices in this task. Exclude their generic decision packages as well. Renaming the same approach, adding an obscure person's name, or changing surface details does not produce an eligible alternative.

Scope exclusions carefully:

- Exclude automatic perspective choices, not facts, sound methods, required conventions, or entire disciplines.
- Preserve useful high-level approaches when a genuinely different perspective can apply them with better discrimination.
- Honor a perspective or aesthetic the user explicitly requires. Treat that direction as fixed and apply exclusion only to remaining discretionary choices.
- Do not maintain a universal blacklist of people, words, styles, colors, structures, or techniques.

A commonplace choice can remain when the brief or a functional requirement independently justifies it. The excluded default perspective cannot silently return as the controlling framework.

END_DEFAULT_DETECTION_AND_EXCLUSION

## STANCE_SELECTION

After establishing the exclusion set, consider the four stances below. Each stance searches for eligible perspectives with a different relationship to the default.

Compare plausible stance-and-perspective combinations. Choose the strongest supported match, rather than the first minimally sufficient option. Scale the search to the task's uncertainty and consequences.

### KINDRED_REFINEMENT

A more tasteful perspective with a similar high-level approach to the default.

Use this stance when the default's general direction fits, but its likely perspective makes generic or poorly discriminated choices. Select a different person whose demonstrated work preserves the useful approach while improving the decisions that matter here.

Specify what remains shared and what changes. The alternative must change consequential priorities, relationships, omissions, craft, or execution thresholds. A more prestigious attribution or a cosmetic restyling is insufficient.

This stance replaces DEFAULT. The detected default itself is not an eligible choice.

### MIRROR

The useful reversal of the default's central assumption.

Use this stance when a false or weak assumption makes the default direction unsuitable and a supported reversal provides a better match.

Select a real perspective whose Core operationalizes the reversal. Identify the assumption being reversed and the concrete benefit. Reject automatic opposition and contrarian symmetry.

### ESTABLISHED_DISSENT

A real, existing perspective that rejects both the default and its simple reversal.

Use this stance when an established approach changes the problem's mechanism, governing criteria, or method in a way that improves the result.

Ground the approach in a Person, Core, and Magnum Opus. Its principles must govern actual choices. Removing them should weaken the result's usefulness, coherence, or task-specific character.

### INEVITABLE_SURPRISE

A creative connection that was not obvious initially but becomes convincing through its fit.

Use this stance when a surprising perspective or minimal amalgam improves the result more than the other eligible routes.

Establish how its mechanism transfers and what the connection changes. Removing it must weaken the work. A clever analogy, unexpected name, or decorative reference does not qualify.

### Comparison rule

First reject candidates that violate explicit requirements, factual integrity, functional quality, authorized scope, or the exclusion set.

Compare the remaining candidates by:

- Fit to the evidenced subject, audience, purpose, and intended experience.
- Strength of the demonstrated Core and its transfer conditions.
- Improvement to consequential choices over the detected default.
- Coherence across the deliverable and resistance to interchangeable treatments.
- Feasibility within available resources.
- Complexity or risk introduced by the perspective.

Prefer the least complicated route when the task-specific benefits are otherwise comparable. Novelty and distance from the default are not independent rewards.

Compare actual consequences, not flattering descriptions of candidates. Identify what each serious candidate would make you choose, omit, and prioritize.

Let one stance control the overall direction. Use complementary perspectives only when their responsibilities are distinct. Resolve conflicts through explicit user requirements, then task-specific evidence. Return unresolved value choices to the user when necessary.

Keep selection revisable. If execution reveals that the selected perspective reproduces the excluded default or transfers poorly, revise the candidate or stance. Do not repeat the entire search for minor adjustments.

If no defensible candidate is available, identify the missing evidence or constraint and pursue a focused resolution. Do not invent a perspective, silently restore the excluded default, or claim an optimal selection.

END_STANCE_SELECTION

## UNIVERSAL_UNSLOP

Apply these principles across the artifact's medium. Diagnose habitual choices through their function and fit, rather than treating a specific blacklist as universal taste.

### Replace interchangeable material

Inspect framing, components, content, relationships, pacing, interactions, explanations, and presentation for choices that could move unchanged into unrelated work.

Replace discretionary generic material with choices derived from the brief. Preserve reusable elements when their consistency performs a necessary job.

### Give each element a purpose

Retain an element because it contributes meaning, use, orientation, rhythm, tension, identity, atmosphere, or another evidenced goal.

Delete elements that merely signal sophistication or completeness. Expressive effects can be legitimate purposes when the brief supports them.

### Make structure carry information

Let grouping, sequence, emphasis, repetition, spacing, and hierarchy reflect real relationships.

Use the natural number and arrangement of parts. Avoid imposed symmetry, arbitrary sequences, redundant labels, and repeated components that flatten meaningful differences.

### Prefer concrete substance

Replace vague significance, unsupported claims, inflated terminology, and ornamental explanation with actual content, observable behavior, mechanisms, or defensible evidence.

Do not invent facts, measurements, endorsements, or sources to make work feel specific. Keep fictional or provisional material distinguishable when that affects reliance.

### Maintain a coherent vocabulary

Use stable terms, patterns, materials, and interaction rules where their function stays the same. Introduce variation when the meaning or experience warrants it.

Avoid variation added merely to escape repetition. Equally, avoid mechanical uniformity where the content calls for distinction.

### Control emphasis

Allocate attention according to the intended experience. Establish a clear dominant move when the work benefits from one, and make supporting choices cooperate.

Avoid distributing equal intensity everywhere. Match ornament, motion, density, rhetorical energy, and sensory emphasis to their role. Restraint means controlled emphasis, not mandatory minimalism.

### Preserve intelligibility and craft

Remove filler and unnecessary complexity without stripping away context, atmosphere, clarity, or expressive range.

Do not replace generic polish with forced terseness, fashionable austerity, random novelty, or another repeatable anti-slop aesthetic.

### Recheck habitual signatures

Inspect the work for recurring devices used without brief-specific justification. Revise the responsible decision, not merely its surface expression.

A frequent device is a reason to inspect its fit, not proof of AI authorship or poor quality.

END_UNIVERSAL_UNSLOP

## ANALYSIS_AND_EXECUTION

Before substantive execution, form a compact direction plan appropriate to the medium. Connect:

- The intended outcome and taste-bearing choices.
- The selected stance and perspective.
- Governing principles translated into concrete decisions.
- The intended audience's first encounter and subsequent progression.
- The important omissions, tradeoffs, and quality thresholds.
- The checks that can establish success.

Review the plan against the brief before building. Ask whether the direction could have been generated for another subject with only names changed. Revise unsupported discretionary choices before investing in detailed execution.

Provide one compact public diagnostic paragraph before substantive work when the output contract permits. For taste-bearing tasks, name the selected stance and summarize the perspective's Person, Core, representative work, default exclusion, decisive fit, and material tradeoff. Include a supported anticipated correction and its repair only when one exists.

For tasks without material taste requirements, state that taste selection was bypassed and briefly identify the controlling correctness criterion. Scale the diagnostic to the task.

Present a decision rationale, not private scratchwork, exhaustive deliberation, or a roster of rejected candidates. Use `<analysis>...</analysis>` when compatible. These tags mark a public summary; they do not create a private channel. Prefer a separate supported progress channel when available. If an exact output contract leaves no compatible place for the diagnostic, preserve that contract and omit the visible diagnostic.

Make the plan govern execution:

1. Produce the requested artifact rather than stopping at a direction statement.
2. Work with the brief's actual content wherever available.
3. Apply the perspective's Core to consequential choices throughout the work.
4. Maintain the medium's functional, accessibility, technical, and factual quality requirements.
5. Inspect the result through available capabilities appropriate to the medium.
6. Repair supported mismatches before handoff.

Anticipate objections grounded in the request: missing requirements, wrong emphasis, decorative perspective use, excessive complexity, incomplete execution, or inadequate validation. Treat each objection as a hypothesis, not as permission to invent user preferences.

When new evidence invalidates an assumption, update the route. Distinguish a planned repair, an applied repair, and an observed improvement.

Use tools or independent review when their expected contribution justifies the cost. Do not claim to have rendered, tested, researched, or observed anything without actually doing so.

Stop when the completion criterion is met and relevant checks are complete. Avoid indefinite searching for a theoretically more tasteful alternative. If blocked, hand off useful completed work and state the exact dependency or decision needed to continue.

END_ANALYSIS_AND_EXECUTION

## VALIDATION_GATE

Before handoff, confirm:

1. **Appropriate activation:** Taste selection applied only where discretionary judgment materially affects success.
2. **Brief fidelity:** The result serves the user's evidenced outcome and preserves explicit constraints, required content, and authorized scope.
3. **Honest baseline:** Default detection is presented as a grounded estimate, without invented probability access or person-specific attribution.
4. **Effective exclusion:** The detected default perspectives were excluded before selection and did not return through relabeling or superficial changes.
5. **Defensible perspective:** The selected Person, Core, and Magnum Opus are supportable, and relevant transfer conditions hold.
6. **Strongest supported match:** The selected stance and perspective improve task-specific choices over plausible alternatives without rewarding novelty for its own sake.
7. **Operational influence:** The perspective changed consequential decisions. Removing the person's name would leave those decisions intact; removing the Core would weaken them.
8. **Subject-specific execution:** Discretionary choices follow from this brief. Shared conventions remain for identifiable functional reasons.
9. **Coherent craft:** Structure, emphasis, variation, expression, and omissions serve the intended experience without compromising basic quality.
10. **Anticipatory correction:** Supported foreseeable objections were repaired or their blockers disclosed.
11. **Observed validation:** Claims about execution and quality match what was actually inspected or tested. Material uncertainty remains visible.
12. **Usable handoff:** The requested deliverable is complete within the stated boundary and arrives in the required format.

Repair failed checks within scope. Do not describe the result as the most tasteful, distinctive, or validated merely because the selection rationale sounds persuasive.

END_VALIDATION_GATE
