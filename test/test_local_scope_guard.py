"""本地功能接口的防误改守卫。

保证：
1. 禁止修改的远程多模态相关文件保持零差异（工作区 vs HEAD）。
2. 本机功能模块（server/routers、server/services、server/models、server/schemas）
   不得 import 远程多模态代码（multimodal_remote / mul_rag）。

该测试不依赖数据库、Milvus 或 docker，可在任意环境运行。
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 本轮必须保持零差异的远程多模态路径
FORBIDDEN_PATHS = [
    "mul_rag",
    "server/utils/multimodal_remote.py",
    "server/routers/multimodal_proxy_router.py",
    "server/services/http_clients.py",
    "web/src/apis/multimodal.js",
    "web/src/views/MultimodalKbView.vue",
    "web/src/components/AuthenticatedImage.vue",
    "web/src/utils/multimodalSearch.mjs",
]

# 本机功能模块扫面范围（排除禁止路径本身）
SCAN_DIRS = [
    ROOT / "server" / "routers",
    ROOT / "server" / "services",
    ROOT / "server" / "models",
    ROOT / "server" / "schemas",
]

# 本轮新增的本机功能模块文件名前缀，只有这些模块受 import 守卫约束
LOCAL_FEATURE_PREFIXES = (
    "feedback_",
    "governance_",
    "evaluation_",
    "audit_",
    "operations_",
    "backup_",
    "monitoring_",
    "alert_",
    "config_history_",
)

# 禁止出现的 import 目标
FORBIDDEN_IMPORTS = ("multimodal_remote", "mul_rag")

_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([\w\.]+)", re.MULTILINE)


def _git(args):
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _require_git(testcase):
    """仓库无 .git 元数据（如源码压缩包环境）时跳过 git 守卫测试。"""
    if not (ROOT / ".git").exists():
        testcase.skipTest("仓库缺少 .git 元数据（如源码压缩包环境），跳过 git 守卫")


class LocalScopeGuardTests(unittest.TestCase):
    def test_forbidden_paths_have_zero_diff(self):
        """禁止修改路径在 HEAD 与工作区之间不得有差异。"""
        _require_git(self)
        status = _git(["status", "--short", "--", *FORBIDDEN_PATHS])
        self.assertEqual(
            status.stdout.strip(),
            "",
            f"禁止修改的路径在工作区发生变化:\n{status.stdout}",
        )
        diff = _git(["diff", "--", *FORBIDDEN_PATHS])
        self.assertEqual(
            diff.stdout.strip(),
            "",
            f"禁止修改的路径存在未提交差异:\n{diff.stdout}",
        )

    def test_forbidden_paths_match_head_content(self):
        """工作区中的禁止路径必须与 HEAD 内容一致。

        不固定绝对 blob 哈希（不同克隆/分支历史下 HEAD 可能合法不同），
        只校验“工作区内容 == HEAD 内容”，从而在任意环境都可运行。
        """
        _require_git(self)
        for path in FORBIDDEN_PATHS:
            target = ROOT / path
            # mul_rag 是目录，只有文件才有可比较的 blob
            if not target.is_file():
                continue
            wc = _git(["hash-object", path]).stdout.strip()
            head = _git(["rev-parse", f"HEAD:{path}"]).stdout.strip()
            self.assertEqual(
                wc, head,
                f"禁止修改的文件 {path} 与 HEAD 内容不一致（工作区={wc} HEAD={head}）",
            )

    def test_local_modules_do_not_import_remote_multimodal(self):
        """本轮新增的本机功能模块不得 import multimodal_remote 或 mul_rag。"""
        violations = []
        for base in SCAN_DIRS:
            if not base.exists():
                continue
            for py in sorted(base.rglob("*.py")):
                # 只检查本轮新增的本机功能模块
                if not py.name.startswith(LOCAL_FEATURE_PREFIXES):
                    continue
                rel = py.relative_to(ROOT).as_posix()
                src = py.read_text(encoding="utf-8", errors="ignore")
                for m in _IMPORT_RE.finditer(src):
                    module = m.group(1)
                    if any(bad in module for bad in FORBIDDEN_IMPORTS):
                        violations.append(f"{rel}: imports '{module}'")
        self.assertEqual(violations, [], "本机功能模块不得 import 远程多模态代码:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
