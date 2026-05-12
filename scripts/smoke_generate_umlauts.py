"""End-to-end smoke test: generate scripts and captions through the real LLM paths
and confirm output uses proper German umlauts (no ae/oe/ue ASCII transliterations).
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import List

from dotenv import load_dotenv

load_dotenv()

from app.adapters.llm_client import get_llm_client  # noqa: E402
from app.core.german import restore_german_umlauts  # noqa: E402
from app.features.topics.captions import generate_caption_bundle  # noqa: E402

ASCII_FORMS = [
    "fuer", "Fuer", "spaeter", "Spaeter", "spaet", "Huerde", "Huerden",
    "zaehlt", "Mobilitaet", "ausschliesslich", "Erklaerung", "Saetze", "Woerter",
    "vollstaendig", "verfuegbar", "uebernimmt", "ueber", "Ueber", "natuerlich",
    "muessen", "koennen", "Koennen", "waehrend", "groesser", "Geraete",
    "hoeren", "Stueck", "Buecher", "Loesung", "moeglich", "Buero", "Schueler",
    "Glueck", "Schluessel", "Bruecke", "fuehlen", "fuehlt", "fuehrt", "muede",
    "Maerz", "waehlen", "erklaert", "Faelle", "betraegt", "haeufig", "schoen",
    "zugaenglich", "regelmaessig", "staendig", "Aenderung", "kuemmern",
    "Foerderung", "oeffentlich", "OEPNV", "benoetigt", "Bedeurfnis",
    "wuerde", "Strasse",
]
ASCII_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in ASCII_FORMS) + r")\b")


def has_ascii_umlaut(text: str) -> List[str]:
    return list(dict.fromkeys(ASCII_RE.findall(text or "")))


def header(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def run_raw_gemini_test() -> None:
    """Call generate_gemini_text directly with a prompt that historically yielded
    transliterated output. Confirms the LLM adapter safety net catches it."""
    header("TEST 1 — raw Gemini text generation")
    llm = get_llm_client()
    prompt = (
        "Schreibe einen kurzen deutschen Absatz (3-4 Sätze) über barrierefreie "
        "Mobilität im öffentlichen Nahverkehr. Erkläre, warum Rollstuhlnutzende "
        "rechtzeitig planen müssen und wie kleine Hürden den Alltag prägen."
    )
    print(f"PROMPT (clean German):\n{prompt}\n")
    try:
        output = llm.generate_gemini_text(
            prompt=prompt,
            system_prompt="Du bist ein präziser deutscher Texter. Antworte ausschließlich auf Deutsch.",
            max_tokens=2000,
            temperature=0.7,
        )
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return
    print(f"OUTPUT:\n{output}\n")
    bad = has_ascii_umlaut(output)
    if bad:
        print(f"❌ FOUND ASCII transliterations: {bad}")
    else:
        print("✅ No ASCII transliterations — umlauts intact.")


def run_caption_bundle_test() -> None:
    """Generate a caption bundle through the real pipeline."""
    header("TEST 2 — caption bundle generation (real pipeline)")
    topic_title = "Begleitperson im ÖPNV mit Merkzeichen B – kostenlose Fahrt"
    script = (
        "Wusstest du, dass Menschen mit Merkzeichen B im öffentlichen Nahverkehr "
        "eine Begleitperson kostenlos mitnehmen dürfen? Das gilt sogar im Fernverkehr, "
        "wenn die Begleitung im Schwerbehindertenausweis vermerkt ist. Trotzdem werden "
        "viele Fahrgäste im Alltag falsch informiert. Frag im Zweifel nach – und nimm "
        "den Ausweis immer mit."
    )
    research_facts = [
        "Personen mit dem Merkzeichen B haben Anspruch auf eine Begleitperson, die unentgeltlich mitfährt.",
        "Der Anspruch gilt im Nahverkehr und im Fernverkehr der Deutschen Bahn.",
        "Es gibt kein gesetzliches Mindestalter für die Begleitperson.",
        "Im Schwerbehindertenausweis muss der Vermerk eingetragen sein.",
        "Verkehrsunternehmen verlangen oft den Ausweis als Nachweis vor Ort.",
    ]
    print(f"TOPIC: {topic_title}")
    print(f"SCRIPT:\n{script}\n")
    try:
        bundle = generate_caption_bundle(
            topic_title=topic_title,
            post_type="value",
            script=script,
            research_facts=research_facts,
            seed_payload={"strict_seed": {"facts": research_facts}},
        )
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return
    selected = bundle.get("selected_body") or ""
    print("SELECTED CAPTION:")
    print(selected)
    print()
    bad = has_ascii_umlaut(selected)
    if bad:
        print(f"❌ Caption has ASCII transliterations: {bad}")
    else:
        print("✅ Caption is clean — proper umlauts only.")

    variants = bundle.get("variants") or []
    print(f"\nAll {len(variants)} variant(s):")
    for v in variants:
        body = v.get("body") or ""
        bad_v = has_ascii_umlaut(body)
        flag = "✅" if not bad_v else f"❌ {bad_v}"
        print(f"  [{v.get('key')}] {flag}")
        print(f"    {body[:200]}{'...' if len(body) > 200 else ''}")


def run_script_like_generation() -> None:
    """Approximate a script-generation call. We use generate_gemini_text with a prompt
    similar to what prompt2 runtime uses, to confirm scripts come out with umlauts."""
    header("TEST 3 — script-like generation")
    llm = get_llm_client()
    prompt = (
        "Schreibe ein kurzes deutsches TikTok-Skript (etwa 60 Wörter) zum Thema: "
        "\"Barrierefreie Schreibtische — was viele über die richtige Höhe nicht wissen.\" "
        "Beginne mit einem überraschenden Hook. Erwähne konkrete Maße (65 bis 125 cm) "
        "und warum regelmäßige Höhenverstellung dem Rücken hilft. "
        "Verwende natürliche gesprochene Sprache, du-Ansprache. "
        "Schreibe alle deutschen Wörter mit den korrekten Umlauten (ä, ö, ü, ß)."
    )
    print(f"PROMPT (German, asks for umlauts explicitly):\n{prompt}\n")
    try:
        output = llm.generate_gemini_text(
            prompt=prompt,
            system_prompt="Du bist ein deutschsprachiger Skripteditor. Antworte nur mit dem Skripttext.",
            max_tokens=2500,
            temperature=0.8,
        )
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return
    print(f"OUTPUT:\n{output}\n")
    bad = has_ascii_umlaut(output)
    if bad:
        print(f"❌ Script has ASCII transliterations: {bad}")
    else:
        print("✅ Script is clean — proper umlauts only.")


def main() -> int:
    run_raw_gemini_test()
    run_caption_bundle_test()
    run_script_like_generation()
    return 0


if __name__ == "__main__":
    sys.exit(main())
