# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CREATE = ROOT / "scripts" / "create-airgap-bundle.sh"
VERIFY = ROOT / "scripts" / "verify-airgap-bundle.sh"
SPDX_FIXTURE = ROOT / "tests" / "fixtures" / "minimal.spdx.json"


def _create_bundle(tmp_path: Path, labels: tuple[str, ...]) -> Path:
    chart = tmp_path / "portable-ai-gateway-0.1.0-alpha.tgz"
    chart.write_bytes(b"synthetic chart archive")
    output = tmp_path / "bundle.tar.gz"
    command = [
        str(CREATE),
        "--version",
        "0.1.0-alpha",
        "--source-archive",
        str(ROOT / "LICENSE"),
        "--chart-archive",
        str(chart),
        "--output",
        str(output),
    ]
    digests = {"gateway": "a", "postgres": "b", "valkey": "c"}
    for label in labels:
        command.extend(
            [
                "--image",
                f"{label}|registry.invalid/{label}@sha256:{digests[label] * 64}|{ROOT / 'Dockerfile'}",
                "--sbom",
                f"{label}|{SPDX_FIXTURE}",
                "--signature",
                f"{label}|{SPDX_FIXTURE}",
            ]
        )
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    return output


def test_standalone_bundle_contains_image_only_relocatable_compose(tmp_path: Path) -> None:
    bundle = _create_bundle(tmp_path, ("gateway", "postgres", "valkey"))
    subprocess.run(
        [str(VERIFY), "--standalone", "--checksums-only", str(bundle)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    with tarfile.open(bundle, "r:gz") as archive:
        root = "portable-ai-gateway-airgap-0.1.0-alpha"
        compose = archive.extractfile(f"{root}/deployment/compose.yaml")
        override = archive.extractfile(f"{root}/deployment/provider-egress.yaml")
        assert compose is not None and override is not None
        compose_text = compose.read().decode()
        override_text = override.read().decode()
    assert "build:" not in compose_text
    assert "../config/development.yaml" in compose_text
    assert "../config/on-prem.yaml" in override_text


def test_standalone_verification_rejects_gateway_only_bundle(tmp_path: Path) -> None:
    bundle = _create_bundle(tmp_path, ("gateway",))
    completed = subprocess.run(
        [str(VERIFY), "--standalone", "--checksums-only", str(bundle)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "exactly three image records" in completed.stderr
