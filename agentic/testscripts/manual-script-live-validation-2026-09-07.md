# Manual script live validation — 2026-09-07

BRIDGECODE_ROUTE: live manual-script validation → [GENERAL, EYE] | MODE: Eye | WHY: Prove scripts of different sizes persist and advance to Scene.

Environment: running Docker web app at http://localhost:8000 using the configured real database. No mocks. This does not establish deployment to the public lippelift.xyz server. Only the five script-fix files were layered onto the existing local web image; other services were preserved. Rollback image: `aiugc-web:before-manual-script-fix-20260907`.

Result: 19 approved scripts across seven real batches. Every batch persisted `S4_SCRIPTED` and displayed Scene as step 2 in the Codex browser. No image or video generation was initiated. Test batches remain available, named `QA Manual…`, for inspection.

| Duration | Script word counts | Batch | Result |
|---|---|---|---|
| 8s | 16, 18, 16 | [c7d87173-68cc-44d4-8962-200b8b95fede](http://localhost:8000/batches/c7d87173-68cc-44d4-8962-200b8b95fede) | Scene |
| 16s | 32, 36, 32 | [bb3ddf18-4a15-488a-a4f5-5a6e822ada68](http://localhost:8000/batches/bb3ddf18-4a15-488a-a4f5-5a6e822ada68) | Scene |
| 24s | 48, 54, 48 | [31d9ad25-22a4-43e2-bc9c-5557c00d8ebf](http://localhost:8000/batches/31d9ad25-22a4-43e2-bc9c-5557c00d8ebf) | Scene |
| 32s | 64, 72, 64 | [c3973acf-42cd-4542-a6e7-ac2ca3631afe](http://localhost:8000/batches/c3973acf-42cd-4542-a6e7-ac2ca3631afe) | Scene |
| 48s | 96, 108, 96 | [f0cc10e5-f1fb-49f9-bc58-f921d43645ea](http://localhost:8000/batches/f0cc10e5-f1fb-49f9-bc58-f921d43645ea) | Scene |
| 60s | 128, 142, 128 | [9f0f99d8-0577-45be-8031-3e55fb8bb641](http://localhost:8000/batches/9f0f99d8-0577-45be-8031-3e55fb8bb641) | Scene |
| 60s browser case | 142 (1,036 characters) | [e9566c19-ad07-423f-9ba9-bcd88d0ae506](http://localhost:8000/batches/e9566c19-ad07-423f-9ba9-bcd88d0ae506) | Scene |

Each three-post batch covered normal text, upper-envelope word counts, and omitted final punctuation. Approval covered form-submitted and previously saved JSON-action paths. The first two approvals retained S2; the third transitioned to S4. Subsequent GETs verified every review status and equality of stored script, dialogue, and concatenated planned beats.

A further live boundary defect was found: the prior 900-character JSON limit returned HTTP 500 for the valid 1,036-character 60-second script. The editor and JSON/form save boundary now allow 4,000 characters. The exact JSON request returned HTTP 200 after the fix; a 4,001-character request returned an actionable HTTP 422 before persistence. Browser save normalized the final full stop and approval advanced to Scene.

Browser verification: Codex computer-use opened all seven real batches, confirmed Scene and the expected generate-image controls, and exercised long-script save and approval with keyboard focus navigation. No generation controls were clicked. Focused regression validation after the limit fix: 133 tests passed.

The earlier broad suite had a known unrelated failure for the retired automated-mode short-script save behavior; it also fails against HEAD before these changes.

Final checks: `/livez` and `/health` both returned HTTP 200. All five running script-fix files matched the workspace SHA-256 hashes. Browser console had no errors; the existing brand styling and six-step workflow remained intact without visible layout errors.
