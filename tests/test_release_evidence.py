# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY_ROOT / "scripts" / "validate-release-evidence.sh"
REPOSITORY = "example/portable-ai-gateway"
SOURCE_COMMIT = "1" * 40
IMAGE_INDEX_DIGEST = f"sha256:{'2' * 64}"
CANDIDATE_ARTIFACT_DIGEST = f"sha256:{'3' * 64}"
AMD64_DIGEST = f"sha256:{'4' * 64}"
ARM64_DIGEST = f"sha256:{'5' * 64}"
HARNESS_SHA256 = hashlib.sha256(b"synthetic-release-harness").hexdigest()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_bytes(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return _sha256(content)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _candidate_artifact(candidate_dir: Path, name: str) -> dict[str, str]:
    content = f"synthetic candidate artifact: {name}\n".encode()
    return {"path": name, "sha256": _write_bytes(candidate_dir / name, content)}


def _build_candidate(candidate_dir: Path) -> tuple[Path, dict[str, Any], str]:
    artifact_names = {
        "image_archive": "portable-ai-gateway-amd64.docker.tar",
        "chart": "portable-ai-gateway-chart.tgz",
        "sbom": "portable-ai-gateway-amd64.spdx.json",
        "sbom_arm64": "portable-ai-gateway-arm64.spdx.json",
        "source": "portable-ai-gateway-source.tar.gz",
        "postgres_image_archive": "postgres-amd64.docker.tar",
        "postgres_image_archive_signature": "postgres-amd64.docker.tar.bundle.json",
        "postgres_sbom": "postgres-amd64.spdx.json",
        "postgres_manifest": "postgres-amd64-manifest.json",
        "valkey_image_archive": "valkey-amd64.docker.tar",
        "valkey_image_archive_signature": "valkey-amd64.docker.tar.bundle.json",
        "valkey_sbom": "valkey-amd64.spdx.json",
        "valkey_manifest": "valkey-amd64-manifest.json",
    }
    artifacts = {
        key: _candidate_artifact(candidate_dir, filename)
        for key, filename in artifact_names.items()
    }
    candidate: dict[str, Any] = {
        "image": {
            "index_digest": IMAGE_INDEX_DIGEST,
            "platforms": [
                {
                    "os": "linux",
                    "architecture": "amd64",
                    "digest": AMD64_DIGEST,
                },
                {
                    "os": "linux",
                    "architecture": "arm64",
                    "digest": ARM64_DIGEST,
                },
            ],
        },
        "artifacts": artifacts,
        "airgap_dependencies": {
            "postgres": {"reference": f"postgres@sha256:{'6' * 64}"},
            "valkey": {"reference": f"valkey@sha256:{'7' * 64}"},
        },
    }
    candidate_path = candidate_dir / "candidate.json"
    _write_json(candidate_path, candidate)
    return candidate_path, candidate, _sha256(candidate_path.read_bytes())


def _raw_report(evidence_dir: Path, label: str) -> dict[str, str]:
    relative_path = f"raw/{label}.json"
    content = json.dumps({"evidence": label}, sort_keys=True).encode()
    return {
        "path": relative_path,
        "sha256": _write_bytes(evidence_dir / relative_path, content),
    }


def _raw_artifact(
    evidence_dir: Path,
    relative_path: str,
    content: bytes,
) -> dict[str, str]:
    return {
        "path": relative_path,
        "sha256": _write_bytes(evidence_dir / relative_path, content),
    }


def _scanner(*, vulnerability: bool) -> dict[str, str]:
    scanner = {
        "name": "synthetic-scanner",
        "version": "1.0.0",
        "policy": "no-known-critical-findings",
    }
    if vulnerability:
        scanner["database_updated_at"] = "2026-09-01T00:00:00Z"
    return scanner


def _gateway_platform(
    evidence_dir: Path,
    architecture: str,
    digest: str,
) -> dict[str, Any]:
    prefix = f"gateway-{architecture}"
    return {
        "os": "linux",
        "architecture": architecture,
        "image_platform_digest": digest,
        "vulnerability": {
            "passed": True,
            "scanner": _scanner(vulnerability=True),
            "report": _raw_report(evidence_dir, f"{prefix}-vulnerability"),
        },
        "licenses": {
            "passed": True,
            "scanner": _scanner(vulnerability=False),
            "report": _raw_report(evidence_dir, f"{prefix}-licenses"),
        },
        "sbom": {
            "verified": True,
            "report": _raw_report(evidence_dir, f"{prefix}-sbom"),
        },
        "provenance": {
            "verified": True,
            "report": _raw_report(evidence_dir, f"{prefix}-provenance"),
        },
    }


def _dependency_image(
    evidence_dir: Path,
    candidate: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    artifacts = candidate["artifacts"]
    prefix = f"{label}-amd64"
    return {
        "label": label,
        "reference": candidate["airgap_dependencies"][label]["reference"],
        "architecture": "amd64",
        "image_archive_sha256": artifacts[f"{label}_image_archive"]["sha256"],
        "candidate_signature_sha256": artifacts[
            f"{label}_image_archive_signature"
        ]["sha256"],
        "sbom_sha256": artifacts[f"{label}_sbom"]["sha256"],
        "manifest_sha256": artifacts[f"{label}_manifest"]["sha256"],
        "vulnerability": {
            "passed": True,
            "scanner": _scanner(vulnerability=True),
            "report": _raw_report(evidence_dir, f"{prefix}-vulnerability"),
        },
        "licenses": {
            "passed": True,
            "scanner": _scanner(vulnerability=False),
            "report": _raw_report(evidence_dir, f"{prefix}-licenses"),
        },
        "sbom": {
            "verified": True,
            "report": _raw_report(evidence_dir, f"{prefix}-sbom"),
        },
        "signature": {
            "verified": True,
            "report": _raw_report(evidence_dir, f"{prefix}-signature"),
        },
    }


def _build_result(
    evidence_dir: Path,
    candidate: dict[str, Any],
    candidate_manifest_sha256: str,
    target: str,
) -> dict[str, Any]:
    artifacts = candidate["artifacts"]
    platform_names = {
        "compose": "docker-compose",
        "kubernetes": "kind",
        "openshift": "openshift",
        "gcp": "gcp-cloud-run",
        "airgap": "airgap-compose",
    }
    harness_executable = _raw_artifact(
        evidence_dir,
        "raw/harness/run",
        b"synthetic-release-harness",
    )
    harness_source = _raw_artifact(
        evidence_dir,
        "raw/harness/source.tar.gz",
        b"synthetic harness corresponding source",
    )
    result: dict[str, Any] = {
        "schema_version": "portable-ai-gateway.dev/release-validation/v2",
        "status": "supported",
        "target": target,
        "repository": REPOSITORY,
        "source_commit": SOURCE_COMMIT,
        "image_index_digest": IMAGE_INDEX_DIGEST,
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "candidate_artifact_digest": CANDIDATE_ARTIFACT_DIGEST,
        "artifacts": {
            "image_archive_sha256": artifacts["image_archive"]["sha256"],
            "chart_sha256": artifacts["chart"]["sha256"],
            "sbom_sha256": artifacts["sbom"]["sha256"],
            "source_archive_sha256": artifacts["source"]["sha256"],
        },
        "harness": {
            "contract": "portable-ai-gateway.dev/release-validation/v2",
            "name": "synthetic-release-harness",
            "version": "1.0.0",
            "source_revision": "test-fixture-v1",
            "executable_sha256": HARNESS_SHA256,
            "executable_artifact": harness_executable,
            "source_archive": harness_source,
        },
        "platform": {
            "name": platform_names[target],
            "version": "1.0.0-test",
            "architecture": "amd64",
        },
        "acceptance": {
            "tests_passed": True,
            "deployment_verified": True,
            "security_negatives_passed": True,
            "audit_privacy_passed": True,
        },
    }
    if target == "compose":
        result["acceptance"]["license_feature_parity_passed"] = True
        result.update(
            {
                "performance": {
                    "requests_per_second": 50,
                    "simultaneous_streams": 100,
                    "gateway_overhead_p95_ms": 50,
                    "repetitions": [
                        {
                            "requests_per_second": 50,
                            "successful_request_ratio": 1.0,
                            "simultaneous_streams": 100,
                            "successful_stream_ratio": 1.0,
                            "gateway_overhead_p95_ms": 50,
                        }
                        for _ in range(3)
                    ],
                    "report": _raw_report(evidence_dir, "performance"),
                },
                "customization": {
                    "synthetic_brief": True,
                    "clean_checkout": True,
                    "operator_lead_time_minutes": 60,
                    "generic_core_source_changes": 0,
                    "domain_application_source_changes": 0,
                    "composition_build_passed": True,
                    "conformance_passed": True,
                    "providers_exercised": [
                        "mock",
                        "openai-compatible",
                        "vertex",
                    ],
                    "report": _raw_report(evidence_dir, "customization"),
                },
                "deterministic_operations": {
                    "provider": "mock",
                    "seed": "release-seed-v1",
                    "runs": 100,
                    "successful_runs": 100,
                    "end_to_end_p95_ms": 100,
                    "raw_content_telemetry_events": 0,
                    "quality": {
                        "evaluated_runs": 100,
                        "exact_match_ratio": 1.0,
                        "failed_runs": 0,
                        "report": _raw_report(evidence_dir, "quality"),
                    },
                    "resources": {
                        "cpu_seconds_total": 1.0,
                        "peak_rss_bytes": 1048576,
                        "report": _raw_report(evidence_dir, "resources"),
                    },
                    "synthetic_cost": {
                        "budget_approved": True,
                        "currency": "EUR",
                        "observed_amount": 0.0,
                        "approved_maximum": 0.0,
                        "budget_violations": 0,
                        "report": _raw_report(evidence_dir, "synthetic-cost"),
                    },
                },
                "release_assurance": {
                    "traceability_coverage_percent": 100,
                    "blocking_findings": {
                        "critical": 0,
                        "high": 0,
                        "non_evaluable": 0,
                    },
                    "report": _raw_report(evidence_dir, "traceability"),
                },
                "artifact_security": {
                    "accepted_amd64_archive_sha256": artifacts["image_archive"][
                        "sha256"
                    ],
                    "accepted_amd64_sbom_sha256": artifacts["sbom"]["sha256"],
                    "accepted_arm64_sbom_sha256": artifacts["sbom_arm64"][
                        "sha256"
                    ],
                    "platforms": [
                        _gateway_platform(evidence_dir, "amd64", AMD64_DIGEST),
                        _gateway_platform(evidence_dir, "arm64", ARM64_DIGEST),
                    ],
                },
                "reproducibility": {
                    "status": "supported",
                    "source_commit": SOURCE_COMMIT,
                    "builds_compared": 2,
                    "platform_manifest_digests_match": True,
                    "rebuilt_platforms": [
                        {"architecture": "amd64", "digest": AMD64_DIGEST},
                        {"architecture": "arm64", "digest": ARM64_DIGEST},
                    ],
                    "report": _raw_report(evidence_dir, "gateway-reproducibility"),
                },
            }
        )
    elif target == "kubernetes":
        result["platform"].update(
            {
                "kubernetes_version": "v1.34.0",
                "nodes": 1,
            }
        )
        result["portability"] = {
            "provider_switch_verified": True,
            "domain_application_changes_for_provider_swap": 0,
            "providers_exercised": ["mock", "openai-compatible"],
            "report": _raw_report(evidence_dir, "kind-provider-portability"),
        }
    elif target == "airgap":
        result["acceptance"].update(
            {
                "zero_runtime_egress": True,
                "offline_signature_verified": True,
                "standalone_compose_verified": True,
            }
        )
        result["dependency_security"] = {
            "images": [
                _dependency_image(evidence_dir, candidate, "postgres"),
                _dependency_image(evidence_dir, candidate, "valkey"),
            ]
        }
    else:  # pragma: no cover - fixture misuse
        raise ValueError(f"unsupported fixture target: {target}")
    return result


def _fixture(tmp_path: Path, target: str) -> tuple[Path, list[str]]:
    candidate_path, candidate, candidate_manifest_sha256 = _build_candidate(
        tmp_path / "candidate"
    )
    evidence_dir = tmp_path / target
    result = _build_result(
        evidence_dir,
        candidate,
        candidate_manifest_sha256,
        target,
    )
    result_path = evidence_dir / "result.json"
    _write_json(result_path, result)
    command = [
        str(VALIDATOR),
        "--result",
        str(result_path),
        "--candidate",
        str(candidate_path),
        "--target",
        target,
        "--repository",
        REPOSITORY,
        "--source-commit",
        SOURCE_COMMIT,
        "--image-index-digest",
        IMAGE_INDEX_DIGEST,
        "--candidate-manifest-sha256",
        candidate_manifest_sha256,
        "--candidate-artifact-digest",
        CANDIDATE_ARTIFACT_DIGEST,
        "--harness-sha256",
        HARNESS_SHA256,
    ]
    return result_path, command


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("target", ["compose", "kubernetes", "airgap"])
def test_release_evidence_accepts_complete_bound_reports(
    tmp_path: Path,
    target: str,
) -> None:
    _, command = _fixture(tmp_path, target)

    completed = _run(command)

    assert completed.returncode == 0, completed.stderr
    assert f"Release evidence contract passed for {target}." in completed.stdout


def test_release_evidence_rejects_failed_arm64_scan(tmp_path: Path) -> None:
    result_path, command = _fixture(tmp_path, "compose")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    arm64 = next(
        platform
        for platform in result["artifact_security"]["platforms"]
        if platform["architecture"] == "arm64"
    )
    arm64["vulnerability"]["passed"] = False
    _write_json(result_path, result)

    completed = _run(command)

    assert completed.returncode != 0
    assert "Compose release-assurance contract failed" in completed.stderr


def test_release_evidence_rejects_raw_report_hash_mismatch(tmp_path: Path) -> None:
    result_path, command = _fixture(tmp_path, "airgap")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["dependency_security"]["images"][0]["licenses"]["report"][
        "sha256"
    ] = "0" * 64
    _write_json(result_path, result)

    completed = _run(command)

    assert completed.returncode != 0
    assert "report hash mismatch" in completed.stderr


def test_release_evidence_rejects_budget_violation(tmp_path: Path) -> None:
    result_path, command = _fixture(tmp_path, "compose")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["deterministic_operations"]["synthetic_cost"]["budget_violations"] = 1
    _write_json(result_path, result)

    completed = _run(command)

    assert completed.returncode != 0
    assert "Compose release-assurance contract failed" in completed.stderr


def test_release_evidence_rejects_core_changes_in_customer_composition(
    tmp_path: Path,
) -> None:
    result_path, command = _fixture(tmp_path, "compose")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["customization"]["domain_application_source_changes"] = 1
    _write_json(result_path, result)

    completed = _run(command)

    assert completed.returncode != 0
    assert "Compose release-assurance contract failed" in completed.stderr


def test_release_evidence_rejects_generic_kubernetes_instead_of_kind(
    tmp_path: Path,
) -> None:
    result_path, command = _fixture(tmp_path, "kubernetes")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["platform"]["name"] = "generic-kubernetes"
    _write_json(result_path, result)

    completed = _run(command)

    assert completed.returncode != 0
    assert "kind portability contract failed" in completed.stderr
