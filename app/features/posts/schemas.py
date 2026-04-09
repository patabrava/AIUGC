"""
Lippe Lift Studio Posts Schemas
Pydantic models for video prompt assembly (Phase 3).
Per Constitution § II: Validated Boundaries
"""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator

from app.features.posts.prompt_defaults import DEFAULT_SCENE


class AudioSection(BaseModel):
    """Audio section for video prompt."""
    dialogue: str = Field(..., description="Spoken dialogue text from Phase 2")
    capture: str = Field(
        default="",
        description="Audio capture description"
    )


class VideoPrompt(BaseModel):
    """Complete video generation prompt structure per Phase 3 requirements."""
    character: str = Field(
        default="Character: 38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights, hazel eyes, and a warm light-medium skin tone. Friendly oval face and natural expression.",
        description="Character definition"
    )
    action: str = Field(
        default="Action: Seated in a wheelchair in the bedroom, she speaks directly to camera in one continuous take. She speaks at a natural conversational pace, uses small natural hand gestures and subtle upper-body nods while speaking, then holds a gentle smile and remains still briefly at the end of the line. She says: ENTER SCRIPT FROM POST HERE",
        description="Action description"
    )
    style: str = Field(
        default="Style: Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, natural skin texture, and direct-to-camera delivery.",
        description="Visual style"
    )
    scene: str = Field(
        default=DEFAULT_SCENE,
        description="Scene setup"
    )
    cinematography: str = Field(
        default="Cinematography: Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.",
        description="Cinematography notes"
    )
    lighting: str = Field(
        default="Lighting: Bright, soft, diffuse frontal light  illuminating her face evenly. Soft shadows are visible behind her.",
        description="Lighting description"
    )
    color_and_grade: str = Field(
        default="Color & Grade: modern smartphone color with a clean, natural palette and natural skin texture. No filters are applied.",
        description="Color and grading notes"
    )
    resolution_and_aspect_ratio: str = Field(
        default="Resolution & Aspect Ratio: 720x1280, 30 fps, vertical.",
        description="Resolution and aspect ratio"
    )
    camera_positioning_and_motion: str = Field(
        default="Camera positioning & movement: Front-facing smartphone camera at natural selfie distance, stable. Minor natural movement typical of a person holding a phone, without noticeable drift or framing changes.",
        description="Camera positioning"
    )
    composition: str = Field(
        default="Composition: Head-and-shoulders centered composition with wheelchair visible in frame and the modern bedroom environment apparent behind her. Pink walls and clean, minimal décor remain visible; natural daylight camera-right provides directional fill while soft ambient lights even out the scene. Background kept legible and consistent, not distracting from the subject.",
        description="Composition details"
    )
    focus_and_lens_effects: str = Field(
        default="Focus & lens effects: Natural smartphone clarity with consistent focus on the subject. No heavy blur, warp, flicker, or beauty filters. Keep skin texture natural and lighting consistent throughout the take.",
        description="Focus and lens effects"
    )
    atmosphere: str = Field(
        default="Atmosphere: Bright, soft, diffuse frontal illumination with flattering, even highlights and gentle shadows behind the subject. Clean, neutral, modern bedroom vibe with daylight warmth balanced by soft ambient lights. Authentic, minimal aesthetic — uncluttered, airy, and intimate without dramatic contrast or stylized color grading.",
        description="Atmospheric notes"
    )
    authenticity_modifiers: str = Field(
        default="Authenticity/UGC Modifiers: smartphone selfie, handheld realism, direct-to-camera delivery, real voice, seamless one-take, natural movement.",
        description="Authenticity modifiers"
    )
    universal_negatives: str = Field(
        default="Universal Negatives (hard constraints): subtitles, captions, watermark, text overlays, words on screen, logo, branding, poor lighting, blurry footage, low resolution, unwanted objects, inconsistent character appearance, audio sync issues, amateur quality, cartoon effects, unrealistic proportions, distorted hands, artificial lighting, oversaturation, excessive camera shake, no audible audio artifacts, no background voices, no music.",
        description="Universal negatives"
    )
    audio: AudioSection = Field(..., description="Audio section with dialogue and capture notes")
    post: str = Field(
        default="",
        description="Post-processing notes"
    )
    sound_effects: str = Field(
        default="",
        description="Sound effects notes"
    )
    optimized_prompt: Optional[str] = Field(
        default=None,
        description="Sora-optimized prompt text with inline negatives",
    )
    veo_prompt: Optional[str] = Field(
        default=None,
        description="Veo prompt text without inline exclusions",
    )
    veo_negative_prompt: Optional[str] = Field(
        default=None,
        description="Veo negativePrompt string for exclusions",
    )
    ending_directive: Optional[str] = Field(
        default=None,
        description="Explicit ending instruction for the prompt",
    )
    audio_block: Optional[str] = Field(
        default=None,
        description="Audio section text for the prompt",
    )

    @field_validator('audio', mode='before')
    @classmethod
    def ensure_audio_section(cls, v):
        """Ensure audio is AudioSection instance."""
        if isinstance(v, dict):
            return AudioSection(**v)
        return v


class BuildPromptRequest(BaseModel):
    """Request to build video prompt for a post."""
    post_id: str = Field(..., description="Post ID to build prompt for")


class BuildPromptResponse(BaseModel):
    """Response after building video prompt."""
    ok: bool = Field(default=True)
    data: Dict[str, Any] = Field(..., description="Prompt data and metadata")


class UpdatePromptRequest(BaseModel):
    """Request to update editable sections of a generated prompt."""
    character: str = Field(..., min_length=1, description="Character section text")
    style: str = Field(..., min_length=1, description="Style section text")
    action: str = Field(..., min_length=1, description="Action section text")
    scene: str = Field(..., min_length=1, description="Scene section text")
    cinematography: str = Field(..., min_length=1, description="Cinematography section text")
    dialogue: str = Field(..., min_length=1, description="Dialogue section text")
    ending: str = Field(..., min_length=1, description="Ending directive text")
    audio_block: str = Field(..., min_length=1, description="Audio block text")
    universal_negatives: str = Field(..., min_length=1, description="Universal negatives text")
    veo_negative_prompt: str = Field(..., min_length=1, description="VEO negative prompt text")
