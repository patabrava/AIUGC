Title: Hostinger VPS project wrapper reuses stale local Docker images instead of rebuilding source

Severity: high

Frequency: deterministic on `aiugc-prod`

Implementation block: live deployment parity for batch video settings UI

Debug scope: Hostinger VPS Docker project deployment, not app source

Environment matrix:
- Workspace: `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC`
- Git commit intended for deploy: `3ffa7b5ebe719d5674faf2917f9a773a9ebf52c5`
- VPS: `srv1498567.hstgr.cloud` (`1498567`)
- Hostinger project: `aiugc-prod`
- Domain: `lippelift.xyz`

What was reproduced:
- Localhost serves the new batch settings UI from the current repo:
  - `Vertex AI only`
  - `Veo Model`
  - dynamic `modelLabel`
- Live `lippelift.xyz` serves the old UI:
  - `Veo 3.1 (Gemini API)` provider select
  - no `Veo Model` selector
  - hardcoded `Veo 3.1` badge
- Live static asset and live HTML both prove old code is still running:
  - `https://lippelift.xyz/static/js/batches/detail.js` contains `provider: 'veo_3_1'`
  - authenticated live HTML still renders the legacy provider `<select>`

Evidence:
- GitHub raw at commit `3ffa7b5...` contains the correct new files.
- Hostinger `docker_compose_update`/`docker_compose_restart`/`docker_compose_up` actions do not rebuild `build:` services.
- Hostinger logs explicitly show:
  - stale image reuse path on existing tags
  - or `pull access denied ...` on unique tags
  - and the platform warning: `docker compose build expansion-worker web worker caption-worker topic-worker`

Failed attempts:
1. `VPS_updateProjectV1` and `VPS_restartProjectV1`
   - Result: containers restarted, but live JS/HTML remained old.
2. `VPS_createNewProjectV1` with commit-pinned compose and then with unique image tags
   - Result: Hostinger wrapper still tried to pull images and never executed `docker compose build`.

Why current attempts failed:
- The Hostinger VPS project API wrapper only performs compose up/pull style actions.
- It does not execute a source build for local `build:` services, so old local Docker images remain authoritative.
- RMCP can prove the problem, but cannot run the required manual Docker build.

Human-in-the-loop action required:
1. Access the VPS shell directly.
2. In the `aiugc-prod` project directory, run:
   - `docker compose build web worker expansion-worker topic-worker caption-worker`
   - `docker compose up -d web worker expansion-worker topic-worker caption-worker`
3. Re-fetch:
   - `https://lippelift.xyz/static/js/batches/detail.js`
   - `https://lippelift.xyz/batches/6458ab6b-d1c7-41bf-bee7-be9b6a6e27a1`
4. Verify the live page now shows `Vertex AI only` and `Veo Model`.

Workaround if shell access is not available:
- Switch the live deployment strategy to prebuilt registry images and deploy by immutable tag, instead of relying on Hostinger VPS compose `build:` wrappers.

Regression test status:
- App source is correct locally and on GitHub.
- Deployment parity remains blocked by Hostinger build behavior.

Ownership:
- Deployment/platform owner with direct VPS shell access.

