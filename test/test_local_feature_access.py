"""本机功能接口的超级管理员访问控制测试（AST 源码级，不导入路由）。

阶段 12 验收：后端必须仍返回 403，不能只依赖前端隐藏导航。
- operations / monitoring / alert / audit 路由的全部端点必须挂 get_superadmin_user。
- base_router 的配置历史与回滚端点必须挂 get_superadmin_user。
- evaluation 路由的全部端点必须挂 get_superadmin_user。
- governance 的管理端点（改元数据/快照/导出/同步）必须挂 get_superadmin_user；
  只读预览与受控下载使用 get_required_user + 服务层权限判定（restricted 由
  resolve_download 拒绝），属预期设计，单独断言。
"""

import unittest
from pathlib import Path

from test_role_routes import dependency_names, endpoints, parse_router

ROOT = Path(__file__).resolve().parents[1]


def _require_superadmin(filename, exempt=()):
    """断言 filename 中（除 exempt 外）每个端点都挂 get_superadmin_user。"""
    failures = []
    for name, node in endpoints(parse_router(filename)).items():
        if name in exempt:
            continue
        if "get_superadmin_user" not in dependency_names(node):
            failures.append("{}:{}:{}".format(filename, name, node.lineno))
    return failures


class LocalFeatureAccessTests(unittest.TestCase):
    maxDiff = None

    def test_operations_router_all_superadmin(self):
        self.assertEqual(_require_superadmin("operations_router.py"), [])

    def test_monitoring_router_all_superadmin(self):
        self.assertEqual(_require_superadmin("monitoring_router.py"), [])

    def test_alert_router_all_superadmin(self):
        self.assertEqual(_require_superadmin("alert_router.py"), [])

    def test_audit_router_all_superadmin(self):
        self.assertEqual(_require_superadmin("audit_router.py"), [])

    def test_config_history_endpoints_superadmin(self):
        found = endpoints(parse_router("base_router.py"))
        expected = {"list_config_history", "get_config_history", "rollback_config"}
        self.assertEqual(expected - found.keys(), set())
        self.assertEqual([
            name for name in sorted(expected)
            if "get_superadmin_user" not in dependency_names(found[name])
        ], [])

    def test_evaluation_router_all_superadmin(self):
        self.assertEqual(_require_superadmin("evaluation_router.py"), [])

    def test_governance_management_endpoints_superadmin(self):
        found = endpoints(parse_router("governance_router.py"))
        expected = {
            "patch_governance_document",
            "create_document_version_snapshot",
            "export_governance_metadata",
            "sync_governance",
        }
        self.assertEqual(expected - found.keys(), set())
        self.assertEqual([
            name for name in sorted(expected)
            if "get_superadmin_user" not in dependency_names(found[name])
        ], [])

    def test_governance_preview_and_download_require_login(self):
        """只读预览/受控下载对已登录用户开放，restricted 由服务层拒绝。"""
        found = endpoints(parse_router("governance_router.py"))
        for name in (
            "list_governance_documents",
            "get_governance_document",
            "download_governance_document",
            "list_document_versions",
            "download_document_version",
        ):
            with self.subTest(name=name):
                self.assertIn("get_required_user", dependency_names(found[name]))


if __name__ == "__main__":
    unittest.main()
