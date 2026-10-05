# MONOPROMPTING — Reusable Instruction Process

Use this process to create or correct a reusable prompt, workflow prompt, skill, system message, agent rule, rubric, or instruction file. A monoprompt is one self-contained operating contract centered on one deliverable. It may contain several dependent stages, but each stage exists to produce or validate that deliverable.

## Best-Agent Definition

Determine what the future model must accomplish, who or what supplies the input, where the prompt will run, what information will be available there, what ambiguity could make the output useless, how deterministic the output must be, and what observable gate proves success. Preserve the user’s actual intent and vocabulary. Inspect the target environment or existing instruction file when available instead of inventing constraints.

Default to transferable principles expressed as a compact operating constitution. Add tighter schemas, ordered stages, fixed fields, or examples when the reuse environment, evaluation method, or known failure pattern requires them. Examples are a last-mile control mechanism.

## Prompt Contract

Write the prompt so it can operate without the conversation that produced it. Include the necessary role or perspective only when it changes execution. Define the central task, accepted inputs, required output, relevant context, constraints, decision rules, allowed autonomy, user-decision boundaries, failure behavior, and validation gate. State what the model should notice, decide, preserve, produce, and verify in direct affirmative language.

Use the smallest structure that makes the contract executable. Most monoprompts need a title and purpose, `Task`, `Inputs`, `Output`, relevant decision or process sections, and `Validation`. Add YAML front matter only when the target skill or tool requires metadata. Add templates or exact schemas when downstream parsing or evaluation requires them. Do not create ornamental sections, mirrored summaries, fake conversations, or multiple independent deliverables inside one prompt.

## Process

Research the target mechanism or environment only when current or unfamiliar behavior affects the prompt. Resolve user-dependent contract choices through the active processflow’s Q&A stage. Plan the prompt around one output and its quality gate. Draft the full self-contained instruction once, then review every section for unique operational value: merge repetition, remove model-known explanation that does not correct a real failure, strengthen vague adjectives into observable behavior, and keep context that the receiving model cannot otherwise know.

When a prompt must support autonomous agent or harness work, embed Best-Agent judgment at its decision points: pursue the real outcome, inspect available evidence, identify the material fragile assumption, protect the highest-cost boundary, apply the likely correction before first execution, ask only at genuine user decision boundaries, and continue until the deliverable is validated or a precise external blocker remains.

## Prompt Correction

When correcting an existing prompt, identify the triggering signal, the desired future behavior, the scope where it should apply, the visible output contract, and the quality gate. Rewrite the smallest durable rule set that makes the next run behave correctly. Merge with the corresponding rule, replace obsolete wording, and remove contradictions or repeated corrections. Describe the desired behavior directly. Mention rejected behavior only when the contrast prevents a proven ambiguity more clearly than an affirmative rule can.

Place a correction at the narrowest level that will reliably be read when triggered. Universal behavior belongs in the system or shared instruction layer, route behavior in the relevant process, repo behavior in verified code, tests, and `agentic/architecture.md`, and one-time task constraints in the active request. Do not promote a local workaround into a universal rule without evidence that it generalizes.

## Writing and Output

Use connected prose for mechanisms, reasoning criteria, and context. Use lists, fields, or tables only where parallel requirements, schemas, steps, or comparison need independent reference. Keep agent-facing language dense, explicit, and executable. Use consistent terms for the same action and distinguish required, optional, and inferred inputs when that affects behavior.

Deliver the finished prompt as the primary output. Add rationale, alternatives, or commentary only when the user requested them or when a material assumption must be surfaced. If the prompt belongs in the repo, update the intended canonical file rather than creating parallel versions unless versioned coexistence is explicitly required.

## Validation Gate

Before delivery, verify that the monoprompt has one central deliverable; is self-contained in its target environment; defines input and output clearly; preserves required context and user preferences; grants enough autonomy without hiding user decision boundaries; uses direct affirmative instructions; contains no repeated or contradictory rules; uses examples only when justified; specifies observable completion; and can be followed without reconstructing the originating conversation. Simulate representative and edge inputs mentally or in a disposable harness run when doing so materially improves confidence, then delete simulation residue after incorporating the correction.
