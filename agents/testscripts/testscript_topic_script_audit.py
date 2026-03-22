"""
EYE testscript: multi-topic, multi-tier script-generation audit.

Validates both boundary behaviors:
- malformed Gemini output must be rejected before bad scripts can pass validation
- valid structured tiered output must pass for 8s, 16s, and 32s
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.video_profiles import get_duration_profile
from app.core.errors import ValidationError
from app.features.topics import agents as topic_agents


class AlwaysFailingLLM:
    def generate_gemini_json(self, prompt, json_schema, system_prompt=None, **kwargs):
        raise ValidationError(
            message="Gemini structured output produced invalid JSON",
            details={"raw_content": '{"problem_agitate_solution":["zu kurz"]'},
        )

    def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
        return "Problem-Agitieren-Lösung Ads\n\nViel zu kurz.\n\nBeschreibung\n\nKurz. #A #B #C"


class ValidTieredLLM:
    def generate_gemini_json(self, prompt, json_schema, system_prompt=None, **kwargs):
        if "32-Sekunden-UGC-Videos" in prompt:
            return {
                "problem_agitate_solution": [
                    "Was viele bei der BahnCard-Ermäßigung übersehen, merkst du meist erst mitten im Buchungsprozess. Wenn Nachweise fehlen oder Angaben nicht sauber vorbereitet sind, ziehen sich Rückfragen unnötig. Genau deshalb spare ich Zeit, Geld und Nerven, wenn ich Berechtigung, Unterlagen und Fristen vor der Reise einmal komplett sortiere."
                ],
                "testimonial": [
                    "Ich dachte lange, die BahnCard-Ermäßigung wäre vor allem bürokratisch und deshalb kaum planbar. In Wirklichkeit entsteht der Stress meistens erst, wenn Nachweise fehlen oder zu spät geprüft werden. Seit ich alles vor der Buchung sammle, laufen Kontrolle, Preisfindung und Reiseplanung deutlich ruhiger und verlässlicher für mich."
                ],
                "transformation": [
                    "Früher habe ich Ermäßigungen oft erst dann geprüft, wenn die Reise schon fast feststand. Genau das hat bei mir Rückfragen, Unsicherheit und unnötigen Zeitdruck ausgelöst. Heute kläre ich Berechtigung, Nachweise und Buchungsdetails vorher, und dadurch fühlt sich die gesamte Planung deutlich kontrollierter und wirklich entspannter an."
                ],
                "description": "Die passende BahnCard-Ermäßigung hängt an sauberen Nachweisen, klaren Fristen und einer ruhigen Vorbereitung vor der Buchung. Wenn du Unterlagen vorher prüfst, vermeidest du unnötige Rückfragen und behältst dein Reisebudget besser im Blick. #BahnCard #Schwerbehinderung #BarrierefreiReisen",
            }
        if "16-Sekunden-UGC-Videos" in prompt:
            return {
                "problem_agitate_solution": [
                    "Was viele bei der BahnCard-Ermäßigung übersehen, merkst du oft erst beim Buchen. Wenn Unterlagen oder Nachweise fehlen, kosten dich Rückfragen Zeit, Nerven und manchmal sogar den günstigeren Tarif."
                ],
                "testimonial": [
                    "Ich habe die Ermäßigung früher immer zu spät geprüft und mich dann geärgert. Seit ich Nachweise vorher sortiere, laufen Buchung, Kontrolle und Reiseplanung spürbar ruhiger."
                ],
                "transformation": [
                    "Früher war jede Buchung unnötig chaotisch und voller Rückfragen zur Berechtigung. Heute prüfe ich Unterlagen früher und bekomme die passende BahnCard-Ermäßigung viel planbarer organisiert."
                ],
                "description": "Für eine passende BahnCard-Ermäßigung zählen vor allem klare Nachweise, der richtige Zeitpunkt und eine saubere Vorbereitung. Wenn du das vorher sortierst, sparst du Rückfragen und planst Reisen verlässlicher. #BahnCard #Schwerbehinderung #Mobilitaet",
            }
        return {
            "problem_agitate_solution": [
                "Was viele bei der BahnCard-Ermäßigung übersehen, merkst du erst beim Buchen, wenn fehlende Nachweise plötzlich alles ausbremsen."
            ],
            "testimonial": [
                "Ich prüfe die Unterlagen heute früher, weil mir fehlende Nachweise sonst kurz vor der Reise alles blockieren."
            ],
            "transformation": [
                "Früh sortierte Nachweise machen die BahnCard-Ermäßigung für mich planbarer, ruhiger und deutlich leichter im Alltag unterwegs."
            ],
            "description": "Die BahnCard-Ermäßigung wird leichter, wenn du Nachweise früh prüfst und die Buchung sauber vorbereitest. So sparst du Rückfragen, Zeit und unnötigen Stress im Reisealltag. #BahnCard #Schwerbehinderung #Reiseplanung",
        }

    def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
        raise AssertionError("Structured output should satisfy this audit without text fallback.")


def _word_count(script: str) -> int:
    return len(str(script or "").split())


def _audit_case(topic: str, dossier: Dict[str, Any]) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for tier in (8, 16, 32):
        scripts = topic_agents.generate_dialog_scripts(
            topic=topic,
            scripts_required=1,
            dossier=dossier,
            profile=get_duration_profile(tier),
        )
        bucket_summary = {}
        for bucket in ("problem_agitate_solution", "testimonial", "transformation"):
            script = getattr(scripts, bucket)[0]
            bucket_summary[bucket] = {
                "script": script,
                "words": _word_count(script),
            }
            topic_agents._validate_dialog_script_tier(
                script,
                get_duration_profile(tier),
                context=f"{topic}:{bucket}",
            )
            topic_agents._validate_dialog_script_semantics(
                script,
                context=f"{topic}:{bucket}",
            )
        results.append(
            {
                "tier": tier,
                "description": scripts.description,
                "buckets": bucket_summary,
            }
        )
    return {"topic": topic, "tiers": results}


def _assert_rejection(topic: str, dossier: Dict[str, Any]) -> Dict[str, Any]:
    topic_agents.get_llm_client = lambda: AlwaysFailingLLM()
    try:
        topic_agents.generate_dialog_scripts(
            topic=topic,
            scripts_required=1,
            dossier=dossier,
            profile=get_duration_profile(8),
        )
    except ValidationError as exc:
        return {"topic": topic, "rejected": exc.message}
    raise AssertionError("Malformed PROMPT_2 output should have been rejected.")


def main() -> int:
    cases = [
        (
            "BahnCard & Schwerbehinderung",
            {
                "topic": "BahnCard & Schwerbehinderung",
                "seed_topic": "BahnCard & Schwerbehinderung",
                "source_summary": "Die Deutsche Bahn bietet ermäßigte BahnCards für berechtigte Personen mit Schwerbehindertenausweis an. Früh prüfen spart Rückfragen und Geld.",
                "facts": [
                    "Menschen mit Schwerbehindertenausweis können unter den Voraussetzungen ermäßigte BahnCards erhalten.",
                    "Frühe Prüfung der Unterlagen spart spätere Rückfragen und Verzögerungen.",
                    "Die Ermäßigung entlastet das Reisebudget und macht Fahrten planbarer.",
                ],
                "angle_options": ["Rabatte früh prüfen und Unterlagen sauber vorbereiten"],
            },
        ),
        (
            "Barrierefreie Wege im Alltag",
            {
                "topic": "Barrierefreie Wege im Alltag",
                "seed_topic": "Barrierefreie Wege und Schwellen",
                "source_summary": "Breite Wege, wenig Gefaelle und klare Flaechen sparen im Alltag Kraft und vermeiden Unsicherheit.",
                "facts": [
                    "Barrierefreie Wege brauchen genug Breite und wenig Gefaelle.",
                    "Kleine Schwellen kosten im Alltag oft unnoetig Kraft.",
                ],
                "angle_options": ["barrierefreie Wege im Alltag"],
            },
        ),
        (
            "Hilfsmittelantrag sauber dokumentieren",
            {
                "topic": "Hilfsmittelantrag sauber dokumentieren",
                "seed_topic": "Fotodokumentation und Nachweise",
                "source_summary": "Gute Fotodokumentation spart Rueckfragen und macht Antraege klarer. Wichtige Punkte sind strukturierte Bilder, klare Reihenfolge und nachvollziehbare Details.",
                "facts": [
                    "Klare Bildserien helfen bei Rueckfragen.",
                    "Struktur spart Zeit bei Nachweisen und Nachpruefungen.",
                ],
                "angle_options": ["Dokumentation so aufbauen, dass Rueckfragen seltener werden"],
            },
        ),
    ]

    summary = {"rejections": [], "valid_runs": []}
    for topic, dossier in cases:
        summary["rejections"].append(_assert_rejection(topic, dossier))

    topic_agents.get_llm_client = lambda: ValidTieredLLM()
    for topic, dossier in cases:
        summary["valid_runs"].append(_audit_case(topic, dossier))

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
