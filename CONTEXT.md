# AIUGC Identity And Scene Generation

This context defines the domain language for AIUGC's actor-consistency workflow. It separates the durable person identity from the per-batch and per-scene assets used to render that person into varied outputs.

## Language

**ActorIdentity**:
A trained, durable person asset that preserves the same actor across different settings, wardrobe, and scene compositions.
_Avoid_: Character LoRA, trained character, prompt-only character

**ActiveActorIdentity**:
The single ActorIdentity currently selected for new batches and scene-reference generation.
_Avoid_: CharacterSnapshot, actor roster

**CharacterSnapshot**:
An immutable copy of the currently active legacy character references stored on a batch at creation time.
_Avoid_: ActorIdentity, live character

**SceneReferenceImage**:
A generated still image that places one ActorIdentity into a specific scene setup for a specific post or shot.
_Avoid_: ActorIdentity, final video

**WardrobeSet**:
A small approved set of clothing looks that can be applied to the ActiveActorIdentity during scene-reference generation.
_Avoid_: Fully open styling, random outfit prompt

**SceneCatalog**:
A controlled set of approved scene types that can be paired with the ActiveActorIdentity during scene-reference generation.
_Avoid_: Freeform scene prompt, unrestricted location prompt

**ScriptIntentMap**:
A deterministic mapping from the approved post script into one allowed scene from the SceneCatalog and one allowed look from the WardrobeSet.
_Avoid_: Freeform prompt interpretation, unconstrained LLM styling choice

**SceneReviewCheckpoint**:
The operator review point after a SceneReferenceImage passes the still gate and before video generation begins.
_Avoid_: Post-video-only correction, pre-generation blind approval

**IdentityGate**:
A validation result that decides whether a still image or video frame matches the canonical ActorIdentity closely enough to pass.
_Avoid_: Prompt quality check, style review

**IdentityGateResult**:
The visible pass/fail outcome of an IdentityGate, including the rejection reason and optional confidence score shown to the operator.
_Avoid_: Silent retry, opaque failure

**CharacterConsistencyMode**:
The existing batch creation mode that will be the only entry point for the ActorIdentity-backed no-drift workflow in the MVP.
_Avoid_: Global default mode, all-route video mode

**ActorTrainingSet**:
The explicit 8-20 image set uploaded by the operator to train the ActorIdentity LoRA used for every later generation.
_Avoid_: Three-image snapshot, implicit auto-expanded training set

**TrainingReadinessGate**:
The rule that keeps CharacterConsistencyMode unavailable until ActorIdentity training is complete and visible training progress has been surfaced to the operator.
_Avoid_: Partial mode access before training, hidden background readiness

**TrainingProgressPolling**:
The MVP mechanism for checking ActorIdentity training status by repeatedly querying Magnific job state.
_Avoid_: Webhook-first progress tracking

**AutoEnableOnTrainingComplete**:
The rule that automatically unlocks CharacterConsistencyMode as soon as polling reports ActorIdentity training completion.
_Avoid_: Manual activation step, extra approval gate

**TrainingProgressDisplay**:
The visible training indicator that shows both progress percentage and the current training phase.
_Avoid_: Percent-only indicator, unlabeled spinner

**ActorSettingsSurface**:
The settings page where the operator uploads the ActorTrainingSet and watches ActorIdentity training progress.
_Avoid_: Batch editor, hidden admin page

**ActorReplacementAction**:
The confirmed settings-page action that replaces or retrains the currently active ActorIdentity.
_Avoid_: Silent overwrite, batch-local actor swap

**LegacyBatchCompatibility**:
The policy that existing CharacterSnapshot-based batches remain usable after ActorIdentity lands, but new work uses the trained identity flow.
_Avoid_: Hard cutover, mixed-mode ambiguity

## Relationships

- An **ActorIdentity** can produce many **SceneReferenceImage** assets
- At any time, the system selects exactly one **ActiveActorIdentity**
- An **ActorTrainingSet** is used to train one **ActorIdentity**
- A **SceneReferenceImage** belongs to exactly one **ActorIdentity**
- A **SceneReferenceImage** may use one approved look from the **WardrobeSet**
- A **SceneReferenceImage** may use one approved scene from the **SceneCatalog**
- A **ScriptIntentMap** chooses the allowed scene and wardrobe before a **SceneReferenceImage** is generated
- A **SceneReviewCheckpoint** happens after the still gate and before video generation
- An **IdentityGate** evaluates a **SceneReferenceImage** or a video output against one **ActorIdentity**
- An **IdentityGateResult** surfaces why an **IdentityGate** passed or failed
- The MVP exposes this workflow only through **CharacterConsistencyMode**
- A **TrainingReadinessGate** must pass before **CharacterConsistencyMode** can be used
- **TrainingProgressPolling** is the initial way the UI learns whether the **TrainingReadinessGate** has opened
- **AutoEnableOnTrainingComplete** opens **CharacterConsistencyMode** without an extra operator click
- **TrainingProgressDisplay** is what the operator sees while training is still in progress
- **ActorSettingsSurface** is the first UI place where training is started and observed
- **ActorSettingsSurface** also shows the current **ActiveActorIdentity**
- **ActorReplacementAction** updates the active identity only after confirmation
- **LegacyBatchCompatibility** keeps old CharacterSnapshot batches valid while new batches use the ActorIdentity flow
- A **CharacterSnapshot** is a legacy batch-scoped reference copy and is not the same concept as an **ActorIdentity**

## Example dialogue

> **Dev:** "If we put the actor in a car for one post and in a bathroom for another, is that still the same **ActorIdentity**?"
> **Domain expert:** "Yes. Inside **CharacterConsistencyMode**, the operator first uploads an **ActorTrainingSet** to train the **ActorIdentity**. The **TrainingReadinessGate** keeps that mode blocked until training progress reaches completion, and the UI learns that state through **TrainingProgressPolling**. The operator sees that state through **TrainingProgressDisplay** on the **ActorSettingsSurface**, which also shows the current **ActiveActorIdentity** and offers an **ActorReplacementAction** with confirmation. Then **AutoEnableOnTrainingComplete** opens the mode, the approved script runs through the **ScriptIntentMap**, that picks an allowed scene and look, the **SceneReferenceImage** must pass the **IdentityGate**, the operator can intervene at the **SceneReviewCheckpoint**, and any rejection is shown through the **IdentityGateResult**. **LegacyBatchCompatibility** keeps the old snapshot batches usable in parallel."

## Flagged ambiguities

- "character" was being used to mean both the legacy three-image batch reference and the new trained identity asset. Resolved: use **CharacterSnapshot** for the legacy batch copy and **ActorIdentity** for the trained durable person asset.
