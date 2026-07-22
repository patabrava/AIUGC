"""Isolated Raw Camera background generation and comparison artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from io import BytesIO
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError
from app.features.characters.scene_reference import get_scene_bible
from app.features.shot_frames.service import load_raw_camera_system_prompt


@dataclass(frozen=True)
class RawCameraBackgroundResult:
    scene_key: str
    prompt_writer_brief: str
    prompt_writer_output: str
    image_bytes: bytes
    mime_type: str
    provider_model: str


COMPARISON_CELL_SIZE = (768, 1365)
COMPARISON_LABEL_HEIGHT = 96


def build_raw_camera_background_brief(scene_key: str) -> str:
    bible = get_scene_bible(scene_key)
    rejectors = ", ".join(bible.scene_specific_rejectors)
    return (
        "Write one finished image-generation prompt for an actor-free canonical background reference picture. "
        "This is an environment-only vertical 9:16 camera image, not a portrait, casting image, or character frame. "
        "No people, faces, bodies, body parts, hands, or wheelchairs may appear anywhere in the image. "
        "Preserve the exact place identity and relative anchor-object layout so the image remains useful behind an "
        "actor in repeated UGC video generation. Resolve realism through ordinary optics, physically plausible light, "
        "true-to-life materials, subtle wear, natural asymmetry, muted color, and an unretouched camera-file finish. "
        f"Exact place identity: {bible.scene_identity} "
        f"Environment anchors: {bible.generation_anchor}. "
        f"Layout lock: {bible.layout_lock}. "
        f"Lighting: {bible.lighting} "
        f"Forbidden changes: {bible.forbidden_changes} "
        f"Also exclude: {rejectors}. "
        "Use a believable smartphone perspective with enough room context for later actor placement. Keep the scene "
        "sparse and stageable. Exclude text, logos, watermarks, UI, fake HDR, bloom, glow, heavy sharpening, beauty "
        "polish, cinematic grading, pastel fade, synthetic materials, stylization, and decorative drift props. "
        "Return only the complete production-ready prompt; do not generate or discuss the image."
    )


def generate_raw_camera_background(
    *,
    scene_key: str,
    llm_client: Optional[Any] = None,
    image_model: str = "gemini-3.1-flash-image",
    image_size: str = "2K",
) -> RawCameraBackgroundResult:
    client = llm_client or get_llm_client()
    brief = build_raw_camera_background_brief(scene_key)
    prompt_writer_output = client.generate_gemini_text(
        prompt=brief,
        system_prompt=load_raw_camera_system_prompt(),
        max_tokens=4096,
        temperature=0.2,
        thinking_budget=0,
    ).strip()
    if not prompt_writer_output:
        raise ValidationError("Raw Camera background prompt writer returned an empty prompt.")
    if prompt_writer_output[-1] not in ".!?":
        raise ValidationError(
            "Raw Camera background prompt writer returned an incomplete prompt.",
            {"output_length": len(prompt_writer_output), "output_tail": prompt_writer_output[-80:]},
        )
    generated = client.generate_gemini_image(
        prompt=prompt_writer_output,
        model=image_model,
        temperature=0.7,
        aspect_ratio="9:16",
        image_size=image_size,
    )
    return RawCameraBackgroundResult(
        scene_key=get_scene_bible(scene_key).scene_id,
        prompt_writer_brief=brief,
        prompt_writer_output=prompt_writer_output,
        image_bytes=generated["image_bytes"],
        mime_type=str(generated["mime_type"]),
        provider_model=str(generated["model"]),
    )


def _portrait_cell(image_bytes: bytes) -> Image.Image:
    with Image.open(BytesIO(image_bytes)) as source:
        normalized = ImageOps.exif_transpose(source).convert("RGB")
        contained = ImageOps.contain(normalized, COMPARISON_CELL_SIZE, Image.Resampling.LANCZOS)
    cell = Image.new("RGB", COMPARISON_CELL_SIZE, "#f4f1eb")
    cell.paste(
        contained,
        ((COMPARISON_CELL_SIZE[0] - contained.width) // 2, (COMPARISON_CELL_SIZE[1] - contained.height) // 2),
    )
    return cell


def compose_side_by_side(*, control_bytes: bytes, treatment_bytes: bytes, scene_name: str) -> bytes:
    width = COMPARISON_CELL_SIZE[0] * 2
    height = COMPARISON_CELL_SIZE[1] + COMPARISON_LABEL_HEIGHT
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=22)
    small_font = ImageFont.load_default(size=18)
    draw.text((24, 14), scene_name, fill="#17202a", font=font)
    draw.text((24, 54), "Current · Reality-First", fill="#5f6b73", font=small_font)
    draw.text(
        (COMPARISON_CELL_SIZE[0] + 24, 54),
        "Test · Raw Camera Casting Realism",
        fill="#5f6b73",
        font=small_font,
    )
    canvas.paste(_portrait_cell(control_bytes), (0, COMPARISON_LABEL_HEIGHT))
    canvas.paste(_portrait_cell(treatment_bytes), (COMPARISON_CELL_SIZE[0], COMPARISON_LABEL_HEIGHT))
    draw.line(
        (COMPARISON_CELL_SIZE[0], COMPARISON_LABEL_HEIGHT, COMPARISON_CELL_SIZE[0], height),
        fill="#d7d2c9",
        width=2,
    )
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_comparison_index(rows: list[dict[str, str]]) -> str:
    cards = []
    for row in rows:
        cards.append(
            f"""
            <article class="comparison">
              <div class="heading">
                <p>{escape(row['scene_key'])}</p>
                <h2>{escape(row['scene_name'])}</h2>
              </div>
              <img src="{escape(row['comparison_path'])}" alt="Side-by-side comparison for {escape(row['scene_name'])}">
              <div class="labels"><span>Current · Reality-First</span><span>Test · Raw Camera Casting Realism</span></div>
            </article>"""
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Background reference prompt comparison</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; color: #17202a; background: #eeeae2; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; }}
    main {{ width: min(1500px, 100%); margin: 0 auto; padding: 36px 24px 72px; }}
    header {{ display: grid; grid-template-columns: 1.4fr 1fr; gap: 32px; align-items: end; margin-bottom: 36px; }}
    h1 {{ margin: 0; font-size: clamp(2rem, 5vw, 4.8rem); line-height: .95; letter-spacing: -.05em; }}
    header p {{ margin: 0; color: #59636a; line-height: 1.6; }}
    .criteria {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 28px; padding: 0; list-style: none; }}
    .criteria li {{ border: 1px solid #cec7ba; border-radius: 999px; background: #f8f6f1; padding: 8px 12px; font-size: .85rem; }}
    .grid {{ display: grid; gap: 28px; }}
    .comparison {{ overflow: hidden; border: 1px solid #cec7ba; border-radius: 18px; background: white; box-shadow: 0 18px 50px rgba(32, 25, 16, .08); }}
    .heading {{ padding: 20px 22px 14px; }}
    .heading p {{ margin: 0 0 4px; color: #7a6f61; font: 700 .72rem/1.2 ui-monospace, monospace; letter-spacing: .08em; text-transform: uppercase; }}
    h2 {{ margin: 0; font-size: 1.35rem; }}
    .comparison img {{ display: block; width: 100%; height: auto; background: #f4f1eb; }}
    .labels {{ display: grid; grid-template-columns: 1fr 1fr; border-top: 1px solid #ded9cf; color: #4f5960; font-size: .85rem; font-weight: 700; }}
    .labels span {{ padding: 14px 18px; }}
    .labels span + span {{ border-left: 1px solid #ded9cf; }}
    @media (max-width: 760px) {{ header {{ grid-template-columns: 1fr; }} main {{ padding-inline: 12px; }} .labels {{ font-size: .7rem; }} }}
  </style>
</head>
<body>
  <main>
    <header><h1>Background realism, side by side.</h1><p>The production Reality-First plate is always on the left. The isolated Raw Camera Casting Realism treatment is on the right. No production asset was replaced.</p></header>
    <ul class="criteria"><li>Physical realism</li><li>Natural materials</li><li>Believable lighting</li><li>Absence of AI polish</li><li>Scene-layout fidelity</li><li>Actor-free composition</li></ul>
    <section class="grid">{''.join(cards)}</section>
  </main>
</body>
</html>
"""
