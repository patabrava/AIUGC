"""
FLOW-FORGE Posts Handlers
FastAPI route handlers for post operations.
Per Constitution § V: Locality & Vertical Slices
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.adapters.supabase_client import get_supabase
from app.core.errors import FlowForgeException, SuccessResponse
from app.core.logging import get_logger

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
