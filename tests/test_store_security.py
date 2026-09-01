# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from portable_ai_gateway.errors import GatewayError
from portable_ai_gateway.schemas import ApiKeyCreate, AuditEvent, Policy, Tenant
from portable_ai_gateway.security import ApiKeyManager, JWTVerifier
from portable_ai_gateway.service import validate_resource_links
from portable_ai_gateway.store import (
    POSTGRES_MUTATION_LOCK_SQL,
    POSTGRES_SNAPSHOT_LOCK_SQL,
    PostgresConnectionPool,
    PostgresPoolExhausted,
    PostgresStore,
    SQLiteStore,
    _postgres_connection_kwargs,
)
from conftest import jwt_token


def test_resource_crud(store) -> None:
    tenant = Tenant(id="acme", name="ACME")
    store.put_resource("tenants", tenant)
    assert store.get_resource("tenants", "acme")["name"] == "ACME"
    assert store.list_resources("tenants")[0]["id"] == "acme"
    assert store.delete_resource("tenants", "acme") is True
    assert store.delete_resource("tenants", "acme") is False


def test_mutation_transaction_rolls_back_every_first_party_write(store) -> None:
    version = store.get_config_version()
    with pytest.raises(RuntimeError, match="audit unavailable"):
        with store.mutation_transaction():
            store.put_resource("tenants", Tenant(id="rollback", name="Rollback"))
            raise RuntimeError("audit unavailable")
    assert store.get_resource("tenants", "rollback") is None
    assert store.get_config_version() == version


def test_sqlite_mutation_rolls_back_when_commit_fails(store) -> None:
    connection = store._connection

    class CommitFailure:
        def __init__(self, delegate) -> None:
            self.delegate = delegate
            self.rollbacks = 0

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def commit(self) -> None:
            raise RuntimeError("commit unavailable")

        def rollback(self) -> None:
            self.rollbacks += 1
            self.delegate.rollback()

    failing = CommitFailure(connection)
    store._connection = failing
    try:
        with pytest.raises(RuntimeError, match="commit unavailable"):
            with store.mutation_transaction():
                store.put_resource("tenants", Tenant(id="commit-failure", name="Commit failure"))
        assert failing.rollbacks == 1
    finally:
        store._connection = connection
    assert store.get_resource("tenants", "commit-failure") is None


def test_postgres_mutation_rolls_back_when_commit_fails() -> None:
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

        def execute(self, statement) -> None:
            assert statement == POSTGRES_MUTATION_LOCK_SQL

    class CommitFailure:
        def __init__(self) -> None:
            self.rollbacks = 0

        def cursor(self) -> Cursor:
            return Cursor()

        def commit(self) -> None:
            raise RuntimeError("commit unavailable")

        def rollback(self) -> None:
            self.rollbacks += 1

    connection = CommitFailure()
    store = PostgresStore.__new__(PostgresStore)
    store._connection = connection
    store._lock = threading.RLock()
    store._transaction_depth = 0
    store._transaction_rollback_only = False

    with pytest.raises(RuntimeError, match="commit unavailable"):
        with store.mutation_transaction():
            pass
    assert connection.rollbacks == 1


def test_postgres_ping_replaces_one_failed_idle_connection() -> None:
    class BrokenCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

        def execute(self, statement) -> None:
            assert statement == "SELECT 1 AS value"
            raise RuntimeError("connection lost")

    class BrokenConnection:
        def __init__(self) -> None:
            self.closed = False

        def cursor(self) -> BrokenCursor:
            return BrokenCursor()

        def close(self) -> None:
            self.closed = True

    class HealthyCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

        def execute(self, statement) -> None:
            assert statement == "SELECT 1 AS value"

        def fetchone(self):
            return (1,)

    class HealthyConnection:
        def cursor(self) -> HealthyCursor:
            return HealthyCursor()

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    broken = BrokenConnection()
    healthy = HealthyConnection()
    store = PostgresStore.__new__(PostgresStore)
    store._connection = broken
    store._connection_factory = lambda: healthy
    store._lock = threading.RLock()
    store._transaction_depth = 0
    store._transaction_rollback_only = False

    assert store.ping() is True
    assert store._connection is healthy
    assert broken.closed is True


def test_postgres_ping_does_not_reconnect_inside_a_transaction() -> None:
    store = PostgresStore.__new__(PostgresStore)
    store._connection = object()
    store._connection_factory = lambda: object()
    store._lock = threading.RLock()
    store._transaction_depth = 1
    store._transaction_rollback_only = False

    with pytest.raises(RuntimeError, match="active transaction"):
        store._replace_connection()


def test_postgres_pool_lazily_borrows_returns_and_closes_connections() -> None:
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, traceback): del exc_type, exc, traceback
        def execute(self, statement) -> None: assert statement == "SELECT 1 AS value"
        def fetchone(self): return (1,)

    class Connection:
        def __init__(self) -> None:
            self.closed = False
        def cursor(self): return Cursor()
        def commit(self) -> None: return None
        def rollback(self) -> None: return None
        def close(self) -> None: self.closed = True

    created: list[Connection] = []
    def factory() -> Connection:
        connection = Connection()
        created.append(connection)
        return connection

    pool = PostgresConnectionPool(factory, min_size=1, max_size=2, acquire_timeout_seconds=0.02)
    first = pool.acquire()
    second = pool.acquire()
    assert len(created) == 2
    pool.release(first)
    assert pool.acquire() is first
    pool.release(first)
    pool.release(second)
    pool.close()
    assert all(connection.closed for connection in created)


def test_postgres_pool_fails_when_bounded_capacity_is_exhausted() -> None:
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, traceback): del exc_type, exc, traceback
        def execute(self, statement) -> None: assert statement == "SELECT 1 AS value"
        def fetchone(self): return (1,)

    class Connection:
        def cursor(self): return Cursor()
        def commit(self) -> None: return None
        def rollback(self) -> None: return None
        def close(self) -> None: return None

    pool = PostgresConnectionPool(Connection, min_size=1, max_size=1, acquire_timeout_seconds=0.02)
    connection = pool.acquire()
    started = time.monotonic()
    with pytest.raises(PostgresPoolExhausted):
        pool.acquire()
    assert time.monotonic() - started < 0.2
    pool.release(connection)


def test_postgres_pool_discards_an_unhealthy_idle_connection() -> None:
    class Cursor:
        def __init__(self, healthy: bool) -> None: self.healthy = healthy
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, traceback): del exc_type, exc, traceback
        def execute(self, statement) -> None:
            assert statement == "SELECT 1 AS value"
            if not self.healthy: raise RuntimeError("socket closed")
        def fetchone(self): return (1,)

    class Connection:
        def __init__(self, healthy: bool) -> None:
            self.healthy, self.closed = healthy, False
        def cursor(self): return Cursor(self.healthy)
        def commit(self) -> None: return None
        def rollback(self) -> None: return None
        def close(self) -> None: self.closed = True

    broken, replacement = Connection(False), Connection(True)
    connections = iter((broken, replacement))
    pool = PostgresConnectionPool(lambda: next(connections), min_size=1, max_size=1)
    acquired = pool.acquire()
    assert acquired is replacement
    assert broken.closed is True
    pool.release(acquired)


def test_sqlite_mutation_boundary_serializes_invariant_validation(tmp_path, settings) -> None:
    path = tmp_path / "concurrent.db"
    first = SQLiteStore(path)
    second = SQLiteStore(path)
    first.initialize()
    second.initialize()
    first.put_resource("tenants", Tenant(id="default", name="Default"))
    barrier = threading.Barrier(2)

    def create_policy(active_store: SQLiteStore, policy_id: str) -> str:
        barrier.wait()
        try:
            with active_store.mutation_transaction():
                policy = Policy(id=policy_id, tenant_id="default")
                validate_resource_links("policies", policy, active_store, settings)
                active_store.put_resource("policies", policy)
            return "created"
        except GatewayError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(
                    lambda pair: create_policy(*pair),
                    ((first, "one"), (second, "two")),
                )
            )
        assert sorted(outcomes) == ["ambiguous_policies", "created"]
        assert len(first.list_resources("policies", "default")) == 1
    finally:
        first.close()
        second.close()


def test_postgres_boundaries_use_matching_transaction_scoped_advisory_locks() -> None:
    assert "pg_advisory_xact_lock(" in POSTGRES_MUTATION_LOCK_SQL
    assert "pg_advisory_xact_lock_shared(" in POSTGRES_SNAPSHOT_LOCK_SQL
    assert "portable-ai-gateway-administrative-mutation" in POSTGRES_MUTATION_LOCK_SQL
    assert "portable-ai-gateway-administrative-mutation" in POSTGRES_SNAPSHOT_LOCK_SQL


def test_cloud_sql_unix_socket_uri_is_supported() -> None:
    values = _postgres_connection_kwargs(
        "postgresql://gateway:secret@/gateway?host=/cloudsql/customer:europe-west1:gateway"
    )
    assert values["unix_sock"] == "/cloudsql/customer:europe-west1:gateway/.s.PGSQL.5432"
    assert values["database"] == "gateway"
    assert values["ssl_context"] is False
    assert "host" not in values


def test_api_key_hash_rotation_expiry_and_revocation(store, settings) -> None:
    manager = ApiKeyManager(store, settings.api_key_pepper)
    record, raw = manager.issue(
        ApiKeyCreate(
            tenant_id="default",
            name="test",
            scopes=["inference:write"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    assert raw not in repr(store.get_api_key(record["id"]))
    assert manager.authenticate(raw).tenant_id == "default"
    _, rotated = manager.rotate(record["id"])
    with pytest.raises(GatewayError, match="Invalid API key"):
        manager.authenticate(raw)
    assert manager.authenticate(rotated).actor == f"api-key:{record['id']}"
    manager.revoke(record["id"])
    with pytest.raises(GatewayError, match="revoked"):
        manager.authenticate(rotated)

    dotted, dotted_raw = manager.issue(
        ApiKeyCreate(
            id="application.prod",
            tenant_id="default",
            name="dotted",
            scopes=["models:read"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    assert manager.authenticate(dotted_raw).actor == f"api-key:{dotted['id']}"


def test_api_key_expiry_is_mandatory() -> None:
    with pytest.raises(Exception):
        ApiKeyCreate(tenant_id="default", name="test", scopes=["inference:write"])
    with pytest.raises(Exception):
        ApiKeyCreate(
            tenant_id="default",
            name="test",
            scopes=["inference:write"],
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

    with pytest.raises(Exception, match="cannot receive scopes"):
        ApiKeyCreate(
            tenant_id="default",
            name="overprivileged",
            scopes=["*"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )


def test_jwt_validates_signature_and_oidc_claims(settings) -> None:
    verifier = JWTVerifier(settings)
    context = verifier.verify(jwt_token(settings.oidc_hs256_secret))
    assert context.method == "jwt"
    assert "admin" in context.scopes
    with pytest.raises(GatewayError, match="issuer"):
        verifier.verify(jwt_token(settings.oidc_hs256_secret, issuer="https://evil.example"))
    with pytest.raises(GatewayError, match="audience"):
        verifier.verify(jwt_token(settings.oidc_hs256_secret, audience="wrong"))
    with pytest.raises(GatewayError, match="expired"):
        verifier.verify(jwt_token(settings.oidc_hs256_secret, expires=1))
    with pytest.raises(GatewayError, match="expired"):
        verifier.verify(jwt_token(settings.oidc_hs256_secret, expires=True))
    with pytest.raises(GatewayError, match="expired"):
        verifier.verify(jwt_token(settings.oidc_hs256_secret, expires=float("nan")))
    with pytest.raises(GatewayError, match="algorithm"):
        verifier.verify(jwt_token(settings.oidc_hs256_secret, algorithm="none"))
    with pytest.raises(GatewayError, match="subject"):
        verifier.verify(jwt_token(settings.oidc_hs256_secret, subject="actor\nforged"))
    with pytest.raises(GatewayError, match="tenant"):
        verifier.verify(jwt_token(settings.oidc_hs256_secret, tenant_id="tenant\nforged"))


def test_runtime_defensively_rejects_non_object_jwks_keys(settings) -> None:
    verifier = JWTVerifier(
        replace(
            settings,
            oidc_hs256_secret=None,
            oidc_jwks_json=json.dumps({"keys": [42]}),
        )
    )
    with pytest.raises(GatewayError, match="signing key is unavailable") as error:
        verifier.verify(jwt_token("unused", algorithm="RS256"))
    assert error.value.status_code == 401


def test_audit_contract_cannot_contain_content(store) -> None:
    now = datetime.now(timezone.utc)
    event = AuditEvent(
        id="event-1",
        timestamp=now,
        request_id="request-1",
        actor="tester",
        tenant_id="default",
        action="chat.completions.create",
        status=200,
        config_version="v1",
    )
    store.append_audit(event)
    serialized = str(store.list_audit())
    assert "messages" not in serialized
    assert "mock response" not in serialized
    with pytest.raises(Exception):
        AuditEvent(
            id="event-2",
            timestamp=now,
            request_id="request-2",
            actor="tester",
            action="x",
            status=200,
            config_version="v1",
            messages=[{"content": "secret"}],
        )
