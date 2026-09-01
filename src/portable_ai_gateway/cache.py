# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Protocol

from portable_ai_gateway.errors import GatewayError


class Cache(Protocol):
    async def ping(self) -> bool: ...
    async def increment(self, key: str, ttl_seconds: int) -> int: ...
    async def acquire_limit(self, key: str, limit: int, ttl_seconds: int) -> str | None: ...
    async def renew_limit(self, key: str, token: str, ttl_seconds: int) -> bool: ...
    async def release_limit(self, key: str, token: str) -> None: ...
    async def close(self) -> None: ...


class InMemoryCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[int, float]] = {}
        self._leases: dict[str, dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def ping(self) -> bool:
        return True

    async def increment(self, key: str, ttl_seconds: int) -> int:
        async with self._lock:
            count, expires = self._values.get(key, (0, 0.0))
            if expires <= time.monotonic():
                count, expires = 0, time.monotonic() + ttl_seconds
            count += 1
            self._values[key] = (count, expires)
            return count

    async def close(self) -> None:
        self._values.clear()
        self._leases.clear()

    async def acquire_limit(self, key: str, limit: int, ttl_seconds: int) -> str | None:
        async with self._lock:
            now = time.monotonic()
            active = {
                token: expires
                for token, expires in self._leases.get(key, {}).items()
                if expires > now
            }
            if len(active) >= limit:
                self._leases[key] = active
                return None
            token = secrets.token_urlsafe(18)
            active[token] = now + ttl_seconds
            self._leases[key] = active
            return token

    async def release_limit(self, key: str, token: str) -> None:
        async with self._lock:
            active = self._leases.get(key)
            if active is None:
                return
            active.pop(token, None)
            if not active:
                self._leases.pop(key, None)

    async def renew_limit(self, key: str, token: str, ttl_seconds: int) -> bool:
        async with self._lock:
            now = time.monotonic()
            active = {
                active_token: expires
                for active_token, expires in self._leases.get(key, {}).items()
                if expires > now
            }
            if token not in active:
                self._leases[key] = active
                return False
            active[token] = now + ttl_seconds
            self._leases[key] = active
            return True


class RedisCache:
    """Valkey/Redis-compatible adapter loaded only when PAG_CACHE_URL is set."""

    def __init__(self, url: str) -> None:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install portable-ai-gateway[redis] to use Valkey/Redis") from exc
        self._client = Redis.from_url(url, decode_responses=True)

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def increment(self, key: str, ttl_seconds: int) -> int:
        async with self._client.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, ttl_seconds, nx=True)
            result = await pipeline.execute()
        return int(result[0])

    async def close(self) -> None:
        await self._client.aclose()

    async def acquire_limit(self, key: str, limit: int, ttl_seconds: int) -> str | None:
        token = secrets.token_urlsafe(18)
        script = """
        local clock = redis.call('TIME')
        local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
        redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
        if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then return 0 end
        redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
        redis.call('PEXPIRE', KEYS[1], ARGV[2])
        return 1
        """
        acquired = bool(await self._client.eval(script, 1, key, limit, ttl_seconds * 1000, token))
        return token if acquired else None

    async def release_limit(self, key: str, token: str) -> None:
        await self._client.zrem(key, token)

    async def renew_limit(self, key: str, token: str, ttl_seconds: int) -> bool:
        script = """
        local clock = redis.call('TIME')
        local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
        local expiry = redis.call('ZSCORE', KEYS[1], ARGV[2])
        if not expiry or tonumber(expiry) <= now then
          redis.call('ZREM', KEYS[1], ARGV[2])
          return 0
        end
        redis.call('ZADD', KEYS[1], now + tonumber(ARGV[1]), ARGV[2])
        redis.call('PEXPIRE', KEYS[1], ARGV[1])
        return 1
        """
        renewed = await self._client.eval(script, 1, key, ttl_seconds * 1000, token)
        return bool(renewed)


def create_cache(cache_url: str | None) -> Cache:
    if not cache_url or cache_url == "memory://":
        return InMemoryCache()
    if cache_url.startswith(("redis://", "rediss://")):
        return RedisCache(cache_url)
    raise ValueError("PAG_CACHE_URL must use redis://, rediss://, or memory://")


class ConcurrencyLease:
    """A short cache lease renewed while the request remains active."""

    def __init__(self, cache: Cache, key: str, token: str, ttl_seconds: int) -> None:
        self._cache = cache
        self._key = key
        self._token = token
        self._ttl_seconds = ttl_seconds
        self._failure: str | None = None

    async def renew_forever(self, interval_seconds: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                if not await self._cache.renew_limit(self._key, self._token, self._ttl_seconds):
                    self._failure = "lease_lost"
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # The lease expires soon and the data plane must not keep an
            # unverified stream open if its authoritative cache is unavailable.
            self._failure = "renewal_unavailable"

    def ensure_active(self) -> None:
        if self._failure is None:
            return
        raise GatewayError(
            503,
            "Tenant concurrency lease is no longer active",
            "server_error",
            "tenant_concurrency_lease_lost",
            audit_decision=self._failure,
        )


class QuotaEnforcer:
    def __init__(
        self,
        cache: Cache,
        *,
        concurrent_lease_seconds: int = 60,
        renewal_interval_seconds: float | None = None,
    ) -> None:
        if concurrent_lease_seconds < 1:
            raise ValueError("concurrent_lease_seconds must be at least 1")
        if renewal_interval_seconds is not None and renewal_interval_seconds <= 0:
            raise ValueError("renewal_interval_seconds must be positive")
        self._cache = cache
        self._concurrent_lease_seconds = concurrent_lease_seconds
        self._renewal_interval_seconds = renewal_interval_seconds or max(
            1.0, concurrent_lease_seconds / 3
        )

    async def check_rate(self, tenant_id: str, requests_per_minute: int | None) -> None:
        if requests_per_minute is None:
            return
        window = int(time.time() // 60)
        value = await self._cache.increment(f"pag:quota:{tenant_id}:rpm:{window}", 120)
        if value > requests_per_minute:
            raise GatewayError(
                429,
                "Tenant request quota exceeded",
                "rate_limit_error",
                "tenant_rate_limit",
                {"Retry-After": "60"},
            )

    @asynccontextmanager
    async def concurrent(self, tenant_id: str, limit: int | None) -> AsyncIterator[ConcurrencyLease | None]:
        key = f"pag:quota:{tenant_id}:concurrent"
        token = (
            None
            if limit is None
            else await self._cache.acquire_limit(key, limit, self._concurrent_lease_seconds)
        )
        if limit is not None and token is None:
            raise GatewayError(
                429,
                "Tenant concurrency quota exceeded",
                "rate_limit_error",
                "tenant_concurrency_limit",
                {"Retry-After": "1"},
            )
        if token is None:
            yield None
            return
        lease = ConcurrencyLease(self._cache, key, token, self._concurrent_lease_seconds)
        renewal = asyncio.create_task(lease.renew_forever(self._renewal_interval_seconds))
        try:
            yield lease
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal
            await self._cache.release_limit(key, token)
