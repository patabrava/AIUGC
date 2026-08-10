# SEO content workflow

The SEO opportunity catalog is an optional upstream input for topic research and blog generation. It does not replace the established topic bank, dossier facts, scripting rules, or recovery paths.

## Activation

The production and local default is disabled:

```env
SEO_TOPIC_CATALOG_ENABLED=false
```

Set the value to `true` and restart the app and topic worker to activate the catalog. The Topics Hub shows `SEO catalog active` when the running process has loaded the enabled setting. Keep it disabled when comparing against the frozen control path.

## Data sources

- `prompt_data/seo_keyword_catalog.json` contains 135 unique keywords: all 131 rows from `Gesamte Keywordrecherche` plus four curated-only keywords. Its 54 curated worksheet rows remain attributable through `curated_rows`.
- The keyword provider and metrics date were not supplied, so both values are stored as `unknown`. Volume, CPC, and competition are prioritization signals, not factual claims for audience copy.
- `prompt_data/internal_link_catalog.json` contains only pages present on the live `https://www.lippelift.de/sitemap.xml` inventory and verified on 2026-08-07.

## Runtime behavior

When disabled, topic selection and the blog prompt use the original files and ordering. When enabled, the agency's deduplicated curated article titles are offered first; the complete keyword sheet supplies metrics and related terms without turning raw keyword fragments into topic cards. The YAML bank, LLM seed generation, and deterministic fallback remain available after them.

Research snapshots an optional `seo_brief` with the primary keyword, related terms, intent, audience, verified internal destinations, CTA, avoided claims, and metric provenance. AIUGC prompt construction continues to read only dossier facts, angles, risks, and the existing scripting guidelines. Workbook notes and first-person actor suggestions are never passed into actor copy.

The SEO blog prompt accepts controlled link tokens such as `[[LINK:funding|Fördermöglichkeiten]]`. The renderer converts a token only when its ID exists in the persisted Lippe Lift allowlist. Unknown IDs, arbitrary URLs, and HTML remain escaped.

## Safe catalog updates

1. Retain the original workbook under `docs/`.
2. Normalize keywords case-insensitively and preserve every curated source row.
3. Record the provider and metrics date when they become known; never infer them.
4. Verify each internal destination against the current sitemap and visible live page.
5. Run `tests/test_seo_catalog_integration.py`, the topic/blog regression suites, both flag-state stress checks, and local Topics Hub browser verification before enabling the flag.
