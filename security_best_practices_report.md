# Security Best Practices Report

Executive summary: The codebase is generally using environment-based secret loading rather than hardcoded secrets, but the current production surface still exposes two common FastAPI risks: unrestricted OpenAPI/docs endpoints and missing host header validation. I did not find evidence of secrets being persisted in `app/core/config.py`, but the app should still be hardened before public deployment.

## High Severity

### [F-1] Host header validation is not enabled
- Location: `app/main.py:76-84`
- Evidence: the FastAPI app is created with defaults and there is no `TrustedHostMiddleware` or equivalent host allowlist middleware in the startup path.
- Impact: attackers can send arbitrary `Host` headers. Depending on downstream routing, redirects, generated URLs, and proxy behavior, this can enable host-header poisoning and make cache or password-reset style flows unreliable or unsafe.
- Fix: add `TrustedHostMiddleware` with an explicit allowlist for the deployment hostnames and any internal proxy hostnames the app must accept.
- Mitigation: if host validation must live at the edge, document that assumption and verify it in the reverse proxy / CDN config.
- False positive notes: this is only fully mitigated if the reverse proxy strips or validates untrusted host headers before forwarding to the app.

### [F-2] Interactive docs and OpenAPI schema are exposed by default
- Location: `app/main.py:76-82`
- Evidence: `FastAPI(...)` is instantiated without `docs_url=None`, `redoc_url=None`, or `openapi_url=None`.
- Impact: public docs and schema exposure increases information disclosure. It makes routes, models, and operational surface area easier to enumerate for an attacker.
- Fix: disable docs/schema in production, or protect them behind authentication or a private network boundary.
- Mitigation: if docs are intentionally public for this deployment, treat that as an explicit exception and restrict by network or auth at the edge.
- False positive notes: this is acceptable for local development; the issue is exposure in a public production deployment.

## Medium Severity

### [F-3] Docker deployment relies on a local `.env` file for secrets
- Location: `docker-compose.yaml:6-8`, `docker-compose.yaml:31-37`
- Evidence: both services use `env_file: .env`, which means the runtime secret set is pulled from a local file on the host.
- Impact: this is not inherently unsafe, but it becomes a secret-leak risk if `.env` is copied, committed, or stored in a shared deployment bundle. It also makes local secret hygiene a deployment dependency.
- Fix: keep `.env` strictly local and untracked, and prefer runtime secret injection in production orchestration where possible.
- Mitigation: maintain a hard `.gitignore` entry for `.env` and rotate any secret that ever lands in a tracked file.
- False positive notes: this pattern is normal for local Docker Compose development; it is only a security issue if the file is treated as source material or shipped into git.

## Low Severity

### [F-4] Configuration module still uses local dotenv loading
- Location: `app/core/config.py:15-20`
- Evidence: `SettingsConfigDict(env_file=".env", ...)` causes the app to read from a local `.env` file when present.
- Impact: this is fine for development, but it reinforces the same secret-at-rest risk as Compose if a real `.env` is ever checked in or shared.
- Fix: keep this for local development, but document that production must inject environment variables externally.
- Mitigation: enforce `.env` exclusion in git and CI, and never commit the generated file.
- False positive notes: the module itself does not write secrets; it only reads configuration.

## Observations

- I did not find tracked live credentials in `app/core/config.py`.
- The prior `setup.sh` secret seeding was removed before this report, which is the right direction.
- I did not inspect deployment infrastructure outside the repo, so proxy-level host validation and docs protection still need runtime verification.
