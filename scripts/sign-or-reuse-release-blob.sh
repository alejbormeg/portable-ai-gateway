#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/sign-or-reuse-release-blob.sh \
    --payload FILE \
    --bundle FILE.sigstore.json \
    --identity EXACT_CERTIFICATE_IDENTITY

GITHUB_REPOSITORY, GITHUB_REF_NAME and GH_TOKEN must identify the release. If a
release asset with the bundle filename already exists, it is downloaded and
accepted only when it verifies the exact payload under the exact identity.
Otherwise a new keyless bundle is produced and immediately verified.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

payload=""
bundle=""
identity=""
while (($#)); do
  case "$1" in
    --payload)
      (($# >= 2)) || die "--payload requires a value"
      payload="$2"
      shift 2
      ;;
    --bundle)
      (($# >= 2)) || die "--bundle requires a value"
      bundle="$2"
      shift 2
      ;;
    --identity)
      (($# >= 2)) || die "--identity requires a value"
      identity="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -f "$payload" ]] || die "payload not found: $payload"
[[ -n "$bundle" && -n "$identity" ]] || die "--bundle and --identity are required"
[[ -n "${GITHUB_REPOSITORY:-}" && "$GITHUB_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || die "GITHUB_REPOSITORY must be owner/repository"
[[ -n "${GITHUB_REF_NAME:-}" && "$GITHUB_REF_NAME" =~ ^v[0-9A-Za-z][0-9A-Za-z._-]*$ ]] \
  || die "GITHUB_REF_NAME must be a safe release tag"
[[ -n "${GH_TOKEN:-}" ]] || die "GH_TOKEN is required"
command -v cosign >/dev/null 2>&1 || die "cosign is required"
command -v gh >/dev/null 2>&1 || die "gh is required"
command -v jq >/dev/null 2>&1 || die "jq is required"
mkdir -p "$(dirname "$bundle")"

work_dir="$(mktemp -d)"
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT

release_json="$work_dir/release.json"
release_error="$work_dir/release.error"
release_exists="false"
if gh api "repos/$GITHUB_REPOSITORY/releases/tags/$GITHUB_REF_NAME" \
  > "$release_json" 2> "$release_error"; then
  release_exists="true"
elif ! grep -Fq '(HTTP 404)' "$release_error"; then
  cat "$release_error" >&2
  die "could not determine whether the release already exists"
fi

issuer="${COSIGN_CERTIFICATE_OIDC_ISSUER:-https://token.actions.githubusercontent.com}"
if [[ "$release_exists" == "true" ]]; then
  asset_name="$(basename "$bundle")"
  asset_count="$(jq --arg name "$asset_name" \
    '[.assets[] | select(.name == $name)] | length' "$release_json")"
  [[ "$asset_count" == "0" || "$asset_count" == "1" ]] \
    || die "release contains duplicate asset: $asset_name"
  if [[ "$asset_count" == "1" ]]; then
    asset_id="$(jq -er --arg name "$asset_name" \
      '.assets[] | select(.name == $name) | .id' "$release_json")"
    downloaded="$work_dir/$asset_name"
    gh api \
      --header 'Accept: application/octet-stream' \
      "repos/$GITHUB_REPOSITORY/releases/assets/$asset_id" \
      > "$downloaded"
    cosign verify-blob \
      --bundle "$downloaded" \
      --certificate-identity "$identity" \
      --certificate-oidc-issuer "$issuer" \
      "$payload" >/dev/null
    cp -- "$downloaded" "$bundle"
    printf 'Reused verified release signature asset: %s\n' "$asset_name"
    exit 0
  fi
fi

cosign sign-blob --yes --bundle "$bundle" "$payload"
cosign verify-blob \
  --bundle "$bundle" \
  --certificate-identity "$identity" \
  --certificate-oidc-issuer "$issuer" \
  "$payload" >/dev/null
printf 'Created and verified release signature asset: %s\n' "$(basename "$bundle")"
