# Manual script public production validation — 2026-09-07

Tested deployed commit `6ef3cae8e36c1a48767d074934f0828ac17f662c` on https://lippelift.xyz using the authenticated native Chrome UI through computer-use. Every creation, edit, save, and approval below was submitted through the public site. Independent read-only database checks confirmed all six batches are `S4_SCRIPTED`, all seven posts are approved, and their video operation IDs are empty.

| Duration | Scripts | Words each | Production batch |
| --- | --- | --- | --- |
| 8s | 1 | 16 | [Original screenshot case](https://lippelift.xyz/batches/06022b59-74b2-4681-9ef5-fedf80c99d70) |
| 16s | 1 | 32 | [16-second proof](https://lippelift.xyz/batches/86b09f18-2e84-48c2-9a22-5dc000675273) |
| 24s | 1 | 48 | [24-second proof](https://lippelift.xyz/batches/30839f6a-4ef0-4a55-96c2-b6a8e7b972fe) |
| 32s | 2 | 64 | [Sibling review proof](https://lippelift.xyz/batches/9f8d387b-d4f7-4bec-924e-ed38f40ddd5a) |
| 48s | 1 | 96 | [48-second proof](https://lippelift.xyz/batches/e25e50e6-bbed-4c84-b1d5-1dbaa5715e6d) |
| 60s | 1 | 142 | [Long-script proof](https://lippelift.xyz/batches/bcba2e2c-a28e-456d-9351-7b70600d3e5c) |

All six screens reached “Step 2 of 6: Approve scene plates” with Generate script image available. No generation buttons were clicked.

The original 8-second screenshot text was submitted verbatim without its final period. Save returned canonical text with the final period and approval succeeded. Missing-final-period variants also passed at 16, 24, 32, 48, and 60 seconds. The second 32-second script retained existing terminal punctuation. Its first sibling approval left “1 approved · 1 remaining” in Scripts; final approval advanced the entire batch.

The 60-second script contained 142 words and 1,036 characters after normalization. Its exact saved text persisted through a browser reload before successful approval. Before that valid replacement, “Hallo Welt.” saved as an invalid draft with the visible message “This 60s script needs 127-142 words; it has 2.” Approval was blocked by the same message and did not advance the workflow. Replacing it with the valid script cleared the error and completed approval.

Desktop browser inspection showed the existing blue/cream styling, readable controls, and no overlapping layout. Keyboard input and native dropdowns worked. The final Scene console had no application errors; it retained the existing Tailwind CDN production-use warning. Mobile layout was not separately tested. The diagnostic tab was separate from the operator's existing unsaved batch and was closed after verification.

This is public-site validation, separate from the earlier localhost/shared-database matrix. QA batches are retained for inspection. No paid generation or publishing was performed.
