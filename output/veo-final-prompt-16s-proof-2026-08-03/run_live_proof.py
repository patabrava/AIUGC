from pathlib import Path

from app.adapters.vertex_ai_client import get_vertex_ai_client
from scripts import run_semantic_ugc_live_smoke as smoke


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
PLATE = ROOT / "output/veo-final-prompt-live-proof-2026-08-03/approved-scene-plate.png"
PLATE_SHA256 = "b82e10a1933d1766092aefc9e2dc9236a4d8c2faf9ba80b7e06fe4d1d0b9a4d5"
SEED = 507150

smoke.RESOLUTION = "1080p"
smoke.PROMPT_TEMPLATE = (
    "Cinematography: One continuous, unedited eight-second vertical smartphone UGC take. "
    "Static seated eye-level camera. The supplied source frame is the authority for the subject, "
    "scene, composition, wardrobe, wheelchair, lighting, and visual style. Preserve its exact "
    "framing and visible boundaries. The camera stays completely still.\n\n"
    "Subject motion: The seated woman speaks directly to the lens with accurate native-German lip "
    "and jaw movement, a restrained conversational expression, irregular natural blinking, quiet "
    "breathing, and minimal speech-coupled head movement. Her shoulders, torso, wheelchair, and "
    "visible body remain stable. Her hands retain the exact visibility and relaxed resting positions "
    "established by the source frame. She communicates through speech and facial expression while "
    "her hands remain at rest.\n\n"
    "Timing: She begins speaking promptly and delivers the dialogue once at a natural conversational "
    "pace, targeting the final spoken word around 6.5 seconds.\n\n"
    "Cut-ready ending: After the final syllable, her speech articulation resolves naturally while she "
    "remains conversationally engaged with the lens, as if the next sentence will follow immediately. "
    "Preserve the same seated posture, shoulder position, wheelchair position, gaze direction, and "
    "resting hand positions through the final frame. Only subtle breathing and natural blinking "
    "continue. Maintain fluid living presence without a concluding gesture, completion expression, "
    "or held end pose.\n\n"
    "Dialogue: “{beat}”\n\n"
    "Audio: One warm adult female voice speaking native German with a natural conversational cadence "
    "and consistent vocal identity. Clean close smartphone-microphone sound with quiet home-office "
    "room tone. The dialogue is the complete spoken performance."
)
smoke.NEGATIVE_PROMPT = (
    "identity drift, face change, facial distortion, age change, hair change, wardrobe change, room "
    "change, lighting change, extra person, wheelchair change, wheelchair deformation, disconnected "
    "wheelchair components, body deformation, malformed hands, extra fingers, fused fingers, hand "
    "gesture, demonstrative hand action, door-opening mime, hand-position change, hands lifting, hands "
    "leaving resting position, hands entering frame, post-dialogue gesture, post-dialogue hand movement, "
    "terminal hand motion, post-dialogue action, body sway, posture reset, large head movement, "
    "exaggerated nodding, concluding nod, farewell expression, completion smile, downward gaze, gaze "
    "drop, looking down, exaggerated mouth closure, pursed-lip end pose, held end pose, frozen stare, "
    "robotic motion, stiff motion, mouth warping, teeth artifacts, lip-sync drift, camera movement, "
    "camera shake, camera pan, camera tilt, camera zoom, camera push-in, camera dolly, camera orbit, "
    "reframing, cut, jump cut, scene transition, wipe transition, end card, freeze frame, repeated "
    "dialogue, extra speech, English speech, music, background voices, subtitles, captions, generated "
    "text, logos, watermarks, gibberish text, plastic skin, waxy skin, over-smoothed skin, beauty "
    "filter, CGI look, 3D render"
)


for index in range(2):
    destination = OUTPUT / f"take-{index}"
    plan = smoke.build_live_plan(
        approved_frame_path=PLATE,
        expected_sha256=PLATE_SHA256,
        script_input_path=OUTPUT / f"take-{index}-script.json",
        output_dir=destination,
        max_budget_usd="3.20",
        max_submissions=1,
        output_count=1,
        retry_requested=False,
        image_generation_collaborators=[],
        seed=SEED,
    )
    result = smoke.execute_live_proof(
        plan,
        confirm_paid_plan=True,
        vertex_factory=get_vertex_ai_client,
        poll_interval_seconds=10.0,
        timeout_seconds=1800.0,
    )
    print(index, result["status"], result["submission"]["operation_id"], flush=True)
