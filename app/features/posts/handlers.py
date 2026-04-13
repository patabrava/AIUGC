"""
Lippe Lift Studio Posts Handlers
FastAPI route handlers for post operations.
Per Constitution § V: Locality & Vertical Slices
"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.adapters.supabase_client import get_supabase
from postgrest.exceptions import APIError
from app.core.errors import FlowForgeException, SuccessResponse, ValidationError, ErrorCode
from app.core.logging import get_logger
from app.features.posts.prompt_builder import build_video_prompt_from_seed, validate_video_prompt, build_optimized_prompt
from app.features.posts.schemas import UpdatePromptRequest
from app.features.batches.state_machine import reconcile_batch_video_pipeline_state
from app.core.states import BatchState

logger = get_logger(__name__)

router = APIRouter(prefix="/posts", tags=["posts"])


class UpdateScriptRequest(BaseModel):
    """Request to update post script."""
    script_text: str = Field(..., min_length=1, max_length=900, description="Script text")
    post_type: Optional[str] = Field(
        default=None,
        max_length=120,
        description="Optional freeform post type (manual drafts only)",
    )


class UpdateScriptReviewRequest(BaseModel):
    """Request to update post script review state."""
    action: str = Field(..., description="Review action: approved, removed, or reset")


def _parse_json_document(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value or {}


def _should_use_legacy_32_visuals(post: dict) -> bool:
    target_length_tier = post.get("target_length_tier")
    if target_length_tier == 32:
        return True
    video_metadata = _parse_json_document(post.get("video_metadata"))
    if isinstance(video_metadata, dict) and video_metadata.get("target_length_tier") == 32:
        return True
    seed_data = _parse_json_document(post.get("seed_data"))
    return seed_data.get("target_length_tier") == 32


def _build_edited_veo_prompt(
    *,
    existing_prompt: dict,
    payload: UpdatePromptRequest,
) -> str:
    existing_veo_prompt = str(existing_prompt.get("veo_prompt") or "").strip()
    submitted_veo_prompt = payload.veo_prompt.strip()
    if submitted_veo_prompt and submitted_veo_prompt != existing_veo_prompt:
        return submitted_veo_prompt
    return build_optimized_prompt(
        payload.dialogue,
        negative_constraints=None,
        prompt_mode="standard_final",
        character=payload.character,
        action=payload.action,
        style=payload.style,
        scene=payload.scene,
        cinematography=payload.cinematography,
        ending=payload.ending,
        audio_block=payload.audio_block,
    )


def _load_post_seed_data(post_id: str, supabase_client):
    """Fetch post plus normalized seed data for localized S2 review updates."""
    response = (
        supabase_client.table("posts")
        .select("id, batch_id, post_type, seed_data, video_prompt_json")
        .eq("id", post_id)
        .execute()
    )

    if not response.data:
        raise FlowForgeException(
            code=ErrorCode.NOT_FOUND,
            message=f"Post {post_id} not found",
            details={"post_id": post_id}
        )

    post = response.data[0]
    seed_data = _parse_json_document(post.get("seed_data"))

    return post, seed_data


def _load_batch_creation_mode(batch_id: str, supabase_client) -> str:
    try:
        response = (
            supabase_client.table("batches")
            .select("id, creation_mode")
            .eq("id", batch_id)
            .execute()
        )
    except Exception:
        # Batch lookup should never block script edits; default to automated behavior.
        return "automated"
    if not response.data:
        return "automated"
    return str(response.data[0].get("creation_mode") or "automated")


@router.put("/{post_id}/script", response_model=SuccessResponse)
async def update_post_script(post_id: str, request: Request):
    """
    Update script text for a post.
    Per Canon § 3.2: Manual script override.
    """
    try:
        content_type = request.headers.get("content-type", "")
        
        if "application/json" in content_type:
            data = await request.json()
            payload = UpdateScriptRequest.model_validate(data)
            script_text = payload.script_text
            submitted_post_type = payload.post_type
        else:
            form = await request.form()
            script_text = str(form.get("script_text", "")).strip()
            if not script_text:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="script_text is required"
                )
            submitted_post_type_raw = form.get("post_type", None)
            submitted_post_type = None if submitted_post_type_raw is None else str(submitted_post_type_raw).strip()
        
        supabase = get_supabase().client
        
        post, current_seed = _load_post_seed_data(post_id, supabase)
        batch_creation_mode = _load_batch_creation_mode(post["batch_id"], supabase)
        current_seed["script"] = script_text
        current_seed["script_review_status"] = "pending"
        current_seed.pop("video_excluded", None)

        is_manual_batch = batch_creation_mode == "manual" or current_seed.get("manual_draft") is True
        resolved_post_type = (submitted_post_type or str(post.get("post_type") or "").strip()) if is_manual_batch else str(post.get("post_type") or "").strip()
        if is_manual_batch and not resolved_post_type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="post_type is required for manual batches",
            )
        if resolved_post_type:
            current_seed["post_type"] = resolved_post_type
            current_seed["manual_post_type"] = resolved_post_type

        update_payload = {
            "seed_data": current_seed,
            # Editing the script must invalidate any prompt assembled from the old text.
            "video_prompt_json": None,
        }

        supabase.table("posts").update(update_payload).eq("id", post_id).execute()
        if resolved_post_type:
            try:
                supabase.table("posts").update({"post_type": resolved_post_type}).eq("id", post_id).execute()
            except APIError as exc:
                error_text = str(exc)
                if exc.code == "PGRST204" or "posts_post_type_check" in error_text or "check" in error_text.lower():
                    logger.warning(
                        "manual_post_type_column_update_fallback",
                        post_id=post_id,
                        error=error_text,
                    )
                else:
                    raise
        
        logger.info(
            "post_script_updated",
            post_id=post_id,
            script_length=len(script_text)
        )
        
        response_payload = {"id": post_id, "script_text": script_text}
        if resolved_post_type:
            response_payload["post_type"] = resolved_post_type
        return SuccessResponse(data=response_payload)
    
    except FlowForgeException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_script_failed", post_id=post_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update script"
        )


@router.put("/{post_id}/script-review", response_model=SuccessResponse)
async def update_post_script_review(post_id: str, request: Request):
    """Approve, remove, or reset an individual post script during S2 review."""
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            data = await request.json()
            payload = UpdateScriptReviewRequest.model_validate(data)
            action = payload.action
        else:
            form = await request.form()
            action = str(form.get("action", "")).strip()

        allowed_actions = {"approved", "removed", "reset"}
        if action not in allowed_actions:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"action must be one of {sorted(allowed_actions)}"
            )

        supabase = get_supabase().client
        post, seed_data = _load_post_seed_data(post_id, supabase)

        if action == "approved":
            seed_data["script_review_status"] = "approved"
            seed_data.pop("video_excluded", None)
        elif action == "removed":
            seed_data["script_review_status"] = "removed"
            seed_data["video_excluded"] = True
        else:
            seed_data["script_review_status"] = "pending"
            seed_data.pop("video_excluded", None)

        update_payload = {
            "seed_data": seed_data,
            "video_prompt_json": None if action == "removed" else post.get("video_prompt_json"),
        }
        if action == "removed":
            # Keep the existing non-null video_status; removal is expressed via seed_data flags.
            update_payload["video_status"] = post.get("video_status") or "pending"

        supabase.table("posts").update(update_payload).eq("id", post_id).execute()

        logger.info(
            "post_script_review_updated",
            post_id=post_id,
            batch_id=post.get("batch_id"),
            action=action
        )

        return SuccessResponse(data={"id": post_id, "action": action, "script_review_status": seed_data["script_review_status"]})

    except FlowForgeException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_script_review_failed", post_id=post_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update script review"
        )


@router.post("/{post_id}/build-prompt", response_model=SuccessResponse)
async def build_post_prompt(post_id: str):
    """
    Build video generation prompt for a post.
    Transitions post from S4_SCRIPTED to S5_PROMPTS_BUILT.
    Per Canon § 3.2: S4_SCRIPTED → S5_PROMPTS_BUILT
    
    Simple assembly: Takes Phase 2 dialogue from seed_data and inserts
    into video generation template.
    """
    correlation_id = f"build_prompt_{post_id}"
    
    try:
        supabase = get_supabase().client
        
        # Fetch post with seed_data
        response = supabase.table("posts").select("*").eq("id", post_id).execute()
        
        if not response.data:
            raise FlowForgeException(
                code="not_found",
                message=f"Post {post_id} not found",
                details={"post_id": post_id}
            )
        
        post = response.data[0]
        seed_data = post.get("seed_data")
        
        if not seed_data:
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Post missing seed_data. Run Phase 2 first.",
                details={"post_id": post_id},
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        
        # Handle JSON string vs dict
        if isinstance(seed_data, str):
            try:
                seed_data = json.loads(seed_data)
            except json.JSONDecodeError as e:
                raise FlowForgeException(
                    code=ErrorCode.VALIDATION_ERROR,
                    message="Invalid seed_data JSON",
                    details={"post_id": post_id, "error": str(e)},
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

        if seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True:
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Removed posts cannot build video prompts.",
                details={"post_id": post_id},
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        # Build video prompt by inserting dialogue into template
        video_prompt = build_video_prompt_from_seed(
            seed_data,
            legacy_32_visuals=_should_use_legacy_32_visuals(post),
        )

        existing_prompt = _parse_json_document(post.get("video_prompt_json"))
        if isinstance(existing_prompt, dict) and existing_prompt:
            preserved_fields = (
                "character",
                "style",
                "action",
                "scene",
                "cinematography",
                "ending_directive",
                "audio_block",
                "universal_negatives",
                "veo_prompt",
                "veo_negative_prompt",
                "optimized_prompt",
            )
            for field_name in preserved_fields:
                if existing_prompt.get(field_name):
                    video_prompt[field_name] = existing_prompt[field_name]
            existing_audio = existing_prompt.get("audio")
            if isinstance(existing_audio, dict):
                video_prompt["audio"] = {
                    **video_prompt.get("audio", {}),
                    **{key: value for key, value in existing_audio.items() if value},
                }
        
        # Validate assembled prompt
        validate_video_prompt(video_prompt)
        
        # Store prompt in posts table
        supabase.table("posts").update({
            "video_prompt_json": video_prompt
        }).eq("id", post_id).execute()

        _maybe_transition_batch_to_prompts_built(
            batch_id=post["batch_id"],
            supabase_client=supabase,
            correlation_id=correlation_id
        )

        logger.info(
            "video_prompt_built",
            post_id=post_id,
            correlation_id=correlation_id,
            dialogue_length=len(video_prompt.get("audio", {}).get("dialogue", ""))
        )
        
        return SuccessResponse(
            data={
                "id": post_id,
                "video_prompt": video_prompt,
                "state_ready": "S5_PROMPTS_BUILT"
            }
        )
    
    except ValidationError as e:
        logger.error(
            "build_prompt_validation_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=e.message
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=e.message
        )
    except FlowForgeException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "build_prompt_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build video prompt"
        )


@router.patch("/{post_id}/prompt", response_model=SuccessResponse)
async def update_post_prompt(post_id: str, request: Request):
    """Update editable prompt sections and rebuild the stored prompt text."""
    correlation_id = f"update_prompt_{post_id}"

    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = UpdatePromptRequest.model_validate(await request.json())
        else:
            form = await request.form()
            payload = UpdatePromptRequest.model_validate({
                "character": str(form.get("character", "")).strip(),
                "style": str(form.get("style", "")).strip(),
                "action": str(form.get("action", "")).strip(),
                "scene": str(form.get("scene", "")).strip(),
                "cinematography": str(form.get("cinematography", "")).strip(),
                "dialogue": str(form.get("dialogue", "")).strip(),
                "ending": str(form.get("ending", "")).strip(),
                "audio_block": str(form.get("audio_block", "")).strip(),
                "universal_negatives": str(form.get("universal_negatives", "")).strip(),
                "veo_prompt": str(form.get("veo_prompt", "")).strip(),
                "veo_negative_prompt": str(form.get("veo_negative_prompt", "")).strip(),
            })

        supabase = get_supabase().client
        response = supabase.table("posts").select("id, batch_id, video_prompt_json, seed_data").eq("id", post_id).execute()
        if not response.data:
            raise FlowForgeException(
                code=ErrorCode.NOT_FOUND,
                message=f"Post {post_id} not found",
                details={"post_id": post_id},
            )

        post = response.data[0]
        existing_prompt = _parse_json_document(post.get("video_prompt_json"))
        if not existing_prompt:
            seed_data = _parse_json_document(post.get("seed_data"))
            if not seed_data:
                raise FlowForgeException(
                    code=ErrorCode.VALIDATION_ERROR,
                    message="Post missing video_prompt_json and seed_data. Build the prompt before editing it.",
                    details={"post_id": post_id},
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            existing_prompt = build_video_prompt_from_seed(
                seed_data,
                legacy_32_visuals=_should_use_legacy_32_visuals(post),
            )

        updated_prompt = {
            **existing_prompt,
            "character": payload.character.strip(),
            "style": payload.style.strip(),
            "action": payload.action.strip(),
            "scene": payload.scene.strip(),
            "cinematography": payload.cinematography.strip(),
            "audio": {
                "dialogue": payload.dialogue.strip(),
                "capture": payload.audio_block.strip(),
            },
            "ending_directive": payload.ending.strip(),
            "audio_block": payload.audio_block.strip(),
            "universal_negatives": payload.universal_negatives.strip(),
            "veo_negative_prompt": payload.veo_negative_prompt.strip(),
        }

        updated_prompt["optimized_prompt"] = build_optimized_prompt(
            payload.dialogue,
            negative_constraints=payload.universal_negatives,
            prompt_mode="standard_final",
            character=payload.character,
            action=payload.action,
            style=payload.style,
            scene=payload.scene,
            cinematography=payload.cinematography,
            ending=payload.ending,
            audio_block=payload.audio_block,
        )
        updated_prompt["veo_prompt"] = _build_edited_veo_prompt(
            existing_prompt=existing_prompt,
            payload=payload,
        )
        validate_video_prompt(updated_prompt)

        supabase.table("posts").update({
            "video_prompt_json": updated_prompt,
        }).eq("id", post_id).execute()

        logger.info(
            "video_prompt_updated",
            post_id=post_id,
            batch_id=post.get("batch_id"),
            correlation_id=correlation_id,
            dialogue_length=len(payload.dialogue.strip()),
            action_length=len(payload.action.strip()),
        )

        return SuccessResponse(
            data={
                "id": post_id,
                "video_prompt": updated_prompt,
                "state_ready": "S5_PROMPTS_BUILT",
            }
        )

    except ValidationError as e:
        logger.error(
            "update_prompt_validation_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=e.message,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=e.message,
        )
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


def _maybe_transition_batch_to_prompts_built(*, batch_id: str, supabase_client, correlation_id: str) -> None:
    """Advance batch to S5_PROMPTS_BUILT when all posts have prompts."""
    try:
        reconcile_batch_video_pipeline_state(
            batch_id=batch_id,
            correlation_id=correlation_id,
            supabase_client=supabase_client,
        )
    except Exception as transition_error:
        logger.exception(
            "batch_prompts_transition_failed",
            batch_id=batch_id,
            correlation_id=correlation_id,
            error=str(transition_error)
        )
