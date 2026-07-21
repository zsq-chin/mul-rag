# Platform Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver secure per-user model switching, enforced three-role access control, in-page user management, persistent themes, reliable multimodal rich references, durable GraphRAG-to-Neo4j jobs, correct graph QA, and bounded production concurrency.

**Architecture:** Keep the Vue/FastAPI/SQLite/Neo4j structure, but add focused policy, credential, rich-content, and graph-job modules. The main API remains the authentication and orchestration boundary; GraphRAG runs in its own persistent worker and calls a token-protected import endpoint. Each task is test-first, independently reviewable, and ends in a focused commit.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, `cryptography`, `httpx`, Vue 3, Pinia, Ant Design Vue, `marked`, DOMPurify, Node test runner, SQLite, Neo4j 5, Docker Compose.

## Global Constraints

- Branch: `feature/platform-hardening`.
- Roles are exact strings: `superadmin`, `admin`, `user`.
- `superadmin` has all features; `admin` has knowledge QA plus ordinary-user management; `user` has knowledge QA only.
- API keys never appear in response payloads, browser persistence, URLs, logs, exception messages, or Git.
- Chat requests identify a personal model only with `user_model_id`; the server verifies ownership and decrypts the key just in time.
- Knowledge-base, multimodal, graph, database, statistics, and system-management APIs require `superadmin`.
- Non-superadmins cannot select or forge multimodal/graph/knowledge-base options in chat.
- Rich HTML is sanitized before rendering; only safe table markup and attributes are retained.
- Graph jobs use `queued -> copying -> building -> converting -> importing -> indexing -> completed`, plus `failed`, `cancelling`, `cancelled`, and `interrupted`.
- Production API runs without `--reload`; GraphRAG remains isolated from chat serving.
- Use remote `http://10.16.33.2:8002/api/v1`, knowledge base `钻井设计资料`, query `井身结构设计图` for multimodal acceptance.
- Do not rewrite shared Git history automatically; rotate the exposed third-party key at its provider.

---

## File Map

### Access and user management

- `server/services/access_control.py`: pure role and chat-feature authorization rules.
- `server/routers/auth_router.py`: filtered user administration and audit operations.
- `server/routers/base_router.py`, `data_router.py`, `statistics_router.py`, `multimodal_proxy_router.py`, `chat_router.py`: route dependencies matching the role matrix.
- `web/src/utils/access.mjs`: role-to-navigation and route checks.
- `web/src/router/index.js`: active authentication/role guard; no user-management route.
- `web/src/layouts/AppLayout.vue`: computed navigation and user drawer host.
- `web/src/components/UserManagementComponent.vue`: embeddable, role-aware management panel.

### Personal models and secret handling

- `server/models/user_model_credential.py`: per-user encrypted credential table.
- `server/services/model_credentials.py`: encryption, URL validation, ownership, selection, and model resolution.
- `server/routers/user_model_router.py`: CRUD, validation, and selection endpoints.
- `src/models/__init__.py`, `src/models/chat_model.py`: runtime custom-model adapter with fully redacted failures.
- `web/src/apis/auth_api.js`: personal-model API client.
- `web/src/stores/userModels.js`: non-persisted credential metadata and recent selection.
- `web/src/components/ModelSelectorComponent.vue`, `UserModelEditor.vue`, `ChatComponent.vue`: add/edit/delete/switch workflow.

### Theme and rich references

- `web/src/stores/theme.js`, `web/src/assets/theme.js`, `web/src/App.vue`: theme state and Ant Design algorithms.
- `web/src/utils/richContent.mjs`, `web/src/components/RichReferenceContent.vue`: sanitized Markdown/HTML table rendering.
- `web/src/components/MessageComponent.vue`, `RefsSidebar.vue`, `AppLayout.vue`: unique previews, responsive media, and theme tokens.

### Multimodal retrieval

- `server/utils/multimodal_remote.py`: complete result/image normalization.
- `server/services/http_clients.py`: reusable async clients and lifecycle.
- `server/routers/chat_router.py`, `multimodal_proxy_router.py`: async list/image/proxy behavior.
- `web/src/views/MultimodalKbView.vue`, `RefsSidebar.vue`: paged thumbnails, lazy image errors, and correct rich-result display.

### Graph jobs and retrieval

- `graphrag_api/schemas.py`: task state and API schemas.
- `graphrag_api/job_store.py`: SQLite task persistence and legal transitions.
- `graphrag_api/pipeline.py`: copy, subprocess, Parquet conversion, import, indexing, cancel, and retry.
- `graphrag_api/main.py`: short-lived job endpoints and file endpoints.
- `server/services/graph_import.py`: allowlisted, idempotent Neo4j import and embedding completion.
- `server/routers/data_router.py`: authenticated graph-job proxy and internal import endpoint.
- `web/src/stores/graphJobs.js`, `web/src/views/GraphView.vue`: resumable progress UI.
- `src/core/retriever.py`, `src/core/graphbase.py`: correct graph gating, ranking, deduplication, and prompt context.

### Runtime

- `server/db_manager.py`, `server/main.py`, `src/__init__.py`: WAL, bounded execution, lifecycle cleanup.
- `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`: secrets, worker health, production command, and limits.
- `scripts/smoke_platform.ps1`: role, model, multimodal, graph, and health acceptance sequence.

---

### Task 1: Remove Secret Leakage and Encode Authorization Rules

**Files:**
- Modify: `.gitignore`
- Create: `server/services/__init__.py`
- Create: `server/services/access_control.py`
- Create: `test/test_access_control.py`
- Modify: `src/utils/web_search_bocha.py`
- Modify: `src/models/chat_model.py`
- Track: `server/models/*.py` and `src/models/*.py` after anchoring the root `/models/` data directory ignore rule.

**Interfaces:**
- Produces: `assert_chat_features_allowed(user: User, meta: dict) -> None` and `can_manage_target(actor: User, target: User) -> bool`.
- Produces: `WebSearcher` that reads only `BOCHA_API_KEY`.
- Produces: model errors containing URL/model name but no key characters.

- [ ] **Step 1: Write failing policy and redaction tests**

```python
# test/test_access_control.py
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException

from server.services.access_control import assert_chat_features_allowed, can_manage_target
from src.utils.web_search_bocha import WebSearcher

class AccessControlTests(unittest.TestCase):
    def user(self, role):
        return SimpleNamespace(id=1, role=role)

    def test_only_superadmin_can_enable_managed_retrieval(self):
        for role in ("admin", "user"):
            with self.assertRaises(HTTPException) as ctx:
                assert_chat_features_allowed(self.user(role), {"use_multimodal_kb": True})
            self.assertEqual(ctx.exception.status_code, 403)
        assert_chat_features_allowed(self.user("superadmin"), {"use_multimodal_kb": True})

    def test_admin_can_manage_only_ordinary_users(self):
        actor = self.user("admin")
        self.assertTrue(can_manage_target(actor, self.user("user")))
        self.assertFalse(can_manage_target(actor, self.user("admin")))
        self.assertFalse(can_manage_target(actor, self.user("superadmin")))

    def test_bocha_key_must_come_from_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "BOCHA_API_KEY"):
                WebSearcher()
```

- [ ] **Step 2: Run the test and verify the missing policy module fails**

Run: `python -m unittest test.test_access_control -v`

Expected: `ModuleNotFoundError: No module named 'server.services.access_control'`.

- [ ] **Step 3: Implement exact role policy and secret lookup**

```python
# server/services/access_control.py
from fastapi import HTTPException, status

MANAGED_CHAT_KEYS = frozenset({
    "use_multimodal_kb", "multimodal_kb_id", "multimodal_file_id",
    "use_graph", "db_id", "selectedKB",
})

def assert_chat_features_allowed(user, meta: dict | None) -> None:
    meta = meta or {}
    requested = any(meta.get(key) not in (None, False, "") for key in MANAGED_CHAT_KEYS)
    if requested and user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前角色只能使用普通知识问答")

def can_manage_target(actor, target) -> bool:
    if actor.role == "superadmin":
        return True
    return actor.role == "admin" and target.role == "user"
```

In `src/utils/web_search_bocha.py`, set `api_key = os.getenv("BOCHA_API_KEY")` and keep the existing missing-key exception. In `src/models/chat_model.py`, replace the stream error with `Error streaming response: {type(e).__name__}; URL: {self.base_url}; Model: {self.model_name}`. Do not include `str(e)` because upstream exception text can itself contain credentials, and never interpolate `self.api_key`.

Change `.gitignore` from `models/` to `/models/`. This keeps the root model-weight directory ignored while allowing the Python source packages `server/models` and `src/models` to be tracked. Add those Python source files to this commit; do not add root model weights.

- [ ] **Step 4: Verify policy tests and scan tracked source for key-shaped literals**

Run: `python -m unittest test.test_access_control -v`

Expected: all tests pass.

Run: `git grep -n -E 'sk-[A-Za-z0-9]{16,}|API Key:.*\*\*\*' -- ':!docs/superpowers/specs/*' ':!docs/superpowers/plans/*'`

Expected: no output. Revoke and rotate the previously committed provider key outside Git before enabling web search.

- [ ] **Step 5: Commit**

```powershell
git add .gitignore server/services/__init__.py server/services/access_control.py test/__init__.py test/test_access_control.py src/utils/web_search_bocha.py server/models/*.py src/models/*.py
git commit -m "fix(security): remove embedded secrets and define access policy"
```

### Task 2: Enforce Backend Role Matrix

**Files:**
- Create: `test/test_role_routes.py`
- Modify: `server/routers/auth_router.py`
- Modify: `server/routers/base_router.py`
- Modify: `server/routers/data_router.py`
- Modify: `server/routers/statistics_router.py`
- Modify: `server/routers/multimodal_proxy_router.py`
- Modify: `server/routers/chat_router.py`
- Modify: `server/routers/college_router.py`

**Interfaces:**
- Consumes: `assert_chat_features_allowed` and `can_manage_target` from Task 1.
- Produces: all management routes guarded by `get_superadmin_user`; authenticated chat remains available to all roles.
- Produces: admin user queries filtered to `User.role == "user"`.

- [ ] **Step 1: Add route-policy regression tests**

```python
# test/test_role_routes.py
import unittest
from types import SimpleNamespace
from fastapi import HTTPException
from server.services.access_control import can_manage_target

class RoleRouteTests(unittest.TestCase):
    def test_admin_cannot_manage_admin(self):
        actor = SimpleNamespace(role="admin")
        self.assertFalse(can_manage_target(actor, SimpleNamespace(role="admin")))

    def test_superadmin_can_manage_every_role(self):
        actor = SimpleNamespace(role="superadmin")
        for role in ("user", "admin", "superadmin"):
            self.assertTrue(can_manage_target(actor, SimpleNamespace(role=role)))
```

Add an AST-based assertion that each `/data/graph`, `/statistics`, `/multimodal`, `/config`, and `/log` endpoint contains `Depends(get_superadmin_user)`. This test must enumerate every current decorator/function pair so a future unguarded endpoint fails review.

- [ ] **Step 2: Run route tests and confirm current unguarded endpoints fail**

Run: `python -m unittest test.test_role_routes -v`

Expected: failures naming graph, statistics, multimodal, config, log, or college endpoints without superadmin dependency.

- [ ] **Step 3: Apply the dependency and target-user rules**

Use `current_user: User = Depends(get_superadmin_user)` on management endpoints. Keep `get_required_user` only for ordinary chat, personal history, personal model endpoints, and `/auth/me`.

In `auth_router.py`, implement list filtering and target checks exactly as follows:

```python
query = db.query(User)
if current_user.role == "admin":
    query = query.filter(User.role == "user")
users = query.order_by(User.id).offset(skip).limit(min(limit, 100)).all()

if current_user.role == "admin" and user_data.role != "user":
    raise HTTPException(status_code=403, detail="管理员只能创建普通用户")

if not can_manage_target(current_user, user):
    raise HTTPException(status_code=403, detail="无权管理该用户")
```

Validate every requested role against `{"superadmin", "admin", "user"}`. Require `get_required_user` for `/auth/me`. Add `assert_chat_features_allowed(current_user, meta)` before retrieval/model execution in both `/chat/` and `/chat/call`.

- [ ] **Step 4: Run role tests and the existing backend suite**

Run: `python -m unittest test.test_access_control test.test_role_routes -v`

Expected: all role tests pass.

Run: `python -m unittest discover -s test -v`

Expected: all existing and new tests pass.

- [ ] **Step 5: Commit**

```powershell
git add server/routers test/test_role_routes.py
git commit -m "fix(auth): enforce server-side role matrix"
```

### Task 3: Add Role-Aware Navigation and In-Page User Drawer

**Files:**
- Create: `web/src/utils/access.mjs`
- Create: `web/tests/access.test.mjs`
- Modify: `web/package.json`
- Modify: `web/src/stores/user.js`
- Modify: `web/src/router/index.js`
- Modify: `web/src/layouts/AppLayout.vue`
- Modify: `web/src/components/UserManagementComponent.vue`

**Interfaces:**
- Produces: `navigationForRole(role: string) -> NavigationItem[]` and `canAccessRoute(role, roles) -> boolean`.
- Produces: `userStore.hydrate()` that refreshes role from `/api/auth/me` once per page load.
- Produces: `AppLayout` drawer state that does not change `route.fullPath`.

- [ ] **Step 1: Write failing navigation tests**

```javascript
// web/tests/access.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { navigationForRole, canAccessRoute } from '../src/utils/access.mjs'

test('ordinary user sees chat only', () => {
  assert.deepEqual(navigationForRole('user').map(item => item.key), ['chat'])
})

test('admin sees chat and user drawer only', () => {
  assert.deepEqual(navigationForRole('admin').map(item => item.key), ['chat', 'users'])
})

test('superadmin sees managed features', () => {
  const keys = navigationForRole('superadmin').map(item => item.key)
  assert.ok(keys.includes('graph'))
  assert.ok(keys.includes('database'))
  assert.ok(keys.includes('multimodal'))
})

test('route roles are exact', () => {
  assert.equal(canAccessRoute('admin', ['superadmin']), false)
  assert.equal(canAccessRoute('superadmin', ['superadmin']), true)
})
```

- [ ] **Step 2: Add the Node test script and verify failure**

Add `"test": "node --test tests/*.test.mjs"` to `web/package.json`.

Run: `pnpm --dir web test`

Expected: module-not-found failure for `src/utils/access.mjs`.

- [ ] **Step 3: Implement pure access metadata and active route guard**

```javascript
// web/src/utils/access.mjs
export const NAVIGATION = [
  { key: 'chat', name: '智能问答', path: '/chat', roles: ['user', 'admin', 'superadmin'] },
  { key: 'users', name: '用户管理', action: 'users', roles: ['admin', 'superadmin'] },
  { key: 'graph', name: '知识图谱', path: '/graph', roles: ['superadmin'] },
  { key: 'database', name: '知识库', path: '/database', roles: ['superadmin'] },
  { key: 'statistics', name: '问答统计', path: '/statistics', roles: ['superadmin'] },
  { key: 'multimodal', name: '多模态知识库', path: '/multimodal-kb', roles: ['superadmin'] },
]

export const navigationForRole = role => NAVIGATION.filter(item => item.roles.includes(role))
export const canAccessRoute = (role, roles = []) => roles.length === 0 || roles.includes(role)
```

Remove `/usermanagement`. Set `/chat` roles to all three and all managed routes to `roles: ['superadmin']`. The guard calls `await userStore.hydrate()`, redirects unauthenticated users to `/login`, and redirects forbidden users to `/chat` with `message.warning('没有权限访问该功能')`.

- [ ] **Step 4: Embed user management in an Ant drawer**

In `AppLayout.vue`, compute navigation from `navigationForRole(userStore.userRole)`. Render route entries with `RouterLink`; render the `users` action as an icon button that sets `userDrawerOpen = true`. Add:

```vue
<a-drawer
  v-model:open="userDrawerOpen"
  title="用户管理"
  placement="right"
  :width="drawerWidth"
  :destroy-on-close="false"
>
  <UserManagementComponent :actor-role="userStore.userRole" />
</a-drawer>
```

Set `drawerWidth` to `computed(() => windowWidth.value < 768 ? '100%' : 760)`. Remove router imports and navigation calls from `UserManagementComponent.vue`; hide role editing when `actorRole === 'admin'`, and force create payload role to `user` for admins.

- [ ] **Step 5: Verify tests and production build**

Run: `pnpm --dir web test`

Expected: all access tests pass.

Run: `pnpm --dir web build`

Expected: Vite build exits 0 with no unresolved imports.

- [ ] **Step 6: Commit**

```powershell
git add web/package.json web/src/utils/access.mjs web/tests/access.test.mjs web/src/stores/user.js web/src/router/index.js web/src/layouts/AppLayout.vue web/src/components/UserManagementComponent.vue
git commit -m "feat(users): add role-aware in-page user management"
```

### Task 4: Persist and Resolve Encrypted Per-User Models

**Files:**
- Create: `server/models/user_model_credential.py`
- Create: `server/services/model_credentials.py`
- Create: `server/routers/user_model_router.py`
- Create: `test/test_user_model_credentials.py`
- Modify: `server/models/user_model.py`
- Modify: `server/db_manager.py`
- Modify: `server/routers/__init__.py`
- Modify: `server/routers/chat_router.py`
- Modify: `src/models/__init__.py`
- Modify: `src/models/chat_model.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `CredentialCipher.encrypt(str) -> str`, `CredentialCipher.decrypt(str) -> str`.
- Produces: `validate_api_base(url: str) -> str` and `resolve_model_for_user(db, user, meta)`.
- Produces: `/api/chat/user-models` CRUD and `/api/chat/user-models/{id}/select`.
- Consumes: `select_model(..., custom_model_info=...)` from `src.models`.

- [ ] **Step 1: Write failing encryption, ownership, and URL tests**

```python
# test/test_user_model_credentials.py
import os
import unittest
from cryptography.fernet import Fernet
from unittest.mock import patch

from server.services.model_credentials import CredentialCipher, validate_api_base

class CredentialTests(unittest.TestCase):
    def test_cipher_round_trip_does_not_contain_plaintext(self):
        cipher = CredentialCipher(Fernet.generate_key().decode())
        encrypted = cipher.encrypt("secret-value")
        self.assertNotIn("secret-value", encrypted)
        self.assertEqual(cipher.decrypt(encrypted), "secret-value")

    def test_https_public_endpoint_is_accepted(self):
        with patch("server.services.model_credentials.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            self.assertEqual(validate_api_base("https://models.example.com/v1"), "https://models.example.com/v1")

    def test_private_endpoint_is_rejected_without_allowlist(self):
        with patch.dict(os.environ, {"USER_MODEL_ALLOWED_HOSTS": ""}, clear=False), patch(
            "server.services.model_credentials.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
        ):
            with self.assertRaisesRegex(ValueError, "不允许"):
                validate_api_base("https://localhost/v1")
```

- [ ] **Step 2: Run the credential test and verify failure**

Run: `python -m unittest test.test_user_model_credentials -v`

Expected: module-not-found failure for `server.services.model_credentials`.

- [ ] **Step 3: Add the credential table and cipher**

```python
# server/models/user_model_credential.py
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from server.models import Base

class UserModelCredential(Base):
    __tablename__ = "user_model_credentials"
    __table_args__ = (UniqueConstraint("user_id", "display_name", name="uq_user_model_display_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    provider = Column(String(40), nullable=False, default="openai-compatible")
    model_name = Column(String(200), nullable=False)
    api_base = Column(String(500), nullable=False)
    encrypted_api_key = Column(Text, nullable=False)
    key_hint = Column(String(4), nullable=False, default="")
    key_version = Column(Integer, nullable=False, default=1)
    last_used_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    user = relationship("User", back_populates="model_credentials")
```

`CredentialCipher` loads a supplied key or `MODEL_CREDENTIAL_MASTER_KEY`, rejects a missing/invalid key with a non-secret message, and wraps `cryptography.fernet.Fernet`. Add `cryptography>=44.0.0` as a direct dependency. Import the model before `Base.metadata.create_all` and add `User.model_credentials` with cascade delete.

- [ ] **Step 4: Implement URL validation and owned CRUD service**

```python
def validate_api_base(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    allow_http = os.getenv("USER_MODEL_ALLOW_HTTP", "false").lower() == "true"
    if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}) or not parsed.hostname:
        raise ValueError("模型地址必须是有效的 HTTPS URL")
    allowed = {host.strip().lower() for host in os.getenv("USER_MODEL_ALLOWED_HOSTS", "").split(",") if host.strip()}
    if parsed.hostname.lower() not in allowed:
        for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                raise ValueError("该模型地址不允许访问")
    return value.strip().rstrip("/")
```

Service queries always include both `UserModelCredential.id == model_id` and `UserModelCredential.user_id == user.id`. It returns dictionaries with `id`, `display_name`, `provider`, `model_name`, `api_base`, `key_hint`, `has_api_key`, `last_used_at`, `created_at`, and `updated_at`; it never returns `encrypted_api_key`.

- [ ] **Step 5: Add typed routes and runtime resolution**

Use Pydantic request models with `api_key: SecretStr` on create and `api_key: SecretStr | None` on update. Implement:

```text
GET    /api/chat/user-models
POST   /api/chat/user-models
PATCH  /api/chat/user-models/{model_id}
DELETE /api/chat/user-models/{model_id}
POST   /api/chat/user-models/{model_id}/select
POST   /api/chat/user-models/validate
```

All depend on `get_required_user`. Selection updates `last_used_at = datetime.now(timezone.utc)`. Validation builds a temporary client with a connect/read timeout and sends the smallest supported request; exceptions pass through a redactor that replaces bearer tokens and `sk-*` patterns with `[REDACTED]`.

Extend `select_model` with:

```python
def select_model(model_provider=None, model_name=None, custom_model_info=None):
    if custom_model_info is not None:
        return CustomModel({
            "name": custom_model_info["model_name"],
            "api_base": custom_model_info["api_base"],
            "api_key": custom_model_info["api_key"],
        })
    # existing built-in provider branches remain unchanged
```

In `chat_router.py`, normalize `meta = meta or {}`, add `db: Session = Depends(get_db)`, call `assert_chat_features_allowed`, then `model = resolve_model_for_user(db, current_user, meta)`. The resolver accepts `meta["user_model_id"]` only after ownership lookup; otherwise it uses the requested built-in provider/name or server default.

- [ ] **Step 6: Verify credential and chat-model tests**

Run: `python -m unittest test.test_user_model_credentials -v`

Expected: encryption, URL, response-shape, ownership, and selection tests pass.

Run: `python -m unittest discover -s test -v`

Expected: full backend suite passes.

- [ ] **Step 7: Commit**

```powershell
git add pyproject.toml uv.lock server/models server/services/model_credentials.py server/routers/user_model_router.py server/routers/chat_router.py server/routers/__init__.py test/test_user_model_credentials.py src/models/__init__.py src/models/chat_model.py
git commit -m "feat(chat): add encrypted per-user model credentials"
```

### Task 5: Add the Knowledge-QA Model Editor and Selector

**Files:**
- Create: `web/src/stores/userModels.js`
- Create: `web/src/components/UserModelEditor.vue`
- Create: `web/src/utils/modelSelection.mjs`
- Create: `web/tests/modelSelection.test.mjs`
- Modify: `web/src/apis/auth_api.js`
- Modify: `web/src/components/ModelSelectorComponent.vue`
- Modify: `web/src/components/ChatComponent.vue`
- Modify: `web/src/stores/user.js`

**Interfaces:**
- Produces: selector values `{ kind: 'builtin', provider, name }` or `{ kind: 'user', userModelId, name }`.
- Produces: chat metadata containing either built-in provider/name or `user_model_id`, never an API key.
- Consumes: Task 4 personal-model API.

- [ ] **Step 1: Write failing model-selection serialization tests**

```javascript
// web/tests/modelSelection.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { applyModelSelection } from '../src/utils/modelSelection.mjs'

test('personal selection sends only opaque id', () => {
  const meta = applyModelSelection({}, { kind: 'user', userModelId: 7, name: '生产模型' })
  assert.equal(meta.user_model_id, 7)
  assert.equal('api_key' in meta, false)
  assert.equal('api_base' in meta, false)
})

test('built-in selection clears personal id', () => {
  const meta = applyModelSelection({ user_model_id: 7 }, { kind: 'builtin', provider: 'openai', name: 'gpt-4.1' })
  assert.equal(meta.user_model_id, undefined)
  assert.equal(meta.model_provider, 'openai')
  assert.equal(meta.model_name, 'gpt-4.1')
})
```

- [ ] **Step 2: Run the web tests and verify the utility is missing**

Run: `pnpm --dir web test`

Expected: module-not-found failure for `modelSelection.mjs`.

- [ ] **Step 3: Implement metadata serialization and API store**

```javascript
// web/src/utils/modelSelection.mjs
export function applyModelSelection(meta, selection) {
  const next = { ...meta }
  delete next.user_model_id
  delete next.model_provider
  delete next.model_name
  if (selection.kind === 'user') next.user_model_id = selection.userModelId
  else {
    next.model_provider = selection.provider
    next.model_name = selection.name
  }
  return next
}
```

`userModels.js` stores only response metadata and selected ID in memory. Its `load`, `create`, `update`, `remove`, `validate`, and `select` actions call `chatApi`; no persistence plugin or browser storage is used. Sort personal models by descending `last_used_at`, then name.

- [ ] **Step 4: Build the editor modal**

`UserModelEditor.vue` contains inputs for display name, provider (`openai-compatible` initially), model name, API base, and password-type API key. On edit, the key input starts empty with text `留空则保留原密钥`; submit omits `api_key` when empty. It clears all form fields in `afterClose` and never logs form data.

- [ ] **Step 5: Integrate selector and chat metadata**

`ModelSelectorComponent.vue` renders built-in and personal groups in a scrollable menu. Each personal row selects the model; an adjacent icon-only menu provides edit/delete with tooltips. A final `Plus` icon command opens `UserModelEditor`. Emit `{ kind: 'builtin', provider, name }` or `{ kind: 'user', userModelId, name }` exactly.

In `ChatComponent.vue`, keep a local `selectedModel` and call:

```javascript
const handleModelSelect = async selection => {
  selectedModel.value = selection
  Object.assign(meta, applyModelSelection(meta, selection))
  if (selection.kind === 'user') await userModelsStore.select(selection.userModelId)
}
```

Delete stale model keys before `Object.assign`, or replace the metadata model fields explicitly so switching cannot retain both kinds. Hide knowledge-base, graph, and multimodal selectors unless `userStore.isSuperAdmin`.

- [ ] **Step 6: Verify tests, build, and browser storage**

Run: `pnpm --dir web test`

Expected: access and model-selection tests pass.

Run: `pnpm --dir web build`

Expected: Vite build exits 0.

Browser acceptance: add a disposable model key, switch models, refresh, and delete it. Inspect Local Storage, Session Storage, IndexedDB, network response bodies, and console output; none may contain the disposable key.

- [ ] **Step 7: Commit**

```powershell
git add web/src/apis/auth_api.js web/src/stores/userModels.js web/src/components/UserModelEditor.vue web/src/components/ModelSelectorComponent.vue web/src/components/ChatComponent.vue web/src/utils/modelSelection.mjs web/tests/modelSelection.test.mjs web/src/stores/user.js
git commit -m "feat(chat): add personal model editor and switching"
```

### Task 6: Add Persistent Light and Dark Themes

**Files:**
- Create: `web/src/stores/theme.js`
- Create: `web/src/utils/themePreference.mjs`
- Create: `web/tests/themePreference.test.mjs`
- Modify: `web/src/assets/theme.js`
- Modify: `web/src/App.vue`
- Modify: `web/src/layouts/AppLayout.vue`
- Modify: `web/src/assets/main.css`
- Modify: `web/src/components/ChatComponent.vue`
- Modify: `web/src/components/MessageComponent.vue`
- Modify: `web/src/components/RefsSidebar.vue`
- Modify: `web/src/components/UserManagementComponent.vue`
- Modify: `web/src/views/GraphView.vue`
- Modify: `web/src/views/DataBaseView.vue`
- Modify: `web/src/views/DataBaseInfoView.vue`
- Modify: `web/src/views/MultimodalKbView.vue`
- Modify: `web/src/views/AnswerStatistics.vue`
- Modify: `web/src/views/SettingView.vue`

**Interfaces:**
- Produces: theme store state `mode: 'light' | 'dark'`, `toggle()`, and `antTheme`.
- Produces: `resolveInitialTheme(saved, prefersDark) -> 'light' | 'dark'`.

- [ ] **Step 1: Write failing preference tests**

```javascript
// web/tests/themePreference.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveInitialTheme } from '../src/utils/themePreference.mjs'

test('saved value wins over system preference', () => {
  assert.equal(resolveInitialTheme('light', true), 'light')
  assert.equal(resolveInitialTheme('dark', false), 'dark')
})

test('system preference is used when no valid value exists', () => {
  assert.equal(resolveInitialTheme(null, true), 'dark')
  assert.equal(resolveInitialTheme('invalid', false), 'light')
})
```

- [ ] **Step 2: Run the web tests and verify failure**

Run: `pnpm --dir web test`

Expected: module-not-found failure for `themePreference.mjs`.

- [ ] **Step 3: Implement preference and Pinia theme state**

```javascript
// web/src/utils/themePreference.mjs
export function resolveInitialTheme(saved, prefersDark) {
  if (saved === 'light' || saved === 'dark') return saved
  return prefersDark ? 'dark' : 'light'
}
```

The store initializes from `localStorage.getItem('theme-mode')` and `matchMedia('(prefers-color-scheme: dark)')`, persists only `light`/`dark`, and sets `document.documentElement.dataset.theme`. Its computed Ant config uses `theme.defaultAlgorithm` or `theme.darkAlgorithm` and shared tokens from `assets/theme.js`.

- [ ] **Step 4: Wire Ant Design and add the icon toggle**

```vue
<!-- web/src/App.vue -->
<script setup>
import { onBeforeMount } from 'vue'
import { useThemeStore } from '@/stores/theme'
const themeStore = useThemeStore()
onBeforeMount(themeStore.apply)
</script>
<template><a-config-provider :theme="themeStore.antTheme"><router-view /></a-config-provider></template>
```

Add an icon-only button near user information in desktop and mobile navigation. Show `Moon` in light mode and `Sun` in dark mode, with tooltips `切换到深色模式` and `切换到浅色模式`.

- [ ] **Step 5: Replace hardcoded structural colors with semantic tokens**

Define `--app-bg`, `--surface`, `--surface-raised`, `--text-primary`, `--text-secondary`, `--border`, `--hover`, and `--danger` under both `[data-theme='light']` and `[data-theme='dark']`. Replace white/black backgrounds and text in the listed layout, chat, user drawer, graph, database, multimodal, statistics, and settings components. Do not change domain accent colors or redesign page composition.

- [ ] **Step 6: Verify tests, color scan, and build**

Run: `pnpm --dir web test`

Expected: all theme and earlier tests pass.

Run: `rg -n "background(-color)?:\s*(white|#fff(?:fff)?|black|#000(?:000)?)|color:\s*(white|black|#000(?:000)?|#fff(?:fff)?)" web/src/layouts/AppLayout.vue web/src/components/ChatComponent.vue web/src/components/MessageComponent.vue web/src/components/RefsSidebar.vue`

Expected: only intentional icon/contrast exceptions with nearby explanatory CSS variables.

Run: `pnpm --dir web build`

Expected: Vite build exits 0.

- [ ] **Step 7: Commit**

```powershell
git add web/src/stores/theme.js web/src/utils/themePreference.mjs web/tests/themePreference.test.mjs web/src/assets/theme.js web/src/App.vue web/src/layouts/AppLayout.vue web/src/assets/main.css web/src/components/ChatComponent.vue web/src/components/MessageComponent.vue web/src/components/RefsSidebar.vue web/src/components/UserManagementComponent.vue web/src/views/GraphView.vue web/src/views/DataBaseView.vue web/src/views/DataBaseInfoView.vue web/src/views/MultimodalKbView.vue web/src/views/AnswerStatistics.vue web/src/views/SettingView.vue
git commit -m "feat(theme): add persistent light and dark modes"
```

### Task 7: Render Sanitized Tables and Responsive Images

**Files:**
- Create: `web/src/utils/richContent.mjs`
- Create: `web/src/components/RichReferenceContent.vue`
- Create: `web/tests/richContent.test.mjs`
- Modify: `web/package.json`
- Modify: `web/src/components/MessageComponent.vue`
- Modify: `web/src/components/RefsSidebar.vue`

**Interfaces:**
- Produces: `renderRichContent(input: string, window: Window) -> string`.
- Produces: reusable component accepting `content` and optional normalized `images`.

- [ ] **Step 1: Add failing sanitizer tests**

```javascript
// web/tests/richContent.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import { renderRichContent } from '../src/utils/richContent.mjs'

test('keeps table spans and removes executable markup', () => {
  const window = new JSDOM('').window
  const html = renderRichContent('<table><tr><td rowspan="2" onclick="alert(1)">A</td></tr></table><script>alert(2)</script>', window)
  assert.match(html, /rowspan="2"/)
  assert.doesNotMatch(html, /onclick|script|alert/)
})

test('renders markdown table', () => {
  const window = new JSDOM('').window
  const html = renderRichContent('| A | B |\n| - | - |\n| 1 | 2 |', window)
  assert.match(html, /<table>/)
  assert.match(html, /<td>1<\/td>/)
})
```

- [ ] **Step 2: Add dependencies and verify failure**

Add runtime `dompurify` and dev `jsdom` dependencies, then run `pnpm --dir web install` so `pnpm-lock.yaml` is updated.

Run: `pnpm --dir web test`

Expected: module-not-found failure for `richContent.mjs`.

- [ ] **Step 3: Implement marked plus DOMPurify rendering**

```javascript
// web/src/utils/richContent.mjs
import { marked } from 'marked'
import createDOMPurify from 'dompurify'

export function renderRichContent(input, window) {
  const source = String(input || '')
  const html = /<\/?(?:table|thead|tbody|tr|th|td)\b/i.test(source) ? source : marked.parse(source)
  return createDOMPurify(window).sanitize(html, {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'code', 'pre', 'ul', 'ol', 'li', 'blockquote', 'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td'],
    ALLOWED_ATTR: ['href', 'target', 'rel', 'src', 'alt', 'title', 'rowspan', 'colspan'],
    ALLOW_UNKNOWN_PROTOCOLS: false,
  })
}
```

`RichReferenceContent.vue` computes sanitized HTML using `window`, renders it inside `.rich-reference-scroll`, and renders deduplicated standalone images only when their URL is not already present in the HTML. It adds `loading="lazy"`, a fixed thumbnail aspect ratio, error placeholder, and click-to-preview modal.

- [ ] **Step 4: Replace text interpolation and fixed preview IDs**

Use `RichReferenceContent` for ordinary and multimodal result text in `RefsSidebar.vue`. In `MessageComponent.vue`, derive the Markdown editor ID from message ID, for example `preview-${props.message.id}`, so multiple answers cannot share DOM state. Add horizontal overflow for tables and `max-width: 100%; height: auto` for answer images.

- [ ] **Step 5: Verify sanitizer tests and build**

Run: `pnpm --dir web test`

Expected: malicious markup is removed and both table forms pass.

Run: `pnpm --dir web build`

Expected: Vite build exits 0.

- [ ] **Step 6: Commit**

```powershell
git add web/package.json web/pnpm-lock.yaml web/src/utils/richContent.mjs web/src/components/RichReferenceContent.vue web/tests/richContent.test.mjs web/src/components/MessageComponent.vue web/src/components/RefsSidebar.vue
git commit -m "fix(chat): render safe tables and responsive images"
```

### Task 8: Normalize, Stream, and Page Multimodal Images

**Files:**
- Create: `server/services/http_clients.py`
- Modify: `pyproject.toml`
- Modify: `server/main.py`
- Modify: `server/utils/multimodal_remote.py`
- Modify: `server/routers/chat_router.py`
- Modify: `server/routers/multimodal_proxy_router.py`
- Modify: `test/test_multimodal_remote.py`
- Modify: `web/src/apis/multimodal.js`
- Modify: `web/src/views/MultimodalKbView.vue`
- Modify: `web/src/components/RefsSidebar.vue`

**Interfaces:**
- Produces normalized result keys `contentType`, `text`, `images`, `fileId`, `fileName`, `page`, `score`, `metadata`.
- Produces reusable `get_multimodal_client() -> httpx.AsyncClient` with lifecycle close.
- Produces server-paged image metadata `{items, page, pageSize, total}`.

- [ ] **Step 1: Extend normalization tests with real remote shapes**

```python
def test_referenced_images_and_image_path_are_deduplicated(self):
    payload = {"results": [{
        "chunk_text": "![井身结构](./images/井身结构.png)",
        "source": json.dumps({
            "file_id": "file-1",
            "image_path": "井身结构.png",
            "referenced_images": ["井身结构.png", {"image_path": "套管程序.png", "caption": "套管程序"}],
        }),
    }]}
    result = normalize_multimodal_results(payload, kb_id="kb-1")[0]
    self.assertEqual([image["path"] for image in result["images"]], ["井身结构.png", "套管程序.png"])

def test_complex_html_table_is_preserved_as_table_content(self):
    payload = {"results": [{"content": '<table><tr><td rowspan="2">A</td></tr></table>', "source": {"file_id": "f"}}]}
    result = normalize_multimodal_results(payload, kb_id="k")[0]
    self.assertEqual(result["contentType"], "table")
    self.assertIn("rowspan", result["text"])
```

- [ ] **Step 2: Run focused tests and verify the new cases fail**

Run: `python -m unittest test.test_multimodal_remote -v`

Expected: failures for `referenced_images` and `contentType`.

- [ ] **Step 3: Complete image-field normalization and path validation**

Accept string/list/dict forms from `image_path`, `imagePath`, `img_name`, `images`, and `referenced_images`. Normalize `./images/name.png`, `images/name.png`, and `name.png` to the basename expected by `/pdf/images`; reject absolute paths, URLs, NUL, and `..` segments. Deduplicate by normalized case-sensitive path while preserving source order. Set `contentType = 'table'` for HTML tables, otherwise remote type or `image`/`text`.

- [ ] **Step 4: Replace blocking proxies with streaming async HTTP**

Add `httpx>=0.28.1`. Create one application-scoped client with limits and connect/read/write/pool timeouts. Build upstream requests with `client.build_request`, use `await client.send(request, stream=True)`, and return `StreamingResponse(response.aiter_bytes(64 * 1024), background=BackgroundTask(response.aclose))`. Forward only allowlisted cache/content headers. Add `get_superadmin_user` to KB list, chat image, and generic multimodal proxy routes.

- [ ] **Step 5: Stop loading the complete image catalog**

Change `MultimodalKbView.vue` from `getAllKbImages` plus client slicing to `getKbImages({ kbId, page, pageSize })`. Store only current-page `items` and server `total`. Abort the previous request when page/KB changes, use 24 items by default, and render only current-page thumbnail URLs. On modal close, clear preview arrays so detached images can be reclaimed. If the remote API returns an unpaged list, the main proxy slices metadata before returning and never fetches binary images eagerly.

- [ ] **Step 6: Verify unit, remote, and build behavior**

Run: `python -m unittest test.test_multimodal_remote -v`

Expected: all normalization and path tests pass.

Remote acceptance: call `/api/chat/multimodal/kbs`, select `钻井设计资料`, submit `井身结构设计图`, and request each returned proxy image. Expected: at least one 200 response with `Content-Type: image/png` and PNG signature bytes.

Run: `pnpm --dir web build`

Expected: Vite build exits 0.

Browser acceptance: open image management, move across at least three pages, and return. Network shows only one metadata page and current-page images at a time; the tab remains responsive and memory stabilizes after closing previews.

- [ ] **Step 7: Commit**

```powershell
git add pyproject.toml uv.lock server/services/http_clients.py server/main.py server/utils/multimodal_remote.py server/routers/chat_router.py server/routers/multimodal_proxy_router.py test/test_multimodal_remote.py web/src/apis/multimodal.js web/src/views/MultimodalKbView.vue web/src/components/RefsSidebar.vue
git commit -m "fix(multimodal): stream and page normalized images"
```

### Task 9: Create Durable Graph Job State and Worker API

**Files:**
- Modify: `.gitignore`
- Create: `graphrag_api/schemas.py`
- Create: `graphrag_api/job_store.py`
- Create: `graphrag_api/pipeline.py`
- Create: `test/test_graph_job_store.py`
- Modify: `graphrag_api/main.py`
- Modify: `docker/graphrag.Dockerfile`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: `JobStore.create`, `get`, `transition`, `request_cancel`, `mark_running_interrupted`, and `retry`.
- Produces: `POST /jobs`, `GET /jobs/{task_id}`, `POST /jobs/{task_id}/cancel`, and `POST /jobs/{task_id}/retry`.
- Produces: one active job per `graph_type` (`ground` or `drill`).

- [ ] **Step 1: Write failing state-machine tests**

```python
# test/test_graph_job_store.py
import tempfile
import unittest
from pathlib import Path
from graphrag_api.job_store import JobStore

class GraphJobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name) / "jobs.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_progress_is_monotonic_and_completion_is_100(self):
        job = self.store.create("ground")
        self.store.transition(job.id, "copying", 5)
        self.store.transition(job.id, "building", 30)
        with self.assertRaises(ValueError):
            self.store.transition(job.id, "building", 20)
        completed = self.store.transition(job.id, "completed", 100)
        self.assertEqual(completed.progress, 100)

    def test_only_one_active_job_per_graph_type(self):
        self.store.create("drill")
        with self.assertRaisesRegex(ValueError, "active"):
            self.store.create("drill")

    def test_restart_marks_running_job_interrupted(self):
        job = self.store.create("ground")
        self.store.transition(job.id, "building", 25)
        self.store.mark_running_interrupted()
        self.assertEqual(self.store.get(job.id).status, "interrupted")
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m unittest test.test_graph_job_store -v`

Expected: module-not-found failure for `graphrag_api.job_store`.

- [ ] **Step 3: Implement schemas and transactional SQLite store**

Define exact enum values from Global Constraints and allowed transitions in a constant map. The jobs table contains `id`, `graph_type`, `status`, `stage`, `progress`, `created_at`, `started_at`, `finished_at`, `cancel_requested`, `input_count`, `relationship_count`, `artifact_path`, `artifact_sha256`, `error_summary`, and `log_tail`. Use `BEGIN IMMEDIATE` for create/transition and a partial unique index on active `graph_type` statuses. Clamp log tail to 32 KiB and error summary to 2 KiB.

Remove the `graphrag_api/` ignore rule from `.gitignore` before editing the worker, then track its existing `__init__.py` and `main.py` together with the new worker modules. Keep generated GraphRAG outputs ignored by their `indexing/output/` rules.

- [ ] **Step 4: Add a background runner with cancellation**

`GraphPipeline.submit(graph_type)` creates a record and puts its ID on an in-process queue serviced by one daemon worker thread. The pipeline uses `subprocess.Popen` with stdout/stderr merged, reads lines incrementally, maps recognized GraphRAG workflow lines into the 10-70 building range, and checks `cancel_requested` between lines. Cancellation terminates, waits up to 10 seconds, then kills if needed. Every exception transitions to `failed` in `finally`; success can only be set by the final indexing stage.

- [ ] **Step 5: Replace blocking build endpoints with job endpoints**

```python
@app.post("/jobs", status_code=202, response_model=JobResponse)
def create_job(request: JobCreate):
    try:
        return pipeline.submit(request.graph_type)
    except ActiveJobError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@app.get("/jobs/{task_id}", response_model=JobResponse)
def get_job(task_id: str):
    job = store.get(task_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job
```

Keep existing file list/download/delete endpoints for compatibility, but make `/build_graph` and `/build_drillgraph` thin deprecated adapters that submit a job and return 202. On startup call `mark_running_interrupted()` before starting the runner.

- [ ] **Step 6: Persist the job database in Compose**

Mount `./saves/data/graphrag-jobs:/app/jobs:rw`, set `GRAPH_JOB_DB=/app/jobs/jobs.db`, add a `/health` worker endpoint, and add a Compose healthcheck. Add `pandas`, `pyarrow`, and `httpx` to `docker/graphrag.Dockerfile`; pin versions compatible with Python 3.12. Remove the host `8111:8111` mapping and use `expose: ['8111']` so only the Compose network and main API can reach job endpoints.

- [ ] **Step 7: Verify state tests and worker import**

Run: `python -m unittest test.test_graph_job_store -v`

Expected: all state, collision, cancel, retry, and restart tests pass.

Run: `python -c "from graphrag_api.main import app; print(app.title)"`

Expected: app imports without starting a graph build.

- [ ] **Step 8: Commit**

```powershell
git add .gitignore graphrag_api docker/graphrag.Dockerfile docker-compose.yml test/test_graph_job_store.py
git commit -m "feat(graph): add durable graph build jobs"
```

### Task 10: Convert GraphRAG Artifacts and Import Them Idempotently

**Files:**
- Create: `server/services/graph_import.py`
- Create: `test/test_graph_artifacts.py`
- Create: `test/test_graph_import.py`
- Modify: `graphrag_api/pipeline.py`
- Modify: `server/routers/data_router.py`
- Modify: `src/core/graphbase.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: `convert_relationships(parquet_path, csv_path) -> ArtifactStats` with UTF-8 columns `h,r,t`.
- Produces: `GraphImportService.import_csv(path, kgdb_name) -> ImportStats`.
- Produces: internal `POST /api/data/graph/internal/import` authenticated by `X-Graph-Internal-Token`.

- [ ] **Step 1: Write failing conversion tests with the real schema**

```python
# test/test_graph_artifacts.py
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from graphrag_api.pipeline import convert_relationships

class GraphArtifactTests(unittest.TestCase):
    def test_relationship_parquet_becomes_deterministic_hrt_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parquet = root / "create_final_relationships.parquet"
            csv = root / "relationships.csv"
            pd.DataFrame([
                {"source": "井筒", "target": "套管", "description": "包含"},
                {"source": "", "target": "无效", "description": "忽略"},
            ]).to_parquet(parquet)
            stats = convert_relationships(parquet, csv)
            output = pd.read_csv(csv)
            self.assertEqual(output.columns.tolist(), ["h", "r", "t"])
            self.assertEqual(output.to_dict("records"), [{"h": "井筒", "r": "包含", "t": "套管"}])
            self.assertEqual(stats.rows, 1)
            self.assertEqual(len(stats.sha256), 64)
```

- [ ] **Step 2: Run conversion tests and verify failure**

Run: `python -m unittest test.test_graph_artifacts -v`

Expected: import failure for `convert_relationships`.

- [ ] **Step 3: Implement deterministic artifact selection and conversion**

Before build, snapshot output directories and start time. After subprocess success, select only a newly created output directory containing `artifacts/create_final_relationships.parquet`. Validate `source`, `target`, and `description`; trim strings, drop blank rows, rename to `h,t,r`, reorder to `h,r,t`, drop exact duplicates, sort by all columns, write UTF-8 CSV with `newline=''`, and calculate SHA-256 over bytes.

- [ ] **Step 4: Add an allowlisted internal import endpoint**

The request accepts `task_id`, `graph_type`, and an artifact path relative to either `/app/indexing/ground_graph_fill` or `/app/indexing_drill/drill_graph_fill`. Resolve the path and require `resolved_path.is_relative_to(allowed_root)`. Compare the header token with `GRAPH_INTERNAL_TOKEN` using `secrets.compare_digest`; reject missing/incorrect tokens with 401 and path escapes with 400.

- [ ] **Step 5: Implement idempotent Neo4j import and indexing**

Use batched Cypher equivalent to:

```cypher
UNWIND $rows AS row
MERGE (h:Entity {name: row.h, kgdb_name: $kgdb_name})
MERGE (t:Entity {name: row.t, kgdb_name: $kgdb_name})
MERGE (h)-[r:RELATION {type: row.r, kgdb_name: $kgdb_name}]->(t)
SET r.description = row.r, r.updated_at = datetime()
```

Do not interpolate `row.r` into a relationship type. After import, call the existing bounded embedding method for nodes missing `entityEmbeddings`, then create/verify the vector index. Return node/relationship/embedded counts. Re-importing the same CSV must not increase relationship count.

- [ ] **Step 6: Connect worker stages to the main API**

After building: transition to `converting` 72-78, call conversion, transition to `importing` 80-94, POST to `MAIN_API_INTERNAL_URL` with the token, transition to `indexing` 95-99, verify returned counts/index, then set `completed` 100. Preserve input files on failure; archive or clear copied input only after completion.

- [ ] **Step 7: Verify conversion, token, path, and idempotence tests**

Run: `python -m unittest test.test_graph_artifacts test.test_graph_import -v`

Expected: all tests pass; Neo4j integration tests skip with a clear reason when `NEO4J_URI` is unavailable.

- [ ] **Step 8: Commit**

```powershell
git add graphrag_api/pipeline.py server/services/graph_import.py server/routers/data_router.py src/core/graphbase.py docker-compose.yml test/test_graph_artifacts.py test/test_graph_import.py
git commit -m "feat(graph): convert and import graphrag artifacts"
```

### Task 11: Proxy and Display Real Graph Job Progress

**Files:**
- Create: `web/src/stores/graphJobs.js`
- Create: `web/src/utils/graphJobs.mjs`
- Create: `web/tests/graphJobs.test.mjs`
- Modify: `server/routers/data_router.py`
- Modify: `web/src/apis/admin_api.js`
- Modify: `web/src/views/GraphView.vue`

**Interfaces:**
- Produces main API job proxy under `/api/data/graph/jobs`.
- Produces graph store polling at 1.5 seconds with page-refresh recovery.
- Consumes Task 9 worker job schema unchanged.

- [ ] **Step 1: Write failing progress-state tests**

```javascript
// web/tests/graphJobs.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { isTerminal, normalizeProgress } from '../src/utils/graphJobs.mjs'

test('only terminal states stop polling', () => {
  assert.equal(isTerminal('completed'), true)
  assert.equal(isTerminal('failed'), true)
  assert.equal(isTerminal('interrupted'), true)
  assert.equal(isTerminal('building'), false)
})

test('progress is clamped', () => {
  assert.equal(normalizeProgress(120), 100)
  assert.equal(normalizeProgress(-2), 0)
})
```

- [ ] **Step 2: Run web tests and verify failure**

Run: `pnpm --dir web test`

Expected: module-not-found failure for `graphJobs.mjs`.

- [ ] **Step 3: Add authenticated async worker proxy endpoints**

Use the shared `httpx.AsyncClient`, `GRAPH_WORKER_URL=http://graphrag-worker:8111`, bounded timeouts, and `get_superadmin_user` for submit/get/cancel/retry. Map worker 409, 404, and 5xx to the same meaningful main API statuses; do not return a success-shaped body on upstream failure.

- [ ] **Step 4: Build the resumable graph job store**

Persist only active task IDs by graph type in `localStorage` under `graph-active-jobs`; this data contains no secrets. On store initialization, fetch each task. Remove it only for terminal status. Clear every polling timer in `onBeforeUnmount` and before starting a replacement poll.

- [ ] **Step 5: Replace boolean generation with staged progress UI**

`GraphView.vue` submits one job, renders Ant progress, current stage label, elapsed time, input count, relationship count, and log/error summary. Show Cancel only while active, Retry only for failed/cancelled/interrupted. Display success only when `status === 'completed' && progress === 100`; every catch branch displays failure. Remove direct browser calls to ports 8111 and 8000.

- [ ] **Step 6: Verify tests and build**

Run: `pnpm --dir web test`

Expected: graph progress tests pass.

Run: `pnpm --dir web build`

Expected: Vite build exits 0.

- [ ] **Step 7: Commit**

```powershell
git add server/routers/data_router.py web/src/apis/admin_api.js web/src/stores/graphJobs.js web/src/utils/graphJobs.mjs web/tests/graphJobs.test.mjs web/src/views/GraphView.vue
git commit -m "feat(graph): show resumable build progress"
```

### Task 12: Correct Graph Retrieval and Answer Context

**Files:**
- Create: `src/core/graph_retrieval.py`
- Create: `test/test_graph_retrieval.py`
- Modify: `src/core/retriever.py`
- Modify: `src/core/graphbase.py`
- Modify: `src/config/__init__.py`

**Interfaces:**
- Produces: `normalize_entities`, `rank_unique_relations`, and `format_graph_context`.
- Produces: graph retrieval independent of `enable_knowledge_base`.
- Produces: graph references with stable IDs shared by prompt and right sidebar.

- [ ] **Step 1: Write failing normalization and context tests**

```python
# test/test_graph_retrieval.py
import unittest
from src.core.graph_retrieval import normalize_entities, rank_unique_relations, format_graph_context

class GraphRetrievalTests(unittest.TestCase):
    def test_entities_are_trimmed_deduplicated_and_bounded(self):
        self.assertEqual(normalize_entities([" 井筒 ", "井筒", "套管", ""], 2), ["井筒", "套管"])

    def test_relations_are_deduplicated_by_triple_and_ranked(self):
        rows = [
            {"source": "井筒", "target": "套管", "relation": "包含", "score": 0.7},
            {"source": "井筒", "target": "套管", "relation": "包含", "score": 0.9},
        ]
        ranked = rank_unique_relations(rows, 10)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["score"], 0.9)

    def test_context_contains_both_ends_and_stable_reference(self):
        text = format_graph_context([{"source": "井筒", "target": "套管", "relation": "包含", "score": 0.9}])
        self.assertIn("[G1]", text)
        self.assertIn("井筒", text)
        self.assertIn("套管", text)
        self.assertIn("包含", text)
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m unittest test.test_graph_retrieval -v`

Expected: module-not-found failure for `src.core.graph_retrieval`.

- [ ] **Step 3: Implement bounded pure retrieval helpers**

Normalize entity strings with order-preserving deduplication. Rank relations by numeric score descending and deduplicate `(source, relation, target)`. Add stable `ref_id = G1, G2, ...`. Context lines use `[G1] source --relation--> target` plus available node/relation descriptions; cap total characters from config.

- [ ] **Step 4: Fix the retriever gate and empty-entity fallback**

Change graph gating to `meta.use_graph and config.enable_knowledge_graph`. Query at most configured `graph_max_entities`; when extraction yields none, call `graph_base.query_node(original_query, ...)` once. Apply configured `graph_similarity_threshold`, `graph_hops`, and `graph_max_relations` with hard maximums of 20 entities, 3 hops, and 100 relationships. Catch graph exceptions, store a structured `refs.graph_base.error`, and continue ordinary answer generation.

- [ ] **Step 5: Fix graph import/read consistency**

In `jsonl_file_add_entity`, use the detected encoding in the actual `open` call instead of hardcoded GBK, close files/sessions in `finally`, and keep `RELATION {type: $relation}` representation consistent with Task 10. Ensure `query_node` returns source, target, relationship type/description, node properties, and score required by the helper.

- [ ] **Step 6: Verify graph tests and backend suite**

Run: `python -m unittest test.test_graph_retrieval -v`

Expected: all graph helper and fallback tests pass.

Run: `python -m unittest discover -s test -v`

Expected: full backend suite passes.

- [ ] **Step 7: Commit**

```powershell
git add src/core/graph_retrieval.py src/core/retriever.py src/core/graphbase.py src/config/__init__.py test/test_graph_retrieval.py
git commit -m "fix(graph-qa): rank and format graph context correctly"
```

### Task 13: Bound Concurrency and Add Production Deployment

**Files:**
- Create: `server/services/concurrency.py`
- Create: `test/test_concurrency.py`
- Create: `docker-compose.prod.yml`
- Create: `.env.example`
- Modify: `server/db_manager.py`
- Modify: `server/main.py`
- Modify: `src/__init__.py`
- Modify: `server/routers/chat_router.py`
- Modify: `server/routers/data_router.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: `BoundedGate(name, limit, acquire_timeout)` async context manager.
- Produces: SQLite WAL/busy timeout and clean executor/client shutdown.
- Produces: development and production Compose profiles with explicit secrets and healthchecks.

- [ ] **Step 1: Write failing bounded-gate tests**

```python
# test/test_concurrency.py
import asyncio
import unittest
from fastapi import HTTPException
from server.services.concurrency import BoundedGate

class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_gate_rejects_when_capacity_does_not_free(self):
        gate = BoundedGate("model", limit=1, acquire_timeout=0.01)
        async with gate:
            with self.assertRaises(HTTPException) as ctx:
                async with gate:
                    pass
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_capacity_is_released_after_exception(self):
        gate = BoundedGate("model", limit=1, acquire_timeout=0.1)
        with self.assertRaises(RuntimeError):
            async with gate:
                raise RuntimeError("boom")
        async with gate:
            self.assertEqual(gate.in_use, 1)
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m unittest test.test_concurrency -v`

Expected: module-not-found failure for `server.services.concurrency`.

- [ ] **Step 3: Implement bounded gates and executors**

Use `asyncio.BoundedSemaphore`, `asyncio.wait_for`, and a `finally` release. Configure separate environment limits: `CHAT_CONCURRENCY=2`, `RETRIEVAL_CONCURRENCY=4`, `GRAPH_IMPORT_CONCURRENCY=1`, and `UPSTREAM_PROXY_CONCURRENCY=16`. On timeout raise 503 with `Retry-After: 2`. Replace the default global executor with explicit `ThreadPoolExecutor(max_workers=int(os.getenv('BLOCKING_WORKERS', '8')), thread_name_prefix='sage-blocking')` and shut it down on app lifespan exit.

- [ ] **Step 4: Apply gates at heavy boundaries**

Wrap model generation, retriever calls, graph import, and generic upstream proxy in their corresponding gates. Sync model/Milvus/Neo4j calls run through the bounded executor; async HTTP stays on the event loop. Remove `requests` and `time.sleep` from every `async def` route in `chat_router.py`, `data_router.py`, and `multimodal_proxy_router.py`.

- [ ] **Step 5: Harden SQLite and lifecycle**

Create the engine with `connect_args={"timeout": 30, "check_same_thread": False}`, a pool pre-ping, and an SQLAlchemy connect event that executes `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=30000`, and `PRAGMA foreign_keys=ON`. Keep each session request/task scoped and close in `finally`. Remove the invalid `threads=10, workers=10, reload=True` direct runner; use one Uvicorn worker while the local model remains in-process.

- [ ] **Step 6: Add production Compose and explicit environment contract**

Development Compose may retain `--reload`. `docker-compose.prod.yml` overrides it with `uv run uvicorn server.main:app --host 0.0.0.0 --port 5050 --workers 1`, adds API/worker healthchecks, stop grace periods, logging limits, memory limits, and no source-code bind mounts. `.env.example` lists empty `MODEL_CREDENTIAL_MASTER_KEY`, `GRAPH_INTERNAL_TOKEN`, `BOCHA_API_KEY`, JWT/Neo4j credentials, URLs, timeouts, and concurrency limits; it contains no usable secret defaults.

- [ ] **Step 7: Verify tests and Compose rendering**

Run: `python -m unittest test.test_concurrency -v`

Expected: bounded acquire/release tests pass.

Run: `docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet`

Expected: exits 0 when required test environment variables are supplied for rendering.

Run: `rg -U -n "async def[\s\S]{0,1200}(requests\.|time\.sleep\()" server/routers`

Expected: no matches.

- [ ] **Step 8: Commit**

```powershell
git add server/services/concurrency.py test/test_concurrency.py server/db_manager.py server/main.py src/__init__.py server/routers/chat_router.py server/routers/data_router.py docker-compose.yml docker-compose.prod.yml .env.example
git commit -m "perf(server): bound blocking work and production concurrency"
```

### Task 14: Integrated Runtime and Browser Acceptance

**Files:**
- Create: `scripts/smoke_platform.ps1`
- Create: `docs/operations/platform-deployment.md`
- Modify: `README.md`

**Interfaces:**
- Consumes every previous task without adding a second implementation path.
- Produces repeatable operator checks and deployment instructions.

- [ ] **Step 1: Write the smoke script before running the stack**

The script accepts `-BaseUrl`, three test credentials, and optional `-RemoteMultimodalBase`. It logs in each role, checks `/auth/me`, verifies the role route matrix, creates/deletes a temporary ordinary user as admin, verifies admin cannot create an admin, checks that a user cannot call graph/multimodal endpoints, and confirms a superadmin can. It prints status codes and exits nonzero on any mismatch; it never accepts or prints real model API keys.

- [ ] **Step 2: Run all automated tests and builds**

Run: `python -m unittest discover -s test -v`

Expected: all tests pass, with external Neo4j tests either passing or explicitly skipped.

Run: `pnpm --dir web test`

Expected: all Node tests pass.

Run: `pnpm --dir web build`

Expected: Vite build exits 0; chunk-size warnings are recorded but are not failures.

- [ ] **Step 3: Build and start the development stack**

Generate fresh local values for `MODEL_CREDENTIAL_MASTER_KEY` and `GRAPH_INTERNAL_TOKEN` in an ignored `.env`; do not print them into task output. Run:

```powershell
docker compose build api web graphrag-worker
docker compose up -d graph api web graphrag-worker
docker compose ps
```

Expected: all four services reach running/healthy state and API `/api/health` returns 200.

- [ ] **Step 4: Run role and personal-model acceptance**

Run `scripts/smoke_platform.ps1` against `http://localhost:5050`. In the browser, verify user management opens as a drawer without route change and admin sees only ordinary users. Add a disposable OpenAI-compatible model, validate, select, send a short chat, refresh, and delete. Confirm the disposable key is absent from database dumps, API responses, browser storage, and container logs.

- [ ] **Step 5: Run multimodal acceptance**

As superadmin, select `钻井设计资料` and ask `井身结构设计图`. Expected: the right panel identifies that KB, renders a complex table without escaped tags, and displays at least one openable image. The proxy response is 200, `Content-Type: image/png`, and begins with the PNG signature. Open image management and page through results; network/memory behavior matches Task 8.

- [ ] **Step 6: Run graph pipeline and graph-QA acceptance**

Upload a small fixture, submit a ground graph job, observe every persisted stage to 100%, and verify the generated CSV has `h,r,t`. Record Neo4j counts, retry/import the same artifact, and verify counts do not duplicate. Enable graph QA and ask a question covered by the fixture; the answer context and right panel contain matching `[G#]` references.

- [ ] **Step 7: Run bounded-load checks**

During a graph build, send parallel health, file-list, current-page image, and chat requests for five minutes. Expected: health remains 200, overload is an explicit 429/503 with retry guidance, memory does not grow continuously after image previews close, and no container restarts. Capture `docker compose ps`, recent 5xx, SQLite lock messages, upstream timeouts, and memory snapshots in the verification notes.

- [ ] **Step 8: Document deployment and commit acceptance assets**

`docs/operations/platform-deployment.md` documents required environment variables, key generation/rotation, server-side source mounts for development, image rebuilds for production, backup/restore for `saves` and Neo4j, worker restart/interrupted-job recovery, and rollback by the Task 1 through Task 13 focused commits.

```powershell
git add scripts/smoke_platform.ps1 docs/operations/platform-deployment.md README.md
git commit -m "test(e2e): verify roles models multimodal graph and load"
```

---

## Review Gates

After every task, Claude Code must stop. Codex will inspect `git diff`, run the task's focused test, run impacted regression tests, and reject unrelated edits. No task may be marked complete from Claude's summary alone.

Before final delivery, run `git diff --check`, the complete backend and frontend suites, Vite build, Compose config validation, Docker health checks, the real `钻井设计资料` multimodal query, one complete graph job, and browser screenshots in both themes for desktop and mobile.
