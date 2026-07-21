"""AST-only role-route tests that never import application routers."""

import ast
import unittest
import warnings
from pathlib import Path

from fastapi import HTTPException

from server.services import access_control


ROUTERS = Path(__file__).resolve().parents[1] / "server" / "routers"
ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "api_route"}


def parse_router(name: str) -> ast.Module:
    source = (ROUTERS / name).read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return ast.parse(source)


def endpoints(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    result = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute) and target.attr in ROUTE_METHODS:
                result[node.name] = node
                break
    return result


def dependency_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = set()
    candidates = [*node.args.defaults, *node.args.kw_defaults, *node.decorator_list]
    for candidate in candidates:
        if candidate is None:
            continue
        for child in ast.walk(candidate):
            if not (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)):
                continue
            if child.func.id != "Depends" or not child.args:
                continue
            dependency = child.args[0]
            if isinstance(dependency, ast.Name):
                names.add(dependency.id)
    return names


def call_names(node: ast.AST) -> set[str]:
    return {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


def has_user_role_filter(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Compare) or len(child.ops) != 1:
            continue
        left = child.left
        if not (
            isinstance(left, ast.Attribute)
            and isinstance(left.value, ast.Name)
            and left.value.id == "User"
            and left.attr == "role"
        ):
            continue
        if isinstance(child.ops[0], ast.Eq) and any(
            isinstance(value, ast.Constant) and value.value == "user"
            for value in child.comparators
        ):
            return True
    return False


class RoleRouteTests(unittest.TestCase):
    maxDiff = None

    def assert_guards(self, filename, expected, excluded=()):
        failures = []
        for name, node in endpoints(parse_router(filename)).items():
            if name in excluded:
                continue
            dependencies = dependency_names(node)
            if expected not in dependencies:
                failures.append(
                    f"{filename}:{name}:{node.lineno} expected {expected}; got {sorted(dependencies)}"
                )
        self.assertEqual(failures, [])

    def test_base_management_routes_require_superadmin(self):
        self.assert_guards(
            "base_router.py", "get_superadmin_user", {"route_index", "health_check"}
        )

    def test_all_data_routes_require_superadmin(self):
        found = endpoints(parse_router("data_router.py"))
        self.assertIn("get_required_user", dependency_names(found["get_databases"]))
        self.assert_guards("data_router.py", "get_superadmin_user", {"get_databases"})

    def test_other_managed_routes_require_superadmin(self):
        for filename in (
            "statistics_router.py", "college_router.py", "multimodal_proxy_router.py"
        ):
            with self.subTest(filename=filename):
                self.assert_guards(filename, "get_superadmin_user")

    def test_chat_management_routes_require_superadmin(self):
        expected = {
            "set_default_agent", "get_chat_models", "update_chat_models", "get_tools",
            "save_agent_config",
        }
        found = endpoints(parse_router("chat_router.py"))
        self.assertEqual(expected - found.keys(), set())
        self.assertEqual([
            name for name in sorted(expected)
            if "get_superadmin_user" not in dependency_names(found[name])
        ], [])

        for name in ("get_multimodal_kbs", "get_multimodal_image"):
            with self.subTest(name=name):
                self.assertIn("get_required_user", dependency_names(found[name]))

    def test_ordinary_chat_requires_login_and_rejects_forged_features(self):
        found = endpoints(parse_router("chat_router.py"))
        for name in ("chat_post", "call", "chat_agent"):
            with self.subTest(name=name):
                self.assertIn("get_required_user", dependency_names(found[name]))
                self.assertIn("assert_chat_features_allowed", call_names(found[name]))
        for name in ("chat_post", "call"):
            with self.subTest(model_resolution=name):
                self.assertIn("resolve_model_for_user", call_names(found[name]))

    def test_personal_model_routes_require_login(self):
        found = endpoints(parse_router("user_model_router.py"))
        expected = {
            "get_user_models", "add_user_model", "edit_user_model",
            "remove_user_model", "mark_user_model_selected", "validate_user_model",
        }
        self.assertEqual(expected - found.keys(), set())
        self.assertEqual([
            name for name in sorted(expected)
            if "get_required_user" not in dependency_names(found[name])
        ], [])

    def test_auth_routes_use_role_specific_guards(self):
        expected = {
            "read_users_me": "get_required_user",
            "cleanup_cas_sessions": "get_superadmin_user",
            "get_cas_session_stats": "get_superadmin_user",
            "create_user": "get_admin_user", "read_users": "get_admin_user",
            "read_user": "get_admin_user", "update_user": "get_admin_user",
            "delete_user": "get_admin_user",
        }
        found = endpoints(parse_router("auth_router.py"))
        self.assertEqual(expected.keys() - found.keys(), set())
        self.assertEqual([
            name for name, guard in expected.items()
            if guard not in dependency_names(found[name])
        ], [])

    def test_user_crud_calls_policy_and_filters_admin_visibility(self):
        found = endpoints(parse_router("auth_router.py"))
        self.assertTrue(has_user_role_filter(found["read_users"]))
        for name in ("read_user", "update_user", "delete_user"):
            with self.subTest(name=name):
                self.assertIn("can_manage_target", call_names(found[name]))
        for name in ("create_user", "update_user"):
            with self.subTest(name=name):
                self.assertIn("assert_role_assignment_allowed", call_names(found[name]))
        self.assertIn(
            "assert_superadmin_transition_allowed",
            call_names(found["update_user"]),
        )

        self.assertTrue(hasattr(access_control, "assert_role_assignment_allowed"))
        assign = access_control.assert_role_assignment_allowed
        admin = type("User", (), {"role": "admin"})()
        superadmin = type("User", (), {"role": "superadmin"})()
        self.assertEqual(assign(admin, "user"), "user")
        with self.assertRaises(HTTPException):
            assign(admin, "admin")
        with self.assertRaises(HTTPException):
            assign(superadmin, "invalid")

    def test_last_superadmin_cannot_be_demoted(self):
        transition = access_control.assert_superadmin_transition_allowed
        target = type("User", (), {"role": "superadmin"})()

        with self.assertRaises(HTTPException) as raised:
            transition(target, "user", superadmin_count=1)
        self.assertEqual(raised.exception.status_code, 400)

        transition(target, "admin", superadmin_count=2)


if __name__ == "__main__":
    unittest.main()
