# FLOW-FORGE UGC System — Project Canon v1.0

**Version:** 1.0  
**Date:** 2025-11-05  
**Status:** Active

---

## 1. System Overview

### 1.1 Mission Statement
FLOW-FORGE is a deterministic UGC video production system that enables editors to:
- Choose per-type post counts (value/lifestyle/product)
- Approve and modify AI-generated scripts
- Generate short vertical videos using Veo 3.1 or Sora 2
- Perform quality assurance reviews
- Publish to TikTok and Instagram with intelligent scheduling

### 1.2 Core Principles
- **Deterministic State Machine:** Every batch progresses through explicit states with clear gates
- **Per-Post Isolation:** Each post is independently processable and recoverable
- **User-Chosen Providers:** Editors select video generation provider per post
- **Research with De-duplication:** Topic discovery agent enforces strict de-dup across Supabase tables
- **Idempotent Operations:** All POST endpoints support Idempotency-Key headers

### 1.3 Architecture Decisions
- **System Architecture:** Vanilla Vertical-Slice Monolith
- **Logic Pattern:** State Machine Core with Feature Slices
- **Interface Type:** Web GUI (Graphical User Interface)
- **Interface Subtype:** Server-Side Rendered with Progressive Enhancement (htmx + Alpine.js)
- **Design System:** Material Design 3
- **Design Style:** Modern Productivity (Clean & Efficient)
- **UX Pattern:** Hybrid Dashboard + Linear Stage Views

---

## 2. Tech Stack

### 2.1 Backend
```yaml
Language: Python 3.11+
Framework: FastAPI
Validation: Pydantic v2
Database: Supabase (PostgreSQL 15+)
Database Client: Supabase Python SDK
Background Jobs: APScheduler
HTTP Client: httpx
LLM Clients: 
  - OpenAI SDK (for GPT-4, etc.)
  - Anthropic SDK (for Claude)
Video Providers: Direct API integration (Veo 3.1, Sora 2)
Social APIs: Direct API integration (TikTok, Instagram Graph API)
```

### 2.2 Frontend
```yaml
Templates: Jinja2
Styling: Tailwind CSS 3.x (Material Design 3 tokens)
Interactivity: 
  - htmx 1.9+ (AJAX, WebSockets, SSE)
  - Alpine.js 3.x (UI state management)
Build Tool: Vite (for Tailwind compilation and asset bundling)
```

### 2.3 Infrastructure
```yaml
Database: Supabase (managed PostgreSQL)
Storage: Supabase Storage (CDN for video assets)
Authentication: Supabase Auth
Deployment: Docker + Railway/Render/Fly.io (or VPS)
```

### 2.4 Development Tools
```yaml
Package Manager: pip + requirements.txt
Linting: ruff (Python), prettier (HTML/CSS/JS)
Testing: pytest (backend), Playwright (E2E testscripts)
Version Control: Git
```

### 2.5 Observability
```yaml
Logging: structlog (structured JSON logs with correlation IDs)
Error Handling: Standard error envelopes (Pydantic models)
Monitoring: Health endpoint + structured logs
```

---

## 3. State Machine

### 3.1 State Definitions

| State | Code | Description |
|-------|------|-------------|
| **Setup** | `S1_SETUP` | Batch created, awaiting configuration |
| **Seeded** | `S2_SEEDED` | Topics fetched, seeds extracted |
| **Scripted** | `S4_SCRIPTED` | Scripts approved, ready for prompts |
| **Prompts Built** | `S5_PROMPTS_BUILT` | Video prompts generated |
| **QA** | `S6_QA` | Videos generated, awaiting review |
| **Publish Plan** | `S7_PUBLISH_PLAN` | QA passed, publish plan created |
| **Complete** | `S8_COMPLETE` | Published to channels |

### 3.2 State Transitions

```
S1_SETUP → S2_SEEDED
  Trigger: POST /batches (create batch)
  Actions: Initialize posts, fetch topics, run Strict Extractor
  Guards: Brand selected, valid post_type_counts

S2_SEEDED → S4_SCRIPTED
  Trigger: PUT /batches/{id}/approve-scripts
  Actions: Validate all scripts
  Guards: All posts have approved scripts

S4_SCRIPTED → S5_PROMPTS_BUILT
  Trigger: POST /posts/{id}/build-prompt
  Actions: Run ActionSynth + VeoPrompt agents
  Guards: Script approved, profile resolved

S5_PROMPTS_BUILT → S6_QA
  Trigger: POST /posts/{id}/videos
  Actions: Submit to provider, poll, upload asset
  Guards: Prompt validated, provider selected

S6_QA → S7_PUBLISH_PLAN
  Trigger: PUT /posts/{id}/approve-qa (all posts)
  Actions: Run auto checks, manual review
  Guards: All posts qa_pass=true

S7_PUBLISH_PLAN → S8_COMPLETE
  Trigger: POST /batches/{id}/publish:confirm
  Actions: Dispatch to channels, store acks
  Guards: Plan saved, valid times/tokens
```

---

## 4. Feature Slices

### 4.1 /batches — Batch Management
- Create, list, get, advance state, duplicate, archive
- Endpoints: POST /batches, GET /batches, GET /batches/{id}
- File: /app/features/batches/

### 4.2 /posts — Post Management
- Script editing, approval, video generation, QA
- Endpoints: PUT /posts/{id}/script, POST /posts/{id}/videos
- File: /app/features/posts/

### 4.3 /topics — Topic Discovery
- Run discovery agent, fetch topics, de-duplication
- Endpoints: POST /topics/discover, GET /topics
- File: /app/features/topics/

### 4.4 /videos — Video Generation
- Build prompts, submit to providers, poll status
- Endpoints: POST /videos/build-prompt, POST /videos/submit
- File: /app/features/videos/

### 4.5 /publish — Publishing
- Create plan, suggest times, confirm, dispatch
- Endpoints: POST /batches/{id}/publish:plan
- File: /app/features/publish/

---

## 5. API Contracts

### 5.1 Standard Error Envelope
```json
{
  "ok": false,
  "code": "validation_error",
  "message": "Script exceeds 32 words",
  "details": {"field": "script_text", "limit": 32, "actual": 45}
}
```

### 5.2 Standard Success Envelope
```json
{
  "ok": true,
  "data": {...}
}
```

### 5.3 Error Codes
- `auth_fail` (401): Authentication failed
- `validation_error` (422): Input validation failed
- `state_transition_error` (409): Invalid state transition
- `third_party_fail` (503): External service failed
- `rate_limit` (429): Rate limit exceeded
- `not_found` (404): Resource not found

---

## 6. LLM Agents

### 6.1 PROMPT 1 — Research Agent
- Extract topic from search result HTML
- Output: title, rotation, cta, spoken_duration (≤8s)
- Constraints: Valid JSON, unique rotation/CTA

### 6.2 Strict Extractor Agent
- Extract seed from topic (no hallucination)
- Output: Factual seed data only

### 6.3 ActionSynth Agent
- Generate action/scene text from script
- Constraints: German, ≤32 words, ends "(stiller Halt)", no hyphens

### 6.4 VeoPrompt Agent
- Generate video prompt JSON
- Constraints: No durations in text, audio present

### 6.5 Engagement Scheduler Agent
- Suggest publish times
- Constraints: TZ=Europe/Berlin, min_gap=30min, no 00:00-06:00

---

## 7. Validation Rules

### 7.1 Script Validation
- German language
- ≤32 words
- Ends with "(stiller Halt)"
- No hyphens

### 7.2 Video Validation
- Duration: 8s (±0.5s)
- Aspect ratio: 9:16
- Resolution: 1080p minimum

### 7.3 Publish Plan Validation
- Future times (UTC)
- Min gap: 30 minutes
- No overlaps per channel
- Valid OAuth tokens

---

## 8. Testing Strategy

### 8.1 Phase-Gated Delivery
- Each feature phase has a testscript
- Whole-app testscripts (not isolated unit tests)
- Prior phases re-verified each phase

### 8.2 Human-as-EYE
- Human operator runs testscripts
- Observes behavior, captures artifacts
- Reports structured evidence

### 8.3 Regression Suite
- Passing testscripts join regression suite
- New failures produce new checks

---

## 9. Repository Layout
See REPO_LAYOUT.md (to be created)

---

## 10. Deployment Strategy
See DEPLOYMENT.md (to be created)

---

## 11. Evolution Log
- v1.0 (2025-11-05): Initial canon established
