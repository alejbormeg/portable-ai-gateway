#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

usage() {
  printf '%s\n' 'Usage: scripts/check-release-gates.sh --phase candidate|release --version VERSION [--legal-approved true]'
}

phase=""
version=""
legal_approved="false"
while (($#)); do
  case "$1" in
    --phase)
      phase="${2:-}"
      shift 2
      ;;
    --version)
      version="${2:-}"
      shift 2
      ;;
    --legal-approved)
      legal_approved="${2:-false}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

[[ "$phase" == "candidate" || "$phase" == "release" ]] || {
  printf '%s\n' 'error: --phase must be candidate or release' >&2
  exit 2
}
[[ -n "$version" ]] || { printf '%s\n' 'error: --version is required' >&2; exit 2; }
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || {
  printf '%s\n' 'error: version must be OCI-compatible SemVer without build metadata' >&2
  exit 1
}
[[ -f VERSION ]] || { printf '%s\n' 'error: VERSION file is missing' >&2; exit 1; }
declared_version="$(tr -d '[:space:]' < VERSION)"
[[ "$version" == "$declared_version" ]] || {
  printf 'error: tag version %s does not match VERSION %s\n' "$version" "$declared_version" >&2
  exit 1
}

if [[ "$phase" == "release" ]]; then
  [[ "$legal_approved" == "true" ]] || {
    printf '%s\n' 'error: repository variable LEGAL_RELEASE_APPROVED must be true after qualified legal approval' >&2
    exit 1
  }

  poc_record=".ai-ra/pocs/portable-ai-gateway-productization/poc.yaml"
  independent_review=".ai-ra/pocs/portable-ai-gateway-productization/evidence/independent-review.md"
  independent_review_record=".ai-ra/pocs/portable-ai-gateway-productization/evidence/independent-review.yaml"
  [[ -f "$poc_record" && -f "$independent_review" && -f "$independent_review_record" ]] || {
    printf '%s\n' 'error: release requires the complete PoC record and independent review' >&2
    exit 1
  }

  # The committed records describe the local source snapshot and may correctly
  # remain inconclusive. A digest-bound supported review cannot live inside the
  # commit it reviews without creating a self-referential hash cycle. The
  # release workflow therefore verifies the external signed review embedded in
  # the immutable acceptance dossier.
fi

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  printf '%s\n' 'error: release gates require a Git checkout' >&2
  exit 1
}

if [[ "$phase" == "release" ]]; then
  [[ -f THIRD_PARTY_NOTICES.md ]] || {
    printf '%s\n' 'error: THIRD_PARTY_NOTICES.md is missing' >&2
    exit 1
  }
  [[ -f LICENSING.md ]] || {
    printf '%s\n' 'error: LICENSING.md is missing' >&2
    exit 1
  }

  if grep -Eiq 'pending (release )?inventory|pending section|inventory pending|to be completed|tbd|todo' THIRD_PARTY_NOTICES.md; then
    printf '%s\n' 'error: third-party notices still contain a pending marker' >&2
    exit 1
  fi
fi

repository_placeholder_pattern='<YOUR_REPOSITORY_URL>|YOUR_REPOSITORY_URL|your-account|github\.com/OWNER|ghcr\.io/OWNER'
if git grep -n -I -E "$repository_placeholder_pattern" -- . \
  ':(exclude)scripts/check-release-gates.sh' \
  ':(exclude).github/workflows/release.yml' \
  ':(exclude).github/workflows/release-candidate.yml' \
  ':(exclude).github/workflows/candidate-validation.yml' > /tmp/portable-ai-gateway-repository-placeholders.txt; then
  printf '%s\n' 'error: unresolved repository placeholder:' >&2
  sed -n '1,200p' /tmp/portable-ai-gateway-repository-placeholders.txt >&2
  exit 1
fi

identity_placeholder_pattern='\{\{[A-Z][A-Z0-9_]*\}\}'
if [[ "$phase" == "release" ]] && git grep -n -I -E "$identity_placeholder_pattern" -- . \
  ':(exclude)scripts/check-release-gates.sh' \
  ':(exclude).github/workflows/release.yml' \
  ':(exclude).github/workflows/release-candidate.yml' \
  ':(exclude).github/workflows/candidate-validation.yml' > /tmp/portable-ai-gateway-release-placeholders.txt; then
  printf '%s\n' 'error: unresolved release placeholders:' >&2
  sed -n '1,200p' /tmp/portable-ai-gateway-release-placeholders.txt >&2
  exit 1
fi

# Customer-input placeholders remain useful only in these reviewed templates
# and documentation examples. Executable defaults and the packaged Helm chart
# must contain concrete values at release time.
technical_placeholder_pattern='YOUR_GCP_PROJECT|REPLACE_[A-Z0-9_]+|example\.(com|invalid)'
if git grep -n -I -E "$technical_placeholder_pattern" -- . \
  ':(exclude)scripts/check-release-gates.sh' \
  ':(exclude).github/workflows/release.yml' \
  ':(exclude).github/workflows/release-candidate.yml' \
  ':(exclude).github/workflows/candidate-validation.yml' \
  ':(exclude)deploy/config/gcp.yaml' \
  ':(exclude)docs/air-gap.md' \
  ':(exclude)docs/api.md' \
  ':(exclude)docs/deployment/gcp.md' \
  ':(exclude)docs/deployment/on-prem.md' \
  ':(exclude)deploy/config/images.env.example' \
  ':(exclude)infra/terraform/gcp/terraform.tfvars.example' \
  ':(exclude)tests/test_config.py' > /tmp/portable-ai-gateway-release-technical-placeholders.txt; then
  printf '%s\n' 'error: unresolved placeholder in an executable release default:' >&2
  sed -n '1,200p' /tmp/portable-ai-gateway-release-technical-placeholders.txt >&2
  exit 1
fi

if [[ "$phase" == "candidate" ]]; then
  command -v helm >/dev/null 2>&1 || {
    printf '%s\n' 'error: helm is required to inspect the candidate chart' >&2
    exit 1
  }
  chart_gate_dir="$(mktemp -d)"
  cleanup() {
    rm -rf -- "$chart_gate_dir"
  }
  trap cleanup EXIT
  helm package deploy/helm/portable-ai-gateway \
    --version "$version" \
    --app-version "$version" \
    --destination "$chart_gate_dir" >/dev/null
  mkdir -p "$chart_gate_dir/unpacked"
  tar -xzf "$chart_gate_dir/portable-ai-gateway-${version}.tgz" -C "$chart_gate_dir/unpacked"
  if grep -RInE "$repository_placeholder_pattern|$technical_placeholder_pattern" "$chart_gate_dir/unpacked"; then
    printf '%s\n' 'error: packaged candidate Helm chart contains an unresolved placeholder' >&2
    exit 1
  fi
fi

if [[ -n "$(git status --porcelain=v1)" ]]; then
  printf '%s\n' 'error: release checkout is not clean' >&2
  git status --short >&2
  exit 1
fi

if grep -E '^FROM[[:space:]]+' Dockerfile \
  | grep -Ev '@sha256:[0-9a-f]{64}([[:space:]]|$)' \
  | grep -q .; then
  printf '%s\n' 'error: every Dockerfile base image must be pinned by sha256 digest for release' >&2
  exit 1
fi

for lock_file in requirements.lock requirements-build.lock requirements-test.lock; do
  [[ -f "$lock_file" ]] || {
    printf 'error: reviewed release lock is missing: %s\n' "$lock_file" >&2
    exit 1
  }
  grep -q -- '--hash=sha256:' "$lock_file" || {
    printf 'error: release lock must contain artifact hashes: %s\n' "$lock_file" >&2
    exit 1
  }
done
for required_dependency in fastapi httpx pydantic pyyaml uvicorn pg8000 redis google-auth cryptography; do
  grep -Eiq "^${required_dependency}([=<>!~]|[[:space:]])" requirements.lock || {
    printf 'error: requirements.lock does not include required runtime dependency: %s\n' "$required_dependency" >&2
    exit 1
  }
done
grep -Eiq '^hatchling([=<>!~]|[[:space:]])' requirements-build.lock || {
  printf '%s\n' 'error: requirements-build.lock does not include hatchling' >&2
  exit 1
}
for required_dependency in pytest pytest-cov; do
  grep -Eiq "^${required_dependency}([=<>!~]|[[:space:]])" requirements-test.lock || {
    printf 'error: requirements-test.lock does not include required test dependency: %s\n' "$required_dependency" >&2
    exit 1
  }
done

if [[ ! -f infra/terraform/gcp/.terraform.lock.hcl ]]; then
  printf '%s\n' 'error: a reviewed Terraform provider lock is required for the GCP release module' >&2
  exit 1
fi

if grep -Eq 'sha256:(a{64}|b{64}|c{64})' deploy/config/images.env.example; then
  printf '%s\n' 'error: dependency image examples still contain synthetic digest placeholders' >&2
  exit 1
fi

for dependency in PAG_POSTGRES_IMAGE PAG_VALKEY_IMAGE; do
  reference="$(sed -n "s/^${dependency}=//p" deploy/config/images.env.example)"
  [[ "$reference" =~ @sha256:[0-9a-f]{64}$ ]] || {
    printf 'error: %s must be pinned to an immutable digest in deploy/config/images.env.example\n' "$dependency" >&2
    exit 1
  }
done

printf '%s gates passed for %s.\n' "$phase" "$version"
