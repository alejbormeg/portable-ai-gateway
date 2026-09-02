# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from portable_ai_gateway.config import Settings
from portable_ai_gateway.errors import GatewayError
from portable_ai_gateway.schemas import AUDIT_TEXT_PATTERN, IDENTIFIER_PATTERN, ApiKeyCreate
from portable_ai_gateway.store import Store, utc_now


@dataclass(frozen=True, slots=True)
class AuthContext:
    actor: str
    tenant_id: str | None
    scopes: frozenset[str]
    method: Literal["jwt", "api-key", "anonymous", "cli"]

    def require(self, scope: str) -> None:
        if scope not in self.scopes and "*" not in self.scopes:
            raise GatewayError(403, "Insufficient scope", "permission_error", "insufficient_scope")


def _unauthorized(message: str = "Invalid or missing bearer token") -> GatewayError:
    return GatewayError(
        401,
        message,
        "authentication_error",
        "invalid_authentication",
        {"WWW-Authenticate": "Bearer"},
    )


def _b64decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise _unauthorized("Malformed JWT") from exc


class JWTVerifier:
    """Offline OIDC JWT verifier. JWKS is injected; the gateway never phones home."""

    def __init__(self, settings: Settings) -> None:
        self._issuer = settings.oidc_issuer
        self._audience = settings.oidc_audience
        self._hs256_secret = settings.oidc_hs256_secret
        self._jwks = json.loads(settings.oidc_jwks_json) if settings.oidc_jwks_json else None

    def verify(self, token: str) -> AuthContext:
        parts = token.split(".")
        if len(parts) != 3:
            raise _unauthorized("Malformed JWT")
        try:
            header = json.loads(_b64decode(parts[0]))
            payload = json.loads(_b64decode(parts[1]))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _unauthorized("Malformed JWT") from exc
        if not isinstance(header, dict) or not isinstance(payload, dict):
            raise _unauthorized("Malformed JWT")
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        signature = _b64decode(parts[2])
        algorithm = header.get("alg")
        if algorithm == "HS256":
            self._verify_hs256(signing_input, signature)
        elif algorithm == "RS256":
            self._verify_rs256(header, signing_input, signature)
        else:
            raise _unauthorized("Unsupported JWT algorithm")
        self._validate_claims(payload)
        raw_scope = payload.get("scope", payload.get("scp", []))
        if isinstance(raw_scope, str):
            scopes = raw_scope.split()
        elif isinstance(raw_scope, list) and all(isinstance(item, str) for item in raw_scope):
            scopes = raw_scope
        else:
            raise _unauthorized("JWT scopes are invalid")
        tenant_id = payload.get("tenant_id", payload.get("tid"))
        if (
            tenant_id is not None
            and (
                not isinstance(tenant_id, str)
                or re.fullmatch(IDENTIFIER_PATTERN, tenant_id) is None
            )
        ):
            raise _unauthorized("JWT tenant is invalid")
        return AuthContext(
            actor=str(payload["sub"]),
            tenant_id=tenant_id,
            scopes=frozenset(scopes),
            method="jwt",
        )

    def _verify_hs256(self, signing_input: bytes, signature: bytes) -> None:
        if not self._hs256_secret:
            raise _unauthorized("HS256 is not configured")
        expected = hmac.new(self._hs256_secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise _unauthorized()

    def _verify_rs256(self, header: dict[str, Any], signing_input: bytes, signature: bytes) -> None:
        if not self._jwks:
            raise _unauthorized("RS256 is not configured")
        kid = header.get("kid")
        candidates = [
            key
            for key in self._jwks["keys"]
            if isinstance(key, dict)
            and key.get("kid") == kid
            and key.get("kty") == "RSA"
            and key.get("alg") in {None, "RS256"}
            and key.get("use") in {None, "sig"}
            and (
                "key_ops" not in key
                or (isinstance(key.get("key_ops"), list) and "verify" in key["key_ops"])
            )
        ]
        if len(candidates) != 1:
            raise _unauthorized("JWT signing key is unavailable")
        key = candidates[0]
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
            exponent = int.from_bytes(_b64decode(key["e"]), "big")
            modulus = int.from_bytes(_b64decode(key["n"]), "big")
            if modulus.bit_length() < 2048 or exponent < 3 or exponent % 2 == 0:
                raise _unauthorized("JWT signing key is too weak")
            public_key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
            public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install portable-ai-gateway[production] for RS256 verification") from exc
        except Exception as exc:
            raise _unauthorized() from exc

    def _validate_claims(self, payload: dict[str, Any]) -> None:
        now = time.time()
        if (
            not isinstance(payload.get("sub"), str)
            or len(payload["sub"]) > 256
            or re.fullmatch(AUDIT_TEXT_PATTERN, payload["sub"]) is None
        ):
            raise _unauthorized("JWT subject is required")
        if self._issuer is None or payload.get("iss") != self._issuer:
            raise _unauthorized("JWT issuer is invalid")
        audience = payload.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        if self._audience is None or not isinstance(audiences, list) or self._audience not in audiences:
            raise _unauthorized("JWT audience is invalid")
        exp = payload.get("exp")
        if (
            isinstance(exp, bool)
            or not isinstance(exp, (int, float))
            or not math.isfinite(exp)
            or exp < now - 30
        ):
            raise _unauthorized("JWT is expired")
        nbf = payload.get("nbf")
        if nbf is not None and (
            isinstance(nbf, bool)
            or not isinstance(nbf, (int, float))
            or not math.isfinite(nbf)
            or nbf > now + 30
        ):
            raise _unauthorized("JWT is not active")


class ApiKeyManager:
    def __init__(self, store: Store, pepper: str) -> None:
        self._store = store
        self._pepper = pepper.encode()

    def issue(self, request: ApiKeyCreate) -> tuple[dict[str, Any], str]:
        key_id = request.id or uuid.uuid4().hex[:20]
        if self._store.get_api_key(key_id) is not None:
            raise GatewayError(409, "API key ID already exists", "conflict_error", "key_exists")
        secret = secrets.token_urlsafe(32)
        raw_key = f"pag.{key_id}.{secret}"
        record = {
            "id": key_id,
            "tenant_id": request.tenant_id,
            "name": request.name,
            "key_hash": self._hash(raw_key),
            "scopes": request.scopes,
            "expires_at": request.expires_at,
            "revoked_at": None,
            "created_at": utc_now(),
            "last_used_at": None,
        }
        return self._store.put_api_key(record), raw_key

    def rotate(self, key_id: str) -> tuple[dict[str, Any], str]:
        existing = self._store.get_api_key(key_id)
        if existing is None or existing["revoked_at"] is not None:
            raise GatewayError(404, "API key not found", "invalid_request_error", "key_not_found")
        secret = secrets.token_urlsafe(32)
        raw_key = f"pag.{key_id}.{secret}"
        updated = self._store.update_api_key(key_id, {"key_hash": self._hash(raw_key)})
        return updated or existing, raw_key

    def revoke(self, key_id: str) -> dict[str, Any]:
        updated = self._store.update_api_key(key_id, {"revoked_at": utc_now()})
        if updated is None:
            raise GatewayError(404, "API key not found", "invalid_request_error", "key_not_found")
        return updated

    def authenticate(self, raw_key: str) -> AuthContext:
        prefix, separator, remainder = raw_key.partition("pag.")
        key_id, secret_separator, secret = remainder.rpartition(".")
        if prefix or not separator or not secret_separator or not key_id or not secret:
            raise _unauthorized("Invalid API key")
        record = self._store.get_api_key(key_id)
        if record is None or not hmac.compare_digest(record["key_hash"], self._hash(raw_key)):
            raise _unauthorized("Invalid API key")
        now = datetime.now(timezone.utc)
        if record["revoked_at"] is not None:
            raise _unauthorized("API key is revoked")
        if record["expires_at"] is not None and record["expires_at"] <= now:
            raise _unauthorized("API key is expired")
        self._store.update_api_key(record["id"], {"last_used_at": now})
        return AuthContext(
            actor=f"api-key:{record['id']}",
            tenant_id=record["tenant_id"],
            scopes=frozenset(record["scopes"]),
            method="api-key",
        )

    def _hash(self, raw_key: str) -> str:
        return hmac.new(self._pepper, raw_key.encode(), hashlib.sha256).hexdigest()


class AuthService:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.jwt = JWTVerifier(settings)
        self.api_keys = ApiKeyManager(store, settings.api_key_pepper)

    def authenticate(self, authorization: str | None) -> AuthContext:
        if not authorization:
            if self.settings.allow_anonymous_dev:
                return AuthContext("anonymous", "default", frozenset({"*"}), "anonymous")
            raise _unauthorized()
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token:
            raise _unauthorized()
        if token.startswith("pag."):
            return self.api_keys.authenticate(token)
        return self.jwt.verify(token)

    def require_admin(self, authorization: str | None) -> AuthContext:
        if not authorization:
            raise _unauthorized()
        context = self.authenticate(authorization)
        if context.method != "jwt":
            raise GatewayError(403, "Administration requires OIDC", "permission_error", "oidc_required")
        if "admin" not in context.scopes and "admin:*" not in context.scopes and "*" not in context.scopes:
            raise GatewayError(403, "Administrator scope is required", "permission_error", "insufficient_scope")
        return context


def public_key_view(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "key_hash"}
