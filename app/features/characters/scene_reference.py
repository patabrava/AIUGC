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


def build_scene_reference_prompt(*, actor_name: str, scene_key: str, wardrobe_key: str, post_type: str) -> str:
    scene = SCENE_CATALOG[scene_key]
    wardrobe = WARDROBE_SET[wardrobe_key]
    return (
        f"Photorealistic vertical UGC still of {actor_name}, one recognizable adult person, "
        f"wearing {wardrobe}, seated naturally in a wheelchair, in {scene}. "
        f"Medium close-up, direct-to-camera friendly expression, natural skin texture, no text, no logo."
    )
