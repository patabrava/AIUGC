"""
FLOW-FORGE Posts Handlers
FastAPI route handlers for post operations.
Per Constitution § V: Locality & Vertical Slices
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.adapters.supabase_client import get_supabase
from app.core.errors import FlowForgeException, SuccessResponse, ValidationError
from app.core.logging import get_logger
from app.features.posts.prompt_builder import build_video_prompt_from_seed, validate_video_prompt

logger = get_logger(__name__)

router = APIRouter(prefix="/posts", tags=["posts"])


class UpdateScriptRequest(BaseModel):
    """Request to update post script."""
    script_text: str = Field(..., min_length=1, max_length=500, description="Script text")


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
        else:
            form = await request.form()
            script_text = str(form.get("script_text", "")).strip()
            if not script_text:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="script_text is required"
                )
        
        supabase = get_supabase().client
        
        # Update the seed_data with the new script
        response = supabase.table("posts").select("seed_data").eq("id", post_id).execute()
        
        if not response.data:
            raise FlowForgeException(
                code="not_found",
                message=f"Post {post_id} not found",
                details={"post_id": post_id}
            )
        
        current_seed = response.data[0].get("seed_data") or {}
        if isinstance(current_seed, str):
            import json
            try:
                current_seed = json.loads(current_seed)
            except json.JSONDecodeError:
                current_seed = {}
        
        # Update the script in seed_data
        current_seed["script"] = script_text
        
        update_response = supabase.table("posts").update({
            "seed_data": current_seed
        }).eq("id", post_id).execute()
        
        logger.info(
            "post_script_updated",
            post_id=post_id,
            script_length=len(script_text)
        )
        
        return SuccessResponse(data={"id": post_id, "script_text": script_text})
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("update_script_failed", post_id=post_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update script"
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
                code="validation_error",
                message="Post missing seed_data. Run Phase 2 first.",
                details={"post_id": post_id}
            )
        
        # Handle JSON string vs dict
        if isinstance(seed_data, str):
            import json
            try:
                seed_data = json.loads(seed_data)
            except json.JSONDecodeError as e:
                raise FlowForgeException(
                    code="validation_error",
                    message="Invalid seed_data JSON",
                    details={"post_id": post_id, "error": str(e)}
                )
        
        # Build video prompt by inserting dialogue into template
        video_prompt = build_video_prompt_from_seed(seed_data)
        
        # Validate assembled prompt
        validate_video_prompt(video_prompt)
        
        # Store prompt in posts table
        update_response = supabase.table("posts").update({
            "video_prompt_json": video_prompt
        }).eq("id", post_id).execute()
        
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
