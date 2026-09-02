# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ContextManager, Protocol

from portable_ai_gateway.schemas import AuditEvent, ConfigurationDocument, Resource


CONFIG_RESOURCE_KINDS = ("tenants", "providers", "models", "routes", "policies", "quotas")
POSTGRES_MUTATION_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtext('portable-ai-gateway-administrative-mutation'))"
)
POSTGRES_SNAPSHOT_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock_shared(hashtext('portable-ai-gateway-administrative-mutation'))"
)
POSTGRES_SCHEMA_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtext('portable-ai-gateway-schema-initialization'))"
)


class PostgresPoolExhausted(RuntimeError):
    """No PostgreSQL connection became available within the configured bound."""


class PostgresConnectionPool:
    """Small synchronous DB-API pool with health-checked idle connections."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        min_size: int = 1,
        max_size: int = 5,
        acquire_timeout_seconds: float = 2.0,
    ) -> None:
        if not 1 <= min_size <= max_size <= 50:
            raise ValueError("PostgreSQL pool size must satisfy 1 <= min_size <= max_size <= 50")
        if not 0 < acquire_timeout_seconds <= 10:
            raise ValueError("PostgreSQL pool acquire timeout must be between 0 and 10 seconds")
        self._factory = connection_factory
        self._max_size = max_size
        self._timeout = acquire_timeout_seconds
        self._idle: list[Any] = []
        self._created = 0
        self._closed = False
        self._condition = threading.Condition()
        for _ in range(min_size):
            self._idle.append(self._new_connection())

    def _new_connection(self) -> Any:
        connection = self._factory()
        self._created += 1
        return connection

    @staticmethod
    def _is_healthy(connection: Any) -> bool:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS value")
                healthy = cursor.fetchone()[0] == 1
            connection.commit()
            return healthy
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            return False

    def acquire(self) -> Any:
        deadline = time.monotonic() + self._timeout
        while True:
            candidate: Any | None = None
            create = False
            with self._condition:
                if self._closed:
                    raise RuntimeError("PostgreSQL pool is closed")
                if self._idle:
                    candidate = self._idle.pop()
                elif self._created < self._max_size:
                    self._created += 1
                    create = True
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise PostgresPoolExhausted("PostgreSQL pool acquire timeout")
                    self._condition.wait(remaining)
                    continue
            if create:
                try:
                    candidate = self._factory()
                except Exception:
                    with self._condition:
                        self._created -= 1
                        self._condition.notify()
                    raise
            assert candidate is not None
            if self._is_healthy(candidate):
                return candidate
            self.release(candidate, broken=True)

    def release(self, connection: Any, *, broken: bool = False) -> None:
        if broken:
            try:
                connection.close()
            except Exception:
                pass
        with self._condition:
            if broken or self._closed:
                self._created -= 1
                if self._closed and not broken:
                    try:
                        connection.close()
                    except Exception:
                        pass
            else:
                self._idle.append(connection)
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            idle, self._idle = self._idle, []
            self._created -= len(idle)
            self._condition.notify_all()
        for connection in idle:
            try:
                connection.close()
            except Exception:
                pass


class _PostgresSessionLock:
    """Borrows one connection per thread-local operation or transaction."""

    def __init__(self, store: "PostgresStore") -> None:
        self._store = store

    def __enter__(self) -> "_PostgresSessionLock":
        state = self._store._state
        depth = getattr(state, "session_depth", 0)
        if depth == 0:
            state.connection = self._store._pool.acquire()
        state.session_depth = depth + 1
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc, traceback
        state = self._store._state
        depth = state.session_depth - 1
        state.session_depth = depth
        if depth != 0:
            return
        connection = state.connection
        del state.connection
        broken = exc_type is not None
        try:
            if broken:
                connection.rollback()
            elif self._store._transaction_depth == 0:
                connection.commit()
        except Exception:
            broken = True
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        finally:
            self._store._pool.release(connection, broken=broken)


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    """Immutable-by-interface configuration revision used for one routing decision."""

    config_version: str | None
    _resources: dict[str, tuple[dict[str, Any], ...]] = field(repr=False)

    def list_resources(self, kind: str, tenant_id: str | None = None) -> list[dict[str, Any]]:
        values = self._resources.get(kind, ())
        if tenant_id is not None:
            values = tuple(item for item in values if item.get("tenant_id") == tenant_id)
        return copy.deepcopy(list(values))

    def get_resource(self, kind: str, resource_id: str) -> dict[str, Any] | None:
        for resource in self._resources.get(kind, ()):
            if resource.get("id") == resource_id:
                return copy.deepcopy(resource)
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


class Store(Protocol):
    def initialize(self) -> None: ...
    def ping(self) -> bool: ...
    def close(self) -> None: ...
    def list_resources(self, kind: str, tenant_id: str | None = None) -> list[dict[str, Any]]: ...
    def get_resource(self, kind: str, resource_id: str) -> dict[str, Any] | None: ...
    def put_resource(self, kind: str, resource: Resource | dict[str, Any]) -> dict[str, Any]: ...
    def delete_resource(self, kind: str, resource_id: str) -> bool: ...
    def delete_tenant(self, tenant_id: str) -> bool: ...
    def put_api_key(self, record: dict[str, Any]) -> dict[str, Any]: ...
    def get_api_key(self, key_id: str) -> dict[str, Any] | None: ...
    def list_api_keys(self, tenant_id: str | None = None) -> list[dict[str, Any]]: ...
    def update_api_key(self, key_id: str, fields: dict[str, Any]) -> dict[str, Any] | None: ...
    def append_audit(self, event: AuditEvent) -> None: ...
    def list_audit(self, tenant_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...
    def get_config_version(self) -> str | None: ...
    def configuration_snapshot(self) -> ConfigurationSnapshot: ...
    def mutation_transaction(self) -> ContextManager[None]: ...
    def reconcile_configuration(self, document: ConfigurationDocument) -> None: ...


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    tenant_id TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS idx_resources_tenant ON resources(kind, tenant_id);
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    scopes TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    request_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    tenant_id TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_time ON audit_events(tenant_id, timestamp DESC);
CREATE TABLE IF NOT EXISTS gateway_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class SQLiteStore:
    """Small, dependency-free store for development, tests, and single-node evaluation."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._transaction_depth = 0
        self._transaction_rollback_only = False

    @contextmanager
    def mutation_transaction(self) -> Iterator[None]:
        """Serialize validation, mutation, and audit and commit them as one unit."""
        with self._lock:
            outermost = self._transaction_depth == 0
            if outermost:
                self._connection.execute("BEGIN IMMEDIATE")
                self._transaction_rollback_only = False
            self._transaction_depth += 1
            try:
                yield
            except BaseException:
                self._transaction_rollback_only = True
                if outermost:
                    self._connection.rollback()
                raise
            else:
                if outermost:
                    if self._transaction_rollback_only:
                        self._connection.rollback()
                        raise RuntimeError("Administrative transaction was marked for rollback")
                    self._commit_or_rollback()
            finally:
                self._transaction_depth -= 1
                if outermost:
                    self._transaction_rollback_only = False

    def _commit_if_outside_transaction(self) -> None:
        if self._transaction_depth == 0:
            self._commit_or_rollback()

    def _commit_or_rollback(self) -> None:
        try:
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def initialize(self) -> None:
        with self._lock:
            self._connection.executescript(SQLITE_SCHEMA)
            self._commit_or_rollback()

    def ping(self) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1").fetchone()[0] == 1

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def list_resources(self, kind: str, tenant_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT payload FROM resources WHERE kind = ?"
        parameters: list[Any] = [kind]
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            parameters.append(tenant_id)
        query += " ORDER BY id"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_resource(self, kind: str, resource_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM resources WHERE kind = ? AND id = ?", (kind, resource_id)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def put_resource(self, kind: str, resource: Resource | dict[str, Any]) -> dict[str, Any]:
        payload = resource.model_dump(mode="json") if isinstance(resource, Resource) else dict(resource)
        with self._lock:
            self._put_resource_no_commit(kind, payload)
            self._mark_imperative_version_no_commit()
            self._commit_if_outside_transaction()
        return payload

    def _put_resource_no_commit(self, kind: str, payload: dict[str, Any]) -> None:
        now = _iso(utc_now())
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self._connection.execute(
            """
            INSERT INTO resources(kind, id, tenant_id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, id) DO UPDATE SET
              tenant_id = excluded.tenant_id,
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (kind, payload["id"], payload.get("tenant_id"), serialized, now, now),
        )

    def _mark_imperative_version_no_commit(self) -> None:
        now = _iso(utc_now())
        self._connection.execute(
            """INSERT INTO gateway_meta(key,value,updated_at) VALUES ('config_version',?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (f"imperative@{now}", now),
        )

    def delete_resource(self, kind: str, resource_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM resources WHERE kind = ? AND id = ?", (kind, resource_id)
            )
            if cursor.rowcount > 0:
                self._mark_imperative_version_no_commit()
            self._commit_if_outside_transaction()
            return cursor.rowcount > 0

    def delete_tenant(self, tenant_id: str) -> bool:
        """Delete a tenant atomically, remove its configuration, and revoke its keys."""
        with self._lock:
            try:
                cursor = self._connection.execute(
                    "DELETE FROM resources WHERE kind = 'tenants' AND id = ?", (tenant_id,)
                )
                deleted = cursor.rowcount > 0
                if deleted:
                    self._connection.execute(
                        "UPDATE api_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE tenant_id = ?",
                        (_iso(utc_now()), tenant_id),
                    )
                    self._connection.execute("DELETE FROM resources WHERE tenant_id = ?", (tenant_id,))
                    self._mark_imperative_version_no_commit()
                self._commit_if_outside_transaction()
                return deleted
            except Exception:
                if self._transaction_depth == 0:
                    self._connection.rollback()
                raise

    def put_api_key(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO api_keys(id, tenant_id, name, key_hash, scopes, expires_at, revoked_at, created_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["tenant_id"],
                    record["name"],
                    record["key_hash"],
                    json.dumps(record["scopes"]),
                    _iso(record.get("expires_at")),
                    _iso(record.get("revoked_at")),
                    _iso(record["created_at"]),
                    _iso(record.get("last_used_at")),
                ),
            )
            self._commit_if_outside_transaction()
        return self.get_api_key(record["id"]) or {}

    def get_api_key(self, key_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        return _key_row(row) if row else None

    def list_api_keys(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM api_keys"
        params: tuple[Any, ...] = ()
        if tenant_id is not None:
            query += " WHERE tenant_id = ?"
            params = (tenant_id,)
        query += " ORDER BY id"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [_key_row(row) for row in rows]

    def update_api_key(self, key_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"name", "key_hash", "scopes", "expires_at", "revoked_at", "last_used_at"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_api_key(key_id)
        encoded: dict[str, Any] = {}
        for key, value in updates.items():
            if key == "scopes":
                encoded[key] = json.dumps(value)
            elif key in {"expires_at", "revoked_at", "last_used_at"}:
                encoded[key] = _iso(value)
            else:
                encoded[key] = value
        assignments = ", ".join(f"{key} = ?" for key in encoded)
        with self._lock:
            # `assignments` uses an internal allowlist; all values remain bound.
            self._connection.execute(
                f"UPDATE api_keys SET {assignments} WHERE id = ?",  # nosec B608
                (*encoded.values(), key_id),
            )
            self._commit_if_outside_transaction()
        return self.get_api_key(key_id)

    def append_audit(self, event: AuditEvent) -> None:
        payload = event.model_dump(mode="json")
        if AuditEvent.FORBIDDEN_CONTENT_FIELDS.intersection(payload):
            raise ValueError("audit payload contains a forbidden content field")
        with self._lock:
            self._connection.execute(
                "INSERT INTO audit_events(id, timestamp, request_id, actor, tenant_id, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    _iso(event.timestamp),
                    event.request_id,
                    event.actor,
                    event.tenant_id,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )
            self._commit_if_outside_transaction()

    def list_audit(self, tenant_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 1000)
        query = "SELECT payload FROM audit_events"
        params: list[Any] = []
        if tenant_id is not None:
            query += " WHERE tenant_id = ?"
            params.append(tenant_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_config_version(self) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM gateway_meta WHERE key = 'config_version'"
            ).fetchone()
        return str(row["value"]) if row else None

    def configuration_snapshot(self) -> ConfigurationSnapshot:
        """Read version and all routing resources from one SQLite snapshot."""
        with self._lock:
            owns_transaction = self._transaction_depth == 0
            if owns_transaction:
                self._connection.execute("BEGIN")
            try:
                version_row = self._connection.execute(
                    "SELECT value FROM gateway_meta WHERE key = 'config_version'"
                ).fetchone()
                placeholders = ",".join("?" for _ in CONFIG_RESOURCE_KINDS)
                # Placeholder count derives from a constant tuple.
                rows = self._connection.execute(
                    f"SELECT kind, payload FROM resources WHERE kind IN ({placeholders}) ORDER BY kind, id",  # nosec B608
                    CONFIG_RESOURCE_KINDS,
                ).fetchall()
                snapshot = _configuration_snapshot(
                    str(version_row["value"]) if version_row else None,
                    ((str(row["kind"]), json.loads(row["payload"])) for row in rows),
                )
                if owns_transaction:
                    self._commit_or_rollback()
                return snapshot
            except Exception:
                if owns_transaction:
                    self._connection.rollback()
                raise

    def reconcile_configuration(self, document: ConfigurationDocument) -> None:
        """Atomically makes config-owned resources equal to the desired document."""
        with self.mutation_transaction():
            with self._lock:
                desired_tenants = {tenant.id for tenant in document.spec.tenants}
                current_tenants = {
                    row["id"]
                    for row in self._connection.execute(
                        "SELECT id FROM resources WHERE kind = 'tenants'"
                    ).fetchall()
                }
                removed_tenants = sorted(current_tenants - desired_tenants)
                if removed_tenants:
                    placeholders = ",".join("?" for _ in removed_tenants)
                    # Placeholder count derives from internal tenant ids.
                    self._connection.execute(
                        f"UPDATE api_keys SET revoked_at = COALESCE(revoked_at, ?) "  # nosec B608
                        f"WHERE tenant_id IN ({placeholders})",
                        (_iso(utc_now()), *removed_tenants),
                    )
                    self._connection.execute(
                        f"DELETE FROM resources WHERE tenant_id IN ({placeholders})",  # nosec B608
                        removed_tenants,
                    )
                for kind in CONFIG_RESOURCE_KINDS:
                    resources = list(getattr(document.spec, kind))
                    desired_ids = [resource.id for resource in resources]
                    if desired_ids:
                        placeholders = ",".join("?" for _ in desired_ids)
                        # Placeholder count derives from validated resource ids.
                        self._connection.execute(
                            f"DELETE FROM resources WHERE kind = ? AND id NOT IN ({placeholders})",  # nosec B608
                            (kind, *desired_ids),
                        )
                    else:
                        self._connection.execute("DELETE FROM resources WHERE kind = ?", (kind,))
                    for resource in resources:
                        self._put_resource_no_commit(kind, resource.model_dump(mode="json"))
                self._connection.execute(
                    """INSERT INTO gateway_meta(key,value,updated_at) VALUES ('config_version',?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                    (document.metadata.version, _iso(utc_now())),
                )


class InMemoryStore(SQLiteStore):
    def __init__(self) -> None:
        super().__init__(":memory:")


def _key_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "name": row["name"],
        "key_hash": row["key_hash"],
        "scopes": json.loads(row["scopes"]),
        "expires_at": datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        "revoked_at": datetime.fromisoformat(row["revoked_at"]) if row["revoked_at"] else None,
        "created_at": datetime.fromisoformat(row["created_at"]),
        "last_used_at": datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None,
    }


POSTGRES_SCHEMA = tuple(
    statement.strip()
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS resources (
          kind TEXT NOT NULL, id TEXT NOT NULL, tenant_id TEXT, payload JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
          PRIMARY KEY(kind, id))
        """,
        "CREATE INDEX IF NOT EXISTS idx_resources_tenant ON resources(kind, tenant_id)",
        """
        CREATE TABLE IF NOT EXISTS api_keys (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, key_hash TEXT NOT NULL,
          scopes JSONB NOT NULL, expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL, last_used_at TIMESTAMPTZ)
        """,
        "CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id)",
        """
        CREATE TABLE IF NOT EXISTS audit_events (
          id TEXT PRIMARY KEY, timestamp TIMESTAMPTZ NOT NULL, request_id TEXT NOT NULL,
          actor TEXT NOT NULL, tenant_id TEXT, payload JSONB NOT NULL)
        """,
        "CREATE INDEX IF NOT EXISTS idx_audit_tenant_time ON audit_events(tenant_id, timestamp DESC)",
        """CREATE TABLE IF NOT EXISTS gateway_meta (
          key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""",
    )
)


class PostgresStore:
    """PostgreSQL adapter using the permissively licensed pg8000 DB-API driver."""

    def __init__(
        self,
        url: str,
        *,
        connection_factory: Callable[[], Any] | None = None,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
        pool_acquire_timeout_seconds: float = 2.0,
    ) -> None:
        try:
            import pg8000.dbapi
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install portable-ai-gateway[postgres] to use PostgreSQL") from exc
        self._connection_factory = connection_factory or (
            lambda: pg8000.dbapi.connect(**_postgres_connection_kwargs(url))
        )
        self._pool = PostgresConnectionPool(
            self._connection_factory,
            min_size=pool_min_size,
            max_size=pool_max_size,
            acquire_timeout_seconds=pool_acquire_timeout_seconds,
        )
        self._state = threading.local()
        self._lock = _PostgresSessionLock(self)
        self._transaction_rollback_only = False

    @property
    def _connection(self) -> Any:
        try:
            return self._state.connection
        except AttributeError as exc:
            raise RuntimeError("PostgreSQL connection used outside an operation") from exc

    @_connection.setter
    def _connection(self, value: Any) -> None:
        if not hasattr(self, "_state"):
            self._state = threading.local()
        self._state.connection = value

    @property
    def _transaction_depth(self) -> int:
        return getattr(self._state, "transaction_depth", 0)

    @_transaction_depth.setter
    def _transaction_depth(self, value: int) -> None:
        self._state.transaction_depth = value

    @property
    def _transaction_rollback_only(self) -> bool:
        return getattr(self._state, "transaction_rollback_only", False)

    @_transaction_rollback_only.setter
    def _transaction_rollback_only(self, value: bool) -> None:
        self._state.transaction_rollback_only = value

    def _replace_connection(self) -> None:
        """Replace a failed idle connection; never retry an open transaction."""
        if self._transaction_depth != 0:
            raise RuntimeError("Cannot reconnect PostgreSQL during an active transaction")
        previous = self._connection
        if hasattr(self, "_pool"):
            self._pool.release(previous, broken=True)
            self._state.connection = self._pool.acquire()
        else:  # pragma: no cover - compatibility for synthetic unit doubles
            replacement = self._connection_factory()
            self._connection = replacement
            try:
                previous.close()
            except Exception:
                pass

    @contextmanager
    def mutation_transaction(self) -> Iterator[None]:
        """Use a database-wide transaction lock across first-party replicas."""
        with self._lock:
            outermost = self._transaction_depth == 0
            if outermost:
                with self._connection.cursor() as cursor:
                    cursor.execute(POSTGRES_MUTATION_LOCK_SQL)
                self._transaction_rollback_only = False
            self._transaction_depth += 1
            try:
                yield
            except BaseException:
                self._transaction_rollback_only = True
                if outermost:
                    self._connection.rollback()
                raise
            else:
                if outermost:
                    if self._transaction_rollback_only:
                        self._connection.rollback()
                        raise RuntimeError("Administrative transaction was marked for rollback")
                    self._commit_or_rollback()
            finally:
                self._transaction_depth -= 1
                if outermost:
                    self._transaction_rollback_only = False

    def _commit_if_outside_transaction(self) -> None:
        if self._transaction_depth == 0:
            self._commit_or_rollback()

    def _commit_or_rollback(self) -> None:
        try:
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def _finish_read_if_outside_transaction(self) -> None:
        # pg8000 starts a transaction for SELECT as well. Closing it here keeps
        # the next advisory-xact boundary from inheriting a stale read snapshot.
        if self._transaction_depth == 0:
            self._commit_or_rollback()

    def initialize(self) -> None:
        with self._lock, self._connection.cursor() as cursor:
            # Both planes can start at once against an empty database. Serialize
            # schema setup so concurrent CREATE ... IF NOT EXISTS statements do
            # not race on PostgreSQL catalog rows.
            cursor.execute(POSTGRES_SCHEMA_LOCK_SQL)
            for statement in POSTGRES_SCHEMA:
                cursor.execute(statement)
            self._commit_or_rollback()

    def ping(self) -> bool:
        with self._lock:
            try:
                return self._ping_once()
            except Exception:
                self._replace_connection()
                return self._ping_once()

    def _ping_once(self) -> bool:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT 1 AS value")
            ready = cursor.fetchone()[0] == 1
            self._finish_read_if_outside_transaction()
            return ready

    def close(self) -> None:
        self._pool.close()

    def list_resources(self, kind: str, tenant_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM resources WHERE kind = %s"
        params: list[Any] = [kind]
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        sql += " ORDER BY id"
        with self._lock, self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            result = [_json_object(row[0]) for row in cursor.fetchall()]
            self._finish_read_if_outside_transaction()
            return result

    def get_resource(self, kind: str, resource_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM resources WHERE kind = %s AND id = %s", (kind, resource_id))
            row = cursor.fetchone()
            result = _json_object(row[0]) if row else None
            self._finish_read_if_outside_transaction()
            return result

    def put_resource(self, kind: str, resource: Resource | dict[str, Any]) -> dict[str, Any]:
        payload = resource.model_dump(mode="json") if isinstance(resource, Resource) else dict(resource)
        now = utc_now()
        with self._lock, self._connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO resources(kind,id,tenant_id,payload,created_at,updated_at)
                   VALUES (%s,%s,%s,CAST(%s AS JSONB),%s,%s)
                   ON CONFLICT(kind,id) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,
                   payload=EXCLUDED.payload,updated_at=EXCLUDED.updated_at""",
                (kind, payload["id"], payload.get("tenant_id"), json.dumps(payload), now, now),
            )
            self._mark_imperative_version(cursor, now)
            self._commit_if_outside_transaction()
        return payload

    def delete_resource(self, kind: str, resource_id: str) -> bool:
        with self._lock, self._connection.cursor() as cursor:
            cursor.execute("DELETE FROM resources WHERE kind = %s AND id = %s", (kind, resource_id))
            deleted = cursor.rowcount > 0
            if deleted:
                self._mark_imperative_version(cursor, utc_now())
            self._commit_if_outside_transaction()
            return deleted

    def delete_tenant(self, tenant_id: str) -> bool:
        """Delete a tenant atomically, remove its configuration, and revoke its keys."""
        with self._lock, self._connection.cursor() as cursor:
            try:
                cursor.execute("DELETE FROM resources WHERE kind = 'tenants' AND id = %s", (tenant_id,))
                deleted = cursor.rowcount > 0
                if deleted:
                    cursor.execute(
                        "UPDATE api_keys SET revoked_at = COALESCE(revoked_at, %s) WHERE tenant_id = %s",
                        (utc_now(), tenant_id),
                    )
                    cursor.execute("DELETE FROM resources WHERE tenant_id = %s", (tenant_id,))
                    self._mark_imperative_version(cursor, utc_now())
                self._commit_if_outside_transaction()
                return deleted
            except Exception:
                if self._transaction_depth == 0:
                    self._connection.rollback()
                raise

    def put_api_key(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO api_keys(id,tenant_id,name,key_hash,scopes,expires_at,revoked_at,created_at,last_used_at)
                   VALUES (%s,%s,%s,%s,CAST(%s AS JSONB),%s,%s,%s,%s)""",
                (record["id"], record["tenant_id"], record["name"], record["key_hash"], json.dumps(record["scopes"]),
                 record.get("expires_at"), record.get("revoked_at"), record["created_at"], record.get("last_used_at")),
            )
            self._commit_if_outside_transaction()
        return self.get_api_key(record["id"]) or {}

    def get_api_key(self, key_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection.cursor() as cursor:
            cursor.execute("SELECT * FROM api_keys WHERE id = %s", (key_id,))
            row = cursor.fetchone()
            result = _postgres_key_row(row) if row else None
            self._finish_read_if_outside_transaction()
            return result

    def list_api_keys(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM api_keys", []
        if tenant_id is not None:
            sql, params = sql + " WHERE tenant_id = %s", [tenant_id]
        sql += " ORDER BY id"
        with self._lock, self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            result = [_postgres_key_row(row) for row in cursor.fetchall()]
            self._finish_read_if_outside_transaction()
            return result

    def update_api_key(self, key_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"name", "key_hash", "scopes", "expires_at", "revoked_at", "last_used_at"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_api_key(key_id)
        values = [json.dumps(value) if key == "scopes" else value for key, value in updates.items()]
        assignments = ", ".join(f"{key} = CAST(%s AS JSONB)" if key == "scopes" else f"{key} = %s" for key in updates)
        with self._lock, self._connection.cursor() as cursor:
            # `assignments` uses an internal allowlist; all values remain bound.
            cursor.execute(f"UPDATE api_keys SET {assignments} WHERE id = %s", (*values, key_id))  # nosec B608
            self._commit_if_outside_transaction()
        return self.get_api_key(key_id)

    def append_audit(self, event: AuditEvent) -> None:
        payload = event.model_dump(mode="json")
        if AuditEvent.FORBIDDEN_CONTENT_FIELDS.intersection(payload):
            raise ValueError("audit payload contains a forbidden content field")
        with self._lock, self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO audit_events(id,timestamp,request_id,actor,tenant_id,payload) VALUES (%s,%s,%s,%s,%s,CAST(%s AS JSONB))",
                (event.id, event.timestamp, event.request_id, event.actor, event.tenant_id, json.dumps(payload)),
            )
            self._commit_if_outside_transaction()

    def list_audit(self, tenant_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 1000)
        sql, params = "SELECT payload FROM audit_events", []
        if tenant_id is not None:
            sql, params = sql + " WHERE tenant_id = %s", [tenant_id]
        sql += " ORDER BY timestamp DESC LIMIT %s"
        params.append(limit)
        with self._lock, self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            result = [_json_object(row[0]) for row in cursor.fetchall()]
            self._finish_read_if_outside_transaction()
            return result

    def get_config_version(self) -> str | None:
        with self._lock, self._connection.cursor() as cursor:
            cursor.execute("SELECT value FROM gateway_meta WHERE key = 'config_version'")
            row = cursor.fetchone()
            result = str(row[0]) if row else None
            self._finish_read_if_outside_transaction()
            return result

    def configuration_snapshot(self) -> ConfigurationSnapshot:
        """Read a revision under a shared advisory transaction lock."""
        with self._lock, self._connection.cursor() as cursor:
            owns_transaction = self._transaction_depth == 0
            try:
                if owns_transaction:
                    cursor.execute(POSTGRES_SNAPSHOT_LOCK_SQL)
                cursor.execute("SELECT value FROM gateway_meta WHERE key = 'config_version'")
                version_row = cursor.fetchone()
                kinds = ",".join("%s" for _ in CONFIG_RESOURCE_KINDS)
                # Placeholder count derives from a constant tuple.
                cursor.execute(
                    f"SELECT kind,payload FROM resources WHERE kind IN ({kinds}) ORDER BY kind,id",  # nosec B608
                    CONFIG_RESOURCE_KINDS,
                )
                snapshot = _configuration_snapshot(
                    str(version_row[0]) if version_row else None,
                    ((str(row[0]), _json_object(row[1])) for row in cursor.fetchall()),
                )
                if owns_transaction:
                    self._commit_or_rollback()
                return snapshot
            except Exception:
                if owns_transaction:
                    self._connection.rollback()
                raise

    @staticmethod
    def _mark_imperative_version(cursor: Any, now: datetime) -> None:
        cursor.execute(
            """INSERT INTO gateway_meta(key,value,updated_at) VALUES ('config_version',%s,%s)
               ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at""",
            (f"imperative@{now.isoformat()}", now),
        )

    def reconcile_configuration(self, document: ConfigurationDocument) -> None:
        with self.mutation_transaction():
            with self._lock, self._connection.cursor() as cursor:
                now = utc_now()
                desired_tenants = {tenant.id for tenant in document.spec.tenants}
                cursor.execute("SELECT id FROM resources WHERE kind = 'tenants'")
                current_tenants = {row[0] for row in cursor.fetchall()}
                removed_tenants = sorted(current_tenants - desired_tenants)
                if removed_tenants:
                    placeholders = ",".join("%s" for _ in removed_tenants)
                    # Placeholder count derives from internal tenant ids.
                    cursor.execute(
                        f"UPDATE api_keys SET revoked_at = COALESCE(revoked_at, %s) "  # nosec B608
                        f"WHERE tenant_id IN ({placeholders})",
                        (now, *removed_tenants),
                    )
                    cursor.execute(
                        f"DELETE FROM resources WHERE tenant_id IN ({placeholders})",  # nosec B608
                        removed_tenants,
                    )
                for kind in CONFIG_RESOURCE_KINDS:
                    resources = list(getattr(document.spec, kind))
                    desired_ids = [resource.id for resource in resources]
                    if desired_ids:
                        placeholders = ",".join("%s" for _ in desired_ids)
                        # Placeholder count derives from validated resource ids.
                        cursor.execute(
                            f"DELETE FROM resources WHERE kind = %s AND id NOT IN ({placeholders})",  # nosec B608
                            (kind, *desired_ids),
                        )
                    else:
                        cursor.execute("DELETE FROM resources WHERE kind = %s", (kind,))
                    for resource in resources:
                        payload = resource.model_dump(mode="json")
                        cursor.execute(
                            """INSERT INTO resources(kind,id,tenant_id,payload,created_at,updated_at)
                               VALUES (%s,%s,%s,CAST(%s AS JSONB),%s,%s)
                               ON CONFLICT(kind,id) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,
                               payload=EXCLUDED.payload,updated_at=EXCLUDED.updated_at""",
                            (kind, payload["id"], payload.get("tenant_id"), json.dumps(payload), now, now),
                        )
                cursor.execute(
                    """INSERT INTO gateway_meta(key,value,updated_at) VALUES ('config_version',%s,%s)
                       ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at""",
                    (document.metadata.version, now),
                )


def _json_object(value: Any) -> dict[str, Any]:
    parsed = json.loads(value) if isinstance(value, str) else value
    return dict(parsed)


def _configuration_snapshot(
    config_version: str | None,
    rows: Iterable[tuple[str, dict[str, Any]]],
) -> ConfigurationSnapshot:
    grouped: dict[str, list[dict[str, Any]]] = {kind: [] for kind in CONFIG_RESOURCE_KINDS}
    for kind, payload in rows:
        if kind in grouped:
            grouped[kind].append(copy.deepcopy(payload))
    return ConfigurationSnapshot(
        config_version=config_version,
        _resources={kind: tuple(values) for kind, values in grouped.items()},
    )


def _postgres_connection_kwargs(url: str) -> dict[str, Any]:
    """Translate a PostgreSQL URI, including Cloud SQL Unix-socket URIs, for pg8000."""
    from urllib.parse import parse_qs, unquote, urlparse

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    port = parsed.port or int(query.get("port", ["5432"])[0])
    query_host = query.get("host", [None])[0]
    values: dict[str, Any] = {
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": unquote(parsed.path.lstrip("/")),
    }
    if query_host and query_host.startswith("/"):
        values["unix_sock"] = f"{query_host.rstrip('/')}/.s.PGSQL.{port}"
        # Cloud SQL Auth Proxy already secures the local Unix-socket transport.
        values["ssl_context"] = False
    else:
        values["host"] = query_host or parsed.hostname or "localhost"
        values["port"] = port
        sslmode = query.get("sslmode", ["prefer"])[0]
        values["ssl_context"] = sslmode in {"require", "verify-ca", "verify-full"}
    return values


def _postgres_key_row(row: Any) -> dict[str, Any]:
    scopes = json.loads(row[4]) if isinstance(row[4], str) else row[4]
    return {
        "id": row[0],
        "tenant_id": row[1],
        "name": row[2],
        "key_hash": row[3],
        "scopes": list(scopes),
        "expires_at": row[5],
        "revoked_at": row[6],
        "created_at": row[7],
        "last_used_at": row[8],
    }


def create_store(
    database_url: str,
    *,
    postgres_pool_min_size: int = 1,
    postgres_pool_max_size: int = 5,
    postgres_pool_acquire_timeout_seconds: float = 2.0,
) -> Store:
    if database_url in {"memory://", "sqlite:///:memory:"}:
        store: Store = InMemoryStore()
    elif database_url.startswith("sqlite:///"):
        path = database_url.removeprefix("sqlite:///")
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        store = SQLiteStore(path)
    elif database_url.startswith(("postgresql://", "postgres://")):
        store = PostgresStore(
            database_url,
            pool_min_size=postgres_pool_min_size,
            pool_max_size=postgres_pool_max_size,
            pool_acquire_timeout_seconds=postgres_pool_acquire_timeout_seconds,
        )
    else:
        raise ValueError("PAG_DATABASE_URL must use memory://, sqlite:///, or postgresql://")
    store.initialize()
    return store
