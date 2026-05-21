from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCENE_CATALOG = {
    "bathroom_adaptation": "bright accessible bathroom with matte white tile, grab rail, folded towel, and soft daylight",
    "car_transfer": "parked compact car beside a calm residential street, open passenger door, soft overcast daylight",
    "neutral_home": "quiet modern living room with warm neutral wall, small side table, one green plant, and soft window light",
    "home_product_demo": "tidy product-friendly home interior with neutral wall, clear table surface, and bright natural light",
    "office_explainer": "compact home office with pale wall, laptop closed on desk, neat papers, and soft side light",
}

WARDROBE_SET = {
    "everyday_sweater": "cream crewneck sweater, no logos, no jewelry, natural makeup",
    "casual_blazer": "soft beige blazer over white top, no logos, no jewelry",
    "home_cardigan": "light grey cardigan over plain white top, no logos, no jewelry",
}


@dataclass(frozen=True)
class ScriptIntent:
    scene_key: str
    wardrobe_key: str
    reason_code: str


@dataclass(frozen=True)
class SceneReferenceAngle:
    key: str
    label: str
    instruction: str
    seed_offset: int


REQUIRED_SCENE_REFERENCE_ANGLES = (
    SceneReferenceAngle(
        key="front_mid",
        label="Front",
        instruction="front-facing medium close-up, shoulders square to camera, direct eye contact",
        seed_offset=101,
    ),
    SceneReferenceAngle(
        key="left_three_quarter",
        label="Left three-quarter",
        instruction="left three-quarter angle, body turned slightly away, face still clearly recognizable",
        seed_offset=202,
    ),
    SceneReferenceAngle(
        key="right_profile",
        label="Right profile",
        instruction="right-side profile angle, same person and same scene, face contour clearly visible",
        seed_offset=303,
    ),
)


def get_scene_reference_angle(angle_key: str) -> SceneReferenceAngle:
    for angle in REQUIRED_SCENE_REFERENCE_ANGLES:
        if angle.key == angle_key:
            return angle
    raise KeyError(f"Unknown scene reference angle: {angle_key}")


def map_script_to_scene_intent(
    *,
    script: str,
    post_type: str,
    target_length_tier: int,
    seed_data: dict[str, Any],
) -> ScriptIntent:
    text = f"{script} {seed_data.get('topic_title', '')} {seed_data.get('topic', '')}".lower()
    if any(token in text for token in ("bad", "dusche", "toilette", "badezimmer", "bathroom", "sicherheit")):
        return ScriptIntent("bathroom_adaptation", "everyday_sweater", "bathroom_terms")
    if any(token in text for token in ("auto", "car", "mobilitaet", "mobility", "transfer", "reise")):
        return ScriptIntent("car_transfer", "casual_blazer", "mobility_terms")
    if post_type == "product":
        return ScriptIntent("home_product_demo", "home_cardigan", "product_default")
    if any(token in text for token in ("erklaert", "advice", "explainer")):
        return ScriptIntent("office_explainer", "casual_blazer", "explainer_terms")
    return ScriptIntent("neutral_home", "everyday_sweater", "default")


def build_scene_reference_prompt(
    *,
    actor_name: str,
    scene_key: str,
    wardrobe_key: str,
    post_type: str,
    provider_lora_name: str | None = None,
) -> str:
    scene = SCENE_CATALOG[scene_key]
    wardrobe = WARDROBE_SET[wardrobe_key]
    actor_ref = f"@{provider_lora_name}::100" if provider_lora_name else actor_name
    return (
        f"Photorealistic vertical UGC still of {actor_ref}, one recognizable adult person, "
        f"wearing {wardrobe}, seated naturally in a wheelchair, in {scene}. "
        f"Medium close-up, direct-to-camera friendly expression, natural skin texture, no text, no logo."
    )


def build_scene_reference_prompt_for_angle(
    *,
    actor_name: str,
    scene_key: str,
    wardrobe_key: str,
    post_type: str,
    angle_key: str,
    provider_lora_name: str | None = None,
) -> str:
    base_prompt = build_scene_reference_prompt(
        actor_name=actor_name,
        scene_key=scene_key,
        wardrobe_key=wardrobe_key,
        post_type=post_type,
        provider_lora_name=provider_lora_name,
    )
    angle = get_scene_reference_angle(angle_key)
    return (
        f"{base_prompt} Keep the exact same background and wardrobe. "
        f"Camera angle requirement: {angle.instruction}."
    )
