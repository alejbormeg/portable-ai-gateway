#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

usage() {
  printf '%s\n' \
    'Usage: scripts/validate-release-evidence.sh --result FILE --candidate FILE --target TARGET --repository OWNER/REPO --source-commit SHA --image-index-digest sha256:... --candidate-manifest-sha256 HEX --candidate-artifact-digest sha256:...'
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

result=""
candidate=""
target=""
repository=""
source_commit=""
image_index_digest=""
candidate_manifest_sha256=""
candidate_artifact_digest=""
harness_sha256=""

while (($#)); do
  case "$1" in
    --result) result="${2:-}"; shift 2 ;;
    --candidate) candidate="${2:-}"; shift 2 ;;
    --target) target="${2:-}"; shift 2 ;;
    --repository) repository="${2:-}"; shift 2 ;;
    --source-commit) source_commit="${2:-}"; shift 2 ;;
    --image-index-digest) image_index_digest="${2:-}"; shift 2 ;;
    --candidate-manifest-sha256) candidate_manifest_sha256="${2:-}"; shift 2 ;;
    --candidate-artifact-digest) candidate_artifact_digest="${2:-}"; shift 2 ;;
    --harness-sha256) harness_sha256="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

for value in result candidate target repository source_commit image_index_digest candidate_manifest_sha256 candidate_artifact_digest; do
  [[ -n "${!value}" ]] || die "--${value//_/-} is required"
done
[[ -s "$result" ]] || die "result is missing or empty: $result"
[[ -s "$candidate" ]] || die "candidate is missing or empty: $candidate"
[[ "$target" =~ ^(compose|kubernetes|openshift|gcp|airgap)$ ]] || die "unsupported target: $target"
[[ "$source_commit" =~ ^[0-9a-f]{40}$ ]] || die "source commit is invalid"
[[ "$image_index_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || die "image index digest is invalid"
[[ "$candidate_manifest_sha256" =~ ^[0-9a-f]{64}$ ]] || die "candidate manifest hash is invalid"
[[ "$candidate_artifact_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || die "candidate artifact digest is invalid"
command -v jq >/dev/null 2>&1 || die "jq is required"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"

candidate_dir="$(cd "$(dirname "$candidate")" && pwd)"
evidence_dir="$(cd "$(dirname "$result")" && pwd)"

candidate_hash() {
  local artifact="$1"
  local path expected
  path="$(jq -er --arg artifact "$artifact" '.artifacts[$artifact].path' "$candidate")"
  expected="$(jq -er --arg artifact "$artifact" '.artifacts[$artifact].sha256' "$candidate")"
  [[ "$path" != /* && "$path" != *..* && "$expected" =~ ^[0-9a-f]{64}$ ]] \
    || die "candidate artifact metadata is invalid: $artifact"
  [[ -s "$candidate_dir/$path" ]] || die "candidate artifact is missing: $path"
  [[ "$(sha256sum "$candidate_dir/$path" | cut -d ' ' -f 1)" == "$expected" ]] \
    || die "candidate artifact hash mismatch: $path"
  printf '%s' "$expected"
}

archive_sha="$(candidate_hash image_archive)"
chart_sha="$(candidate_hash chart)"
sbom_sha="$(candidate_hash sbom)"
sbom_arm64_sha="$(candidate_hash sbom_arm64)"
source_sha="$(candidate_hash source)"
amd64_digest="$(jq -er '.image.platforms[] | select(.os == "linux" and .architecture == "amd64" and (.variant // "") == "") | .digest' "$candidate")"
arm64_digest="$(jq -er '.image.platforms[] | select(.os == "linux" and .architecture == "arm64") | .digest' "$candidate")"
[[ "$amd64_digest" =~ ^sha256:[0-9a-f]{64}$ && "$arm64_digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || die "candidate platform digests are invalid"

jq -e \
  --arg target "$target" \
  --arg repository "$repository" \
  --arg commit "$source_commit" \
  --arg digest "$image_index_digest" \
  --arg manifest_sha "$candidate_manifest_sha256" \
  --arg artifact_digest "$candidate_artifact_digest" \
  --arg archive_sha "$archive_sha" \
  --arg chart_sha "$chart_sha" \
  --arg sbom_sha "$sbom_sha" \
  --arg source_sha "$source_sha" \
  '.schema_version == "portable-ai-gateway.dev/release-validation/v2"
   and .status == "supported"
   and .target == $target
   and .repository == $repository
   and .source_commit == $commit
   and .image_index_digest == $digest
   and .candidate_manifest_sha256 == $manifest_sha
   and .candidate_artifact_digest == $artifact_digest
   and .artifacts.image_archive_sha256 == $archive_sha
   and .artifacts.chart_sha256 == $chart_sha
   and .artifacts.sbom_sha256 == $sbom_sha
   and .artifacts.source_archive_sha256 == $source_sha
   and .harness.contract == "portable-ai-gateway.dev/release-validation/v2"
   and (.harness.name | type == "string" and length > 0)
   and (.harness.version | type == "string" and length > 0)
   and (.harness.source_revision | type == "string" and length > 0)
   and (.harness.executable_sha256 | test("^[0-9a-f]{64}$"))
   and .harness.executable_artifact.path == "raw/harness/run"
   and .harness.executable_artifact.sha256 == .harness.executable_sha256
   and .harness.source_archive.path == "raw/harness/source.tar.gz"
   and (.harness.source_archive.sha256 | test("^[0-9a-f]{64}$"))
   and (.platform.name | type == "string" and length > 0)
   and (.platform.version | type == "string" and length > 0)
   and .platform.architecture == "amd64"
   and .acceptance.tests_passed == true
   and .acceptance.deployment_verified == true
   and .acceptance.security_negatives_passed == true
   and .acceptance.audit_privacy_passed == true' \
  "$result" >/dev/null || die "common release-evidence contract failed for $target"

if [[ -n "$harness_sha256" ]]; then
  [[ "$harness_sha256" =~ ^[0-9a-f]{64}$ ]] || die "harness executable hash is invalid"
  [[ "$(jq -er '.harness.executable_sha256' "$result")" == "$harness_sha256" ]] \
    || die "result does not bind the executed harness bytes"
fi

validate_report() {
  local path="$1"
  local expected="$2"
  [[ "$path" =~ ^raw/[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || die "unsafe report path: $path"
  case "/$path/" in
    *"/../"*|*"/./"*|*"//"*) die "non-canonical report path: $path" ;;
  esac
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || die "invalid report hash for $path"
  [[ -s "$evidence_dir/$path" ]] || die "report is missing or empty: $path"
  [[ "$(sha256sum "$evidence_dir/$path" | cut -d ' ' -f 1)" == "$expected" ]] \
    || die "report hash mismatch: $path"
}

validate_report \
  "$(jq -er '.harness.executable_artifact.path' "$result")" \
  "$(jq -er '.harness.executable_artifact.sha256' "$result")"
validate_report \
  "$(jq -er '.harness.source_archive.path' "$result")" \
  "$(jq -er '.harness.source_archive.sha256' "$result")"

case "$target" in
  compose)
    jq -e '.platform.name == "docker-compose"' "$result" >/dev/null \
      || die "Compose platform identity contract failed"
    ;;
  kubernetes)
    jq -e \
      '.platform.name == "kind"
       and (.platform.kubernetes_version | type == "string" and length > 0)
       and .platform.nodes >= 1
       and .portability.provider_switch_verified == true
       and .portability.domain_application_changes_for_provider_swap == 0
       and ([.portability.providers_exercised[]] | sort) == ["mock", "openai-compatible"]
       and (.portability.report.path | type == "string")
       and (.portability.report.sha256 | test("^[0-9a-f]{64}$"))' \
      "$result" >/dev/null || die "kind portability contract failed"
    validate_report \
      "$(jq -er '.portability.report.path' "$result")" \
      "$(jq -er '.portability.report.sha256' "$result")"
    ;;
  openshift)
    jq -e '.platform.name == "openshift" and .platform.security_profile == "restricted"' \
      "$result" >/dev/null || die "OpenShift platform identity contract failed"
    ;;
  gcp)
    jq -e '.platform.name == "gcp-cloud-run"' "$result" >/dev/null \
      || die "GCP platform identity contract failed"
    ;;
  airgap)
    jq -e '.platform.name == "airgap-compose"' "$result" >/dev/null \
      || die "air-gap platform identity contract failed"
    ;;
esac

if [[ "$target" == "compose" ]]; then
  jq -e \
    --arg commit "$source_commit" \
    --arg archive_sha "$archive_sha" \
    --arg sbom_sha "$sbom_sha" \
    --arg sbom_arm64_sha "$sbom_arm64_sha" \
    --arg amd64_digest "$amd64_digest" \
    --arg arm64_digest "$arm64_digest" \
    '.performance.requests_per_second >= 50
     and .performance.simultaneous_streams >= 100
     and .performance.gateway_overhead_p95_ms <= 50
     and (.performance.repetitions | length) >= 3
     and all(.performance.repetitions[];
       .requests_per_second >= 50
       and .successful_request_ratio >= 0.99
       and .simultaneous_streams >= 100
       and .successful_stream_ratio >= 0.99
       and .gateway_overhead_p95_ms <= 50)
     and (.performance.report.path | type == "string")
     and (.performance.report.sha256 | test("^[0-9a-f]{64}$"))
     and .acceptance.license_feature_parity_passed == true
     and .customization.synthetic_brief == true
     and .customization.clean_checkout == true
     and .customization.operator_lead_time_minutes >= 0
     and .customization.operator_lead_time_minutes <= 480
     and .customization.generic_core_source_changes == 0
     and .customization.domain_application_source_changes == 0
     and .customization.composition_build_passed == true
     and .customization.conformance_passed == true
     and ([.customization.providers_exercised[]] | sort) == ["mock", "openai-compatible", "vertex"]
     and (.customization.report.path | type == "string")
     and (.customization.report.sha256 | test("^[0-9a-f]{64}$"))
     and .deterministic_operations.provider == "mock"
     and (.deterministic_operations.seed | type == "string" and length > 0)
     and .deterministic_operations.runs >= 100
     and .deterministic_operations.successful_runs == .deterministic_operations.runs
     and .deterministic_operations.end_to_end_p95_ms <= 2000
     and .deterministic_operations.raw_content_telemetry_events == 0
     and .deterministic_operations.quality.evaluated_runs == .deterministic_operations.runs
     and .deterministic_operations.quality.exact_match_ratio == 1
     and .deterministic_operations.quality.failed_runs == 0
     and (.deterministic_operations.quality.report.path | type == "string")
     and (.deterministic_operations.quality.report.sha256 | test("^[0-9a-f]{64}$"))
     and (.deterministic_operations.resources.cpu_seconds_total | type == "number")
     and .deterministic_operations.resources.cpu_seconds_total >= 0
     and (.deterministic_operations.resources.peak_rss_bytes | type == "number")
     and .deterministic_operations.resources.peak_rss_bytes > 0
     and (.deterministic_operations.resources.report.path | type == "string")
     and (.deterministic_operations.resources.report.sha256 | test("^[0-9a-f]{64}$"))
     and .deterministic_operations.synthetic_cost.budget_approved == true
     and (.deterministic_operations.synthetic_cost.currency | test("^[A-Z]{3}$"))
     and (.deterministic_operations.synthetic_cost.observed_amount | type == "number")
     and .deterministic_operations.synthetic_cost.observed_amount >= 0
     and (.deterministic_operations.synthetic_cost.approved_maximum | type == "number")
     and .deterministic_operations.synthetic_cost.approved_maximum >= 0
     and .deterministic_operations.synthetic_cost.observed_amount <= .deterministic_operations.synthetic_cost.approved_maximum
     and .deterministic_operations.synthetic_cost.budget_violations == 0
     and (.deterministic_operations.synthetic_cost.report.path | type == "string")
     and (.deterministic_operations.synthetic_cost.report.sha256 | test("^[0-9a-f]{64}$"))
     and .release_assurance.traceability_coverage_percent == 100
     and .release_assurance.blocking_findings == {critical: 0, high: 0, non_evaluable: 0}
     and (.release_assurance.report.path | type == "string")
     and (.release_assurance.report.sha256 | test("^[0-9a-f]{64}$"))
     and .artifact_security.accepted_amd64_archive_sha256 == $archive_sha
     and .artifact_security.accepted_amd64_sbom_sha256 == $sbom_sha
     and .artifact_security.accepted_arm64_sbom_sha256 == $sbom_arm64_sha
     and (.artifact_security.platforms | length) == 2
     and ([.artifact_security.platforms[].architecture] | sort) == ["amd64", "arm64"]
     and any(.artifact_security.platforms[]; .architecture == "amd64" and .image_platform_digest == $amd64_digest)
     and any(.artifact_security.platforms[]; .architecture == "arm64" and .image_platform_digest == $arm64_digest)
     and all(.artifact_security.platforms[];
       .os == "linux"
       and .vulnerability.passed == true
       and (.vulnerability.scanner.name | type == "string" and length > 0)
       and (.vulnerability.scanner.version | type == "string" and length > 0)
       and (.vulnerability.scanner.database_updated_at | type == "string" and length > 0)
       and (.vulnerability.scanner.policy | type == "string" and length > 0)
       and (.vulnerability.report.path | type == "string")
       and (.vulnerability.report.sha256 | test("^[0-9a-f]{64}$"))
       and .licenses.passed == true
       and (.licenses.scanner.name | type == "string" and length > 0)
       and (.licenses.scanner.version | type == "string" and length > 0)
       and (.licenses.scanner.policy | type == "string" and length > 0)
       and (.licenses.report.path | type == "string")
       and (.licenses.report.sha256 | test("^[0-9a-f]{64}$"))
       and .sbom.verified == true
       and (.sbom.report.path | type == "string")
       and (.sbom.report.sha256 | test("^[0-9a-f]{64}$"))
       and .provenance.verified == true
       and (.provenance.report.path | type == "string")
       and (.provenance.report.sha256 | test("^[0-9a-f]{64}$")))
     and .reproducibility.status == "supported"
     and .reproducibility.source_commit == $commit
     and .reproducibility.builds_compared >= 2
     and .reproducibility.platform_manifest_digests_match == true
     and ([.reproducibility.rebuilt_platforms[].architecture] | sort) == ["amd64", "arm64"]
     and any(.reproducibility.rebuilt_platforms[]; .architecture == "amd64" and .digest == $amd64_digest)
     and any(.reproducibility.rebuilt_platforms[]; .architecture == "arm64" and .digest == $arm64_digest)
     and (.reproducibility.report.path | type == "string")
     and (.reproducibility.report.sha256 | test("^[0-9a-f]{64}$"))' \
    "$result" >/dev/null || die "Compose release-assurance contract failed"

  while IFS=$'\t' read -r report_path report_sha; do
    validate_report "$report_path" "$report_sha"
  done < <(
    jq -r '
      .artifact_security.platforms[]
      | (.vulnerability.report, .licenses.report, .sbom.report, .provenance.report)
      | [.path, .sha256] | @tsv
    ' "$result"
    jq -r '[.reproducibility.report.path, .reproducibility.report.sha256] | @tsv' "$result"
    jq -r '
      .performance.report,
      .customization.report,
      .deterministic_operations.quality.report,
      .deterministic_operations.resources.report,
      .deterministic_operations.synthetic_cost.report,
      .release_assurance.report
      | [.path, .sha256] | @tsv
    ' "$result"
  )
fi

if [[ "$target" == "airgap" ]]; then
  postgres_archive_sha="$(candidate_hash postgres_image_archive)"
  postgres_signature_sha="$(candidate_hash postgres_image_archive_signature)"
  postgres_sbom_sha="$(candidate_hash postgres_sbom)"
  postgres_manifest_sha="$(candidate_hash postgres_manifest)"
  valkey_archive_sha="$(candidate_hash valkey_image_archive)"
  valkey_signature_sha="$(candidate_hash valkey_image_archive_signature)"
  valkey_sbom_sha="$(candidate_hash valkey_sbom)"
  valkey_manifest_sha="$(candidate_hash valkey_manifest)"
  postgres_reference="$(jq -er '.airgap_dependencies.postgres.reference' "$candidate")"
  valkey_reference="$(jq -er '.airgap_dependencies.valkey.reference' "$candidate")"
  jq -e \
    --arg postgres_reference "$postgres_reference" \
    --arg postgres_archive_sha "$postgres_archive_sha" \
    --arg postgres_signature_sha "$postgres_signature_sha" \
    --arg postgres_sbom_sha "$postgres_sbom_sha" \
    --arg postgres_manifest_sha "$postgres_manifest_sha" \
    --arg valkey_reference "$valkey_reference" \
    --arg valkey_archive_sha "$valkey_archive_sha" \
    --arg valkey_signature_sha "$valkey_signature_sha" \
    --arg valkey_sbom_sha "$valkey_sbom_sha" \
    --arg valkey_manifest_sha "$valkey_manifest_sha" \
    '.acceptance.zero_runtime_egress == true
     and .acceptance.offline_signature_verified == true
     and .acceptance.standalone_compose_verified == true
     and (.dependency_security.images | length) == 2
     and ([.dependency_security.images[].label] | sort) == ["postgres", "valkey"]
     and any(.dependency_security.images[];
       .label == "postgres"
       and .reference == $postgres_reference
       and .image_archive_sha256 == $postgres_archive_sha
       and .candidate_signature_sha256 == $postgres_signature_sha
       and .sbom_sha256 == $postgres_sbom_sha
       and .manifest_sha256 == $postgres_manifest_sha)
     and any(.dependency_security.images[];
       .label == "valkey"
       and .reference == $valkey_reference
       and .image_archive_sha256 == $valkey_archive_sha
       and .candidate_signature_sha256 == $valkey_signature_sha
       and .sbom_sha256 == $valkey_sbom_sha
       and .manifest_sha256 == $valkey_manifest_sha)
     and all(.dependency_security.images[];
       .architecture == "amd64"
       and .vulnerability.passed == true
       and (.vulnerability.scanner.name | type == "string" and length > 0)
       and (.vulnerability.scanner.version | type == "string" and length > 0)
       and (.vulnerability.scanner.database_updated_at | type == "string" and length > 0)
       and (.vulnerability.scanner.policy | type == "string" and length > 0)
       and (.vulnerability.report.sha256 | test("^[0-9a-f]{64}$"))
       and .licenses.passed == true
       and (.licenses.scanner.name | type == "string" and length > 0)
       and (.licenses.scanner.version | type == "string" and length > 0)
       and (.licenses.scanner.policy | type == "string" and length > 0)
       and (.licenses.report.sha256 | test("^[0-9a-f]{64}$"))
       and .sbom.verified == true
       and (.sbom.report.sha256 | test("^[0-9a-f]{64}$"))
       and .signature.verified == true
       and (.signature.report.sha256 | test("^[0-9a-f]{64}$")))' \
    "$result" >/dev/null || die "air-gap evidence contract failed"
  while IFS=$'\t' read -r report_path report_sha; do
    validate_report "$report_path" "$report_sha"
  done < <(
    jq -r '
      .dependency_security.images[]
      | (.vulnerability.report, .licenses.report, .sbom.report, .signature.report)
      | [.path, .sha256] | @tsv
    ' "$result"
  )
fi

find "$evidence_dir/raw" -type f -print -quit 2>/dev/null | grep -q . \
  || die "raw evidence is mandatory for $target"

printf 'Release evidence contract passed for %s.\n' "$target"
