# 远端多模态 RAG 后端部署（I3.4 / I3.5 / I3.6）

本文档交付远端多模态 RAG 后端（`mul_rag/backend`）的生产守护模板。**远端宿主不可达时无法实测**，本目录为部署清单与可执行模板；部署后按“部署验证”一节逐项确认，任何一项不满足即视为该部署未完成。

> 约束：`mul_rag/**` 保持零改动。远端代码、数据、环境一律在部署机上操作，本目录只放模板与说明。

## 1. 守护化（I3.4）

远端后端不能依赖人工终端会话，用 systemd 守护：

1. 在远端宿主安装后端（`mul_rag/backend` → `/opt/mul_rag/backend`），用 `mul_rag_environment.yml` 建 Conda 环境 `mul_rag`。
2. 复制并填写环境文件：
   ```bash
   sudo cp multimodal-rag.env.example /etc/multimodal-rag.env
   sudo vi /etc/multimodal-rag.env   # 填入 OLLAMA_BASE_URL / OLM_OCR_ENDPOINT / Token 等
   sudo chmod 600 /etc/multimodal-rag.env
   ```
3. 安装启动脚本与单元：
   ```bash
   sudo cp start_remote_multimodal.sh /opt/mul_rag/start.sh
   sudo chmod +x /opt/mul_rag/start.sh
   sudo cp multimodal-rag.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now multimodal-rag
   ```
4. 校验：
   ```bash
   systemctl status multimodal-rag          # active (running)
   curl -fsS http://127.0.0.1:8002/api/v1/health
   systemctl restart multimodal-rag         # 自动重启生效
   ```
   `Restart=always` 保证崩溃/开机自启；`TimeoutStopSec=60` 让 Uvicorn 优雅收尾在途请求。

日志轮转（systemd journald 已自带），如需独立文件日志可加 `/etc/logrotate.d/multimodal-rag`：

```
/opt/mul_rag/backend/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

## 2. 监听与 worker（I3.6）

- Uvicorn 监听 `0.0.0.0:8002`，`start_remote_multimodal.sh` 已固定 **不使用 `--reload`**。
- 涉及 GPU/全局模型状态保持 **单 worker**（`--workers 1`），负载用并发队列控制，不盲目加 worker。

## 3. 持久化与备份（I3.5）

必须持久化并纳入备份的目录（`mul_rag/backend` 下）：

| 目录 | 内容 |
| --- | --- |
| `knowledge_base/` | 知识库文件 |
| `data/` | 索引、图片等运行数据 |
| `logs/` | 日志 |
| `tmp/` | 临时文件（可随重建清空，不强制备份） |

外加配置与代码：`mul_rag_environment.yml`、`app.py`、`services/**`、`/etc/multimodal-rag.env`（含 Token，备份须加密）。

建议备份脚本（远端宿主，每日 + 部署升级前）：

```bash
#!/usr/bin/env bash
set -euo pipefail
STAMP=$(date +%Y%m%d%H%M%S)
BACKUP=/backup/multimodal-rag
mkdir -p "$BACKUP"
tar czf "$BACKUP/backend-$STAMP.tar.gz" -C /opt/mul_rag backend/knowledge_base backend/data backend/logs \
    backend/mul_rag_environment.yml backend/app.py
# 环境文件含秘密，单独加密备份
openssl enc -aes-256-cbc -pbkdf2 -in /etc/multimodal-rag.env -out "$BACKUP/env-$STAMP.enc"
# 只保留最近 14 份
find "$BACKUP" -name 'backend-*.tar.gz' -mtime +14 -delete
```

### 部署升级前的恢复演练（强制）

1. 用备份在**另一台同构 GPU 主机**恢复目录到临时路径，`conda env create -f mul_rag_environment.yml`。
2. 以恢复的目录启动一次服务，`curl /api/v1/health` 通过后，抽查一条已入库文档能检索到。
3. 演练完成即标记“本次升级可恢复”；演练失败则不升级，先修复备份链路。

## 4. SAGE 侧接线

SAGE 生产 Compose 只注入环境变量指向该远端地址：

```env
MULTIMODAL_ENABLED=true
MULTIMODAL_MODE=remote
MULTIMODAL_KB_API_BASE=http://<远端宿主>:8002/api/v1   # 内网阶段按策略配合 MULTIMODAL_ALLOW_HTTP
MULTIMODAL_SERVICE_TOKEN=<与 /etc/multimodal-rag.env 中一致>
```

SAGE 的 `/api/operations/dependencies` 会显示远端多模态 `healthy/degraded/down`；远端故障只降级该项，不使普通聊天退出流量（I3.3）。

## 5. 部署验证清单（远端可达时执行）

- [ ] `systemctl is-active multimodal-rag` 为 `active`
- [ ] `curl -fsS http://<宿主>:8002/api/v1/health` 返回 `{"ok": true, ...}`
- [ ] `ps -ef | grep uvicorn` 无 `--reload`
- [ ] 重启 systemd 服务后进程自动拉起，端口正常
- [ ] 上传一份 PDF → 入库 → 检索命中（真实端到端）
- [ ] 备份脚本生成产物，恢复演练通过
- [ ] SAGE 依赖状态页显示远端 `healthy`
