# Fix: actor ignored on 32s character-consistency videos

## Root cause (confirmed by code read)
For character-consistency / ActorIdentity videos, the selected actor is overwritten by
hardcoded "generic German woman" character text — worse the longer the video:

- **Base prompt, tier 32 only** (`handlers.py:1543`): `prompt_character = LEGACY_SHORT_CHARACTER`
  replaces the post's real character. Tier 16 (else branch) keeps the real character.
- **Every extension hop** (`prompt_builder.py:221`, `LEAN_EXTENSION_CHARACTER`): re-asserts the
  same hardcoded stranger. 32s has 3 hops; 16s has 1; 8s has 0.

Net: 8s ok / 16s ok (1 nudge) / 32s broken (base override + 3 nudges) -> actor "completely ignored".
The light-mode continuation template already does the right thing (defer to previous segment,
no hardcoded face), proving the pattern.

## Plan
- [x] 1. Scope the tier-32 legacy character/style override to NON character-consistency modes
      so CC tier-32 keeps the real per-post character (matches tier-16 behavior).
- [x] 2. Replace `LEAN_EXTENSION_CHARACTER` hardcoded person with a continuity-deferring
      identity-preservation directive (mirrors the working light-mode continuation).
- [x] 3. Add regression tests: CC tier-32 base uses post character (not LEGACY_SHORT_CHARACTER);
      extension prompt defers to previous segment instead of hardcoding the specific face.
      Existing automated/topic 32s tests stay green (they legitimately use the generic persona).
- [x] 4. Run tests locally — 192 passed across duration-routing, veo-prompt-contract,
      character-consistency, actor-identity, and poller-transition suites.
- [ ] 5. Commit + push + watch deploy green.

## Review
Two-line root cause: tier-32 base prompt hardcoded `LEGACY_SHORT_CHARACTER` (overriding the
selected actor — tier 16 didn't), and every extension hop hardcoded `LEAN_EXTENSION_CHARACTER`
(a generic stranger). 32s = base override + 3 hops -> actor erased; 16s = 1 hop -> survived;
8s = base only -> fine. Fix scopes the tier-32 override to non-CC modes and makes extension
hops preserve identity by deferring to the previous segment. Updated one test that pinned the
old extension text; added two regression tests.
