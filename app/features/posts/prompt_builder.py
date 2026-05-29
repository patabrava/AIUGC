"""
Lippe Lift Studio Video Prompt Assembly
Simple prompt builder that inserts Phase 2 dialogue into video generation template.
Per Canon Phase 3: S4_SCRIPTED → S5_PROMPTS_BUILT
"""

import re
from typing import Dict, Any, Iterable, Optional

from app.features.posts.prompt_defaults import DEFAULT_SCENE, DEFAULT_SCENE_BODY, LEGACY_SCENE_BODY
from app.features.posts.schemas import VideoPrompt, AudioSection
from app.features.characters.actor_identity import (
    is_character_consistency_mode,
    is_character_consistency_light_mode,
)
from app.features.characters.scene_reference import get_scene_bible
from app.core.logging import get_logger
from app.core.errors import ValidationError


__all__ = [
    "STANDARD_FINAL_AUDIO_BLOCK",
    "SORA_NEGATIVE_CONSTRAINTS",
    "VEO_NEGATIVE_PROMPT",
    "build_negative_prompt",
    "build_video_prompt_from_seed",
    "sync_video_prompt_with_seed_data",
    "validate_video_prompt",
    "build_optimized_prompt",
    "split_dialogue_sentences",
    "build_veo_prompt_segment",
    "build_lean_veo_base_prompt",
    "build_reference_image_scene_base_prompt",
    "build_character_consistency_mid_base_prompt",
    "build_character_consistency_mid_continuation_prompt",
    "build_lean_veo_light_continuation_prompt",
    "build_lean_veo_continuation_prompt",
    "propose_scene_plan",
    "ensure_scene_plan",
    "resolve_scene_for_post",
]


STANDARD_FINAL_AUDIO_BLOCK = (
    "Audio: Recorded with a modern smartphone microphone in a quiet indoor room. "
    "The voice is clear, natural, and close to the microphone. No music and no "
    "background voices. Subtle natural room acoustics typical of a small bedroom. "
    "After the final word, the audio gently settles into a quiet room tone for a "
    "brief moment before the clip ends."
)

EXTENDED_CONTINUATION_AUDIO_BLOCK = (
    "Audio: Recorded with a modern smartphone microphone in a quiet indoor room. "
    "The voice is clear, natural, and close to the microphone. No music and no "
    "background voices. Subtle natural room acoustics typical of a small bedroom. "
    "Keep the spoken delivery continuous and steady with no dramatic pause, no "
    "trailing silence, and no settling room tone at the end of this segment."
)

STANDARD_FINAL_ENDING_DIRECTIVE = (
    "After the final spoken word, speech stops completely. She does not begin a new word or "
    "syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief "
    "moment before the clip ends."
)

EXTENDED_FINAL_ENDING_DIRECTIVE = (
    "After the final spoken word, speech stops completely. She does not begin a new word or "
    "syllable. Her mouth closes and comes fully to rest, she holds a gentle smile, and remains "
    "still for a brief moment before the clip ends."
)

EXTENDED_CONTINUATION_ENDING_DIRECTIVE = (
    "Continue directly into the next segment with no concluding pause or scene-ending hold."
)

SORA_NEGATIVE_CONSTRAINTS = (
    "Universal Negatives (hard constraints): subtitles, captions, watermark, text overlays, "
    "words on screen, logo, branding, poor lighting, blurry footage, low resolution, unwanted "
    "objects, inconsistent character appearance, audio sync issues, amateur quality, cartoon "
    "effects, unrealistic proportions, distorted hands, artificial lighting, oversaturation, "
    "excessive camera shake, no audible audio artifacts, no background voices, no music."
)

VEO_NEGATIVE_PROMPT = (
    "subtitles, burned-in subtitles, auto-generated subtitles, closed captions, lower-third captions, "
    "karaoke text, speech transcription overlays, captions, watermark, text overlays, words on screen, "
    "readable typography, UI text, logos, branding, poor lighting, blurry footage, low resolution, "
    "unwanted objects, character inconsistency, lip-sync drift, cartoon styling, unrealistic proportions, "
    "distorted hands, artificial lighting, oversaturation, excessive camera shake, background voices, "
    "music bed, audio hiss, static, clipping, abrupt cuts, angle changes, "
    "mirror appearing or disappearing, layout changes, background drift, new furniture, extra plants, wall color change, "
    "bedding color change, different room, lighting shift"
)

_VEO_SCENE_LOCK_CLAUSE = (
    ", mirror appearing or disappearing, layout changes, background drift, new furniture, extra plants, "
    "wall color change, bedding color change, different room, lighting shift"
)

_VEO_BASE_NEGATIVES = (
    "subtitles, burned-in subtitles, auto-generated subtitles, closed captions, lower-third captions, "
    "karaoke text, speech transcription overlays, captions, watermark, text overlays, words on screen, "
    "readable typography, UI text, logos, branding, poor lighting, blurry footage, low resolution, "
    "unwanted objects, character inconsistency, lip-sync drift, cartoon styling, unrealistic proportions, "
    "distorted hands, artificial lighting, oversaturation, excessive camera shake, background voices, "
    "music bed, audio hiss, static, clipping, abrupt cuts, angle changes"
)


def build_negative_prompt(*, creation_mode: str, is_extension: bool) -> str:
    """Build mode-aware Veo negativePrompt text."""
    if is_character_consistency_mode(creation_mode) and not is_extension:
        return _VEO_BASE_NEGATIVES
    return _VEO_BASE_NEGATIVES + _VEO_SCENE_LOCK_CLAUSE

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
    "{dialogue}\n\n"
    "Ending:\n"
    "{ending}\n\n"
    "Audio:\n"
    "{audio_block}{negatives_section}"
)

LEAN_CONTINUATION_PROMPT_TEMPLATE = (
    "Character:\n"
    "{character}\n\n"
    "Style:\n"
    "{style}\n\n"
    "Continuity:\n"
    "{continuity}\n\n"
    "Language:\n"
    "{language}\n\n"
    "Dialogue:\n"
    "{dialogue}\n\n"
    "Ending:\n"
    "{ending}\n\n"
    "Audio:\n"
    "{audio_block}"
)

LEAN_LIGHT_BASE_PROMPT_TEMPLATE = (
    "Scene:\n"
    "{scene}\n\n"
    "Action:\n"
    "Use the submitted actor identity reference images only as the woman identity source. "
    "Do not use reference-image backgrounds, wardrobe, or rooms as the scene source; the Scene "
    "block defines the environment. Preserve the same face, facial proportions, skin texture, "
    "hair identity, and age from the actor references while following the Scene block exactly. "
    "The woman remains seated and speaks directly to camera in one continuous natural smartphone "
    "take. Do not invent a new face, hairstyle, or age. Use small natural hand gestures and subtle "
    "upper-body nods while speaking.\n\n"
    "Language:\n"
    "Speak only in German, with natural conversational pacing.\n\n"
    "Dialogue:\n"
    "{dialogue}\n\n"
    "Ending:\n"
    "{ending}\n\n"
    "Audio:\n"
    "{audio_block}"
)

REFERENCE_SCENE_BASE_PROMPT_TEMPLATE = (
    "Character:\n"
    "{character}\n\n"
    "Style:\n"
    "{style}\n\n"
    "Scene:\n"
    "{scene}\n\n"
    "Action:\n"
    "Use the submitted actor identity reference images only as the woman identity source. "
    "Do not use reference-image backgrounds, wardrobe, or rooms as the scene source; the Scene "
    "block defines the environment. Preserve the same face, facial proportions, skin texture, "
    "hair identity, and age from the actor references while following the Scene block exactly. "
    "The woman remains seated and speaks directly to camera in one continuous natural smartphone "
    "take. Do not invent a new face, hairstyle, or age. Use small natural hand gestures and subtle "
    "upper-body nods while speaking.\n\n"
    "Cinematography:\n"
    "{cinematography}\n\n"
    "Dialogue:\n"
    "{dialogue}\n\n"
    "Ending:\n"
    "{ending}\n\n"
    "Audio:\n"
    "{audio_block}"
)

LEAN_LIGHT_CONTINUATION_PROMPT_TEMPLATE = (
    "Action:\n"
    "Continue from the previous generated segment with the same referenced woman, same wheelchair "
    "setup, same room, same wardrobe, same lighting, same camera position, and same speaking rhythm. "
    "Do not redesign the person or the environment. Continue as one seamless smartphone take.\n\n"
    "Language:\n"
    "Speak only in German. Maintain the same voice, accent, and speaking pace from the previous segment.\n\n"
    "Dialogue:\n"
    "{dialogue}\n\n"
    "Ending:\n"
    "{ending}\n\n"
    "Audio:\n"
    "{audio_block}"
)

LEAN_LIGHT_CONTINUATION_ENDING_DIRECTIVE = (
    "Continue directly into the next segment with no concluding pause, no scene-ending hold, and no visual reset."
)

LEAN_LIGHT_BASE_AUDIO_BLOCK = (
    "Natural single-speaker smartphone room audio. Clear close voice. No music. No background voices."
)

LEAN_EXTENSION_CHARACTER = (
    "Same person as the previous segment: 38-year-old German woman with shoulder-length "
    "light brown hair with subtle blonde highlights, hazel eyes, warm light-medium skin "
    "tone, friendly oval face, natural expression."
)

LEAN_EXTENSION_STYLE = (
    "Maintain the same realistic smartphone selfie video look from the previous segment."
)

LEAN_EXTENSION_CONTINUITY = (
    "Maintain the same bedroom, lighting, framing, camera position, and wardrobe from the previous segment."
)

MID_EXTENSION_CONTINUITY = (
    "Maintain the same environment, lighting, framing, camera position, and wardrobe from the previous segment."
)

LEAN_EXTENSION_LANGUAGE = (
    "Speak only in German. Maintain the same voice, accent, and speaking pace from the previous segment."
)

LEAN_CONTINUATION_AUDIO_BLOCK = (
    "Natural single-speaker smartphone room audio. No music. No background voices."
)

LEAN_FINAL_AUDIO_BLOCK = (
    "Natural single-speaker smartphone room audio. No music. No background voices. "
    "Let the room tone settle briefly after the final word."
)

DEFAULT_CHARACTER = (
    "38-year-old German woman with shoulder-length light brown hair with subtle blonde "
    "highlights, softly layered and resting around the shoulders; hazel almond-shaped eyes; "
    "naturally full light-brown brows; a straight nose with a gently rounded tip; medium-full "
    "muted-pink lips; a friendly oval face with a soft jawline and rounded chin; faint forehead "
    "lines and subtle smile lines; warm light-medium skin tone with natural skin texture; calm, "
    "direct-to-camera expression and relaxed upright posture."
)

LEGACY_SHORT_CHARACTER = (
    "38-year-old German woman with shoulder-length light brown hair with subtle blonde "
    "highlights, hazel eyes, and a warm light-medium skin tone. Friendly oval face and natural "
    "expression."
)

DEFAULT_STYLE = (
    "Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, "
    "soft flattering indoor light, and natural skin texture."
)

DEFAULT_CINEMATOGRAPHY = (
    "Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie "
    "distance. The camera is stable, with only minimal natural movement. The "
    "framing remains consistent throughout the shot without noticeable camera drift or "
    "reframing."
)

LEGACY_32_CHARACTER = (
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

LEGACY_32_STYLE = (
    "Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, "
    "soft flattering indoor light, and natural skin texture."
)

LEGACY_32_CINEMATOGRAPHY = (
    "Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie "
    "distance. The camera is handheld but stable, with only minimal natural movement. The "
    "framing remains consistent throughout the shot without noticeable camera drift or "
    "reframing."
)

CHARACTER_SOURCE_KEYS = (
    "character",
    "character_prompt",
    "character_description",
    "prompt_character",
)

REFERENCE_IMAGE_CHARACTER_PREFIXES = (
    "same person as the uploaded",
    "same person as uploaded",
)


def _is_reference_image_character_text(value: str) -> bool:
    normalized = " ".join(value.lower().split())
    return any(
        normalized.startswith(prefix) and "reference image" in normalized
        for prefix in REFERENCE_IMAGE_CHARACTER_PREFIXES
    )


def _uses_reference_image_scene_prompt(prompt_style: Any) -> bool:
    return str(prompt_style or "").strip() in {
        "character_consistency",
        "manual_character_consistency",
        "character_consistency_mid",
    }

logger = get_logger(__name__)

_SCENE_PROPOSAL_SYSTEM_PROMPT = (
    "You are a UGC video director. Given a brand and post topics, produce three concise, "
    "visually distinct scene descriptions for the same single character. Return one scene for "
    "value, one for lifestyle, and one for product posts. Each scene must describe location, "
    "lighting, and a few set details in one sentence."
)

_SCENE_PROPOSAL_USER_TEMPLATE = (
    "Brand: {brand}\n\n"
    "Topics:\n{topics_block}\n\n"
    "Return JSON with exactly these keys: value, lifestyle, product."
)


def _get_llm_client():
    from app.adapters.llm_client import get_llm_client

    return get_llm_client()


def propose_scene_plan(*, brand: str, topic_titles: Iterable[str], correlation_id: str) -> dict[str, str]:
    topics = [str(title).strip() for title in topic_titles if str(title).strip()][:8]
    prompt = _SCENE_PROPOSAL_USER_TEMPLATE.format(
        brand=brand,
        topics_block="\n".join(f"- {title}" for title in topics) or "- (none yet)",
    )
    fallback_scene = f"Scene: {get_scene_bible('home_living_room_advice_a').scene_identity}"
    fallback = {"value": fallback_scene, "lifestyle": fallback_scene, "product": fallback_scene}
    try:
        response = _get_llm_client().generate_json(prompt, system_prompt=_SCENE_PROPOSAL_SYSTEM_PROMPT)
    except Exception as exc:  # noqa: BLE001 - LLM fallback must be non-blocking here.
        logger.warning("scene_plan_llm_failed_fallback_to_scene_bible", correlation_id=correlation_id, error=str(exc))
        return fallback

    return {
        "value": str(response.get("value") or fallback_scene).strip() or fallback_scene,
        "lifestyle": str(response.get("lifestyle") or fallback_scene).strip() or fallback_scene,
        "product": str(response.get("product") or fallback_scene).strip() or fallback_scene,
    }


def _update_batch_scene_plan(batch_id: str, payload: dict) -> None:
    from app.features.batches.queries import update_batch_scene_plan

    update_batch_scene_plan(batch_id=batch_id, scene_plan=payload["scene_plan"])


def ensure_scene_plan(batch: dict, *, topic_titles: list[str], correlation_id: str) -> Optional[dict[str, str]]:
    if not is_character_consistency_mode(str(batch.get("creation_mode") or "automated").strip()):
        return None
    existing = batch.get("scene_plan")
    if isinstance(existing, dict) and all(existing.get(key) for key in ("value", "lifestyle", "product")):
        return {
            "value": str(existing["value"]),
            "lifestyle": str(existing["lifestyle"]),
            "product": str(existing["product"]),
        }
    plan = propose_scene_plan(
        brand=str(batch.get("brand") or ""),
        topic_titles=topic_titles,
        correlation_id=correlation_id,
    )
    _update_batch_scene_plan(str(batch["id"]), {"scene_plan": plan})
    batch["scene_plan"] = plan
    return plan


def resolve_scene_for_post(*, post_type: str, scene_plan: Optional[dict], override: Optional[str]) -> str:
    cleaned_override = (override or "").strip()
    if cleaned_override:
        return cleaned_override
    if isinstance(scene_plan, dict):
        planned_scene = str(scene_plan.get(post_type) or "").strip()
        if planned_scene:
            return planned_scene
    return DEFAULT_SCENE


def _get_prompt_contract(prompt_mode: str) -> Dict[str, str]:
    if prompt_mode == "extended_base_or_continuation":
        return {
            "action_direction": (
                "Seated in a wheelchair, she delivers the line directly to camera in one continuous "
                "take. She speaks with brisk but natural pacing, clear articulation, and no dramatic "
                "pauses, using small natural hand gestures and subtle upper-body nods while speaking."
            ),
            "audio_block": EXTENDED_CONTINUATION_AUDIO_BLOCK,
            "ending_directive": EXTENDED_CONTINUATION_ENDING_DIRECTIVE,
        }
    if prompt_mode == "extended_final":
        return {
            "action_direction": (
                "Seated in a wheelchair, she delivers the line directly to camera in one continuous "
                "take. She speaks with brisk but natural pacing, clear articulation, and controlled "
                "energy, using small natural hand gestures and subtle upper-body nods while speaking."
            ),
            "audio_block": STANDARD_FINAL_AUDIO_BLOCK,
            "ending_directive": EXTENDED_FINAL_ENDING_DIRECTIVE,
        }
    return {
        "action_direction": (
            "Seated in a wheelchair, she delivers the line directly to camera in one continuous take. "
            "She speaks at a natural conversational pace, using small natural hand gestures and subtle "
            "upper-body nods while speaking."
        ),
        "audio_block": STANDARD_FINAL_AUDIO_BLOCK,
        "ending_directive": STANDARD_FINAL_ENDING_DIRECTIVE,
    }


def _resolve_character_value(
    seed_data: Dict[str, Any],
    legacy_32_visuals: bool,
    *,
    use_legacy_short_character: bool = False,
) -> str:
    if use_legacy_short_character:
        return LEGACY_SHORT_CHARACTER
    for key in CHARACTER_SOURCE_KEYS:
        value = seed_data.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = value.strip()
            if _is_reference_image_character_text(cleaned):
                continue
            return cleaned
    return LEGACY_32_CHARACTER if legacy_32_visuals else DEFAULT_CHARACTER


def sync_video_prompt_with_seed_data(
    video_prompt: Dict[str, Any],
    seed_data: Dict[str, Any],
    *,
    legacy_32_visuals: bool = False,
    use_legacy_short_character: bool = False,
) -> Dict[str, Any]:
    if not isinstance(video_prompt, dict) or not isinstance(seed_data, dict):
        return video_prompt

    resolved_character = _resolve_character_value(
        seed_data,
        legacy_32_visuals,
        use_legacy_short_character=use_legacy_short_character,
    )

    if is_character_consistency_light_mode(video_prompt.get("prompt_style")):
        audio_payload = video_prompt.get("audio")
        dialogue = str(audio_payload.get("dialogue") or "").strip() if isinstance(audio_payload, dict) else ""
        if not dialogue:
            return video_prompt
        updated_prompt = dict(video_prompt)
        updated_prompt["veo_prompt"] = build_lean_veo_base_prompt(
            dialogue,
            scene=str(updated_prompt.get("scene") or "").strip() or None,
            include_final_ending=True,
        )
        updated_prompt["prompt_style"] = "character_consistency_light"
        return updated_prompt
    if _uses_reference_image_scene_prompt(video_prompt.get("prompt_style")):
        seed_dialogue = str(seed_data.get("script") or seed_data.get("dialog_script") or "").strip()
        audio_payload = video_prompt.get("audio")
        stored_dialogue = str(audio_payload.get("dialogue") or "").strip() if isinstance(audio_payload, dict) else ""
        dialogue = seed_dialogue or stored_dialogue
        if not dialogue:
            return video_prompt
        updated_prompt = dict(video_prompt)
        updated_prompt["character"] = resolved_character
        updated_prompt["veo_prompt"] = build_reference_image_scene_base_prompt(
            dialogue,
            character=resolved_character,
            style=str(updated_prompt.get("style") or "").strip() or None,
            scene=str(updated_prompt.get("scene") or "").strip() or None,
            cinematography=str(updated_prompt.get("cinematography") or "").strip() or None,
            ending=str(updated_prompt.get("ending_directive") or "").strip() or None,
            audio_block=LEAN_FINAL_AUDIO_BLOCK,
            legacy_32_visuals=legacy_32_visuals,
            include_final_ending=True,
        )
        updated_prompt["prompt_style"] = str(video_prompt.get("prompt_style") or "character_consistency").strip()
        return updated_prompt

    current_character = str(video_prompt.get("character") or "").strip()
    if not resolved_character or current_character == resolved_character:
        return video_prompt
    if (
        current_character not in {DEFAULT_CHARACTER, LEGACY_SHORT_CHARACTER, LEGACY_32_CHARACTER}
        and not _is_reference_image_character_text(current_character)
    ):
        return video_prompt

    updated_prompt = dict(video_prompt)
    updated_prompt["character"] = resolved_character

    audio_payload = updated_prompt.get("audio")
    if isinstance(audio_payload, dict):
        dialogue = str(audio_payload.get("dialogue") or "").strip()
    else:
        dialogue = ""

    if dialogue:
        updated_prompt["optimized_prompt"] = build_optimized_prompt(
            dialogue,
            negative_constraints=updated_prompt.get("universal_negatives"),
            prompt_mode="standard_final",
            character=resolved_character,
            action=updated_prompt.get("action"),
            style=updated_prompt.get("style"),
            scene=updated_prompt.get("scene"),
            cinematography=updated_prompt.get("cinematography"),
            ending=updated_prompt.get("ending_directive"),
            audio_block=updated_prompt.get("audio_block"),
            legacy_32_visuals=legacy_32_visuals,
        )
        updated_prompt["veo_prompt"] = build_optimized_prompt(
            dialogue,
            negative_constraints=None,
            prompt_mode="standard_final",
            character=resolved_character,
            action=updated_prompt.get("action"),
            style=updated_prompt.get("style"),
            scene=updated_prompt.get("scene"),
            cinematography=updated_prompt.get("cinematography"),
            ending=updated_prompt.get("ending_directive"),
            audio_block=updated_prompt.get("audio_block"),
            legacy_32_visuals=legacy_32_visuals,
        )

    return updated_prompt


def _scene_for_template(scene: str, *, legacy_32_visuals: bool) -> str:
    if legacy_32_visuals:
        return LEGACY_SCENE_BODY
    cleaned = scene.strip()
    return cleaned[len("Scene: "):].strip() if cleaned.startswith("Scene: ") else cleaned


def build_video_prompt_from_seed(
    seed_data: Dict[str, Any],
    *,
    legacy_32_visuals: bool = False,
    use_legacy_short_character: bool = False,
    post_type: str = "value",
    scene_plan: Optional[dict] = None,
    scene_override: Optional[str] = None,
    prompt_style: str = "standard",
) -> Dict[str, Any]:
    """
    Assemble video generation prompt by inserting dialogue from Phase 2 seed data.
    
    Args:
        seed_data: Post seed_data containing dialog_script from Phase 2
        
    Returns:
        Complete video prompt JSON ready for video generation API
        
    Raises:
        ValidationError: If seed_data is missing required dialogue
        
    Per Constitution § XII: Schema-first validation
    """
    # Extract dialogue from seed_data
    dialogue = seed_data.get("script")
    dialogue_source = "seed_script"

    if not dialogue:
        # Fallback to dialog_script (PROMPT_2) if PROMPT_1 script missing
        dialogue = seed_data.get("dialog_script")
        dialogue_source = "dialog_script" if dialogue else None

    if not dialogue:
        raise ValidationError(
            message="Missing dialogue in seed_data. Post must have dialog_script or script.",
            details={"seed_data_keys": list(seed_data.keys())}
        )
    
    # Normalize dialogue to avoid duplicate ending markers
    normalized_dialogue = dialogue.strip()
    suffix_variants = ["(After delivering the dialogue, the character maintains a still, gentle smile with no further facial or mouth movements)", "( After delivering the dialogue, the character maintains a still, gentle smile with no further facial or mouth movements)"]
    for suffix in suffix_variants:
        if normalized_dialogue.endswith(suffix):
            normalized_dialogue = normalized_dialogue[: -len(suffix)].rstrip()
            break

    action_value = (
        "Seated in a wheelchair in the bedroom, she speaks directly to camera in one continuous "
        "take. She speaks at a natural conversational pace, uses small natural hand gestures and "
        "subtle upper-body nods while speaking, then holds a gentle smile and remains still briefly "
        "after the spoken line."
    )

    # Assemble complete prompt using template defaults
    character_value = _resolve_character_value(
        seed_data,
        legacy_32_visuals,
        use_legacy_short_character=use_legacy_short_character,
    )
    style_value = LEGACY_32_STYLE if legacy_32_visuals else DEFAULT_STYLE
    scene_value = LEGACY_SCENE_BODY if legacy_32_visuals else _scene_for_template(
        resolve_scene_for_post(post_type=post_type, scene_plan=scene_plan, override=scene_override),
        legacy_32_visuals=False,
    )
    cinematography_value = LEGACY_32_CINEMATOGRAPHY if legacy_32_visuals else DEFAULT_CINEMATOGRAPHY

    optimized_prompt = build_optimized_prompt(
        normalized_dialogue,
        negative_constraints=SORA_NEGATIVE_CONSTRAINTS,
        prompt_mode="standard_final",
        character=character_value,
        action=action_value,
        audio_block=STANDARD_FINAL_AUDIO_BLOCK,
        ending=STANDARD_FINAL_ENDING_DIRECTIVE,
        legacy_32_visuals=legacy_32_visuals,
    )
    if is_character_consistency_light_mode(prompt_style):
        veo_prompt = build_lean_veo_base_prompt(
            normalized_dialogue,
            scene=scene_value,
            include_final_ending=True,
        )
    elif _uses_reference_image_scene_prompt(prompt_style):
        veo_prompt = build_reference_image_scene_base_prompt(
            normalized_dialogue,
            character=character_value,
            style=style_value,
            scene=scene_value,
            cinematography=cinematography_value,
            ending=STANDARD_FINAL_ENDING_DIRECTIVE,
            audio_block=LEAN_FINAL_AUDIO_BLOCK,
            legacy_32_visuals=legacy_32_visuals,
            include_final_ending=True,
        )
    else:
        veo_prompt = build_optimized_prompt(
            normalized_dialogue,
            negative_constraints=None,
            prompt_mode="standard_final",
            character=character_value,
            action=action_value,
            audio_block=STANDARD_FINAL_AUDIO_BLOCK,
            ending=STANDARD_FINAL_ENDING_DIRECTIVE,
            legacy_32_visuals=legacy_32_visuals,
        )

    # Keep a single audio block in the final prompt to avoid contradictory synthesis cues.
    audio_section = AudioSection(dialogue=normalized_dialogue, capture=STANDARD_FINAL_AUDIO_BLOCK)

    base_prompt = VideoPrompt(
        character=character_value,
        style=style_value,
        scene=f"Scene: {scene_value}",
        cinematography=(f"Cinematography: {cinematography_value}" if legacy_32_visuals else DEFAULT_CINEMATOGRAPHY),
        audio=audio_section,
        universal_negatives=SORA_NEGATIVE_CONSTRAINTS,
        ending_directive=STANDARD_FINAL_ENDING_DIRECTIVE,
        audio_block=STANDARD_FINAL_AUDIO_BLOCK,
        post="",
        sound_effects="",
        optimized_prompt=optimized_prompt,
        veo_prompt=veo_prompt,
        veo_negative_prompt=VEO_NEGATIVE_PROMPT,
    )
    video_prompt = base_prompt.model_copy(update={"action": action_value})

    # Convert to dict for storage and API submission
    prompt_dict = video_prompt.model_dump()
    prompt_dict["optimized_prompt"] = optimized_prompt
    prompt_dict["veo_prompt"] = veo_prompt
    prompt_dict["veo_negative_prompt"] = VEO_NEGATIVE_PROMPT
    prompt_dict["prompt_style"] = prompt_style
    
    logger.info(
        "video_prompt_assembled",
        dialogue_length=len(dialogue),
        dialogue_preview=dialogue[:50] + "..." if len(dialogue) > 50 else dialogue,
        dialogue_source=dialogue_source,
        optimized_prompt_length=len(optimized_prompt),
    )
    
    return prompt_dict


def validate_video_prompt(prompt_data: Dict[str, Any]) -> bool:
    """
    Validate that prompt data conforms to VideoPrompt schema.
    
    Args:
        prompt_data: Prompt dictionary to validate
        
    Returns:
        True if valid
        
    Raises:
        ValidationError: If prompt is invalid
    """
    try:
        VideoPrompt.model_validate(prompt_data)
        return True
    except Exception as e:
        raise ValidationError(
            message=f"Video prompt validation failed: {str(e)}",
            details={"prompt_keys": list(prompt_data.keys())}
        )


def build_optimized_prompt(
    dialogue: str,
    negative_constraints: Optional[str] = SORA_NEGATIVE_CONSTRAINTS,
    *,
    prompt_mode: str = "standard_final",
    character: Optional[str] = None,
    action: Optional[str] = None,
    style: Optional[str] = None,
    scene: Optional[str] = None,
    cinematography: Optional[str] = None,
    ending: Optional[str] = None,
    audio_block: Optional[str] = None,
    legacy_32_visuals: bool = False,
) -> str:
    cleaned_dialogue = dialogue.strip()
    contract = _get_prompt_contract(prompt_mode)
    character_default = LEGACY_32_CHARACTER if legacy_32_visuals else DEFAULT_CHARACTER
    style_default = LEGACY_32_STYLE if legacy_32_visuals else DEFAULT_STYLE
    scene_default = LEGACY_SCENE_BODY if legacy_32_visuals else DEFAULT_SCENE_BODY
    cinematography_default = LEGACY_32_CINEMATOGRAPHY if legacy_32_visuals else DEFAULT_CINEMATOGRAPHY
    return OPTIMIZED_PROMPT_TEMPLATE.format(
        character=(character or character_default).strip(),
        style=(style or style_default).strip(),
        action_direction=(action or contract["action_direction"]).strip(),
        scene=(scene or scene_default).strip(),
        cinematography=(cinematography or cinematography_default).strip(),
        dialogue=cleaned_dialogue,
        ending=(ending or contract["ending_directive"]).strip(),
        audio_block=(audio_block or contract["audio_block"]).strip(),
        negatives_section=f"\n\n{negative_constraints}" if negative_constraints else "",
    )
def split_dialogue_sentences(dialogue: str) -> list[str]:
    cleaned = " ".join(dialogue.split()).strip()
    if not cleaned:
        return []
    decimal_tokens: dict[str, str] = {}

    def _protect_decimal(match: re.Match[str]) -> str:
        token = f"__DECIMAL_{len(decimal_tokens)}__"
        decimal_tokens[token] = match.group(0)
        return token

    protected = re.sub(r"\b(\d+)\.(\d+)\b", _protect_decimal, cleaned)
    sentence_matches = re.findall(r"[^.!?]+[.!?]", protected)
    remainder_start = sum(len(match) for match in sentence_matches)
    remainder = protected[remainder_start:].strip()
    sentences = [match.strip() for match in sentence_matches if match.strip()]
    
    for token, value in decimal_tokens.items():
        sentences = [sentence.replace(token, value) for sentence in sentences]
        remainder = remainder.replace(token, value)
    if remainder:
        if sentences:
            sentences[-1] = f"{sentences[-1].rstrip()} {remainder}".strip()
        else:
            sentences = [remainder]
    return sentences


def build_veo_prompt_segment(
    dialogue: str,
    *,
    include_quotes: bool = False,
    include_ending: bool = False,
    character: Optional[str] = None,
    action: Optional[str] = None,
    style: Optional[str] = None,
    scene: Optional[str] = None,
    cinematography: Optional[str] = None,
    ending: Optional[str] = None,
    audio_block: Optional[str] = None,
    negative_constraints: Optional[str] = None,
    legacy_32_visuals: bool = False,
) -> str:
    cleaned_dialogue = dialogue.strip()
    prompt_dialogue = f"\"{cleaned_dialogue}\"" if include_quotes else cleaned_dialogue
    prompt_mode = "extended_final" if include_ending else "extended_base_or_continuation"
    return build_optimized_prompt(
        prompt_dialogue,
        negative_constraints=negative_constraints,
        prompt_mode=prompt_mode,
        character=character,
        action=action,
        style=style,
        scene=scene,
        cinematography=cinematography,
        ending=ending,
        audio_block=audio_block,
        legacy_32_visuals=legacy_32_visuals,
    )


def build_lean_veo_continuation_prompt(
    dialogue: str,
    *,
    include_final_ending: bool = False,
) -> str:
    return _build_character_consistency_continuation_prompt(
        dialogue,
        continuity=LEAN_EXTENSION_CONTINUITY,
        include_final_ending=include_final_ending,
    )


def _build_character_consistency_continuation_prompt(
    dialogue: str,
    *,
    continuity: str,
    include_final_ending: bool = False,
) -> str:
    cleaned_dialogue = dialogue.strip()
    ending = (
        EXTENDED_FINAL_ENDING_DIRECTIVE
        if include_final_ending
        else EXTENDED_CONTINUATION_ENDING_DIRECTIVE
    )
    audio_block = LEAN_FINAL_AUDIO_BLOCK if include_final_ending else LEAN_CONTINUATION_AUDIO_BLOCK
    return LEAN_CONTINUATION_PROMPT_TEMPLATE.format(
        character=LEAN_EXTENSION_CHARACTER,
        style=LEAN_EXTENSION_STYLE,
        continuity=continuity,
        language=LEAN_EXTENSION_LANGUAGE,
        dialogue=cleaned_dialogue,
        ending=ending,
        audio_block=audio_block,
    )


def _normalize_scene_block(scene: str) -> str:
    cleaned = str(scene or "").strip()
    if cleaned.startswith("Scene:"):
        cleaned = cleaned[len("Scene:"):].strip()
    return cleaned or DEFAULT_SCENE_BODY


def _join_scene_terms(values: tuple[str, ...]) -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        return "none"
    return ", ".join(cleaned)


def _canonical_scene_bible_for_text(scene_text: str) -> Any:
    try:
        from app.features.characters.scene_reference import SCENE_BIBLES
    except Exception:  # pragma: no cover - prompt fallback must stay available during partial imports
        return None

    normalized_scene = " ".join(str(scene_text or "").split())
    for bible in SCENE_BIBLES.values():
        normalized_identity = " ".join(str(bible.scene_identity).split())
        if normalized_scene == normalized_identity or normalized_identity in normalized_scene:
            return bible
    return None


def _apply_minimal_scene_bible_lock(scene_text: str) -> str:
    resolved_scene = _normalize_scene_block(scene_text)
    bible = _canonical_scene_bible_for_text(resolved_scene)
    if bible is None:
        return resolved_scene
    return (
        f"{resolved_scene}\n\n"
        "Minimal scene Bible lock:\n"
        f"Object budget: only these set anchors may be visible beyond the actor and wheelchair context: {bible.anchor_lock}.\n"
        f"Layout lock: {bible.layout_lock}.\n"
        "Simplicity rule: keep the background sparse, uncluttered, and secondary; use no decorative props beyond the listed anchors.\n"
        f"Forbidden scene additions: {_join_scene_terms(bible.scene_specific_rejectors)}.\n"
        f"Camera boundary: {bible.composition} Do not widen into an establishing shot or reveal unlisted room areas."
    )


def build_lean_veo_base_prompt(
    dialogue: str,
    *,
    scene: Optional[str] = None,
    include_final_ending: bool = False,
) -> str:
    ending = STANDARD_FINAL_ENDING_DIRECTIVE if include_final_ending else LEAN_LIGHT_CONTINUATION_ENDING_DIRECTIVE
    audio_block = LEAN_FINAL_AUDIO_BLOCK if include_final_ending else LEAN_LIGHT_BASE_AUDIO_BLOCK
    resolved_scene = _apply_minimal_scene_bible_lock(scene or DEFAULT_SCENE_BODY)
    return LEAN_LIGHT_BASE_PROMPT_TEMPLATE.format(
        scene=resolved_scene,
        dialogue=dialogue.strip(),
        ending=ending,
        audio_block=audio_block,
    )


def build_reference_image_scene_base_prompt(
    dialogue: str,
    *,
    character: Optional[str] = None,
    style: Optional[str] = None,
    scene: Optional[str] = None,
    cinematography: Optional[str] = None,
    ending: Optional[str] = None,
    audio_block: Optional[str] = None,
    legacy_32_visuals: bool = False,
    include_final_ending: bool = True,
) -> str:
    cleaned_dialogue = dialogue.strip()
    resolved_character = (character or LEGACY_SHORT_CHARACTER).strip()
    resolved_style = (style or (LEGACY_32_STYLE if legacy_32_visuals else DEFAULT_STYLE)).strip()
    resolved_cinematography = (
        cinematography or (LEGACY_32_CINEMATOGRAPHY if legacy_32_visuals else DEFAULT_CINEMATOGRAPHY)
    ).strip()
    resolved_scene = _apply_minimal_scene_bible_lock(scene or (LEGACY_SCENE_BODY if legacy_32_visuals else DEFAULT_SCENE_BODY))
    resolved_ending = (
        ending
        or (STANDARD_FINAL_ENDING_DIRECTIVE if include_final_ending else LEAN_LIGHT_CONTINUATION_ENDING_DIRECTIVE)
    ).strip()
    resolved_audio = (
        audio_block
        or (LEAN_FINAL_AUDIO_BLOCK if include_final_ending else LEAN_LIGHT_BASE_AUDIO_BLOCK)
    ).strip()
    return REFERENCE_SCENE_BASE_PROMPT_TEMPLATE.format(
        character=resolved_character,
        style=resolved_style,
        scene=resolved_scene,
        cinematography=resolved_cinematography,
        dialogue=cleaned_dialogue,
        ending=resolved_ending,
        audio_block=resolved_audio,
    )


def build_character_consistency_mid_base_prompt(
    dialogue: str,
    *,
    character: Optional[str] = None,
    action: Optional[str] = None,
    style: Optional[str] = None,
    scene: Optional[str] = None,
    cinematography: Optional[str] = None,
    ending: Optional[str] = None,
    audio_block: Optional[str] = None,
    legacy_32_visuals: bool = False,
    include_final_ending: bool = True,
) -> str:
    return build_reference_image_scene_base_prompt(
        dialogue,
        character=character,
        style=style,
        scene=scene,
        cinematography=cinematography,
        ending=ending,
        audio_block=audio_block,
        legacy_32_visuals=legacy_32_visuals,
        include_final_ending=include_final_ending,
    )


def build_character_consistency_mid_continuation_prompt(
    dialogue: str,
    *,
    include_final_ending: bool = False,
) -> str:
    return _build_character_consistency_continuation_prompt(
        dialogue,
        continuity=MID_EXTENSION_CONTINUITY,
        include_final_ending=include_final_ending,
    )


def build_lean_veo_light_continuation_prompt(
    dialogue: str,
    *,
    include_final_ending: bool = False,
) -> str:
    ending = EXTENDED_FINAL_ENDING_DIRECTIVE if include_final_ending else LEAN_LIGHT_CONTINUATION_ENDING_DIRECTIVE
    audio_block = LEAN_FINAL_AUDIO_BLOCK if include_final_ending else LEAN_CONTINUATION_AUDIO_BLOCK
    return LEAN_LIGHT_CONTINUATION_PROMPT_TEMPLATE.format(
        dialogue=dialogue.strip(),
        ending=ending,
        audio_block=audio_block,
    )
