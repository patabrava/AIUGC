# GitHub Actions Auto-Deploy Template

Use this playbook when you want a repository to auto-deploy on every push to `main` and prove the deploy actually reached the live host.

## Goal

Build a production workflow with three properties:

1. A push to `main` triggers deployment automatically.
2. The deploy runs on the target host, not only in GitHub.
3. The run is verified with a live health check and a checked-out commit on the server.

## Step 1: Decide the production contract

Write down these values before touching GitHub Actions:

- `PROD_SSH_HOST`: production host name or IP
- `PROD_SSH_USER`: SSH user
- `PROD_APP_ROOT`: server directory that will contain the repo checkout and runtime files
- `HEALTHCHECK_URL`: live endpoint used to prove the deploy succeeded
- `REPO_REMOTE`: Git repository URL used by the host

Keep the runtime contract explicit. The server should run from a checkout on the host and a server-only env file, not from a hidden repo wrapper or a local developer `.env`.

## Step 2: Prepare the host

On the production machine, create a dedicated app root and a stable checkout path:

```bash
sudo mkdir -p /opt/my-app
sudo chown "$USER":"$USER" /opt/my-app
git clone https://github.com/<org>/<repo>.git /opt/my-app/repo
```

Create the server-only env file and lock down its permissions:

```bash
cp .env.production.example /opt/my-app/.env.production
chmod 600 /opt/my-app/.env.production
```

If the app depends on Compose, make the deploy script own the full lifecycle:

- fetch `origin/main`
- hard reset or fast-forward the host checkout to `origin/main`
- start services with the server env file
- poll the health endpoint until healthy

## Step 3: Make the deploy script deterministic

The deploy script should live in the repo, usually as something like `scripts/deploy/production.sh`.

The important behavior is:

- refuse to deploy if the server env file is missing
- refuse to deploy if the host checkout has uncommitted local changes
- update the host checkout from `origin/main`
- tear down the previous compose project before `up`
- run the production compose file with the server env file
- wait for `HEALTHCHECK_URL`

Recommended shape:

```bash
git fetch origin main
git checkout main
git merge --ff-only origin/main
docker compose -p aiugc-prod -f docker-compose.production.yml --env-file "$ENV_FILE" down --remove-orphans
docker compose -f docker-compose.production.yml --env-file "$ENV_FILE" up -d --build --remove-orphans
curl --fail "$HEALTHCHECK_URL"
```

## Step 4: Add the workflow

Create a workflow under `.github/workflows/` that triggers on `push` to `main`.

Use one job with these stages:

1. Checkout repo
2. Validate deploy config
3. Deploy over SSH
4. Wait for health

Keep the workflow simple and explicit. Avoid hiding deploy behavior in composite actions unless you really need reuse.

### Minimum workflow inputs

- `PROD_SSH_HOST`
- `PROD_SSH_USER`
- `PROD_APP_ROOT`
- one SSH credential, either:
  - `PROD_SSH_PRIVATE_KEY`, or
  - `PROD_SSH_PASSWORD`
- optional `PROD_ENV_FILE_B64` if the host env file must be materialized during deploy

### Required validation

Validate the deploy config before calling the SSH action:

- fail if host is empty
- fail if user is empty
- fail if both SSH key and password are empty

This prevents vague SSH errors that are hard to diagnose from Actions logs.

## Step 5: Decide how SSH auth will work

Pick one authentication path:

### Option A: SSH key

Use a private key in GitHub Secrets and the matching public key on the host.

Good when:

- you can manage host SSH keys cleanly
- you want the standard deploy pattern

### Option B: SSH password fallback

Use a password secret if key auth is not stable in the hosting environment.

Good when:

- the host provider makes key provisioning awkward
- you need a reliable short-term path to prove the pipeline

Do not keep both paths ambiguous. Make the workflow select one path intentionally.

## Step 6: Store secrets in GitHub

Set repository or environment secrets for:

- `PROD_SSH_HOST`
- `PROD_SSH_USER`
- `PROD_APP_ROOT`
- `PROD_SSH_PRIVATE_KEY` or `PROD_SSH_PASSWORD`
- `PROD_ENV_FILE_B64` if needed

If you use GitHub Environments, put production-only secrets in the `production` environment and require approvals only if that matches your release process.

## Step 7: Materialize the server env file if needed

If the host does not already have the final production env file, let the workflow write it before the deploy script runs.

Pattern:

```bash
printf '%s' "$PROD_ENV_FILE_B64" | base64 -d > "$ENV_FILE"
chmod 600 "$ENV_FILE"
```

This avoids relying on repo-local `.env` files or host provider injection that may drift from the real runtime.

## Step 8: Verify the deploy on every push

Push a trivial commit to `main` to prove the workflow is wired correctly.

Then verify all three signals:

1. GitHub Actions shows a new `Deploy Production` run.
2. The deploy job finishes successfully.
3. The live health endpoint returns `200`.

Recommended checks:

```bash
gh run list --workflow "Deploy Production" --limit 5
gh run view <run-id> --log
curl -fsS https://<domain>/health
```

## Step 9: Prove the server actually redeployed

Health alone is not enough. Also verify the host is on the new commit.

On the server:

```bash
cd /opt/my-app/repo
git rev-parse --short HEAD
git log -1 --oneline
```

That is the strongest proof that the VPS pulled the latest `main`.

## Step 10: Keep rollback simple

Rollback should be a plain git checkout plus a redeploy:

```bash
cd /opt/my-app/repo
git log --oneline -5
git checkout <known-good-commit>
docker compose -f docker-compose.production.yml --env-file /opt/my-app/.env.production up -d --build --remove-orphans
curl --fail https://<domain>/health
```

## Production checks to preserve

- The workflow triggers on `push` to `main`.
- The host deploy script updates the server checkout before starting containers.
- The server uses a server-only env file.
- The deploy waits for a live health check.
- GitHub Actions logs show the run result.
- The host commit hash matches the pushed commit.

## Common failure modes

- Missing or empty SSH secrets
- Wrong host user
- GitHub workflow runs, but the deploy script never reaches the server
- The host still points at an old commit
- The app is healthy only because a stale instance is still running
- The health endpoint is not tied to real dependencies

## Recommended baseline for new projects

Start with this exact shape:

- one workflow
- one deploy script
- one server env file
- one health endpoint
- one proof of redeploy via server commit hash

Keep the first version boring and explicit. Add complexity only when the deployment path is already proven stable.
