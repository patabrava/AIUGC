from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCENE_REFERENCE_IDENTITY_STRENGTH = 100
SCENE_REFERENCE_FIXED_GENERATION = False
SCENE_REFERENCE_ENGINE = "magnific_sparkle"
SCENE_REFERENCE_CREATIVE_DETAILING = 18
SCENE_REFERENCE_RESOLUTION = "2k"
SCENE_REFERENCE_DEFAULT_STYLE_STRENGTH = 65

WARDROBE_SET = {
    "everyday_sweater": "cream crewneck sweater, neutral beige trousers, no logos, no jewelry, no glasses, natural makeup",
    "casual_blazer": "soft beige blazer over white top, no logos, no jewelry",
    "home_cardigan": "light grey cardigan over plain white top, no logos, no jewelry",
}


@dataclass(frozen=True)
class ScriptIntent:
    scene_key: str
    wardrobe_key: str
    reason_code: str


@dataclass(frozen=True)
class SceneBible:
    scene_id: str
    version: int
    name: str
    scene_identity: str
    generation_anchor: str
    consistency_anchor: str
    layout_lock: str
    must_match: tuple[str, ...]
    acceptance_checklist: tuple[str, ...]
    anchor_lock: str
    scene_specific_rejectors: tuple[str, ...]
    composition: str
    lighting: str
    forbidden_changes: str
    scene_moment: str
    wardrobe_lock: str = "cream crewneck sweater, neutral trousers, no logos, no jewelry"

    def scene_consistency_contract(self) -> dict[str, Any]:
        return {
            "scene_bible_id": self.scene_id,
            "scene_bible_version": self.version,
            "layout_lock": self.layout_lock,
            "anchor_lock": self.anchor_lock,
            "wardrobe_lock": self.wardrobe_lock,
            "wardrobe_drift_rejector": "different pants color, changed sweater, logos, jewelry, glasses, hat, or changed hairstyle",
            "drift_rejectors_by_scene": list(self.scene_specific_rejectors),
            "must_match": list(self.must_match),
            "drift_rejectors": [
                "different room or location",
                "moved anchor objects",
                "missing wheelchair context",
                "extra adult person",
                "changed wardrobe",
                "changed lighting family",
            ],
            "acceptance_checklist": list(self.acceptance_checklist),
        }

    def provider_metadata(self) -> dict[str, Any]:
        return {
            "scene_bible_id": self.scene_id,
            "scene_bible_version": self.version,
            "scene_bible_name": self.name,
            "scene_bible_identity": self.scene_identity,
            "scene_generation_anchor": self.generation_anchor,
            "scene_consistency_contract": self.scene_consistency_contract(),
        }


@dataclass(frozen=True)
class SceneReferenceAngle:
    key: str
    label: str
    instruction: str
    motion_instruction: str
    seed_offset: int


SCENE_BIBLES = {
    "bathroom_accessibility_a": SceneBible(
        scene_id="bathroom_accessibility_a",
        version=1,
        name="Accessible bathroom A",
        scene_identity=(
            "Accessible bathroom scene A. Keep this exact room identity across all generated angles: small rectangular bathroom, matte off-white square wall tiles, pale grey microcement floor, single horizontal brushed-steel grab rail on the actor's left, wall-mounted white sink at rear right, folded sage-green towel on a narrow oak shelf, frosted window high on rear wall, soft daylight entering from upper left, no mirror visible, no shower curtain, no plants, no extra furniture."
        ),
        generation_anchor=(
            "the same compact accessible bathroom: off-white square wall tiles, pale grey floor, brushed-steel grab rail behind actor left, wall-mounted white sink behind actor right, frosted window high rear-left, one narrow oak shelf with one folded sage-green towel"
        ),
        consistency_anchor=(
            "same grab rail, sink, sage-green towel shelf, frosted window, soft daylight, uncluttered accessible bathroom layout"
        ),
        layout_lock=(
            "same compact accessible bathroom: grab rail behind actor left, white sink behind actor right, "
            "frosted window high rear-left, oak towel shelf with folded sage-green towel"
        ),
        must_match=(
            "grab rail remains behind actor left",
            "white wall-mounted sink remains behind actor right",
            "frosted window remains high on the rear-left wall",
            "sage-green towel remains folded on a narrow oak shelf",
            "soft daylight stays consistent across all three angles",
        ),
        acceptance_checklist=(
            "Does this look like the same bathroom as the other two angles?",
            "Are the grab rail, sink, window, and towel shelf still present?",
            "Do the anchor objects stay in the same relative positions?",
            "Is the actor still the dominant visible subject with wheelchair context?",
            "Is the lighting still soft daylight rather than a different room mood?",
        ),
        anchor_lock=(
            "grab rail, wall-mounted sink, frosted window, and single oak towel shelf stay visible as the same four anchors"
        ),
        scene_specific_rejectors=(
            "tall cabinets",
            "doors behind the actor",
            "plants",
            "ladder shelves",
            "radiators",
            "mirrors",
            "shower curtains",
            "extra towels",
        ),
        composition=(
            "Vertical 9:16 UGC smartphone frame, medium shot, subject and room context visible, hands visible. Camera height 1.35m, 28mm smartphone lens feel, camera about 1.8m from subject."
        ),
        lighting="Soft daylight from high rear-left frosted window, gentle fill from front-right, no dramatic shadows.",
        forbidden_changes=(
            "Do not change wall color, floor color, grab rail position, towel color, sink position, window position, lighting direction, or room layout."
        ),
        scene_moment="Mid-motion while reaching toward the grab rail, with a natural turn through the scene.",
    ),
    "car_transfer_residential_a": SceneBible(
        scene_id="car_transfer_residential_a",
        version=1,
        name="Residential car transfer A",
        scene_identity=(
            "Residential car transfer scene A. Keep this exact location identity across all generated angles: silver compact hatchback parked beside a calm residential curb, front passenger door open, dark grey car interior visible, low brick garden wall behind the car, pale sidewalk paving, muted green hedge in the far background, overcast daylight, no traffic, no pedestrians, no storefronts."
        ),
        generation_anchor=(
            "quiet residential curb beside a silver compact hatchback, front passenger door open, dark grey car interior visible, low brick garden wall and muted green hedge behind the actor, soft overcast daylight"
        ),
        consistency_anchor=(
            "same silver hatchback, open passenger door, quiet curb, low brick garden wall, muted hedge, overcast daylight"
        ),
        layout_lock=(
            "same quiet residential curb: silver compact hatchback beside actor, open passenger door, "
            "dark grey interior, low brick garden wall, muted green hedge"
        ),
        must_match=(
            "silver hatchback remains next to the actor",
            "front passenger door remains open",
            "dark grey car interior remains visible",
            "low brick garden wall remains behind the car",
            "muted green hedge remains in the far background",
        ),
        acceptance_checklist=(
            "Does this look like the same curbside car setup as the other two angles?",
            "Is the silver hatchback still next to the actor?",
            "Is the front passenger door still open with dark grey interior visible?",
            "Do the brick wall and hedge stay in the same location family?",
            "Is the lighting still soft overcast daylight?",
        ),
        anchor_lock=(
            "silver compact hatchback, open front passenger door, dark grey interior, low brick wall, and hedge stay visible as the same curbside setup"
        ),
        scene_specific_rejectors=(
            "black car",
            "white van",
            "closed passenger door",
            "busy traffic",
            "storefronts",
            "pedestrians",
            "parking garage",
            "night lighting",
        ),
        composition=(
            "Vertical 9:16 UGC smartphone frame, medium shot, subject and open passenger door visible, hands visible. Camera height 1.4m, 28mm smartphone lens feel, camera about 2m from subject."
        ),
        lighting="Soft overcast daylight from above-front, low contrast, natural skin texture.",
        forbidden_changes=(
            "Do not change car color, door position, curb setting, background wall, hedge, lighting weather, or quiet residential mood."
        ),
        scene_moment="Mid-motion while turning toward the open passenger door, with the body shifting naturally.",
    ),
    "home_living_room_advice_a": SceneBible(
        scene_id="home_living_room_advice_a",
        version=1,
        name="Home living room advice A",
        scene_identity=(
            "Home living room advice scene A. Keep this exact room identity across all generated angles: quiet modern living room, warm off-white wall, narrow light-oak side table on actor's right, single matte white ceramic mug on the table, one small green rubber plant in a terracotta pot, linen beige curtain at far left, pale oak floor, no television, no bed, no kitchen, no wall art."
        ),
        generation_anchor=(
            "quiet modern living room behind the actor, warm off-white wall, narrow light-oak side table on actor's right with a single white ceramic mug, small green plant in terracotta pot, linen beige curtain at far left, soft window light"
        ),
        consistency_anchor=(
            "same side table, white mug, small green plant, beige curtain, warm off-white wall, soft window light"
        ),
        layout_lock=(
            "same quiet living room: warm off-white wall, narrow light-oak side table on actor right, "
            "white mug, small green plant, beige curtain at far left"
        ),
        must_match=(
            "light-oak side table remains on actor right",
            "single white mug remains on the side table",
            "small green plant remains in a terracotta pot",
            "beige curtain remains at far left",
            "warm off-white wall and soft window light remain consistent",
        ),
        acceptance_checklist=(
            "Does this look like the same living room as the other two angles?",
            "Are the side table, white mug, small plant, and beige curtain still present?",
            "Do the anchor objects stay in the same relative positions?",
            "Is the actor still the dominant visible subject with wheelchair context?",
            "Is the lighting still soft window light rather than a different room mood?",
        ),
        anchor_lock=(
            "light-oak side table, one white mug, one terracotta plant, beige curtain, and warm off-white wall stay visible as the same living room setup"
        ),
        scene_specific_rejectors=(
            "television",
            "bed",
            "kitchen",
            "wall art",
            "large sofa",
            "multiple mugs",
            "different plant pots",
            "dark curtains",
        ),
        composition=(
            "Vertical 9:16 UGC smartphone frame, medium shot, subject and table context visible, hands visible. Camera height 1.35m, 28mm smartphone lens feel, camera about 1.7m from subject."
        ),
        lighting="Soft window light from left side, gentle warm indoor fill from front, calm natural shadows.",
        forbidden_changes=(
            "Do not change wall color, table position, plant type, mug color, curtain position, floor color, or lighting direction."
        ),
        scene_moment="Mid-motion while making a natural conversational gesture beside the side table.",
    ),
}

SCENE_ALIASES = {
    "bathroom_adaptation": "bathroom_accessibility_a",
    "car_transfer": "car_transfer_residential_a",
    "neutral_home": "home_living_room_advice_a",
    "home_product_demo": "home_living_room_advice_a",
    "office_explainer": "home_living_room_advice_a",
}

SCENE_CATALOG = {
    **{scene_id: bible.scene_identity for scene_id, bible in SCENE_BIBLES.items()},
    **{alias: SCENE_BIBLES[scene_id].scene_identity for alias, scene_id in SCENE_ALIASES.items()},
}


def parse_scene_reference_style_loras(raw_config: str | None) -> dict[str, list[dict[str, int | str]]]:
    parsed: dict[str, list[dict[str, int | str]]] = {}
    for chunk in str(raw_config or "").split(","):
        entry = chunk.strip()
        if not entry or "=" not in entry:
            continue
        scene_key, style_spec = entry.split("=", 1)
        scene_id = SCENE_ALIASES.get(scene_key.strip(), scene_key.strip())
        if scene_id not in SCENE_BIBLES:
            continue
        style_name, _, strength_text = style_spec.strip().partition(":")
        style_name = style_name.strip()
        if not style_name:
            continue
        strength = SCENE_REFERENCE_DEFAULT_STYLE_STRENGTH
        if strength_text.strip():
            try:
                strength = int(strength_text.strip())
            except ValueError:
                strength = SCENE_REFERENCE_DEFAULT_STYLE_STRENGTH
        strength = max(0, min(200, strength))
        parsed[scene_id] = [{"name": style_name, "strength": strength}]
    return parsed


def scene_reference_style_loras_for(scene_key: str, raw_config: str | None) -> list[dict[str, int | str]]:
    scene_id = SCENE_ALIASES.get(scene_key, scene_key)
    return parse_scene_reference_style_loras(raw_config).get(scene_id, [])


def _human_join_rejectors(rejectors: tuple[str, ...]) -> str:
    if len(rejectors) <= 1:
        return "".join(rejectors)
    return f"{', '.join(rejectors[:-1])}, or {rejectors[-1]}"


def get_scene_bible(scene_key: str) -> SceneBible:
    scene_id = SCENE_ALIASES.get(scene_key, scene_key)
    return SCENE_BIBLES[scene_id]


def build_scene_bible_provider_metadata(scene_key: str) -> dict[str, Any]:
    return get_scene_bible(scene_key).provider_metadata()


def build_scene_consistency_contract(scene_key: str) -> dict[str, Any]:
    return get_scene_bible(scene_key).scene_consistency_contract()


REQUIRED_SCENE_REFERENCE_ANGLES = (
    SceneReferenceAngle(
        key="front_mid",
        label="Front",
        instruction="front-facing medium close-up, shoulders square to camera, direct eye contact",
        motion_instruction="natural forward-facing pause within the same motion",
        seed_offset=101,
    ),
    SceneReferenceAngle(
        key="left_three_quarter",
        label="Left three-quarter",
        instruction="left three-quarter angle, body turned slightly away, face still clearly recognizable",
        motion_instruction="natural turn through the scene toward actor left",
        seed_offset=202,
    ),
    SceneReferenceAngle(
        key="right_profile",
        label="Right profile",
        instruction="right-side profile angle, same person and same scene, face contour clearly visible",
        motion_instruction="natural side-profile continuation of the same motion",
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
    if any(token in text for token in ("bad", "dusche", "toilette", "badezimmer", "bathroom", "haltegriff", "wc")):
        return ScriptIntent("bathroom_accessibility_a", "everyday_sweater", "bathroom_terms")
    if any(token in text for token in ("auto", "car", "mobilitaet", "mobility", "transfer", "reise")):
        return ScriptIntent("car_transfer_residential_a", "everyday_sweater", "mobility_terms")
    if post_type == "product":
        return ScriptIntent("home_living_room_advice_a", "everyday_sweater", "product_default")
    return ScriptIntent("home_living_room_advice_a", "everyday_sweater", "default")


def build_scene_reference_prompt(
    *,
    actor_name: str,
    scene_key: str,
    wardrobe_key: str,
    post_type: str,
    provider_lora_name: str | None = None,
) -> str:
    scene = get_scene_bible(scene_key)
    wardrobe = WARDROBE_SET[wardrobe_key]
    actor_ref = f"@{provider_lora_name}::{SCENE_REFERENCE_IDENTITY_STRENGTH}" if provider_lora_name else actor_name
    return (
        f"Photorealistic vertical UGC smartphone still of {actor_ref}, one recognizable adult woman, "
        "large face identity lock, chest-up seated portrait framing, "
        "face occupying 35 to 45 percent of image height, full head visible, eyes and facial features sharp, "
        f"wearing {wardrobe}. "
        f"Background is the same supporting scene: {scene.generation_anchor}. "
        f"{actor_ref} is the dominant identity signal and the only visible adult person in the frame. "
        "Keep the scene recognizable but secondary behind the actor. "
        "Do not show the full wheelchair, full legs, full-body pose, distant shot, wide establishing shot, tiny face, or scene-dominant composition. "
        "Keep the same wardrobe across all angles: same cream crewneck sweater and neutral trousers. "
        f"Keep the same supporting location details across every angle: {scene.consistency_anchor}. "
        f"Do not add {_human_join_rejectors(scene.scene_specific_rejectors)}. "
        "Medium close-up, direct-to-camera friendly expression when angle allows, natural skin texture, soft realistic lighting, no text, no logo."
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
        f"{base_prompt}\n\n"
        "Keep the exact same background location, wardrobe, lighting, and actor identity. "
        f"Camera angle requirement: {angle.instruction}. "
        f"Motion requirement: {angle.motion_instruction}."
    )
