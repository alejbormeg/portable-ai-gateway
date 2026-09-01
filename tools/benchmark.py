# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


@dataclass(slots=True)
class Measurement:
    total: int
    successful: int
    elapsed_seconds: float
    latency_ms: list[float]
    max_active: int = 0
    first_byte_ms: list[float] | None = None

    def summary(self) -> dict[str, Any]:
        ratio = self.successful / self.total if self.total else 0.0
        result: dict[str, Any] = {
            "total": self.total,
            "successful": self.successful,
            "success_ratio": round(ratio, 6),
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "completed_requests_per_second": round(self.successful / self.elapsed_seconds, 3)
            if self.elapsed_seconds
            else 0.0,
            "latency_ms": _percentiles(self.latency_ms),
        }
        if self.first_byte_ms is not None:
            result["max_active"] = self.max_active
            result["first_byte_ms"] = _percentiles(self.first_byte_ms)
        return result


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _percentiles(values: list[float]) -> dict[str, float | None]:
    return {
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "mean": round(statistics.fmean(values), 3) if values else None,
    }


def _validated_url(value: str, allow_non_loopback: bool) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/v1/chat/completions")
    ):
        raise argparse.ArgumentTypeError("URL must be a credential-free Chat Completions endpoint")
    if not allow_non_loopback:
        host = parsed.hostname.lower()
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            raise argparse.ArgumentTypeError("non-loopback URL requires --allow-non-loopback")
    return value


def _payload(stream: bool) -> dict[str, Any]:
    return {
        "model": "benchmark-chat",
        "messages": [{"role": "user", "content": "synthetic-benchmark"}],
        "stream": stream,
    }


async def _warmup(client: httpx.AsyncClient, url: str, headers: dict[str, str], count: int) -> None:
    for _ in range(count):
        response = await client.post(url, headers=headers, json=_payload(False))
        if response.status_code != 200:
            raise RuntimeError(f"warm-up failed with HTTP {response.status_code}")


async def _measure_rate(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    offered_rps: float,
    duration_seconds: float,
) -> Measurement:
    total = max(1, math.ceil(offered_rps * duration_seconds))
    latencies: list[float] = []
    successful = 0
    started = time.perf_counter()

    async def request_at(index: int) -> None:
        nonlocal successful
        due = started + index / offered_rps
        await asyncio.sleep(max(0.0, due - time.perf_counter()))
        request_started = time.perf_counter()
        try:
            response = await client.post(url, headers=headers, json=_payload(False))
            valid = response.status_code == 200
            if valid:
                body = response.json()
                valid = isinstance(body, dict) and body.get("object") == "chat.completion"
            if valid:
                successful += 1
        except (httpx.HTTPError, ValueError):
            pass
        finally:
            latencies.append((time.perf_counter() - request_started) * 1000)

    await asyncio.gather(*(request_at(index) for index in range(total)))
    return Measurement(total, successful, time.perf_counter() - started, latencies)


async def _measure_streams(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    concurrency: int,
) -> Measurement:
    latencies: list[float] = []
    first_bytes: list[float] = []
    successful = 0
    active = 0
    max_active = 0
    started = time.perf_counter()

    async def stream_one() -> None:
        nonlocal active, max_active, successful
        request_started = time.perf_counter()
        entered = False
        try:
            async with client.stream("POST", url, headers=headers, json=_payload(True)) as response:
                if response.status_code != 200 or not response.headers.get("content-type", "").startswith(
                    "text/event-stream"
                ):
                    return
                active += 1
                entered = True
                max_active = max(max_active, active)
                iterator = response.aiter_raw()
                first = await anext(iterator)
                first_bytes.append((time.perf_counter() - request_started) * 1000)
                complete = bool(first)
                async for chunk in iterator:
                    complete = complete and bool(chunk)
                if complete:
                    successful += 1
        except (httpx.HTTPError, StopAsyncIteration):
            pass
        finally:
            if entered:
                active -= 1
            latencies.append((time.perf_counter() - request_started) * 1000)

    await asyncio.gather(*(stream_one() for _ in range(concurrency)))
    return Measurement(
        concurrency,
        successful,
        time.perf_counter() - started,
        latencies,
        max_active,
        first_bytes,
    )


async def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    baseline_url = _validated_url(args.baseline_url, args.allow_non_loopback)
    gateway_url = _validated_url(args.gateway_url, args.allow_non_loopback)
    bearer = os.getenv(args.bearer_env) if args.bearer_env else None
    if args.bearer_env and bearer is None:
        raise RuntimeError(f"bearer environment variable is unset: {args.bearer_env}")
    baseline_headers = {"Content-Type": "application/json"}
    gateway_headers = dict(baseline_headers)
    if bearer:
        gateway_headers["Authorization"] = f"Bearer {bearer}"
    timeout = httpx.Timeout(args.timeout_seconds)
    limits = httpx.Limits(max_connections=max(args.streams * 2, 256), max_keepalive_connections=128)
    repetitions: list[dict[str, Any]] = []
    baseline_valid = True
    gateway_valid = True
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        await _warmup(client, baseline_url, baseline_headers, args.warmup_requests)
        await _warmup(client, gateway_url, gateway_headers, args.warmup_requests)
        for index in range(args.repetitions):
            if index % 2:
                gateway_rate = await _measure_rate(
                    client, gateway_url, gateway_headers, args.offered_rps, args.duration_seconds
                )
                baseline_rate = await _measure_rate(
                    client, baseline_url, baseline_headers, args.offered_rps, args.duration_seconds
                )
            else:
                baseline_rate = await _measure_rate(
                    client, baseline_url, baseline_headers, args.offered_rps, args.duration_seconds
                )
                gateway_rate = await _measure_rate(
                    client, gateway_url, gateway_headers, args.offered_rps, args.duration_seconds
                )
            baseline_streams = await _measure_streams(
                client, baseline_url, baseline_headers, args.streams
            )
            gateway_streams = await _measure_streams(
                client, gateway_url, gateway_headers, args.streams
            )
            baseline_summary = baseline_rate.summary()
            gateway_summary = gateway_rate.summary()
            baseline_stream_summary = baseline_streams.summary()
            gateway_stream_summary = gateway_streams.summary()
            baseline_p95 = baseline_summary["latency_ms"]["p95"]
            gateway_p95 = gateway_summary["latency_ms"]["p95"]
            overhead = round(float(gateway_p95) - float(baseline_p95), 3)
            baseline_ok = (
                baseline_summary["success_ratio"] >= args.minimum_success_ratio
                and baseline_stream_summary["success_ratio"] >= args.minimum_success_ratio
                and baseline_stream_summary["max_active"] >= args.streams
            )
            gateway_ok = (
                gateway_summary["success_ratio"] >= args.minimum_success_ratio
                and gateway_summary["completed_requests_per_second"] >= args.minimum_rps
                and gateway_stream_summary["success_ratio"] >= args.minimum_success_ratio
                and gateway_stream_summary["max_active"] >= args.streams
                and overhead <= args.maximum_overhead_p95_ms
            )
            baseline_valid = baseline_valid and baseline_ok
            gateway_valid = gateway_valid and gateway_ok
            repetitions.append(
                {
                    "repetition": index + 1,
                    "baseline_rate": baseline_summary,
                    "gateway_rate": gateway_summary,
                    "baseline_streams": baseline_stream_summary,
                    "gateway_streams": gateway_stream_summary,
                    "gateway_added_p95_ms": overhead,
                    "baseline_valid": baseline_ok,
                    "gateway_thresholds_met": gateway_ok,
                }
            )
    status = "supported" if baseline_valid and gateway_valid else "inconclusive" if not baseline_valid else "rejected"
    exit_code = 0 if status == "supported" else 2 if status == "inconclusive" else 1
    return (
        {
            "schema_version": "pag.benchmark/v1",
            "status": status,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "parameters": {
                "offered_rps": args.offered_rps,
                "minimum_completed_rps": args.minimum_rps,
                "duration_seconds": args.duration_seconds,
                "streams": args.streams,
                "minimum_success_ratio": args.minimum_success_ratio,
                "maximum_gateway_added_p95_ms": args.maximum_overhead_p95_ms,
                "warmup_requests": args.warmup_requests,
                "repetitions": args.repetitions,
            },
            "repetitions": repetitions,
        },
        exit_code,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic paired gateway benchmark")
    parser.add_argument(
        "--baseline-url", default="http://127.0.0.1:9000/v1/chat/completions"
    )
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8080/v1/chat/completions")
    parser.add_argument("--allow-non-loopback", action="store_true")
    parser.add_argument("--bearer-env")
    parser.add_argument("--offered-rps", type=float, default=60.0)
    parser.add_argument("--minimum-rps", type=float, default=50.0)
    parser.add_argument("--duration-seconds", type=float, default=10.0)
    parser.add_argument("--streams", type=int, default=100)
    parser.add_argument("--minimum-success-ratio", type=float, default=0.99)
    parser.add_argument("--maximum-overhead-p95-ms", type=float, default=50.0)
    parser.add_argument("--warmup-requests", type=int, default=20)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.offered_rps <= 0 or args.minimum_rps <= 0 or args.duration_seconds <= 0:
        parser.error("rates and duration must be positive")
    if args.streams < 1 or args.warmup_requests < 0 or args.repetitions < 1:
        parser.error("streams/repetitions must be positive and warm-up non-negative")
    if not 0 < args.minimum_success_ratio <= 1 or args.maximum_overhead_p95_ms < 0:
        parser.error("success ratio or overhead threshold is invalid")
    try:
        report, exit_code = asyncio.run(_run(args))
    except (RuntimeError, argparse.ArgumentTypeError, httpx.HTTPError) as exc:
        parser.exit(2, f"benchmark inconclusive: {exc}\n")
    serialized = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n")
    print(serialized)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
