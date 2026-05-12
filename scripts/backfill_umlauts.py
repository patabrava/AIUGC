"""One-shot backfill: restore German umlauts in stored captions/scripts.

Scope: replaces ASCII-transliterated tokens ("fuer", "spaeter", "Huerden", ...) with
proper umlauts ("für", "später", "Hürden", ...) in caption/script fields. Uses
word-boundary regex so only standalone German words are rewritten; substrings inside
unrelated tokens (e.g. "Steuer", "kaufen", "Gross"-as-surname) are untouched.

Run:
    python3 scripts/backfill_umlauts.py --dry-run
    python3 scripts/backfill_umlauts.py --apply
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Any, Dict, Iterable, List, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ["SUPABASE_KEY"]
)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

# (ascii_form, umlaut_form). Case matters — we list each capitalization separately.
# These are deliberately conservative: only words we observed in the codebase fallbacks
# or that are unambiguously German (no English/Latin collisions).
REPLACEMENTS: List[Tuple[str, str]] = [
    ("fuer", "für"),
    ("Fuer", "Für"),
    ("FUER", "FÜR"),
    ("spaeter", "später"),
    ("Spaeter", "Später"),
    ("spaet", "spät"),
    ("Spaet", "Spät"),
    ("Huerde", "Hürde"),
    ("Huerden", "Hürden"),
    ("zaehlt", "zählt"),
    ("zaehl", "zähl"),
    ("Mobilitaet", "Mobilität"),
    ("ausschliesslich", "ausschließlich"),
    ("Erklaerungen", "Erklärungen"),
    ("Erklaerung", "Erklärung"),
    ("Saetze", "Sätze"),
    ("Woerter", "Wörter"),
    ("vollstaendig", "vollständig"),
    ("abzukuerzen", "abzukürzen"),
    ("verfuegbar", "verfügbar"),
    ("uebernimmt", "übernimmt"),
    ("ueber", "über"),
    ("Ueber", "Über"),
    ("natuerlich", "natürlich"),
    ("Natuerlich", "Natürlich"),
    ("muessen", "müssen"),
    ("koennen", "können"),
    ("Koennen", "Können"),
    ("waehrend", "während"),
    ("Waehrend", "Während"),
    ("groesser", "größer"),
    ("Maedchen", "Mädchen"),
    ("Geraet", "Gerät"),
    ("Geraete", "Geräte"),
    ("hoeren", "hören"),
    ("Hoeren", "Hören"),
    ("Stueck", "Stück"),
    ("Buecher", "Bücher"),
    ("Loesung", "Lösung"),
    ("Loesungen", "Lösungen"),
    ("moeglich", "möglich"),
    ("Moeglich", "Möglich"),
    ("Buero", "Büro"),
    ("Schueler", "Schüler"),
    ("Glueck", "Glück"),
    ("Ruecken", "Rücken"),
    ("Schluessel", "Schlüssel"),
    ("Bruecke", "Brücke"),
    ("fuehlen", "fühlen"),
    ("fuehlt", "fühlt"),
    ("fuehrt", "führt"),
    ("muede", "müde"),
    ("Maerz", "März"),
    ("waehlen", "wählen"),
    ("erklaert", "erklärt"),
    ("erzaehl", "erzähl"),
    ("Faelle", "Fälle"),
    ("betraegt", "beträgt"),
    ("haeufig", "häufig"),
    ("schoen", "schön"),
    ("Schoen", "Schön"),
    ("gemaess", "gemäß"),
    ("Gemaess", "Gemäß"),
    ("zugaenglich", "zugänglich"),
    ("Zugaenglich", "Zugänglich"),
    ("zugaenglichkeit", "zugänglichkeit"),
    ("Zugaenglichkeit", "Zugänglichkeit"),
    ("regelmaessig", "regelmäßig"),
    ("Regelmaessig", "Regelmäßig"),
    ("staendig", "ständig"),
    ("Staendig", "Ständig"),
    ("veraendert", "verändert"),
    ("veraendern", "verändern"),
    ("Veraenderung", "Veränderung"),
    ("veraenderung", "veränderung"),
    ("erklaeren", "erklären"),
    ("Erklaeren", "Erklären"),
    ("erlaeutern", "erläutern"),
    ("erlaeutert", "erläutert"),
    ("Erlaeuterung", "Erläuterung"),
    ("naeher", "näher"),
    ("Naeher", "Näher"),
    ("aergerlich", "ärgerlich"),
    ("Aerger", "Ärger"),
    ("aerztlich", "ärztlich"),
    ("Aerztlich", "Ärztlich"),
    ("Aerzte", "Ärzte"),
    ("aerzte", "ärzte"),
    ("pruefen", "prüfen"),
    ("Pruefen", "Prüfen"),
    ("prueft", "prüft"),
    ("geprueft", "geprüft"),
    ("gewoehnlich", "gewöhnlich"),
    ("Gewoehnlich", "Gewöhnlich"),
    ("ungewoehnlich", "ungewöhnlich"),
    ("moegen", "mögen"),
    ("bemuehen", "bemühen"),
    ("bemueht", "bemüht"),
    ("Beduerfnis", "Bedürfnis"),
    ("beduerfnis", "bedürfnis"),
    ("Beduerfnisse", "Bedürfnisse"),
    ("loeschen", "löschen"),
    ("Loeschen", "Löschen"),
    ("loescht", "löscht"),
    ("wuerde", "würde"),
    ("Wuerde", "Würde"),
    ("wuerden", "würden"),
    ("Wuerden", "Würden"),
    ("kuenftig", "künftig"),
    ("Kuenftig", "Künftig"),
    ("groesse", "größe"),
    ("Groesse", "Größe"),
    ("groesste", "größte"),
    ("Groesste", "Größte"),
    ("Strasse", "Straße"),
    ("strasse", "straße"),
    ("Strassen", "Straßen"),
    ("strassen", "straßen"),
    ("Fuesse", "Füße"),
    ("fuesse", "füße"),
    ("Fuss", "Fuß"),
    ("fuss", "fuß"),
    ("massnahme", "maßnahme"),
    ("Massnahme", "Maßnahme"),
    ("massnahmen", "maßnahmen"),
    ("Massnahmen", "Maßnahmen"),
    ("Erfaehrt", "Erfährt"),
    ("erfaehrt", "erfährt"),
    ("anhaengt", "anhängt"),
    ("abhaengig", "abhängig"),
    ("Abhaengig", "Abhängig"),
    ("zugaenge", "zugänge"),
    ("Zugaenge", "Zugänge"),
    ("Aenderung", "Änderung"),
    ("aenderung", "änderung"),
    ("Aenderungen", "Änderungen"),
    ("aenderungen", "änderungen"),
    ("kuemmern", "kümmern"),
    ("kuemmert", "kümmert"),
    ("Foerderung", "Förderung"),
    ("foerderung", "förderung"),
    ("foerdern", "fördern"),
    ("Foerder", "Förder"),
    ("oeffentlich", "öffentlich"),
    ("Oeffentlich", "Öffentlich"),
    ("oeffentliche", "öffentliche"),
    ("Oeffentliche", "Öffentliche"),
    ("oeffentlichen", "öffentlichen"),
    ("Oeffentlichen", "Öffentlichen"),
    ("OEPNV", "ÖPNV"),
    ("Oepnv", "Öpnv"),
    ("toedlich", "tödlich"),
    ("noetig", "nötig"),
    ("benoetigt", "benötigt"),
    ("benoetigen", "benötigen"),
    ("Benoetigt", "Benötigt"),
    ("woechentlich", "wöchentlich"),
    ("aergern", "ärgern"),
    ("erwaehnen", "erwähnen"),
    ("erwaehnt", "erwähnt"),
    ("Erwaehnt", "Erwähnt"),
    ("aelter", "älter"),
    ("Aelter", "Älter"),
    ("aeltere", "ältere"),
    ("Aeltere", "Ältere"),
    ("aenderlich", "änderlich"),
    ("zaehlen", "zählen"),
    ("Zaehlen", "Zählen"),
    ("ausfuehrlich", "ausführlich"),
    ("Ausfuehrlich", "Ausführlich"),
    ("hoeher", "höher"),
    ("Hoeher", "Höher"),
    ("hoechste", "höchste"),
    ("Hoechste", "Höchste"),
    ("staerker", "stärker"),
    ("Staerker", "Stärker"),
    ("staerkere", "stärkere"),
    ("traegt", "trägt"),
    ("Traegt", "Trägt"),
    ("traeger", "träger"),
    ("Traeger", "Träger"),
    ("Traeger", "Träger"),
    ("voellig", "völlig"),
    ("Voellig", "Völlig"),
    ("ungaengig", "ungängig"),
    ("zugaenglichkeitstandard", "zugänglichkeitstandard"),
    ("Verschluss", "Verschluss"),  # noop, keep for clarity
]

# Pre-compile a single alternation with word boundaries for speed.
_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(src) for src, _ in REPLACEMENTS) + r")\b"
)
_LOOKUP = {src: dst for src, dst in REPLACEMENTS}


def fix_text(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    return _PATTERN.sub(lambda m: _LOOKUP[m.group(0)], value)


def fix_json(node: Any) -> Any:
    """Recursively rewrite string leaves inside dict/list payloads."""
    if isinstance(node, str):
        return fix_text(node)
    if isinstance(node, list):
        return [fix_json(item) for item in node]
    if isinstance(node, dict):
        return {key: fix_json(val) for key, val in node.items()}
    return node


# (table, [scalar text columns], [jsonb columns]).
TARGETS: List[Tuple[str, List[str], List[str]]] = [
    (
        "posts",
        ["publish_caption", "topic_title", "topic_rotation", "topic_cta"],
        ["seed_data", "video_prompt_json", "video_metadata", "blog_content"],
    ),
    (
        "topic_scripts",
        ["script", "title", "anchor_topic", "disclaimer",
         "source_summary", "primary_source_title"],
        ["seed_payload", "quality_notes"],
    ),
    (
        "topic_registry",
        ["title", "script", "canonical_topic", "merge_reason"],
        [],
    ),
    (
        "topic_research_dossiers",
        ["seed_topic", "topic", "anchor_topic"],
        ["normalized_payload"],
    ),
    (
        "video_prompt_audit",
        ["prompt_text", "negative_prompt"],
        [],
    ),
]


def fetch_all(table: str, columns: List[str]) -> Iterable[Dict[str, Any]]:
    select_clause = ",".join(["id", *columns])
    page_size = 500
    offset = 0
    with httpx.Client(timeout=30.0) as client:
        while True:
            resp = client.get(
                f"{SUPABASE_URL}/rest/v1/{table}",
                headers={**HEADERS, "Prefer": "count=none"},
                params={
                    "select": select_clause,
                    "limit": str(page_size),
                    "offset": str(offset),
                    "order": "id.asc",
                },
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                return
            for row in rows:
                yield row
            if len(rows) < page_size:
                return
            offset += page_size


def patch_row(table: str, row_id: str, patch: Dict[str, Any]) -> None:
    with httpx.Client(timeout=30.0) as client:
        resp = client.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params={"id": f"eq.{row_id}"},
            json=patch,
        )
        if resp.status_code >= 300:
            raise RuntimeError(
                f"PATCH {table} id={row_id} failed: {resp.status_code} {resp.text[:200]}"
            )


def diff_columns(row: Dict[str, Any], text_cols: List[str], json_cols: List[str]) -> Dict[str, Any]:
    patch: Dict[str, Any] = {}
    for col in text_cols:
        current = row.get(col)
        updated = fix_text(current)
        if updated != current:
            patch[col] = updated
    for col in json_cols:
        current = row.get(col)
        updated = fix_json(current)
        if updated != current:
            patch[col] = updated
    return patch


def run(apply: bool) -> int:
    grand_changed = 0
    for table, text_cols, json_cols in TARGETS:
        print(f"\n=== {table} ===  text={text_cols} json={json_cols}")
        scanned = 0
        changed = 0
        sample_shown = 0
        for row in fetch_all(table, text_cols + json_cols):
            scanned += 1
            patch = diff_columns(row, text_cols, json_cols)
            if not patch:
                continue
            changed += 1
            if sample_shown < 3:
                print(f"  id={row['id']}")
                for col, new_val in patch.items():
                    old_val = row.get(col)
                    if isinstance(old_val, str) and isinstance(new_val, str):
                        # Print a tight before/after snippet around first diff position.
                        for i, (a, b) in enumerate(zip(old_val, new_val)):
                            if a != b:
                                start = max(0, i - 30)
                                end = min(len(old_val), i + 30)
                                print(f"    {col}: …{old_val[start:end]!r} → …{new_val[start:end]!r}")
                                break
                    else:
                        print(f"    {col}: <jsonb mutated>")
                sample_shown += 1
            if apply:
                patch_row(table, row["id"], patch)
        print(f"  scanned={scanned}  changed={changed}")
        grand_changed += changed
    print(f"\nTotal rows changed: {grand_changed}  (apply={apply})")
    return grand_changed


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    started = time.time()
    run(apply=args.apply)
    print(f"Elapsed: {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
