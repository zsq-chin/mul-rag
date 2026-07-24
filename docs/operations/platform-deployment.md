# SAGE Platform Deployment Guide

This document covers environment variables, secret management, development and
production deployment, backup/restore, graph worker operations, and rollback
procedures for the SAGE platform.

---

## 1. Environment Variables

### Required

| Variable | Description |
|---|---|
| `JWT_SECRET_KEY` | HMAC signing key for authentication tokens |
| `MODEL_CREDENTIAL_MASTER_KEY` | Fernet key (32-byte base64) for encrypting user model credentials |
| `GRAPH_INTERNAL_TOKEN` | Shared secret between the API and the graphrag-worker for import calls |
| `MYSQL_ROOT_PASSWORD` | MySQL root password |
| `MYSQL_PASSWORD` | Application MySQL user password |
| `NEO4J_USERNAME` | Neo4j authentication username |
| `NEO4J_PASSWORD` | Neo4j authentication password |
| `MINIO_ACCESS_KEY` | MinIO/S3 access key for Milvus storage |
| `MINIO_SECRET_KEY` | MinIO/S3 secret key for Milvus storage |

### Optional

| Variable | Default | Description |
|---|---|---|
| `BOCHA_API_KEY` | _(empty)_ | Bocha web search API key; required only if web search is enabled |
| `MULTIMODAL_KB_API_BASE` | `http://10.16.33.2:8002/api/v1` | Remote multimodal RAG endpoint |
| `MULTIMODAL_KB_DEFAULT_KB_ID` | _(empty)_ | Default multimodal knowledge base ID |
| `MULTIMODAL_KB_TIMEOUT` | `30` | Multimodal request timeout (seconds) |
| `MULTIMODAL_KB_TOP_K` | `5` | Default multimodal result count |
| `GRAPH_WORKER_URL` | `http://graphrag-worker:8111` | GraphRAG worker internal URL |
| `USER_MODEL_ALLOWED_HOSTS` | _(empty)_ | Comma-separated hostnames allowed for user model endpoints |
| `USER_MODEL_ALLOW_HTTP` | `false` | Allow HTTP (non-TLS) user model endpoints |
| `CHAT_CONCURRENCY` | `2` | Max concurrent chat requests |
| `RETRIEVAL_CONCURRENCY` | `4` | Max concurrent retrieval requests |
| `GRAPH_IMPORT_CONCURRENCY` | `1` | Max concurrent graph imports |
| `UPSTREAM_PROXY_CONCURRENCY` | `16` | Max concurrent upstream proxy requests |
| `BLOCKING_WORKERS` | `8` | Thread pool size for blocking I/O |
| `CONCURRENCY_ACQUIRE_TIMEOUT` | `30` | Seconds to wait for a concurrency slot |
| `WEB_PORT` | `80` | Host port for the web frontend (production) |
| `MODEL_DIR` | `./models` | Path to local model weights |

---

## 2. Secret Generation and Rotation

### Important: `.env` Does Not Rotate Existing Credentials

Changing a value in `.env` only affects newly created containers. Credentials
already stored inside MySQL, Neo4j, or MinIO data volumes remain unchanged.
When rotating a database credential:

1. **Change the credential in the database first** (see per-service instructions below).
2. **Update `.env`** to match the new value.
3. **Recreate dependent services** so they pick up the new value:

```powershell
docker compose up -d --force-recreate api web graphrag-worker
```

Never put plaintext secrets in shell commands or commit them to version control.

### JWT Secret Key

```powershell
# Generate
python -c "import secrets; print(secrets.token_hex(32))"

# Rotate: update JWT_SECRET_KEY, restart API. Existing tokens are invalidated
# immediately; users must re-authenticate.
```

### Model Credential Master Key

```powershell
# Generate (Fernet-compatible base64 key)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> **Warning:** Rotating `MODEL_CREDENTIAL_MASTER_KEY` invalidates all stored
> user model credentials unless they are migrated first. To migrate, decrypt
> each credential with the old key and re-encrypt with the new key before
> swapping the environment variable.

### Graph Internal Token

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Must match between the API (`GRAPH_INTERNAL_TOKEN`) and the graphrag-worker
(`GRAPH_INTERNAL_TOKEN`).

### MySQL Credentials

Generate strong passwords:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

To rotate credentials in an existing volume:

1. Connect interactively and change the password inside MySQL:

```powershell
docker compose exec mysql mysql -u root -p
# At the mysql> prompt:
# ALTER USER 'root'@'%' IDENTIFIED BY '<new-root-password>';
# ALTER USER 'app_user'@'%' IDENTIFIED BY '<new-app-password>';
# FLUSH PRIVILEGES;
# EXIT;
```

2. Update `MYSQL_ROOT_PASSWORD` and/or `MYSQL_PASSWORD` in `.env`.
3. Recreate dependent services:

```powershell
docker compose up -d --force-recreate mysql api web graphrag-worker
```

### Neo4j Credentials

Set `NEO4J_USERNAME` and `NEO4J_PASSWORD`. The Neo4j container reads
`NEO4J_AUTH=username/password` from these values.

To rotate credentials in an existing volume:

1. Connect interactively and change the password:

```powershell
docker compose exec graph cypher-shell -u neo4j -p <current-password>
# At the cypher> prompt:
# ALTER CURRENT USER SET PASSWORD FROM '<current-password>' TO '<new-password>';
# :exit
```

2. Update `NEO4J_USERNAME` and `NEO4J_PASSWORD` in `.env`.
3. Recreate graph and API:

```powershell
docker compose up -d --force-recreate graph api web graphrag-worker
```

### MinIO Credentials

Set `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY`. These must also be passed as
`MINIO_ACCESS_KEY_ID` and `MINIO_SECRET_ACCESS_KEY` in the Milvus service
environment (already configured in `docker-compose.yml`).

MinIO reads credentials from environment variables at startup; there is no
in-place rotation command. To rotate:

1. Update `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` in `.env`.
2. Recreate MinIO and Milvus so both pick up the new values:

```powershell
docker compose up -d --force-recreate minio milvus
```

---

## 3. Development vs. Production

### Development (source mounts, hot reload)

Development Compose (`docker-compose.yml`) bind-mounts:

- `./server` and `./src` into the API container (with `--reload`)
- `./web` into the web container (Vite dev server)
- `./graphrag_api` into the graphrag-worker container

Edits to Python or Vue source files are reflected immediately.

### Production (image rebuilds, no source mounts)

Production Compose (`docker-compose.prod.yml`) removes all source bind mounts.
To deploy changes:

1. Rebuild the affected images:

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml build api web graphrag-worker
```

2. Restart the services:

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api web graphrag-worker
```

Production uses `uvicorn server.main:app --host 0.0.0.0 --port 5050 --workers 1`
(no `--reload`). The web container uses a production Nginx target with
`NODE_ENV=production`.

---

## 4. Build, Start, Health, Status, and Log Commands

### Base (Development)

```powershell
# Build
docker compose build api web graphrag-worker

# Start
docker compose up -d mysql graph milvus etcd minio api web graphrag-worker

# Health check (use a portable Python check since curl may not be in the image)
python -c "import urllib.request; r=urllib.request.urlopen('http://localhost:5050/api/health'); print(r.status)"

# Status
docker compose ps

# Logs (follow)
docker compose logs -f api
docker compose logs -f graphrag-worker
```

### Production

```powershell
# Build
docker compose -f docker-compose.yml -f docker-compose.prod.yml build api web graphrag-worker

# Start
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Health check (portable Python check)
$port = if ($env:WEB_PORT) { $env:WEB_PORT } else { '80' }
python -c "import urllib.request; r=urllib.request.urlopen('http://localhost:$port/api/health'); print(r.status)"

# Status
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

# Logs
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f api
```

---

## 5. Backup and Restore

> **Before any backup or restore of live databases**, stop the services that
> write to them. Restart them afterward.

### Saves directory (`./saves`)

Contains SQLite databases, uploaded files, and indexing artifacts.

```powershell
# Stop services that write to saves/
docker compose stop api web graphrag-worker

# Backup
$stamp = Get-Date -Format yyyyMMdd
tar -czf "saves-backup-$stamp.tar.gz" saves/

# Restore
tar -xzf saves-backup-YYYYMMDD.tar.gz

# Restart
docker compose start api web graphrag-worker
```

### MySQL

Use the container's own `MYSQL_ROOT_PASSWORD` environment variable so secrets
never appear on the host command line.

```powershell
# Backup (runs a shell inside the container that reads $MYSQL_ROOT_PASSWORD)
docker compose exec -T mysql sh -c `
    'mysqldump -u root --password="$MYSQL_ROOT_PASSWORD" --all-databases --single-transaction --quick' `
    > mysql-backup.sql

# Restore
Get-Content mysql-backup.sql | docker compose exec -T mysql sh -c `
    'mysql -u root --password="$MYSQL_ROOT_PASSWORD"'
```

### Neo4j

Neo4j `neo4j-admin database dump/load` requires the database to be offline.
The data directory is bind-mounted at `./docker/volumes/neo4j/data`.

```powershell
# Stop Neo4j
docker compose stop graph

# Backup (copy the bind-mounted data directory)
$stamp = Get-Date -Format yyyyMMdd
Copy-Item -Recurse ./docker/volumes/neo4j/data "./neo4j-backup-$stamp"

# Restore
# 1. Quarantine current data before overwriting
$quarantine = "./docker/volumes/neo4j/data.quarantine-$(Get-Date -Format yyyyMMdd-HHmmss)"
Rename-Item ./docker/volumes/neo4j/data $quarantine
# 2. Copy backup back
Copy-Item -Recurse "./neo4j-backup-YYYYMMDD" ./docker/volumes/neo4j/data
# 3. Remove quarantine once restore is verified
# Remove-Item -Recurse -Force $quarantine

# Restart
docker compose start graph
```

### Milvus / MinIO / etcd

All three use bind mounts under `./docker/volumes/milvus`, not named Docker
volumes. Stop all related services before copying.

```powershell
# Stop Milvus stack
docker compose stop milvus etcd minio

# Backup all bind-mounted data
$stamp = Get-Date -Format yyyyMMdd
Copy-Item -Recurse ./docker/volumes/milvus "./milvus-backup-$stamp"

# Restore
# 1. Quarantine current data before overwriting
$quarantine = "./docker/volumes/milvus.quarantine-$(Get-Date -Format yyyyMMdd-HHmmss)"
Rename-Item ./docker/volumes/milvus $quarantine
# 2. Copy backup back
Copy-Item -Recurse "./milvus-backup-YYYYMMDD" ./docker/volumes/milvus
# 3. Remove quarantine once restore is verified
# Remove-Item -Recurse -Force $quarantine

# Restart
docker compose start etcd minio milvus
```

### Graph Job Database

The graph job database is stored at `./saves/data/graphrag-jobs/jobs.db`.

```powershell
# Stop the worker first
docker compose stop graphrag-worker

# Backup
Copy-Item saves/data/graphrag-jobs/jobs.db graph-jobs-backup.db

# Restore
Copy-Item graph-jobs-backup.db saves/data/graphrag-jobs/jobs.db

# Restart
docker compose start graphrag-worker
```

---

## 6. Graph Worker Operations

### Restart

```powershell
docker compose restart graphrag-worker
```

On startup the worker automatically marks any previously running jobs as
`interrupted`. Use the retry endpoint to resume them.

### Interrupted Job Recovery

1. Check job status:

```powershell
# List active jobs via the API (requires superadmin token)
python -c "
import urllib.request, json
req = urllib.request.Request('http://localhost:5050/api/data/graph/jobs/{task_id}',
    headers={'Authorization': 'Bearer $TOKEN'})
print(json.loads(urllib.request.urlopen(req).read()))
"
```

2. Retry an interrupted or failed job:

```powershell
python -c "
import urllib.request
req = urllib.request.Request('http://localhost:5050/api/data/graph/jobs/{task_id}/retry',
    method='POST', headers={'Authorization': 'Bearer $TOKEN'})
print(urllib.request.urlopen(req).status)
"
```

3. Cancel a stuck job:

```powershell
python -c "
import urllib.request
req = urllib.request.Request('http://localhost:5050/api/data/graph/jobs/{task_id}/cancel',
    method='POST', headers={'Authorization': 'Bearer $TOKEN'})
print(urllib.request.urlopen(req).status)
"
```

---

## 7. Rollback

The `feature/platform-hardening` branch delivers changes as focused commits in
Task order. To roll back to a known-good state, use `git revert` to preserve
history:

| Task | Commit Message |
|---|---|
| Task 1 | `fix(security): remove embedded secrets and define access policy` |
| Task 2 | `fix(auth): enforce server-side role matrix` |
| Task 3 | `feat(users): add role-aware in-page user management` |
| Task 4 | `feat(chat): add encrypted per-user model credentials` |
| Task 5 | `feat(chat): add personal model editor and switching` |
| Task 6 | `feat(theme): add persistent light and dark modes` |
| Task 7 | `fix(chat): render safe tables and responsive images` |
| Task 8 | `fix(multimodal): stream and page normalized images` |
| Task 9 | `feat(graph): add durable graph build jobs` |
| Task 10 | `feat(graph): convert and import graphrag artifacts` |
| Task 11 | `feat(graph): show resumable build progress` |
| Task 12 | `fix(graph-qa): rank and format graph context correctly` |
| Task 13 | `perf(server): bound blocking work and production concurrency` |
| Task 14 | `test(e2e): verify roles models multimodal graph and load` |

```powershell
# View recent commits to identify the target
git log --oneline feature/platform-hardening

# Create a rollback branch from the current HEAD
git checkout -b rollback/to-task-N feature/platform-hardening

# Revert everything after the known-good commit (from newest to oldest)
# Example: to roll back to task 12, use task 12's commit hash as <last-good-commit>
git revert --no-commit <last-good-commit>..HEAD

# Review the staged changes, then commit
git diff --cached
git commit -m "rollback: revert to task N state"

# Rebuild and redeploy
docker compose -f docker-compose.yml -f docker-compose.prod.yml build api web graphrag-worker
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api web graphrag-worker
```

> **Do not use `git branch -f` or `git reset --hard` on shared branches.**
> `git revert` creates new commits that undo changes while preserving the full
> history, making it safe for collaboration.

---

## 8. Remote Multimodal Endpoint

Set `MULTIMODAL_KB_API_BASE` to the remote RAG API URL. The API proxy checks
reachability without sending secrets.

Verify connectivity using a portable in-container Python check (curl may not
be present in the API image):

```powershell
# Check from inside the API container (reads the variable from the container's own environment)
docker compose exec api python -c "
import os, urllib.request
base = os.environ['MULTIMODAL_KB_API_BASE']
r = urllib.request.urlopen(base + '/health')
print(r.status)
"
```

Image proxy requests are authenticated by the SAGE API (user token), then
forwarded to the multimodal backend. No user credentials are sent upstream.

---

## 9. Server Source Editing Workflow

### Development

Edit files in `./server/`, `./src/`, or `./web/src/` directly. The API
container (with `--reload`) and the Vite dev server pick up changes
automatically.

### Production

Source edits require an image rebuild:

```powershell
# 1. Edit source files
# 2. Rebuild the affected image
docker compose -f docker-compose.yml -f docker-compose.prod.yml build api

# 3. Restart
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api
```

The production API container does **not** mount source code. Any edit that is
not baked into the image will not take effect.

---

## 10. Smoke Test

Run the platform smoke script against a running stack:

```powershell
$sa = Get-Credential -UserName superadmin
$ad = Get-Credential -UserName admin
$us = Get-Credential -UserName tester

.\scripts\smoke_platform.ps1 -BaseUrl http://localhost:5050 `
    -SuperadminCredential $sa `
    -AdminCredential $ad `
    -UserCredential $us `
    -RemoteMultimodalBase http://10.16.33.2:8002/api/v1
```

The script validates login, `/auth/me`, the role route matrix, user management,
and per-role access to chat, knowledge retrieval, graph, multimodal, config,
and statistics endpoints. It exits nonzero on any mismatch.
