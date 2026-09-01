# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text()
CANDIDATE = (ROOT / ".github" / "workflows" / "release-candidate.yml").read_text()
VALIDATION = (ROOT / ".github" / "workflows" / "candidate-validation.yml").read_text()
GATES = (ROOT / "scripts" / "check-release-gates.sh").read_text()
REUSE_SIGNATURE = (ROOT / "scripts" / "sign-or-reuse-release-blob.sh").read_text()


def test_release_promotes_without_rebuilding() -> None:
    assert "docker/build-push-action@" not in RELEASE
    assert "docker build " not in RELEASE
    assert CANDIDATE.count("docker/build-push-action@") == 1
    assert "platforms: linux/amd64,linux/arm64" in CANDIDATE


def test_independent_review_is_bound_to_the_released_digest() -> None:
    assert "candidate-independent-review" in VALIDATION
    assert "portable-ai-gateway.dev/independent-review/v1" in VALIDATION
    assert "actions/runs/$GITHUB_RUN_ID/approvals" in VALIDATION
    assert "approval-history.json" in VALIDATION
    assert 'record_path == "review/independent-review.json"' in RELEASE
    assert 'approval_history.path == "approval-history.json"' in RELEASE
    assert "expected_reviewers" in RELEASE
    assert "reviewed_digest" not in GATES


def test_release_signature_retries_reuse_an_exact_verified_bundle() -> None:
    assert "cosign verify-blob" in REUSE_SIGNATURE
    assert "Reused verified release signature asset" in REUSE_SIGNATURE
    assert "scripts/sign-or-reuse-release-blob.sh" in RELEASE
    assert RELEASE.count("scripts/sign-or-reuse-release-blob.sh") == 3


def test_release_version_rejects_oci_incompatible_build_metadata() -> None:
    version_gate = next(line for line in GATES.splitlines() if '"$version" =~' in line)
    assert "[+-]" not in version_gate
    assert "(-[0-9A-Za-z.-]+)?" in version_gate


def test_release_signs_and_verifies_both_platforms() -> None:
    assert 'cosign sign --yes "$IMAGE_REPOSITORY@$AMD64_DIGEST"' in RELEASE
    assert 'cosign sign --yes "$IMAGE_REPOSITORY@$ARM64_DIGEST"' in RELEASE
    assert 'for digest in "$IMAGE_DIGEST" "$AMD64_DIGEST" "$ARM64_DIGEST"' in RELEASE


def test_publication_is_draft_first_and_refuses_different_existing_assets() -> None:
    assert 'gh release create "$GITHUB_REF_NAME"' in RELEASE
    assert "--draft" in RELEASE
    assert 'cmp --silent "$asset" "$downloaded_dir/$name"' in RELEASE
    assert 'gh release edit "$GITHUB_REF_NAME"' in RELEASE
    assert "--draft=false" in RELEASE
    assert "--clobber" not in RELEASE

    assert "PAG_VALIDATION_HARNESS_SHA256" in VALIDATION
    assert "PAG_VALIDATION_HARNESS_SOURCE_SHA256" in VALIDATION
    assert '[[ "$harness_sha256" == "$EXPECTED_HARNESS_SHA256" ]]' in VALIDATION
