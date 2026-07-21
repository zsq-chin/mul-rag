import os
import unittest
import importlib.util
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException

from server.services.access_control import assert_chat_features_allowed, can_manage_target

# Load web_search_bocha directly to avoid triggering src/__init__.py (which connects to Milvus).
_spec = importlib.util.spec_from_file_location(
    "web_search_bocha",
    os.path.join(os.path.dirname(__file__), "..", "src", "utils", "web_search_bocha.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
WebSearcher = _mod.WebSearcher


class AccessControlTests(unittest.TestCase):
    def user(self, role):
        return SimpleNamespace(id=1, role=role)

    def test_every_authenticated_role_can_use_knowledge_retrieval(self):
        for role in ("user", "admin", "superadmin"):
            assert_chat_features_allowed(self.user(role), {
                "use_multimodal_kb": True,
                "multimodal_kb_id": "drilling-design",
                "db_id": "ordinary-kb",
                "selectedKB": 0,
            })

    def test_only_superadmin_can_enable_graph_retrieval(self):
        for role in ("admin", "user"):
            with self.assertRaises(HTTPException) as ctx:
                assert_chat_features_allowed(self.user(role), {"use_graph": True})
            self.assertEqual(ctx.exception.status_code, 403)
        assert_chat_features_allowed(self.user("superadmin"), {"use_graph": True})

    def test_admin_can_manage_only_ordinary_users(self):
        actor = self.user("admin")
        self.assertTrue(can_manage_target(actor, self.user("user")))
        self.assertFalse(can_manage_target(actor, self.user("admin")))
        self.assertFalse(can_manage_target(actor, self.user("superadmin")))

    def test_bocha_key_must_come_from_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "BOCHA_API_KEY"):
                WebSearcher()

    def test_passive_graph_name_does_not_block_ordinary_chat(self):
        """graph_name alone (use_graph=False) must not trigger 403."""
        meta = {"graph_name": "neo4j", "use_graph": False}
        for role in ("admin", "user"):
            assert_chat_features_allowed(self.user(role), meta)

    def test_model_error_path_never_references_api_key(self):
        """The OpenAIBase._stream_response error message must never leak credentials."""
        chat_model_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "models", "chat_model.py"
        )
        with open(chat_model_path, "r", encoding="utf-8") as f:
            source = f.read()

        # Extract only the OpenAIBase._stream_response method body
        # (not DashScope._stream_response which legitimately uses self.api_key for API calls)
        marker = "class OpenAIBase"
        idx = source.index(marker)
        rest = source[idx:]
        method_marker = "def _stream_response(self, messages):"
        m_idx = rest.index(method_marker)
        after = rest[m_idx:]
        # End at the next def at the same indent level (4 spaces)
        lines = after.split("\n")
        body_lines = [lines[0]]
        for line in lines[1:]:
            if line.startswith("    def ") and not line.startswith("        "):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)

        # The exception-construction block must not contain forbidden fragments
        forbidden = ["self.api_key", "API Key:", "str(e)"]
        for fragment in forbidden:
            self.assertNotIn(
                fragment,
                body,
                f"_stream_response error path contains forbidden fragment: {fragment!r}",
            )


def _extract_class_method_body(source: str, class_name: str, method_name: str) -> str:
    """Extract the body of a method from a specific class in source text."""
    marker = f"class {class_name}"
    idx = source.index(marker)
    rest = source[idx:]
    method_marker = f"def {method_name}(self"
    m_idx = rest.index(method_marker)
    after = rest[m_idx:]
    lines = after.split("\n")
    body_lines = [lines[0]]
    for line in lines[1:]:
        # Stop at the next def at the same indent level
        if line.startswith("    def ") and not line.startswith("        "):
            break
        body_lines.append(line)
    return "\n".join(body_lines)


def _load_chat_model_source() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "src", "models", "chat_model.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_web_search_source() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "src", "utils", "web_search_bocha.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class CredentialLeakRegressionTests(unittest.TestCase):
    """Source-level tests ensuring error paths never leak raw exceptions or credentials."""

    # ---- 1a: OpenAIBase.get_models must not interpolate raw exception ----

    def test_get_models_error_log_does_not_use_raw_exception(self):
        """OpenAIBase.get_models must log only type(e).__name__, not {e} or str(e)."""
        source = _load_chat_model_source()
        body = _extract_class_method_body(source, "OpenAIBase", "get_models")

        # The except block must not interpolate the raw exception object
        forbidden_fragments = ["{e}", "str(e)"]
        for fragment in forbidden_fragments:
            self.assertNotIn(
                fragment,
                body,
                f"get_models uses forbidden raw exception fragment: {fragment!r}",
            )

    def test_get_models_error_log_uses_exception_type(self):
        """OpenAIBase.get_models error log must use type(e).__name__."""
        source = _load_chat_model_source()
        body = _extract_class_method_body(source, "OpenAIBase", "get_models")
        self.assertIn("type(e).__name__", body,
                       "get_models must use type(e).__name__ in error log")

    # ---- 1b: DashScope._stream_response must catch Exception safely ----

    def test_dashscope_stream_has_try_except(self):
        """DashScope._stream_response must wrap SDK call/iteration in try/except."""
        source = _load_chat_model_source()
        body = _extract_class_method_body(source, "DashScope", "_stream_response")
        self.assertIn("try:", body, "DashScope._stream_response must have a try: block")
        self.assertIn("except", body, "DashScope._stream_response must have an except: block")

    def test_dashscope_stream_error_uses_only_exception_type_and_model(self):
        """DashScope error must contain only type(e).__name__ and model name, nothing else."""
        source = _load_chat_model_source()
        body = _extract_class_method_body(source, "DashScope", "_stream_response")
        self.assertIn("type(e).__name__", body,
                       "DashScope error must reference type(e).__name__")
        self.assertIn("model", body,
                       "DashScope error must reference the model name")

    def test_dashscope_stream_error_never_leaks_raw_exception(self):
        """DashScope error must not contain raw exception text, key, or base URL."""
        source = _load_chat_model_source()
        body = _extract_class_method_body(source, "DashScope", "_stream_response")

        # {e} and str(e) must not appear anywhere in the method
        for fragment in ["{e}", "str(e)"]:
            self.assertNotIn(
                fragment, body,
                f"DashScope._stream_response contains forbidden: {fragment!r}",
            )

        # self.api_key and base_url must not appear in the except block only
        # (they are legitimate in the SDK call inside the try block)
        except_block = ""
        in_except = False
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("except "):
                in_except = True
            if in_except:
                except_block += line + "\n"

        for fragment in ["self.api_key", "base_url"]:
            self.assertNotIn(
                fragment, except_block,
                f"DashScope except block contains forbidden: {fragment!r}",
            )

    def test_dashscope_stream_raises_runtime_error_from_none(self):
        """DashScope._stream_response must raise RuntimeError from None in except."""
        source = _load_chat_model_source()
        body = _extract_class_method_body(source, "DashScope", "_stream_response")
        self.assertIn("RuntimeError", body,
                       "DashScope must raise RuntimeError")
        self.assertIn("from None", body,
                       "DashScope must raise ... from None to suppress the chain")

    # ---- 1c: WebSearcher.search must not interpolate raw exception ----

    def test_web_searcher_error_does_not_use_raw_exception(self):
        """WebSearcher.search must log only type(e).__name__, not {e} or str(e)."""
        source = _load_web_search_source()
        body = _extract_class_method_body(source, "WebSearcher", "search")

        forbidden_fragments = ["{e}", "str(e)"]
        for fragment in forbidden_fragments:
            self.assertNotIn(
                fragment,
                body,
                f"WebSearcher.search uses forbidden raw exception fragment: {fragment!r}",
            )

    def test_web_searcher_error_uses_exception_type(self):
        """WebSearcher.search error output must use type(e).__name__."""
        source = _load_web_search_source()
        body = _extract_class_method_body(source, "WebSearcher", "search")
        self.assertIn("type(e).__name__", body,
                       "WebSearcher.search must use type(e).__name__ in error output")
