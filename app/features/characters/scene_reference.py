from __future__ import annotations

import hashlib
import re
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
    "hallway_stairlift_a": SceneBible(
        scene_id="hallway_stairlift_a",
        version=1,
        name="Home stairlift hallway A",
        scene_identity="Home stairlift hallway scene A. Keep this exact room identity across all generated angles: a middle-class German entry hallway meeting a straight wooden staircase, neutral warm-grey painted walls, mid-oak stair treads with white-painted risers, an installed stairlift with a brushed-aluminium rail track running along the right edge of the treads and a folded grey-upholstered stairlift chair parked at the bottom landing, a white-painted wooden handrail mounted on the left wall opposite the rail, a narrow charcoal-and-cream patterned hallway runner on the landing floor, a small landing window high on the upper stairs letting in soft daylight, no people, no front door open, no coats or shoes, no framed pictures, no plants, no clutter.",
        generation_anchor="the same home stairlift hallway behind the actor: straight oak staircase with white risers, brushed-aluminium stairlift rail along the right of the treads, folded grey stairlift chair parked at the bottom landing, white-painted handrail on the left wall, charcoal-and-cream hallway runner, soft daylight from a high landing window",
        consistency_anchor="same stairlift rail and folded chair, oak staircase, left-wall handrail, hallway runner, high landing window, soft daylight",
        layout_lock="same home stairlift hallway: brushed-aluminium stairlift rail along the right of the oak treads, folded grey stairlift chair at the bottom landing, white-painted handrail on the left wall, charcoal-and-cream runner on the landing, high landing window",
        must_match=(
            "brushed-aluminium stairlift rail remains along the right of the treads",
            "folded grey stairlift chair remains parked at the bottom landing",
            "white-painted handrail remains on the left wall opposite the rail",
            "charcoal-and-cream runner remains on the landing floor",
            "soft daylight from the high landing window stays consistent across all three angles",
        ),
        acceptance_checklist=(
            "Does this look like the same stairlift hallway as the other two angles?",
            "Are the stairlift rail, folded chair, left-wall handrail, and hallway runner still present?",
            "Do the anchor objects stay in the same relative positions?",
            "Is the actor still the dominant visible subject with wheelchair context?",
            "Is the lighting still soft landing daylight rather than a different room mood?",
        ),
        anchor_lock="stairlift rail along the treads, folded grey stairlift chair at the bottom landing, white-painted left-wall handrail, and hallway runner stay visible as the same stairlift setup",
        scene_specific_rejectors=(
            "curved or spiral staircase",
            "second flight of stairs",
            "elevator or platform lift",
            "carpeted full stairs",
            "open front door",
            "coat rack or shoe rack",
            "framed wall pictures",
            "potted plants",
        ),
        composition="Vertical 9:16 UGC smartphone frame, medium shot, subject and staircase context visible, hands visible. Camera height 1.4m, 28mm smartphone lens feel, camera about 1.9m from subject.",
        lighting="Soft natural daylight from the high landing window above, gentle even fill across the hallway, calm low-contrast indoor shadows.",
        forbidden_changes="Do not change wall color, staircase material, stairlift rail position, folded chair position, handrail side, runner pattern, window position, or hallway layout.",
        scene_moment="Mid-motion while gesturing toward the folded stairlift chair at the bottom landing, with a natural turn through the scene.",
        wardrobe_lock="cream crewneck sweater, neutral trousers, no logos, no jewelry",
    ),
    "entryway_ramp_a": SceneBible(
        scene_id="entryway_ramp_a",
        version=1,
        name="Home entryway threshold ramp A",
        scene_identity="Home entryway threshold ramp scene A. Keep this exact place identity across all generated angles: a residential front entrance seen from just inside the hall, a white-painted front door with a long upper pane of frosted glass standing ajar at the rear, a low light-grey aluminium threshold ramp bridging the raised doorstep for wheelchair access, a small charcoal coir mat on the pale oak floor in front of the ramp, three brushed-steel coat hooks on the warm off-white left wall with a single tan coat hanging, a slim light-oak console table against the right wall, soft daylight spilling in through the open glazed door from rear-centre, no stairs, no street traffic, no shoe rack, no plants, no wall art.",
        generation_anchor="the same home entryway behind the actor: white front door with frosted upper pane standing ajar at rear-centre, low light-grey aluminium threshold ramp over the doorstep, charcoal coir mat on pale oak floor, brushed-steel coat hooks with one tan coat on the warm off-white left wall, slim light-oak console table at right, soft daylight from the open glazed door",
        consistency_anchor="same white glazed front door ajar, light-grey threshold ramp, charcoal coir mat, steel coat hooks with tan coat, slim oak console, soft daylight from the doorway",
        layout_lock="same home entryway: white glazed front door ajar at rear-centre, light-grey aluminium threshold ramp over the doorstep, charcoal coir mat in front, steel coat hooks on left wall, slim oak console at right",
        must_match=(
            "white glazed front door remains ajar at rear-centre",
            "light-grey aluminium threshold ramp remains over the doorstep",
            "charcoal coir mat remains on the pale oak floor in front of the ramp",
            "steel coat hooks with one tan coat remain on the left wall",
            "slim light-oak console remains against the right wall",
        ),
        acceptance_checklist=(
            "Does this look like the same entryway as the other two angles?",
            "Are the front door, threshold ramp, coir mat, coat hooks, and console still present?",
            "Do the anchor objects stay in the same relative positions?",
            "Is the actor still the dominant visible subject with wheelchair context?",
            "Is the lighting still soft daylight from the doorway rather than a different room mood?",
        ),
        anchor_lock="white glazed front door, light-grey threshold ramp, charcoal coir mat, steel coat hooks with tan coat, and slim oak console stay visible as the same entryway setup",
        scene_specific_rejectors=(
            "stairs",
            "shoe rack",
            "umbrella stand",
            "potted plants",
            "wall art",
            "doormat with text",
            "glass storm door",
            "patterned tiled floor",
        ),
        composition="Vertical 9:16 UGC smartphone frame, medium shot, subject and entryway context visible, hands visible. Camera height 1.35m, 28mm smartphone lens feel, camera about 1.9m from subject.",
        lighting="Soft natural daylight spilling in through the open glazed front door from rear-centre, gentle indoor fill from the left, calm low-contrast shadows.",
        forbidden_changes="Do not change door color, door position, ramp color or placement, mat color, coat hook position, console position, floor color, or daylight direction.",
        scene_moment="Mid-motion while turning toward the open front door, with the body shifting naturally over the threshold ramp.",
        wardrobe_lock="cream crewneck sweater, neutral trousers, no logos, no jewelry",
    ),
    "bedroom_accessibility_a": SceneBible(
        scene_id="bedroom_accessibility_a",
        version=1,
        name="Accessible bedroom A",
        scene_identity="Accessible bedroom scene A. Keep this exact room identity across all generated angles: calm neutral adult bedroom, warm off-white walls, pale oak floor, a single adjustable care bed centered with a plain light-grey duvet and one matching pillow, a clear open transfer space on the actor's left side of the bed, a small light-oak bedside table at rear right holding one matte ceramic table lamp and one clear glass of water, a window with sheer light-linen curtains on the rear-left wall letting in soft daylight, no headboard decor, no wall art, no rug, no plants, no wardrobe, no clutter.",
        generation_anchor="the same calm accessible bedroom behind the actor: warm off-white walls, pale oak floor, single adjustable care bed with plain light-grey duvet, clear transfer space beside the bed, small light-oak bedside table at rear right with one ceramic lamp and one glass of water, sheer light-linen curtains over a rear-left window, soft daylight",
        consistency_anchor="same adjustable care bed, light-grey duvet, bedside table with lamp and glass of water, curtained window, clear transfer space, soft daylight, uncluttered neutral bedroom",
        layout_lock="same calm accessible bedroom: adjustable care bed centered with light-grey duvet, clear transfer space on actor left, light-oak bedside table at rear right with ceramic lamp and glass of water, sheer-curtained window on rear-left wall",
        must_match=(
            "adjustable care bed remains centered with a plain light-grey duvet",
            "clear transfer space remains on the actor's left of the bed",
            "light-oak bedside table remains at rear right with one lamp and one glass of water",
            "sheer light-linen curtains remain over the rear-left window",
            "soft daylight stays consistent across all three angles",
        ),
        acceptance_checklist=(
            "Does this look like the same bedroom as the other two angles?",
            "Are the care bed, bedside table with lamp and glass, transfer space, and curtained window still present?",
            "Do the anchor objects stay in the same relative positions?",
            "Is the actor still the dominant visible subject with wheelchair context?",
            "Is the lighting still soft daylight rather than a different room mood?",
        ),
        anchor_lock="adjustable care bed, light-grey duvet, bedside table with lamp and glass of water, and sheer-curtained rear-left window stay visible as the same four anchors",
        scene_specific_rejectors=(
            "tall wardrobes",
            "bunk or double beds",
            "headboard wall art",
            "rugs",
            "plants",
            "television",
            "dressers with clutter",
            "dark blackout curtains",
        ),
        composition="Vertical 9:16 UGC smartphone frame, medium shot, seated subject and bedroom context visible, hands visible. Camera height 1.35m, 28mm smartphone lens feel, camera about 1.8m from subject.",
        lighting="Soft daylight from the sheer-curtained rear-left window, gentle even fill from front, calm natural shadows.",
        forbidden_changes="Do not change wall color, floor color, bed position, duvet color, bedside table position, lamp or glass placement, curtain position, window position, or room layout.",
        scene_moment="Mid-motion while resting one hand on the bedside table and turning naturally toward the camera.",
        wardrobe_lock="cream crewneck sweater, neutral trousers, no logos, no jewelry",
    ),
    "garden_patio_a": SceneBible(
        scene_id="garden_patio_a",
        version=1,
        name="Garden patio A",
        scene_identity="Residential garden patio scene A. Keep this exact place identity across all generated angles: quiet suburban backyard terrace, pale grey rectangular paving slabs underfoot, one simple light-oak slatted outdoor bench at rear right, three terracotta pots holding low green leafy plants clustered at the left edge of the paving, low trimmed garden greenery and a weathered light-brown wooden slat fence across the rear, soft overcast daylight from above, calm and still air, no garden table, no parasol, no barbecue, no garden tools, no pets, no people.",
        generation_anchor="quiet residential garden patio behind the actor, pale grey paving slabs, light-oak slatted bench at rear right, three terracotta pots with low green plants at the left edge, low garden greenery and a weathered wooden slat fence across the rear, soft overcast daylight",
        consistency_anchor="same paved patio, light-oak slatted bench, three terracotta pots, low greenery, wooden slat fence, soft overcast daylight",
        layout_lock="same residential garden patio: pale grey paving underfoot, light-oak slatted bench at rear right, three terracotta pots at the left edge, low greenery and wooden slat fence across the rear",
        must_match=(
            "light-oak slatted bench remains at rear right",
            "three terracotta pots remain clustered at the left edge of the paving",
            "low garden greenery remains across the rear",
            "weathered wooden slat fence remains behind the greenery",
            "soft overcast daylight stays consistent across all three angles",
        ),
        acceptance_checklist=(
            "Does this look like the same garden patio as the other two angles?",
            "Are the slatted bench, terracotta pots, greenery, and wooden fence still present?",
            "Do the anchor objects stay in the same relative positions?",
            "Is the actor still the dominant visible subject with wheelchair context?",
            "Is the lighting still soft overcast daylight rather than a different outdoor mood?",
        ),
        anchor_lock="light-oak slatted bench, three terracotta pots, low garden greenery, and weathered wooden slat fence stay visible as the same patio setup",
        scene_specific_rejectors=(
            "garden table",
            "parasol or umbrella",
            "barbecue",
            "swimming pool",
            "garden tools",
            "pets",
            "flower beds in bloom",
            "patio string lights",
        ),
        composition="Vertical 9:16 UGC smartphone frame, medium shot, subject and patio context visible, hands visible. Camera height 1.4m, 28mm smartphone lens feel, camera about 1.9m from subject.",
        lighting="Soft overcast daylight from above, gentle even fill, low contrast, natural outdoor skin texture, no harsh sun.",
        forbidden_changes="Do not change paving color, bench position, number or color of terracotta pots, fence material, greenery, lighting weather, or quiet backyard mood.",
        scene_moment="Mid-motion while gesturing toward the garden greenery, with the body turning naturally on the patio.",
        wardrobe_lock="cream crewneck sweater, neutral trousers, no logos, no jewelry",
    ),
    "home_kitchen_advice_a": SceneBible(
        scene_id="home_kitchen_advice_a",
        version=1,
        name="Home kitchen advice A",
        scene_identity="Home kitchen advice scene A. Keep this exact room identity across all generated angles: calm modern accessible kitchen, pale light-oak flat-front cabinetry, matte cream wall, one clear lowered counter section with open knee space on the actor's right, a single matte white kettle and one plain white mug on the lowered counter, light grey quartz worktop, a small square window over a stainless undermount sink at rear, soft daylight entering from upper right, uncluttered surfaces, no upper wall cabinets above the lowered section, no hanging utensils, no fruit bowl, no plants, no appliances on display, no extra furniture.",
        generation_anchor="the same calm accessible kitchen behind the actor: pale light-oak cabinetry, matte cream wall, lowered counter section with open knee space on actor's right holding one white kettle and one white mug, light grey quartz worktop, small square window over a stainless sink at rear, soft daylight from upper right",
        consistency_anchor="same lowered counter section, white kettle, single white mug, square window over sink, light-oak cabinetry, soft daylight",
        layout_lock="same calm accessible kitchen: lowered counter section with open knee space on actor right, white kettle and single white mug on it, light grey quartz worktop, square window over stainless sink at rear, pale light-oak cabinetry",
        must_match=(
            "lowered counter section with open knee space remains on actor right",
            "single white kettle remains on the lowered counter",
            "single white mug remains beside the kettle",
            "square window over the stainless sink remains at rear",
            "pale light-oak cabinetry and soft daylight remain consistent",
        ),
        acceptance_checklist=(
            "Does this look like the same kitchen as the other two angles?",
            "Are the lowered counter, white kettle, single mug, and window over the sink still present?",
            "Do the anchor objects stay in the same relative positions?",
            "Is the actor still the dominant visible subject with wheelchair context?",
            "Is the lighting still soft daylight rather than a different room mood?",
        ),
        anchor_lock="lowered counter section with knee space, white kettle, one white mug, square window over the sink, and pale light-oak cabinetry stay visible as the same kitchen setup",
        scene_specific_rejectors=(
            "upper wall cabinets above the lowered counter",
            "hanging utensils",
            "fruit bowl",
            "plants",
            "displayed appliances",
            "multiple mugs",
            "open shelving with dishes",
            "dark cabinetry",
        ),
        composition="Vertical 9:16 UGC smartphone frame, medium shot, subject and lowered counter context visible, hands visible. Camera height 1.4m, 28mm smartphone lens feel, camera about 1.8m from subject.",
        lighting="Soft natural daylight from the upper-right window over the sink, gentle indoor fill from front, calm natural shadows.",
        forbidden_changes="Do not change cabinetry color, wall color, lowered counter position, kettle color, mug count, worktop color, window position, or lighting direction.",
        scene_moment="Mid-motion while making a natural conversational gesture beside the lowered counter section.",
        wardrobe_lock="cream crewneck sweater, neutral trousers, no logos, no jewelry",
    ),
    "home_dining_nook_advice_a": SceneBible(
        scene_id="home_dining_nook_advice_a",
        version=1,
        name="Home dining nook advice A",
        scene_identity="Home dining nook advice scene A. Keep this exact room identity across all generated angles: small quiet dining nook, warm pale-greige wall, compact light-oak rectangular dining table set against the wall, two simple light-oak chairs (one tucked at the near end, one along the back edge), a single low matte cream ceramic bowl holding two or three lemons centered on the table, a tall narrow window with a thin off-white linen sheer to the actor's left letting in soft daylight, plain pale oak floor, no pendant light, no rug, no wall art, no sideboard, no plants, no clutter on the table.",
        generation_anchor="the same small dining nook behind the actor: warm pale-greige wall, compact light-oak dining table against the wall, two simple light-oak chairs, a single low cream ceramic bowl with two or three lemons centered on the table, tall narrow window with thin linen sheer to actor's left, soft daylight",
        consistency_anchor="same light-oak dining table, two oak chairs, cream lemon bowl, narrow linen-sheer window, warm pale-greige wall, soft daylight",
        layout_lock="same small dining nook: warm pale-greige wall, compact light-oak table against the wall, two oak chairs (one near end, one back edge), single cream bowl with lemons centered, narrow window with linen sheer to actor's left",
        must_match=(
            "light-oak dining table remains against the wall",
            "two simple light-oak chairs remain in the same near-end and back-edge positions",
            "single cream ceramic bowl with two or three lemons remains centered on the table",
            "narrow window with thin linen sheer remains to the actor's left",
            "warm pale-greige wall and soft daylight remain consistent",
        ),
        acceptance_checklist=(
            "Does this look like the same dining nook as the other two angles?",
            "Are the dining table, two chairs, cream lemon bowl, and linen-sheer window still present?",
            "Do the anchor objects stay in the same relative positions?",
            "Is the actor still the dominant visible subject with wheelchair context?",
            "Is the lighting still soft daylight rather than a different room mood?",
        ),
        anchor_lock="light-oak dining table, two oak chairs, one cream lemon bowl, narrow linen-sheer window, and warm pale-greige wall stay visible as the same dining-nook setup",
        scene_specific_rejectors=(
            "pendant light",
            "rug",
            "wall art",
            "sideboard",
            "plants",
            "extra chairs",
            "tablecloth",
            "kitchen counter",
        ),
        composition="Vertical 9:16 UGC smartphone frame, medium shot, subject and table context visible, hands visible. Camera height 1.35m, 28mm smartphone lens feel, camera about 1.7m from subject.",
        lighting="Soft natural daylight from the narrow linen-sheer window at left, gentle warm indoor fill from front, calm low-contrast shadows.",
        forbidden_changes="Do not change wall color, table position, chair count or placement, bowl color, lemon content, window position, floor color, or lighting direction.",
        scene_moment="Mid-motion while resting one hand near the table edge and making a natural conversational gesture toward the camera.",
        wardrobe_lock="cream crewneck sweater, neutral trousers, no logos, no jewelry",
    ),
    "home_office_advice_a": SceneBible(
        scene_id="home_office_advice_a",
        version=1,
        name="Home office advice A",
        scene_identity="Home office advice scene A. Keep this exact room identity across all generated angles: tidy small home study, warm off-white wall behind, a simple light-oak desk against that wall, one closed silver-grey laptop centered on the desk, a small neat stack of plain white papers on the desk's right edge, a narrow oak wall shelf above carrying three or four upright books with muted spines, a plain window with a thin pale roller blind at far left letting in soft daylight, pale oak floor, calm and uncluttered, no people, no monitor, no desk lamp, no cables, no plants, no wall art, no pinboard, no coffee cup.",
        generation_anchor="tidy small home study behind the actor, warm off-white wall, light-oak desk with one closed silver-grey laptop and a small stack of white papers, narrow oak shelf above with a few upright books, plain window with pale roller blind at far left, soft window daylight",
        consistency_anchor="same light-oak desk, closed laptop, small paper stack, single book shelf, pale roller blind, warm off-white wall, soft window daylight",
        layout_lock="same tidy home study: warm off-white wall, light-oak desk against the wall, closed silver-grey laptop centered, small white paper stack at desk right, narrow oak book shelf above, pale roller-blind window at far left",
        must_match=(
            "light-oak desk remains against the off-white wall behind the actor",
            "closed silver-grey laptop remains centered on the desk",
            "small stack of white papers remains at the desk's right edge",
            "narrow oak shelf with a few upright books remains above the desk",
            "pale roller-blind window and soft daylight remain at far left",
        ),
        acceptance_checklist=(
            "Does this look like the same home office as the other two angles?",
            "Are the desk, closed laptop, paper stack, book shelf, and roller-blind window still present?",
            "Do the anchor objects stay in the same relative positions?",
            "Is the actor still the dominant visible subject with wheelchair context?",
            "Is the lighting still soft window daylight rather than a different room mood?",
        ),
        anchor_lock="light-oak desk, closed silver-grey laptop, small white paper stack, narrow oak book shelf, and pale roller-blind window stay visible as the same study setup",
        scene_specific_rejectors=(
            "external monitor",
            "desk lamp",
            "visible cables",
            "plants",
            "wall art",
            "corkboard or pinboard",
            "coffee mug",
            "office chair clutter",
        ),
        composition="Vertical 9:16 UGC smartphone frame, medium shot, subject and desk context visible, hands visible. Camera height 1.35m, 28mm smartphone lens feel, camera about 1.8m from subject.",
        lighting="Soft daylight from the pale roller-blind window at far left, gentle warm indoor fill from front, calm natural shadows.",
        forbidden_changes="Do not change wall color, desk position, laptop color or closed state, paper stack position, book shelf, window position, floor color, or lighting direction.",
        scene_moment="Mid-motion while making a natural conversational gesture beside the desk.",
        wardrobe_lock="cream crewneck sweater, neutral trousers, no logos, no jewelry",
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
        instruction="front-facing waist-up seated smartphone reference, shoulders square to camera, direct eye contact, same body scale as the other references",
        motion_instruction="quiet forward-facing seated pause with lap, hands, scene anchors, and wheelchair armrest visible",
        seed_offset=101,
    ),
    SceneReferenceAngle(
        key="left_three_quarter",
        label="Left three-quarter",
        instruction="slight left three-quarter waist-up seated smartphone reference, face clearly recognizable, same body scale and camera distance as the front reference",
        motion_instruction="quiet seated pause with only a subtle left angle change, lap, hands, scene anchors, and wheelchair armrest visible",
        seed_offset=202,
    ),
    SceneReferenceAngle(
        key="right_profile",
        label="Right profile",
        instruction="slight right three-quarter waist-up seated smartphone reference, face clearly recognizable, same body scale and camera distance as the front reference",
        motion_instruction="quiet seated pause with only a subtle right angle change, lap, hands, scene anchors, and wheelchair armrest visible",
        seed_offset=303,
    ),
)


def get_scene_reference_angle(angle_key: str) -> SceneReferenceAngle:
    for angle in REQUIRED_SCENE_REFERENCE_ANGLES:
        if angle.key == angle_key:
            return angle
    raise KeyError(f"Unknown scene reference angle: {angle_key}")


# Specialized scenes are selected when the script/topic explicitly names them. Order is
# priority: the first scene whose pattern matches the lowercased "{script} {topic}" text
# wins. entryway is checked before stairlift so "Treppenrampe vor der Haustür" routes to
# the ramp scene rather than the staircase. Tokens are regex fragments hardened against
# German compound false friends (verified against a red-team corpus): word-start boundaries
# (_WORD_START) and negative look-arounds keep e.g. "Pflegestufen", "Bad Kissingen",
# "Mobilitätshilfe", "Autonomie", "Kindergarten" and "Wintergarten" out of these scenes.
_WORD_START = r"(?<![a-zà-ÿ])"  # token sits at the start of a (possibly compound) word
SPECIALIZED_SCENE_ROUTES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("entryway_ramp_a", "entryway_terms", ("hauseingang", "eingang", "haustür", "haustuer", "türschwelle", "tuerschwelle", "türverbreiterung", "tuerverbreiterung", "rampe", "auffahrrampe", "rollstuhlrampe", "threshold", "doorstep")),
    ("hallway_stairlift_a", "stairlift_terms", ("treppenlift", _WORD_START + "treppe", "treppenstufe", "stiege", "handlauf", "geländer", "gelaender", "stairlift", "stairs", "stuhllift", "sitzlift", "plattformlift", "hublift", "senkrechtlift", "etagenlift", _WORD_START + "lift", "etage")),
    ("bathroom_accessibility_a", "bathroom_terms", ("badezimmer", "badewanne", "badumbau", "barrierefreies bad", "dusch", "toilette", "bathroom", "haltegriff", "wc", "sanitär", "sanitaer", "nasszelle", "waschtisch", "waschbecken")),
    ("bedroom_accessibility_a", "bedroom_terms", ("schlafzimmer", "schlafraum", "pflegebett", "krankenbett", "bettkante", "bettgalgen", "matratze", "nachttisch", "aufstehhilfe", "aufrichthilfe", "dekubitus", "wundliegen", "lagerung", "bedroom", "nightstand")),
    ("car_transfer_residential_a", "mobility_terms", (_WORD_START + r"auto(?![a-zà-ÿ])", "autotransfer", "autositz", "ins auto", "im auto", "aus dem auto", "pkw", "fahrzeug", r"car(?!e)", "beifahrersitz", "schwenksitz", "einstiegshilfe", "umsetzhilfe", "rutschbrett")),
    ("garden_patio_a", "garden_terms", (_WORD_START + r"garten(?!zwerg)(?!center)", "terrasse", "hinterhof", "backyard", "fresh air", "outdoor", _WORD_START + "patio")),
)

_SPECIALIZED_SCENE_MATCHERS = tuple(
    (scene_id, reason_code, re.compile("|".join(tokens)))
    for scene_id, reason_code, tokens in SPECIALIZED_SCENE_ROUTES
)

# Seed-data fields that carry the per-post topic label, richest first. Matching and the
# neutral rotation key are built from these plus the script, so a video routes by its own
# topic even when the script text does not repeat the keyword.
_TOPIC_FIELDS = ("topic_title", "canonical_topic", "research_title", "topic")

# Generic / abstract advice content (the bulk of scripts) carries no scene keyword. Rather
# than collapsing every such video onto one room, it is distributed deterministically across
# these neutral talking-head plates, so videos vary while staying reproducible.
NEUTRAL_SCENE_POOL: tuple[str, ...] = (
    "home_living_room_advice_a",
    "home_kitchen_advice_a",
    "home_dining_nook_advice_a",
    "home_office_advice_a",
)


def _neutral_pool_scene_for(rotation_key: str) -> str:
    digest = hashlib.md5(rotation_key.encode("utf-8")).hexdigest()
    return NEUTRAL_SCENE_POOL[int(digest, 16) % len(NEUTRAL_SCENE_POOL)]


def map_script_to_scene_intent(
    *,
    script: str,
    post_type: str,
    target_length_tier: int,
    seed_data: dict[str, Any],
) -> ScriptIntent:
    topic_text = " ".join(str(seed_data.get(key) or "") for key in _TOPIC_FIELDS)
    text = f"{script} {topic_text}".lower()
    for scene_id, reason_code, matcher in _SPECIALIZED_SCENE_MATCHERS:
        if matcher.search(text):
            return ScriptIntent(scene_id, "everyday_sweater", reason_code)
    rotation_key = next(
        (value for key in _TOPIC_FIELDS if (value := str(seed_data.get(key) or "").strip())),
        str(script or "").strip(),
    )
    if rotation_key:
        scene_id = _neutral_pool_scene_for(f"{rotation_key}|{post_type}")
        return ScriptIntent(scene_id, "everyday_sweater", "neutral_rotation")
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
        "Photorealistic vertical UGC smartphone still, waist-up seated wheelchair-user reference "
        f"in {scene.generation_anchor}. "
        "Mandatory composition: camera 1.8 meters from subject at chest height, top of head through lap visible, "
        "full torso visible, lap, hands, scene anchors, and wheelchair armrest visible in the lower frame, "
        "face occupying 10 to 16 percent of image height, eyes and facial features sharp. "
        f"Background is the same supporting scene: {scene.generation_anchor}. "
        f"{actor_ref}, one recognizable adult woman, is seated in this scene wearing {wardrobe}. "
        f"{actor_ref} is the dominant identity signal and the only visible adult person in the frame. "
        "Keep the scene recognizable and consistent behind the actor, with lap, hands, scene anchors, and wheelchair armrest visible. "
        "Do not use a headshot, passport photo, corporate headshot, business portrait, suit jacket, blazer, collared white blouse, white shirt, blue blouse, close-up crop, face-only crop, shoulders-only crop, glamour portrait, full-body pose, distant shot, wide establishing shot, tiny face, or scene-dominant composition. "
        "Keep the same wardrobe across all angles: same cream crewneck sweater and neutral trousers. "
        f"Keep the same supporting location details across every angle: {scene.consistency_anchor}. "
        f"Do not add {_human_join_rejectors(scene.scene_specific_rejectors)}. "
        "Casual accessibility UGC still, direct-to-camera friendly expression when angle allows, natural skin texture, soft realistic lighting, no text, no logo."
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
        f"Pose requirement: {angle.motion_instruction}."
    )
