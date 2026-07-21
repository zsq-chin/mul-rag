import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base
from server.models.user_model import User
from server.services.model_credentials import (
    CredentialCipher,
    create_user_model,
    get_owned_model,
    list_user_models,
    serialize_user_model,
    resolve_model_for_user,
    validate_api_base,
)


class CredentialCipherTests(unittest.TestCase):
    def test_cipher_round_trip_does_not_contain_plaintext(self):
        cipher = CredentialCipher(Fernet.generate_key().decode())
        encrypted = cipher.encrypt("secret-value")
        self.assertNotIn("secret-value", encrypted)
        self.assertEqual(cipher.decrypt(encrypted), "secret-value")

    def test_missing_master_key_is_rejected_without_secret_details(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "MODEL_CREDENTIAL_MASTER_KEY"):
                CredentialCipher()

    def test_invalid_master_key_is_rejected_with_a_generic_message(self):
        with self.assertRaisesRegex(ValueError, "格式无效"):
            CredentialCipher("not-a-valid-key")


class ApiBaseValidationTests(unittest.TestCase):
    @patch("server.services.model_credentials.socket.getaddrinfo")
    def test_https_public_endpoint_is_accepted(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("8.8.8.8", 443))]
        self.assertEqual(
            validate_api_base("https://models.example.com/v1/"),
            "https://models.example.com/v1",
        )

    @patch("server.services.model_credentials.socket.getaddrinfo")
    def test_private_endpoint_is_rejected_without_allowlist(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch.dict(os.environ, {"USER_MODEL_ALLOWED_HOSTS": ""}, clear=False):
            with self.assertRaisesRegex(ValueError, "不允许"):
                validate_api_base("https://localhost/v1")

    def test_http_userinfo_query_and_fragment_are_rejected(self):
        for value in (
            "http://models.example.com/v1",
            "https://name:pass@models.example.com/v1",
            "https://models.example.com/v1?api_key=secret",
            "https://models.example.com/v1#secret",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_api_base(value)


class UserModelOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.owner = User(username="owner", password_hash="x", role="user")
        self.other = User(username="other", password_hash="x", role="user")
        self.db.add_all([self.owner, self.other])
        self.db.commit()
        self.cipher = CredentialCipher(Fernet.generate_key().decode())

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @patch("server.services.model_credentials.validate_api_base", return_value="https://models.example.com/v1")
    def test_serialized_models_never_contain_encrypted_or_plaintext_keys(self, _validate):
        model = create_user_model(
            self.db,
            self.owner,
            SimpleNamespace(
                display_name="现场模型",
                provider="openai-compatible",
                model_name="field-model",
                api_base="https://models.example.com/v1",
                api_key="sk-plain-secret",
            ),
            self.cipher,
        )

        payload = serialize_user_model(model)
        encoded = repr(payload)
        self.assertNotIn("encrypted_api_key", payload)
        self.assertNotIn("sk-plain-secret", encoded)
        self.assertEqual(payload["key_hint"], "cret")
        self.assertTrue(payload["has_api_key"])
        self.assertEqual(list_user_models(self.db, self.owner)[0]["id"], model.id)

    @patch("server.services.model_credentials.validate_api_base", return_value="https://models.example.com/v1")
    def test_owned_lookup_cannot_read_another_users_model(self, _validate):
        model = create_user_model(
            self.db,
            self.owner,
            SimpleNamespace(
                display_name="私有模型",
                provider="openai-compatible",
                model_name="private-model",
                api_base="https://models.example.com/v1",
                api_key="secret-value",
            ),
            self.cipher,
        )

        with self.assertRaises(HTTPException) as ctx:
            get_owned_model(self.db, self.other, model.id)
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("server.services.model_credentials.validate_api_base", return_value="https://models.example.com/v1")
    def test_runtime_resolution_decrypts_only_the_owners_selected_model(self, _validate):
        model = create_user_model(
            self.db,
            self.owner,
            SimpleNamespace(
                display_name="运行模型",
                provider="openai-compatible",
                model_name="runtime-model",
                api_base="https://models.example.com/v1",
                api_key="runtime-secret",
            ),
            self.cipher,
        )

        master_key = Fernet.generate_key().decode()
        replacement_cipher = CredentialCipher(master_key)
        model.encrypted_api_key = replacement_cipher.encrypt("runtime-secret")
        self.db.commit()

        sentinel = object()
        select_model = Mock(return_value=sentinel)
        with patch.dict(os.environ, {"MODEL_CREDENTIAL_MASTER_KEY": master_key}, clear=False):
            resolved = resolve_model_for_user(
                self.db,
                self.owner,
                {"user_model_id": model.id},
                model_selector=select_model,
            )

        self.assertIs(resolved, sentinel)
        select_model.assert_called_once_with(custom_model_info={
            "model_name": "runtime-model",
            "api_base": "https://models.example.com/v1",
            "api_key": "runtime-secret",
        })

        with self.assertRaises(HTTPException) as ctx:
            resolve_model_for_user(
                self.db,
                self.other,
                {"user_model_id": model.id},
                model_selector=select_model,
            )
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
