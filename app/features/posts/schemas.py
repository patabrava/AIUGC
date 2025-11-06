"""
FLOW-FORGE Posts Schemas
Pydantic models for video prompt assembly (Phase 3).
Per Constitution § II: Validated Boundaries
"""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator


class AudioSection(BaseModel):
    """Audio section for video prompt."""
    dialogue: str = Field(..., description="Spoken dialogue text from Phase 2")
    capture: str = Field(
        default="Audio: Recorded through modern smartphone mic — clear, front-facing voice with intimate presence and a soft, short living-room bloom (RT60 ≈ 0.3–0.4 s). Camera 20–30 cm from mouth, mic unobstructed. HVAC/appliances off; noise floor ≤ –55 dBFS with a faint, even room-tone bed. No music, one-take natural pacing.",
        description="Audio capture description"
    )


class VideoPrompt(BaseModel):
    """Complete video generation prompt structure per Phase 3 requirements."""
    character: str = Field(
        default="Character: 38-year-old German woman with long, damp, light brown hair with natural blonde highlights; hazel, almond-shaped eyes with subtle eye wrinkles (fine crow’s feet) at the outer corners; a friendly oval face; soft forehead lines (fine horizontal expression lines) that are faint at rest; gentle laugh lines (light nasolabial folds) framing the mouth; and a warm light-medium skin tone with neutral undertones. She is looking directly at the camera with a neutral, friendly expression. Filmed on an iPhone 15 Pro, bright soft vanity lighting, neutral clean color palette, hyper-realistic skin texture with visible pores..",
        description="Character definition"
    )
    action: str = Field(
        default="Action: Sits in a wheelchair in the bedroom, hair still slightly damp, looking directly into camera with a neutral, friendly expression that turns to a gentle smile. Maintains steady head-and-shoulders orientation; uses small, natural hand gestures and subtle upper-body nods while speaking. Remains seated and centered for a single continuous take with no cuts or alternate angles and says: ENTER SCRIPT FROM POST HERE",
        description="Action description"
    )
    style: str = Field(
        default="Style: Smartphone selfie, UGC authenticity: bright vanity lighting, neutral clean color palette, hyper-realistic skin texture with visible pores, influencer-style monologue and direct-to-camera delivery. Raw, unfiltered TikTok aesthetic with natural skin tone and no filters.",
        description="Visual style"
    )
    scene: str = Field(
        default="Scene: The woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. Clean, minimal décor. Natural daylight streams through an unseen window camera-right, supplemented by soft ambient lighting creating even, flattering illumination across the space.",
        description="Scene setup"
    )
    cinematography: str = Field(
        default="Cinematography: Camera Shot: Medium close-up from a slightly high angle, with centered framing that keeps her head and shoulders in the shot. This camera shot does not change during the whole take. Lens & DOF: modern smartphone front camera (~24 mm equiv.), deep depth of field keeping the background in focus with a natural subtle falloff. Camera Motion: Subtle handheld sway and jitter consistent with a selfie grip, including very slight natural arm movements as she speaks and gestures.",
        description="Cinematography notes"
    )
    lighting: str = Field(
        default="Lighting: Bright, soft, diffuse frontal light  illuminating her face evenly. Soft shadows are visible behind her.",
        description="Lighting description"
    )
    color_and_grade: str = Field(
        default="Color & Grade: modern smartphone  HDR auto-tone; a neutral clean color palette; natural skin texture with visible pores is preserved; no filters are applied.",
        description="Color and grading notes"
    )
    resolution_and_aspect_ratio: str = Field(
        default="Resolution & Aspect Ratio: 720x1280, 30 fps, vertical.",
        description="Resolution and aspect ratio"
    )
    camera_positioning_and_motion: str = Field(
        default="Camera positioning & movement: Medium close-up from a slightly high angle, centered framing that keeps head and shoulders fully in frame. Front-facing modern smartphone (~24 mm equiv.) held at selfie distance (camera ~20–30 cm from face). Subtle handheld sway and micro arm jitter consistent with a selfie grip; no intentional camera moves or cuts. Maintain framing and facial positioning to match the Golden Face/Look Anchor precisely.",
        description="Camera positioning"
    )
    composition: str = Field(
        default="Composition: Head-and-shoulders centered composition with wheelchair visible in frame and the modern bedroom environment apparent behind her. Pink walls and clean, minimal décor remain visible; natural daylight camera-right provides directional fill while soft ambient lights even out the scene. Background kept legible and consistent, not distracting from the subject.",
        description="Composition details"
    )
    focus_and_lens_effects: str = Field(
        default="Focus & lens effects: Face-priority autofocus locked on her eyes; deep depth of field with background in focus and a natural, subtle falloff. No focus hunting, no warp or flicker. Preserve skin texture and pores; no heavy bokeh, no digital smoothing or beauty filters. Modern smartphone HDR auto-tone preserved; maintain consistent white balance and colorimetry throughout the take.",
        description="Focus and lens effects"
    )
    atmosphere: str = Field(
        default="Atmosphere: Bright, soft, diffuse frontal illumination with flattering, even highlights and gentle shadows behind the subject. Clean, neutral, modern bedroom vibe with daylight warmth balanced by soft ambient lights. Authentic, minimal aesthetic — uncluttered, airy, and intimate without dramatic contrast or stylized color grading.",
        description="Atmospheric notes"
    )
    authenticity_modifiers: str = Field(
        default="Authenticity/UGC Modifiers: smartphone selfie, handheld realism, living room review, bright vanity lighting, influencer-style monologue, direct-to-camera, product review, raw unfiltered TikTok aesthetic, real voice, micro hand jitters, seamless one-take.",
        description="Authenticity modifiers"
    )
    universal_negatives: str = Field(
        default="Universal Negatives (hard constraints): subtitles, captions, watermark, text overlays, words on screen, logo, branding, poor lighting, blurry footage, low resolution, artifacts, unwanted objects, inconsistent character appearance, audio sync issues, amateur quality, cartoon effects, unrealistic proportions, distorted hands, artificial lighting, oversaturation, compression noise, excessive camera shake.",
        description="Universal negatives"
    )
    audio: AudioSection = Field(..., description="Audio section with dialogue and capture notes")
    post: str = Field(
        default="Post: gentle HPF @ 80 Hz, light 3:1 compression (≈–3 dB GR), subtle de-ess around 6–8 kHz; peaks capped at –1 dBTP, delivery loudness around –14 LUFS integrated.",
        description="Post-processing notes"
    )
    sound_effects: str = Field(
        default="Sound effects (SFX): Recorded through modern smartphone mic — clear, front-facing voice with intimate presence. Post notes: gentle HPF @ 80 Hz, light 3:1 compression (≈–3 dB GR), subtle de-ess around 6–8 kHz; peaks capped at –1 dBTP, delivery loudness around –14 LUFS integrated.",
        description="Sound effects notes"
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
