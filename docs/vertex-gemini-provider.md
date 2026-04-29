# Vertex Gemini Provider

This app uses Vertex AI as the default Gemini transport so Gemini usage is billed through the configured Google Cloud project.

## Default Production Mode

Use:

```bash
GEMINI_PROVIDER=vertex
GEMINI_DEEP_RESEARCH_PROVIDER=vertex_grounded
GEMINI_API_FALLBACK_ENABLED=false
VERTEX_AI_ENABLED=true
VERTEX_AI_PROJECT_ID=project-89aac146-ec35-4755-b83
VERTEX_AI_LOCATION=us-central1
VERTEX_GROUNDED_RESEARCH_LOCATION=global
```

This routes text generation, structured JSON, image generation, and topic research through Google Cloud IAM and Vertex billing.

## Deep Research Status

Google's official Deep Research documentation currently describes native Deep Research as Gemini API Interactions only. It also states that Deep Research cannot be accessed through `generate_content`. Until Google publishes a Vertex Interactions endpoint, this app uses Vertex Gemini with Google Search grounding for research workloads.

## Legacy Fallback

Only enable this when the exact Gemini Deep Research Interactions agent is required:

```bash
GEMINI_API_FALLBACK_ENABLED=true
GEMINI_PROVIDER=gemini_api
GEMINI_DEEP_RESEARCH_PROVIDER=gemini_api
GEMINI_API_KEY=...
```

Do not enable the fallback in production if the goal is to consume the Vertex-linked Google Cloud credit balance.

## Live Verification

Run a small real Vertex call with ADC configured:

```bash
VERTEX_AI_ENABLED=true \
VERTEX_AI_PROJECT_ID=project-89aac146-ec35-4755-b83 \
VERTEX_AI_LOCATION=us-central1 \
GEMINI_PROVIDER=vertex \
GEMINI_DEEP_RESEARCH_PROVIDER=vertex_grounded \
python3 - <<'PY'
from app.adapters.llm_client import LLMClient

client = LLMClient()
print(client.generate_gemini_text("Return exactly: vertex-ok", max_tokens=128, temperature=0, thinking_budget=0))
PY
```

Expected output contains `vertex-ok`, and logs should show the Vertex Gemini adapter path.
