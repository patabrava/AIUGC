# Production Auto-Deploy

## Server bootstrap

Production deploys must run from a VPS checkout and a server-only env file at `/opt/aiugc-prod/.env.production`.
The Traefik router defaults to `Host(\`lippelift.xyz\`)` when `TRAEFIK_HOST_RULE` is missing, so live routing still works with a minimal env file.

```bash
sudo mkdir -p /opt/aiugc-prod
sudo chown "$USER":"$USER" /opt/aiugc-prod
cp .env.production.example /opt/aiugc-prod/.env.production
chmod 600 /opt/aiugc-prod/.env.production
git clone https://github.com/patabrava/AIUGC.git /opt/aiugc-prod/repo
cd /opt/aiugc-prod/repo
bash scripts/deploy/production.sh
```

The deploy script enforces a health gate before success by checking `https://lippelift.xyz/health`.
It also uses a fixed Compose project name and tears down the old stack before bringing the production stack back up, which avoids stale-container name conflicts during redeploys.

## GitHub secrets

- `PROD_SSH_HOST=srv1498567.hstgr.cloud`
- `PROD_SSH_USER=<deploy-user>`
- `PROD_SSH_PRIVATE_KEY=<private key contents>`
- `PROD_APP_ROOT=/opt/aiugc-prod`

## Manual rollback

```bash
cd /opt/aiugc-prod/repo
git log --oneline -5
git checkout <good-commit>
docker compose -f docker-compose.production.yml --env-file /opt/aiugc-prod/.env.production up -d --build --remove-orphans
curl --fail https://lippelift.xyz/health
```
