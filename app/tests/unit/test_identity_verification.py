"""
Demonstrates that RS's report-access identity source does not come from
`utils.utils.decode_jwt_token` (an UNVERIFIED `jwt.decode(...,
options={"verify_signature": False})`), and instead comes from a
signature-VERIFIED decode performed inside `api.deps.get_current_user`.

Two things are proven:

  1. The old unverified-decode primitive (`decode_jwt_token`, still present
     in utils/utils.py for any other legitimate non-security uses) happily
     reports a tampered/forged `user_id` claim from a token whose payload
     was edited after signing -- i.e. it is not a safe identity source.

  2. `api.deps.get_current_user`'s GateKeeper branch, which is what report
     endpoints now use to obtain `user_id` (via `token.id`), performs its
     own signature-verified decode and REJECTS that same tampered token,
     while accepting a token whose signature actually matches its payload.
     This shows the identity used for filesystem-path scoping cannot be
     spoofed merely by editing the (unverified) payload of a JWT-shaped
     string.
"""
import base64
import json
import os
from unittest import TestCase
from unittest.mock import MagicMock, patch

import jwt as pyjwt
from fastapi import HTTPException

REQUIRED_ENV_VARS = {
    "REPORTING_GATEKEEPER_USERNAME": "mock_user",
    "REPORTING_GATEKEEPER_PASSWORD": "mock_pass",
    "REPORTING_BACKEND_CORS_ORIGINS": '["*"]',
    "REPORTING_POSTGRES_USER": "mock_pg_user",
    "REPORTING_POSTGRES_PASSWORD": "mock_pg_pass",
    "REPORTING_POSTGRES_DB": "mock_db",
    "REPORTING_POSTGRES_HOST": "mock_host",
    "REPORTING_POSTGRES_PORT": "5432",
    "SQLALCHEMY_DATABASE_URI": "sqlite:///./test.db",
    "REPORTING_SERVICE_NAME": "mock_service",
    "REPORTING_SERVICE_PORT": "8000",
    "REPORTING_GATEKEEPER_BASE_URL": "http://mock.gatekeeper/",
    "JWT_ACCESS_TOKEN_EXPIRATION_TIME": "3600",
    "JWT_SIGNING_KEY": "unit-test-shared-secret-not-a-placeholder-0001",
    "PDF_DIRECTORY": "/tmp/identity_test_pdfs/",
    "REPORTING_USING_GATEKEEPER": "True",
}


def _tamper_user_id(token: str, new_user_id: str) -> str:
    """
    Take a validly-signed JWT and rewrite the `user_id` claim in its
    payload segment WITHOUT re-signing it. This invalidates the signature
    (the attacker doesn't have the signing key) but leaves the token
    structurally valid ("header.payload.signature" with a decodable
    payload) -- exactly the shape an unverified `jwt.decode(...,
    options={"verify_signature": False})` will still happily parse.
    """
    header_b64, payload_b64, signature_b64 = token.split(".")

    def _b64url_decode(segment: str) -> bytes:
        padding = "=" * (-len(segment) % 4)
        return base64.urlsafe_b64decode(segment + padding)

    def _b64url_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    payload = json.loads(_b64url_decode(payload_b64))
    payload["user_id"] = new_user_id
    tampered_payload_b64 = _b64url_encode(json.dumps(payload).encode())

    return f"{header_b64}.{tampered_payload_b64}.{signature_b64}"


class TestIdentitySourceCannotBeSpoofed(TestCase):
    @classmethod
    def setUpClass(cls):
        for k, v in REQUIRED_ENV_VARS.items():
            os.environ[k] = v
        super().setUpClass()

        from core.config import settings
        from api import deps
        from utils.utils import decode_jwt_token

        cls.settings = settings
        cls.deps = deps
        cls.decode_jwt_token = staticmethod(decode_jwt_token)

        cls.real_user_id = "user-A-legit-owner"
        cls.spoofed_user_id = "user-B-attacker-target"

        cls.legit_token = pyjwt.encode(
            {"user_id": cls.real_user_id, "exp": 9999999999},
            settings.JWT_SIGNING_KEY,
            algorithm="HS256",
        )
        cls.tampered_token = _tamper_user_id(cls.legit_token, cls.spoofed_user_id)


    def test_unverified_decode_trusts_tampered_payload(self):
        decoded = self.decode_jwt_token(self.tampered_token)
        self.assertEqual(
            decoded["user_id"],
            self.spoofed_user_id,
            "decode_jwt_token (unverified) must be shown to accept a "
            "tampered user_id claim -- this is exactly why it must not be "
            "used as an identity source.",
        )


    def _call_get_current_user(self, token: str):
        with patch.object(
            self.settings, "REPORTING_USING_GATEKEEPER", True
        ), patch.object(self.deps, "requests") as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=200)
            return self.deps.get_current_user(db=None, token=token)

    def test_verified_identity_source_accepts_legit_token(self):
        auth = self._call_get_current_user(self.legit_token)
        self.assertEqual(auth.id, self.real_user_id)
        self.assertEqual(auth.user_id, self.real_user_id)
        self.assertEqual(str(auth), self.legit_token)

    def test_verified_identity_source_rejects_tampered_token(self):
        """
        This is the crux: even though the tampered token still
        decodes fine under the OLD unverified path (see test above), the
        NEW identity source used by report endpoints (`token.id`, sourced
        from api.deps.get_current_user's verified decode) must refuse it
        rather than silently reporting the spoofed user_id.
        """
        with self.assertRaises(HTTPException) as ctx:
            self._call_get_current_user(self.tampered_token)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_tampered_token_never_yields_spoofed_identity(self):
        """Belt-and-braces: whatever happens, .id must never equal the
        attacker's target user_id when using the tampered token."""
        try:
            auth = self._call_get_current_user(self.tampered_token)
        except HTTPException:
            return
        self.fail(
            f"Tampered token was accepted and produced identity "
            f"{auth.id!r}; it must be rejected instead."
        )
