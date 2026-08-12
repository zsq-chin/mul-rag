#!/usr/bin/env bash
# 远端多模态 RAG 后端启动脚本（I3.4 / I3.6）。
#
# 由 systemd 以 /opt/mul_rag/start.sh 调用，也可手动执行：
#   source /opt/mul_rag/start.sh   # 或 bash /opt/mul_rag/start.sh
#
# 行为：
#   - 激活 Conda 环境 mul_rag（与 mul_rag/backend/mul_rag_environment.yml 一致）；
#   - 运行变量来自 /etc/multimodal-rag.env（OLLAMA_BASE_URL / OLM_OCR_ENDPOINT 等）；
#   - exec uvicorn 监听 0.0.0.0:8002，不使用 --reload；
#   - 涉及 GPU/全局模型状态保持单 worker，负载用并发队列控制（I3.6），不盲目加 worker。
set -euo pipefail

CONDA_BASE="${CONDA_BASE:-/opt/conda}"
CONDA_ENV="${CONDA_ENV:-mul_rag}"
BACKEND_DIR="${BACKEND_DIR:-/opt/mul_rag/backend}"
ENV_FILE="${ENV_FILE:-/etc/multimodal-rag.env}"
BIND="${MULTIMODAL_BIND:-0.0.0.0}"
PORT="${MULTIMODAL_PORT:-8002}"

# 载入运行变量（缺失时跳过；真实 Token/密码绝不写入本脚本）
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

# 激活 Conda 环境
# shellcheck disable=SC1091
source "${CONDA_BASE}/etc/profile.d/conda.sh"
conda activate "${CONDA_ENV}"

cd "${BACKEND_DIR}"

exec uvicorn app:app --host "${BIND}" --port "${PORT}" --workers 1
