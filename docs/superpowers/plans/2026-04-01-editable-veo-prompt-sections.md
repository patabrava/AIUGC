# Editable VEO Prompt Sections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to edit structured VEO prompt sections after auto-generation, with edits rebuilt into the flat `veo_prompt` text sent to VEO 3.1.

**Architecture:** Toggle-to-edit modal on the existing "Maximize Prompt" view. A new `PATCH /posts/{post_id}/prompt` endpoint accepts edited sections, merges them into the stored `video_prompt_json`, and rebuilds `veo_prompt`/`optimized_prompt` using the same `OPTIMIZED_PROMPT_TEMPLATE`. The downstream VEO submission path is untouched.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, Jinja2, htmx, Alpine.js, Supabase (PostgreSQL)

**Spec:** `docs/superpowers/specs/2026-04-01-editable-veo-prompt-sections-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `app/features/posts/schemas.py` | Add `ending_directive`, `audio_block` fields to `VideoPrompt`; add `UpdatePromptRequest` model |
| `app/features/posts/prompt_builder.py` | Parameterize `OPTIMIZED_PROMPT_TEMPLATE`; update `build_optimized_prompt()` to accept all sections; populate new fields in `build_video_prompt_from_seed()` |
| `app/features/posts/handlers.py` | New `PATCH /posts/{post_id}/prompt` endpoint |
| `templates/batches/detail/_post_modals.html` | Edit/view toggle with Alpine.js, textareas, Save/Cancel |
| `templates/batches/detail/_post_card.html` | Expand `prompt_sections` to include all editable fields |
| `static/js/batches/detail.js` | Add `editing` state and save/cancel methods to `promptModalComponent` |
| `tests/test_veo_prompt_contract.py` | New tests for parameterized template and edit rebuild |

---

### Task 1: Add `ending_directive` and `audio_block` fields to VideoPrompt schema

**Files:**
- Modify: `app/features/posts/schemas.py:20-98`
- Test: `tests/test_veo_prompt_contract.py`

- [ ] **Step 1: Write failing test for new schema fields**

In `tests/test_veo_prompt_contract.py`, add:

```python
from app.features.posts.schemas import VideoPrompt, AudioSection


def test_video_prompt_has_ending_directive_field():
    prompt = VideoPrompt(
        audio=AudioSection(dialogue="test", capture=""),
    )
    assert prompt.ending_directive is None


def test_video_prompt_has_audio_block_field():
    prompt = VideoPrompt(
        audio=AudioSection(dialogue="test", capture=""),
    )
    assert prompt.audio_block is None


def test_video_prompt_ending_directive_roundtrips():
    prompt = VideoPrompt(
        audio=AudioSection(dialogue="test", capture=""),
        ending_directive="Custom ending.",
        audio_block="Custom audio block.",
    )
    data = prompt.model_dump()
    restored = VideoPrompt.model_validate(data)
    assert restored.ending_directive == "Custom ending."
    assert restored.audio_block == "Custom audio block."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_veo_prompt_contract.py::test_video_prompt_has_ending_directive_field tests/test_veo_prompt_contract.py::test_video_prompt_has_audio_block_field tests/test_veo_prompt_contract.py::test_video_prompt_ending_directive_roundtrips -v`
Expected: FAIL — `ending_directive` and `audio_block` are not fields on `VideoPrompt`

- [ ] **Step 3: Add fields to VideoPrompt**

In `app/features/posts/schemas.py`, add two new optional fields after `veo_negative_prompt` (line 98, before the `@field_validator`):

```python
    ending_directive: Optional[str] = Field(
        default=None,
        description="Ending directive for video prompt rebuild",
    )
    audio_block: Optional[str] = Field(
        default=None,
        description="Audio block description for video prompt rebuild",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_veo_prompt_contract.py::test_video_prompt_has_ending_directive_field tests/test_veo_prompt_contract.py::test_video_prompt_has_audio_block_field tests/test_veo_prompt_contract.py::test_video_prompt_ending_directive_roundtrips -v`
Expected: PASS

- [ ] **Step 5: Run full existing test suite to verify no regressions**

Run: `pytest tests/test_veo_prompt_contract.py -v`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add app/features/posts/schemas.py tests/test_veo_prompt_contract.py
git commit -m "feat: add ending_directive and audio_block fields to VideoPrompt schema"
```

---

### Task 2: Add `UpdatePromptRequest` schema

**Files:**
- Modify: `app/features/posts/schemas.py:109-118`
- Test: `tests/test_veo_prompt_contract.py`

- [ ] **Step 1: Write failing test for UpdatePromptRequest**

In `tests/test_veo_prompt_contract.py`, add:

```python
from app.features.posts.schemas import UpdatePromptRequest


def test_update_prompt_request_accepts_all_fields():
    req = UpdatePromptRequest(
        character="Custom character",
        style="Custom style",
        action="Custom action",
        scene="Custom scene",
        cinematography="Custom cinematography",
        dialogue="Custom dialogue",
        ending="Custom ending",
        audio_block="Custom audio",
        universal_negatives="Custom negatives",
        veo_negative_prompt="Custom veo negatives",
    )
    assert req.character == "Custom character"
    assert req.dialogue == "Custom dialogue"


def test_update_prompt_request_rejects_empty_fields():
    import pytest
    with pytest.raises(Exception):
        UpdatePromptRequest(
            character="",
            style="x",
            action="x",
            scene="x",
            cinematography="x",
            dialogue="x",
            ending="x",
            audio_block="x",
            universal_negatives="x",
            veo_negative_prompt="x",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_veo_prompt_contract.py::test_update_prompt_request_accepts_all_fields tests/test_veo_prompt_contract.py::test_update_prompt_request_rejects_empty_fields -v`
Expected: FAIL — `UpdatePromptRequest` does not exist

- [ ] **Step 3: Add UpdatePromptRequest to schemas.py**

In `app/features/posts/schemas.py`, after `BuildPromptResponse`, add:

```python
class UpdatePromptRequest(BaseModel):
    """Request to update editable sections of a video prompt."""
    character: str = Field(..., min_length=1, description="Character description")
    style: str = Field(..., min_length=1, description="Visual style")
    action: str = Field(..., min_length=1, description="Action direction")
    scene: str = Field(..., min_length=1, description="Scene description")
    cinematography: str = Field(..., min_length=1, description="Cinematography notes")
    dialogue: str = Field(..., min_length=1, description="Spoken dialogue text")
    ending: str = Field(..., min_length=1, description="Ending directive")
    audio_block: str = Field(..., min_length=1, description="Audio block description")
    universal_negatives: str = Field(..., min_length=1, description="Universal negatives")
    veo_negative_prompt: str = Field(..., min_length=1, description="VEO negative prompt")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_veo_prompt_contract.py::test_update_prompt_request_accepts_all_fields tests/test_veo_prompt_contract.py::test_update_prompt_request_rejects_empty_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/features/posts/schemas.py tests/test_veo_prompt_contract.py
git commit -m "feat: add UpdatePromptRequest schema for prompt editing"
```

---

### Task 3: Parameterize `OPTIMIZED_PROMPT_TEMPLATE` and update `build_optimized_prompt()`

**Files:**
- Modify: `app/features/posts/prompt_builder.py:75-105` (template), `app/features/posts/prompt_builder.py:251-265` (function)
- Test: `tests/test_veo_prompt_contract.py`

- [ ] **Step 1: Write failing test for parameterized template**

In `tests/test_veo_prompt_contract.py`, add:

```python
from app.features.posts.prompt_builder import build_optimized_prompt


def test_build_optimized_prompt_default_output_unchanged():
    """Verify that calling build_optimized_prompt with no extra kwargs produces
    the same output as before parameterization."""
    dialogue = "Bevor du deinen barrierefreien Umbau startest."
    result = build_optimized_prompt(dialogue, negative_constraints=None)
    assert "Character:" in result
    assert "Style:" in result
    assert "Scene:" in result
    assert "Cinematography:" in result
    assert dialogue in result
    assert "38-year-old German woman" in result


def test_build_optimized_prompt_custom_sections():
    """Verify that custom section values override defaults in the output."""
    dialogue = "Test dialogue."
    result = build_optimized_prompt(
        dialogue,
        negative_constraints=None,
        character="A 25-year-old man with dark hair.",
        style="Cinematic drone footage.",
        scene="An open rooftop at sunset.",
        cinematography="Wide-angle lens, slow dolly.",
        ending="He turns away and walks off.",
        audio_block="Studio recording with boom mic.",
    )
    assert "A 25-year-old man with dark hair." in result
    assert "Cinematic drone footage." in result
    assert "An open rooftop at sunset." in result
    assert "Wide-angle lens, slow dolly." in result
    assert "He turns away and walks off." in result
    assert "Studio recording with boom mic." in result
    assert "Test dialogue." in result
    # Default values should NOT appear
    assert "38-year-old German woman" not in result
    assert "blush-pink walls" not in result
```

- [ ] **Step 2: Run tests to verify the custom sections test fails**

Run: `pytest tests/test_veo_prompt_contract.py::test_build_optimized_prompt_default_output_unchanged tests/test_veo_prompt_contract.py::test_build_optimized_prompt_custom_sections -v`
Expected: `test_build_optimized_prompt_default_output_unchanged` PASSES (current behavior), `test_build_optimized_prompt_custom_sections` FAILS (no `character` kwarg yet)

- [ ] **Step 3: Parameterize the template and update build_optimized_prompt()**

In `app/features/posts/prompt_builder.py`, replace the `OPTIMIZED_PROMPT_TEMPLATE` (lines 75-105) with:

```python
_DEFAULT_CHARACTER = (
    "38-year-old German woman with long, light brown hair with natural blonde highlights, "
    "straight with a slight natural wave, parted slightly off-center to the left, falling "
    "softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle "
    "crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown "
    "shade; a straight nose with a gently rounded tip; medium-full lips with a natural "
    "muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft "
    "forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm "
    "light-medium skin tone with neutral undertones and smooth natural skin texture; slim "
    "build with relaxed upright posture."
)

_DEFAULT_STYLE = (
    "Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, "
    "soft flattering indoor light, and natural skin texture."
)

_DEFAULT_SCENE = (
    "A modern, tidy bedroom with blush-pink walls and minimal decor. Bright soft vanity light "
    "and natural daylight from camera-right create an even, flattering indoor look. The "
    "wheelchair is partially visible in the frame."
)

_DEFAULT_CINEMATOGRAPHY = (
    "Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie "
    "distance. The camera is handheld but stable, with only minimal natural movement. The "
    "framing remains consistent throughout the shot without noticeable camera drift or "
    "reframing."
)

OPTIMIZED_PROMPT_TEMPLATE = (
    "Character:\n"
    "{character}\n\n"
    "Style:\n"
    "{style}\n\n"
    "Action:\n"
    "{action_direction}\n\n"
    "Scene:\n"
    "{scene}\n\n"
    "Cinematography:\n"
    "{cinematography}\n\n"
    "Dialogue:\n"
    "\"{dialogue}\"\n\n"
    "Ending:\n"
    "{ending}\n\n"
    "{audio}{negatives_section}"
)
```

Then update `build_optimized_prompt()` (lines 251-265) to:

```python
def build_optimized_prompt(
    dialogue: str,
    negative_constraints: Optional[str] = SORA_NEGATIVE_CONSTRAINTS,
    *,
    prompt_mode: str = "standard_final",
    character: Optional[str] = None,
    style: Optional[str] = None,
    scene: Optional[str] = None,
    cinematography: Optional[str] = None,
    ending: Optional[str] = None,
    audio_block: Optional[str] = None,
) -> str:
    cleaned_dialogue = dialogue.strip()
    contract = _get_prompt_contract(prompt_mode)
    return OPTIMIZED_PROMPT_TEMPLATE.format(
        character=character or _DEFAULT_CHARACTER,
        style=style or _DEFAULT_STYLE,
        action_direction=contract["action_direction"],
        scene=scene or _DEFAULT_SCENE,
        cinematography=cinematography or _DEFAULT_CINEMATOGRAPHY,
        dialogue=cleaned_dialogue,
        ending=ending or contract["ending_directive"],
        audio=audio_block or contract["audio_block"],
        negatives_section=f"\n\n{negative_constraints}" if negative_constraints else "",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_veo_prompt_contract.py::test_build_optimized_prompt_default_output_unchanged tests/test_veo_prompt_contract.py::test_build_optimized_prompt_custom_sections -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `pytest tests/test_veo_prompt_contract.py -v`
Expected: All tests pass — existing tests like `test_veo_prompt_requires_exact_german_dialogue` should produce identical output since defaults match the previous hardcoded values.

- [ ] **Step 6: Commit**

```bash
git add app/features/posts/prompt_builder.py tests/test_veo_prompt_contract.py
git commit -m "feat: parameterize OPTIMIZED_PROMPT_TEMPLATE for editable sections"
```

---

### Task 4: Populate `ending_directive` and `audio_block` in `build_video_prompt_from_seed()`

**Files:**
- Modify: `app/features/posts/prompt_builder.py:142-225`
- Test: `tests/test_veo_prompt_contract.py`

- [ ] **Step 1: Write failing test**

In `tests/test_veo_prompt_contract.py`, add:

```python
def test_build_video_prompt_from_seed_stores_ending_directive():
    script = "Ein einfacher Testsatz."
    prompt = build_video_prompt_from_seed({"script": script})
    assert "ending_directive" in prompt
    assert prompt["ending_directive"] is not None
    assert "After the final spoken word, speech stops completely." in prompt["ending_directive"]


def test_build_video_prompt_from_seed_stores_audio_block():
    script = "Ein einfacher Testsatz."
    prompt = build_video_prompt_from_seed({"script": script})
    assert "audio_block" in prompt
    assert prompt["audio_block"] is not None
    assert "Recorded with a modern smartphone microphone" in prompt["audio_block"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_veo_prompt_contract.py::test_build_video_prompt_from_seed_stores_ending_directive tests/test_veo_prompt_contract.py::test_build_video_prompt_from_seed_stores_audio_block -v`
Expected: FAIL — `ending_directive` key missing or None in output

- [ ] **Step 3: Update build_video_prompt_from_seed() to populate new fields**

In `app/features/posts/prompt_builder.py`, in `build_video_prompt_from_seed()`, add these lines after line 204 (after the `VEO_NEGATIVE_PROMPT` assignment in the `VideoPrompt` constructor) — add the new fields to the constructor:

Change the `VideoPrompt` constructor call (lines 197-205) to:

```python
    # Assemble complete prompt using template defaults
    contract = _get_prompt_contract("standard_final")
    base_prompt = VideoPrompt(
        audio=audio_section,
        universal_negatives=SORA_NEGATIVE_CONSTRAINTS,
        post="",
        sound_effects="",
        optimized_prompt=optimized_prompt,
        veo_prompt=veo_prompt,
        veo_negative_prompt=VEO_NEGATIVE_PROMPT,
        ending_directive=contract["ending_directive"],
        audio_block=contract["audio_block"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_veo_prompt_contract.py::test_build_video_prompt_from_seed_stores_ending_directive tests/test_veo_prompt_contract.py::test_build_video_prompt_from_seed_stores_audio_block -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/test_veo_prompt_contract.py -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add app/features/posts/prompt_builder.py tests/test_veo_prompt_contract.py
git commit -m "feat: populate ending_directive and audio_block in build_video_prompt_from_seed"
```

---

### Task 5: Add `PATCH /posts/{post_id}/prompt` endpoint

**Files:**
- Modify: `app/features/posts/handlers.py:1-20` (imports), append new handler
- Test: `tests/test_veo_prompt_contract.py`

- [ ] **Step 1: Write failing test for the rebuild logic**

The endpoint handler will call `build_optimized_prompt()` with user-provided values. Test the rebuild logic directly (no HTTP needed — the handler is thin):

In `tests/test_veo_prompt_contract.py`, add:

```python
def test_rebuild_prompt_with_edited_sections():
    """Simulate what the PATCH endpoint does: take user edits, rebuild flat prompts."""
    # Start with an auto-generated prompt
    original = build_video_prompt_from_seed({"script": "Originaler Text."})

    # Simulate user edits
    edited_scene = "A bright kitchen with white tiles."
    edited_dialogue = "Neuer bearbeiteter Text."

    # Rebuild veo_prompt (no inline negatives)
    new_veo_prompt = build_optimized_prompt(
        edited_dialogue,
        negative_constraints=None,
        scene=edited_scene,
    )

    # Rebuild optimized_prompt (with inline negatives)
    new_optimized_prompt = build_optimized_prompt(
        edited_dialogue,
        negative_constraints=original["universal_negatives"],
        scene=edited_scene,
    )

    assert "A bright kitchen with white tiles." in new_veo_prompt
    assert "Neuer bearbeiteter Text." in new_veo_prompt
    assert "A bright kitchen with white tiles." in new_optimized_prompt
    assert original["universal_negatives"] in new_optimized_prompt
    # Defaults still used for non-overridden sections
    assert "38-year-old German woman" in new_veo_prompt
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_veo_prompt_contract.py::test_rebuild_prompt_with_edited_sections -v`
Expected: PASS (this uses the already-parameterized function from Task 3)

- [ ] **Step 3: Add the PATCH endpoint**

In `app/features/posts/handlers.py`, add the import for `UpdatePromptRequest` and `build_optimized_prompt` at the top (around line 15):

```python
from app.features.posts.prompt_builder import (
    build_video_prompt_from_seed,
    validate_video_prompt,
    build_optimized_prompt,
)
from app.features.posts.schemas import UpdatePromptRequest
```

Then append the new endpoint after the existing `_maybe_transition_batch_to_prompts_built` function:

```python
@router.patch("/{post_id}/prompt", response_model=SuccessResponse)
async def update_post_prompt(post_id: str, body: UpdatePromptRequest):
    """
    Update editable sections of a video prompt and rebuild flat prompt text.
    Requires that video_prompt_json already exists (prompt must be generated first).
    """
    correlation_id = f"update_prompt_{post_id}"

    try:
        supabase = get_supabase().client

        response = supabase.table("posts").select("id, video_prompt_json").eq("id", post_id).execute()

        if not response.data:
            raise FlowForgeException(
                code=ErrorCode.NOT_FOUND,
                message=f"Post {post_id} not found",
                details={"post_id": post_id},
                status_code=status.HTTP_404_NOT_FOUND,
            )

        post = response.data[0]
        existing_prompt = post.get("video_prompt_json")

        if not existing_prompt:
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Post has no video_prompt_json. Generate the prompt first.",
                details={"post_id": post_id},
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # Rebuild flat prompts from edited sections
        veo_prompt = build_optimized_prompt(
            body.dialogue,
            negative_constraints=None,
            character=body.character,
            style=body.style,
            scene=body.scene,
            cinematography=body.cinematography,
            ending=body.ending,
            audio_block=body.audio_block,
        )
        optimized_prompt = build_optimized_prompt(
            body.dialogue,
            negative_constraints=body.universal_negatives,
            character=body.character,
            style=body.style,
            scene=body.scene,
            cinematography=body.cinematography,
            ending=body.ending,
            audio_block=body.audio_block,
        )

        # Merge edits into existing prompt
        updated_prompt = {**existing_prompt}
        updated_prompt["character"] = body.character
        updated_prompt["style"] = body.style
        updated_prompt["action"] = body.action
        updated_prompt["scene"] = body.scene
        updated_prompt["cinematography"] = body.cinematography
        updated_prompt["universal_negatives"] = body.universal_negatives
        updated_prompt["ending_directive"] = body.ending
        updated_prompt["audio_block"] = body.audio_block
        updated_prompt["veo_prompt"] = veo_prompt
        updated_prompt["optimized_prompt"] = optimized_prompt
        updated_prompt["veo_negative_prompt"] = body.veo_negative_prompt
        # Update audio.dialogue with the edited dialogue
        if isinstance(updated_prompt.get("audio"), dict):
            updated_prompt["audio"]["dialogue"] = body.audio_block
        else:
            updated_prompt["audio"] = {"dialogue": body.audio_block, "capture": ""}

        # Validate the merged prompt
        validate_video_prompt(updated_prompt)

        supabase.table("posts").update({
            "video_prompt_json": updated_prompt,
        }).eq("id", post_id).execute()

        logger.info(
            "video_prompt_updated",
            post_id=post_id,
            correlation_id=correlation_id,
        )

        return SuccessResponse(data={"id": post_id, "video_prompt": updated_prompt})

    except FlowForgeException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "update_prompt_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update video prompt",
        )
```

- [ ] **Step 4: Verify the endpoint is reachable**

Run: `python -c "from app.features.posts.handlers import router; routes = [r.path for r in router.routes]; print(routes); assert '/{post_id}/prompt' in routes"`
Expected: Route list printed including `/{post_id}/prompt`

- [ ] **Step 5: Commit**

```bash
git add app/features/posts/handlers.py tests/test_veo_prompt_contract.py
git commit -m "feat: add PATCH /posts/{post_id}/prompt endpoint for prompt editing"
```

---

### Task 6: Update Alpine.js component for edit mode

**Files:**
- Modify: `static/js/batches/detail.js:10-25`

- [ ] **Step 1: Update promptModalComponent**

In `static/js/batches/detail.js`, replace the `promptModalComponent` function (lines 10-25) with:

```javascript
    window.promptModalComponent = function (postId) {
        return {
            expanded: false,
            editing: false,
            saving: false,
            errorMessage: '',
            postId,
            open() {
                this.expanded = true;
                this.editing = false;
                this.errorMessage = '';
                window.batchDetailExpanded = true;
                document.body.style.overflow = 'hidden';
            },
            close() {
                this.expanded = false;
                this.editing = false;
                this.saving = false;
                this.errorMessage = '';
                window.batchDetailExpanded = false;
                document.body.style.overflow = '';
            },
            startEdit() {
                this.editing = true;
                this.errorMessage = '';
            },
            cancelEdit() {
                this.editing = false;
                this.errorMessage = '';
                // Reset textareas to original values
                this.$el.querySelectorAll('textarea[data-original]').forEach(ta => {
                    ta.value = ta.dataset.original;
                });
            },
            async savePrompt() {
                this.saving = true;
                this.errorMessage = '';
                const fields = {};
                this.$el.querySelectorAll('textarea[data-field]').forEach(ta => {
                    fields[ta.dataset.field] = ta.value;
                });
                try {
                    const resp = await fetch(`/posts/${this.postId}/prompt`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(fields),
                    });
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({}));
                        this.errorMessage = err.detail || 'Failed to save prompt.';
                        this.saving = false;
                        return;
                    }
                    window.location.reload();
                } catch (e) {
                    this.errorMessage = 'Network error. Please try again.';
                    this.saving = false;
                }
            },
        };
    };
```

- [ ] **Step 2: Verify syntax**

Run: `node -c static/js/batches/detail.js`
Expected: No syntax errors

- [ ] **Step 3: Commit**

```bash
git add static/js/batches/detail.js
git commit -m "feat: add edit/save/cancel state to promptModalComponent"
```

---

### Task 7: Update modal template for edit mode

**Files:**
- Modify: `templates/batches/detail/_post_modals.html`
- Modify: `templates/batches/detail/_post_card.html:185-191`

- [ ] **Step 1: Expand prompt_sections in _post_card.html**

In `templates/batches/detail/_post_card.html`, replace the `prompt_sections` definition (lines 185-191):

```jinja
                {% set prompt_sections = [
                    ('Character', prompt.character, 'character'),
                    ('Style', prompt.style, 'style'),
                    ('Action', prompt.action, 'action'),
                    ('Scene', prompt.scene, 'scene'),
                    ('Cinematography', prompt.cinematography, 'cinematography'),
                    ('Dialogue', prompt.audio.dialogue if prompt.audio else '', 'dialogue'),
                    ('Ending', prompt.ending_directive or '', 'ending'),
                    ('Audio', prompt.audio_block or '', 'audio_block'),
                    ('Universal Negatives', prompt.universal_negatives, 'universal_negatives'),
                    ('VEO Negative Prompt', prompt.veo_negative_prompt or '', 'veo_negative_prompt'),
                ] %}
```

Note: The snapshot card's existing loop uses `title, value` — update the loop at lines 195-200 to only use the first two elements:

```jinja
                        {% for title, value, field_name in prompt_sections %}
                        <div>
                            <p class="text-xs font-semibold text-gray-600 uppercase tracking-wide">{{ title }}</p>
                            <p class="mt-1 text-xs text-gray-700" style="display:-webkit-box;-webkit-line-clamp:3;line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;">{{ value or '—' }}</p>
                        </div>
                        {% endfor %}
```

- [ ] **Step 2: Replace modal template with edit/view toggle**

Replace the entire contents of `templates/batches/detail/_post_modals.html` with:

```html
<div
    x-cloak
    x-show="expanded"
    x-transition.opacity
    @keydown.escape.window="close()"
    class="fixed inset-0 z-50 flex items-center justify-center px-4 py-6"
    role="dialog"
    aria-modal="true"
    aria-label="Expanded video prompt"
>
    <div class="absolute inset-0 bg-gray-900/75" aria-hidden="true" @click="close()"></div>
    <div class="relative z-10 w-full max-w-3xl bg-white rounded-xl shadow-2xl border border-gray-200 p-6">
        <div class="flex items-start justify-between gap-4">
            <div>
                <p class="text-xs font-semibold uppercase tracking-wide text-blue-600">Prompt Details</p>
                <h5 class="mt-1 text-lg font-semibold text-gray-900">Video prompt for {{ post.topic_title }}</h5>
                <p x-show="!editing" class="mt-2 text-sm text-gray-500">Same structure, expanded for verification and copy.</p>
                <p x-show="editing" x-cloak class="mt-2 text-sm text-amber-600">Editing mode — modify sections below and save.</p>
            </div>
            <button
                type="button"
                class="inline-flex items-center justify-center rounded-full border border-gray-200 text-gray-600 hover:text-gray-900 hover:border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500"
                @click="close()"
                aria-label="Close expanded prompt"
            >
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
        </div>
        <!-- Error message -->
        <div x-show="errorMessage" x-cloak class="mt-3 p-3 bg-red-50 border border-red-200 rounded-md">
            <p class="text-sm text-red-700" x-text="errorMessage"></p>
        </div>
        <div class="mt-4 space-y-4 max-h-[70vh] overflow-y-auto pr-1">
            {% for title, value, field_name in prompt_sections %}
            <div>
                <p class="text-xs font-semibold uppercase tracking-wide text-gray-600">{{ title }}</p>
                <!-- View mode -->
                <p x-show="!editing" class="mt-1 text-sm text-gray-800 whitespace-pre-wrap">{{ value or '—' }}</p>
                <!-- Edit mode -->
                <textarea
                    x-show="editing"
                    x-cloak
                    data-field="{{ field_name }}"
                    data-original="{{ value or '' }}"
                    class="mt-1 w-full text-sm text-gray-800 border border-gray-300 rounded-md p-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 resize-y"
                    rows="3"
                >{{ value or '' }}</textarea>
            </div>
            {% endfor %}
        </div>
        <div class="mt-4 flex justify-end gap-2">
            <!-- View mode buttons -->
            <button
                x-show="!editing"
                type="button"
                class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-blue-700 border border-blue-200 hover:bg-blue-50 rounded-md focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition"
                @click="startEdit()"
            >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                Edit Prompt
            </button>
            <button
                x-show="!editing"
                type="button"
                class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                @click="close()"
            >
                Close
            </button>
            <!-- Edit mode buttons -->
            <button
                x-show="editing"
                x-cloak
                type="button"
                class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 border border-gray-300 hover:bg-gray-50 rounded-md focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-500 transition"
                @click="cancelEdit()"
                :disabled="saving"
            >
                Cancel
            </button>
            <button
                x-show="editing"
                x-cloak
                type="button"
                class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-green-600 hover:bg-green-700 rounded-md focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500 transition"
                @click="savePrompt()"
                :disabled="saving"
            >
                <svg x-show="saving" class="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
                <span x-text="saving ? 'Saving...' : 'Save Changes'"></span>
            </button>
        </div>
    </div>
</div>
```

- [ ] **Step 3: Verify templates render without syntax errors**

Run: `python -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('templates')); env.get_template('batches/detail/_post_modals.html'); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add templates/batches/detail/_post_modals.html templates/batches/detail/_post_card.html
git commit -m "feat: add edit/view toggle to prompt modal with editable textareas"
```

---

### Task 8: End-to-end verification

- [ ] **Step 1: Run full prompt-related test suite**

Run: `pytest tests/test_veo_prompt_contract.py -v`
Expected: All tests pass (original + new)

- [ ] **Step 2: Run broader test suite to check for regressions**

Run: `pytest tests/ -v --timeout=30 -x`
Expected: No unexpected failures

- [ ] **Step 3: Manual smoke test**

Start the server: `uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`

1. Navigate to a batch in S4_SCRIPTED or S5_PROMPTS_BUILT state
2. Click "Generate Prompt" on a post (if not already generated)
3. Click "Maximize Prompt" — verify all 10 sections are shown
4. Click "Edit Prompt" — verify textareas appear with current values
5. Modify a section (e.g., change Scene text)
6. Click "Save Changes" — verify page reloads with updated values
7. Click "Maximize Prompt" again — verify edits persisted
8. Click "Regenerate Prompt" — verify it resets to auto-generated defaults

- [ ] **Step 4: Final commit (if any fixes needed)**

```bash
git add -u
git commit -m "fix: address issues found during end-to-end verification"
```
