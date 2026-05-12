"""German umlaut normalization.

Restores proper German umlauts (ä, ö, ü, ß) on LLM output when a known German
word arrived in ASCII-transliterated form ("fuer" → "für", "spaeter" → "später",
"koennen" → "können", …). Uses a curated whitelist with word-boundary matching,
so substrings inside unrelated tokens are untouched (e.g. "Steuer", "kaufen",
"Gross" as a surname, "Mass" as in physics units).

Apply at the seam between LLM output and persisted storage.
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Tuple

# (ascii_form, umlaut_form). Listed per capitalization to avoid case-folding bugs.
_REPLACEMENTS: List[Tuple[str, str]] = [
    ("fuer", "für"), ("Fuer", "Für"), ("FUER", "FÜR"),
    ("spaeter", "später"), ("Spaeter", "Später"),
    ("spaet", "spät"), ("Spaet", "Spät"),
    ("Huerde", "Hürde"), ("Huerden", "Hürden"),
    ("zaehlt", "zählt"), ("zaehl", "zähl"),
    ("zaehlen", "zählen"), ("Zaehlen", "Zählen"),
    ("Mobilitaet", "Mobilität"),
    ("ausschliesslich", "ausschließlich"),
    ("Erklaerungen", "Erklärungen"), ("Erklaerung", "Erklärung"),
    ("Saetze", "Sätze"), ("Woerter", "Wörter"),
    ("vollstaendig", "vollständig"), ("abzukuerzen", "abzukürzen"),
    ("verfuegbar", "verfügbar"),
    ("uebernimmt", "übernimmt"),
    ("ueber", "über"), ("Ueber", "Über"),
    ("natuerlich", "natürlich"), ("Natuerlich", "Natürlich"),
    ("muessen", "müssen"),
    ("koennen", "können"), ("Koennen", "Können"),
    ("waehrend", "während"), ("Waehrend", "Während"),
    ("groesser", "größer"),
    ("groesse", "größe"), ("Groesse", "Größe"),
    ("groesste", "größte"), ("Groesste", "Größte"),
    ("Maedchen", "Mädchen"),
    ("Geraet", "Gerät"), ("Geraete", "Geräte"),
    ("hoeren", "hören"), ("Hoeren", "Hören"),
    ("hoeher", "höher"), ("Hoeher", "Höher"),
    ("hoechste", "höchste"), ("Hoechste", "Höchste"),
    ("Stueck", "Stück"), ("Buecher", "Bücher"),
    ("Loesung", "Lösung"), ("Loesungen", "Lösungen"),
    ("loeschen", "löschen"), ("Loeschen", "Löschen"), ("loescht", "löscht"),
    ("moeglich", "möglich"), ("Moeglich", "Möglich"),
    ("Buero", "Büro"),
    ("Schueler", "Schüler"),
    ("Glueck", "Glück"),
    ("Ruecken", "Rücken"),
    ("Schluessel", "Schlüssel"),
    ("Bruecke", "Brücke"),
    ("fuehlen", "fühlen"), ("fuehlt", "fühlt"), ("fuehrt", "führt"),
    ("muede", "müde"),
    ("Maerz", "März"),
    ("waehlen", "wählen"),
    ("erklaert", "erklärt"), ("erklaeren", "erklären"), ("Erklaeren", "Erklären"),
    ("erzaehl", "erzähl"),
    ("Faelle", "Fälle"),
    ("betraegt", "beträgt"),
    ("haeufig", "häufig"),
    ("schoen", "schön"), ("Schoen", "Schön"),
    ("gemaess", "gemäß"), ("Gemaess", "Gemäß"),
    ("zugaenglich", "zugänglich"), ("Zugaenglich", "Zugänglich"),
    ("zugaenge", "zugänge"), ("Zugaenge", "Zugänge"),
    ("regelmaessig", "regelmäßig"), ("Regelmaessig", "Regelmäßig"),
    ("staendig", "ständig"), ("Staendig", "Ständig"),
    ("veraendert", "verändert"), ("veraendern", "verändern"),
    ("Veraenderung", "Veränderung"), ("veraenderung", "veränderung"),
    ("erlaeutern", "erläutern"), ("erlaeutert", "erläutert"),
    ("Erlaeuterung", "Erläuterung"),
    ("naeher", "näher"), ("Naeher", "Näher"),
    ("aergerlich", "ärgerlich"), ("aergern", "ärgern"), ("Aerger", "Ärger"),
    ("aerztlich", "ärztlich"), ("Aerztlich", "Ärztlich"),
    ("Aerzte", "Ärzte"), ("aerzte", "ärzte"),
    ("pruefen", "prüfen"), ("Pruefen", "Prüfen"),
    ("prueft", "prüft"), ("geprueft", "geprüft"),
    ("gewoehnlich", "gewöhnlich"), ("Gewoehnlich", "Gewöhnlich"),
    ("ungewoehnlich", "ungewöhnlich"),
    ("moegen", "mögen"),
    ("bemuehen", "bemühen"), ("bemueht", "bemüht"),
    ("Beduerfnis", "Bedürfnis"), ("beduerfnis", "bedürfnis"),
    ("Beduerfnisse", "Bedürfnisse"),
    ("wuerde", "würde"), ("Wuerde", "Würde"),
    ("wuerden", "würden"), ("Wuerden", "Würden"),
    ("kuenftig", "künftig"), ("Kuenftig", "Künftig"),
    ("Aenderung", "Änderung"), ("aenderung", "änderung"),
    ("Aenderungen", "Änderungen"), ("aenderungen", "änderungen"),
    ("kuemmern", "kümmern"), ("kuemmert", "kümmert"),
    ("Foerderung", "Förderung"), ("foerderung", "förderung"),
    ("foerdern", "fördern"), ("Foerder", "Förder"),
    ("oeffentlich", "öffentlich"), ("Oeffentlich", "Öffentlich"),
    ("oeffentliche", "öffentliche"), ("Oeffentliche", "Öffentliche"),
    ("oeffentlichen", "öffentlichen"), ("Oeffentlichen", "Öffentlichen"),
    ("OEPNV", "ÖPNV"), ("Oepnv", "Öpnv"),
    ("toedlich", "tödlich"), ("noetig", "nötig"),
    ("benoetigt", "benötigt"), ("benoetigen", "benötigen"),
    ("Benoetigt", "Benötigt"),
    ("woechentlich", "wöchentlich"),
    ("erwaehnen", "erwähnen"), ("erwaehnt", "erwähnt"), ("Erwaehnt", "Erwähnt"),
    ("aelter", "älter"), ("Aelter", "Älter"),
    ("aeltere", "ältere"), ("Aeltere", "Ältere"),
    ("Erfaehrt", "Erfährt"), ("erfaehrt", "erfährt"),
    ("anhaengt", "anhängt"),
    ("abhaengig", "abhängig"), ("Abhaengig", "Abhängig"),
    ("ausfuehrlich", "ausführlich"), ("Ausfuehrlich", "Ausführlich"),
    ("staerker", "stärker"), ("Staerker", "Stärker"),
    ("traegt", "trägt"), ("Traegt", "Trägt"),
    ("traeger", "träger"), ("Traeger", "Träger"),
    ("voellig", "völlig"), ("Voellig", "Völlig"),
    ("Strasse", "Straße"), ("strasse", "straße"),
    ("Strassen", "Straßen"), ("strassen", "straßen"),
    ("Fuss", "Fuß"), ("Fuesse", "Füße"),
    ("massnahme", "maßnahme"), ("Massnahme", "Maßnahme"),
    ("massnahmen", "maßnahmen"), ("Massnahmen", "Maßnahmen"),
]

_LOOKUP = {src: dst for src, dst in _REPLACEMENTS}
_PATTERN = re.compile(r"\b(" + "|".join(re.escape(s) for s, _ in _REPLACEMENTS) + r")\b")


def restore_german_umlauts(text: str) -> str:
    """Replace whole-word ASCII transliterations with proper umlauts.

    Safe to call on any string — only matches a curated whitelist of German words
    via word boundaries. Idempotent.
    """
    if not isinstance(text, str) or not text:
        return text
    return _PATTERN.sub(lambda m: _LOOKUP[m.group(0)], text)


def restore_german_umlauts_in_json(value: Any) -> Any:
    """Walk a JSON-shaped Python value and normalize string leaves."""
    if isinstance(value, str):
        return restore_german_umlauts(value)
    if isinstance(value, list):
        return [restore_german_umlauts_in_json(item) for item in value]
    if isinstance(value, dict):
        return {key: restore_german_umlauts_in_json(val) for key, val in value.items()}
    return value


def restore_in_json_text(text: str) -> str:
    """Parse JSON text, normalize string leaves, re-serialize. Falls back to plain text
    normalization if the input is not valid JSON.
    """
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return restore_german_umlauts(text)
    return json.dumps(restore_german_umlauts_in_json(parsed), ensure_ascii=False)
