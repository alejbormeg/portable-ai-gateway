#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/verify-airgap-bundle.sh [--standalone] --signature BUNDLE.sigstore.json BUNDLE.tar.gz
  scripts/verify-airgap-bundle.sh [--standalone] --checksums-only BUNDLE.tar.gz

The default mode requires offline Sigstore verification of the complete bundle
and every included image archive. Set COSIGN_CERTIFICATE_IDENTITY to the exact
approved release workflow identity. If accepted image archives were signed by a
different workflow, set COSIGN_ARCHIVE_CERTIFICATE_IDENTITY (or its reviewed
REGEXP variant) to that exact signer. Issuers default to GitHub Actions OIDC.

--checksums-only is an explicit non-production format/debug mode. It validates
archive structure and checksums but can never establish publisher authenticity.
--standalone additionally requires exactly gateway, PostgreSQL and Valkey image
records, as used by the official release bundle.
EOF
}

bundle=""
bundle_signature=""
checksums_only="false"
standalone="false"
while (($#)); do
  case "$1" in
    --signature)
      (($# >= 2)) || { printf '%s\n' 'error: --signature requires a file' >&2; exit 2; }
      bundle_signature="$2"
      shift 2
      ;;
    --checksums-only)
      checksums_only="true"
      shift
      ;;
    --standalone)
      standalone="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      printf 'error: unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
    *)
      [[ -z "$bundle" ]] || { printf '%s\n' 'error: exactly one bundle path is required' >&2; exit 2; }
      bundle="$1"
      shift
      ;;
  esac
done

[[ -n "$bundle" ]] || { usage >&2; exit 2; }
[[ -f "$bundle" ]] || { printf 'error: bundle not found: %s\n' "$bundle" >&2; exit 1; }
if [[ "$checksums_only" == "true" ]]; then
  [[ -z "$bundle_signature" ]] || {
    printf '%s\n' 'error: --signature and --checksums-only are mutually exclusive' >&2
    exit 2
  }
else
  [[ -n "$bundle_signature" && -f "$bundle_signature" ]] || {
    printf '%s\n' 'error: production verification requires --signature BUNDLE.sigstore.json' >&2
    exit 1
  }
  command -v cosign >/dev/null 2>&1 || {
    printf '%s\n' 'error: cosign is required for production verification' >&2
    exit 1
  }
  identity_args=()
  if [[ -n "${COSIGN_CERTIFICATE_IDENTITY:-}" ]]; then
    identity_args=(--certificate-identity "$COSIGN_CERTIFICATE_IDENTITY")
  elif [[ -n "${COSIGN_CERTIFICATE_IDENTITY_REGEXP:-}" ]]; then
    identity_args=(--certificate-identity-regexp "$COSIGN_CERTIFICATE_IDENTITY_REGEXP")
  else
    printf '%s\n' 'error: COSIGN_CERTIFICATE_IDENTITY or COSIGN_CERTIFICATE_IDENTITY_REGEXP is required' >&2
    exit 1
  fi
  issuer="${COSIGN_CERTIFICATE_OIDC_ISSUER:-https://token.actions.githubusercontent.com}"
  cosign verify-blob --offline \
    --bundle "$bundle_signature" \
    "${identity_args[@]}" \
    --certificate-oidc-issuer "$issuer" \
    "$bundle"

  archive_identity_args=("${identity_args[@]}")
  if [[ -n "${COSIGN_ARCHIVE_CERTIFICATE_IDENTITY:-}" ]]; then
    archive_identity_args=(--certificate-identity "$COSIGN_ARCHIVE_CERTIFICATE_IDENTITY")
  elif [[ -n "${COSIGN_ARCHIVE_CERTIFICATE_IDENTITY_REGEXP:-}" ]]; then
    archive_identity_args=(--certificate-identity-regexp "$COSIGN_ARCHIVE_CERTIFICATE_IDENTITY_REGEXP")
  fi
  archive_issuer="${COSIGN_ARCHIVE_CERTIFICATE_OIDC_ISSUER:-$issuer}"
fi

# Treat a transferred archive as hostile until its structure and independently
# communicated checksum have been reviewed. The project bundle format contains
# regular files/directories only and never needs links or special devices.
while IFS= read -r member; do
  case "$member" in
    /*|../*|*/../*|*/..)
      printf 'error: unsafe archive path: %s\n' "$member" >&2
      exit 1
      ;;
  esac
done < <(tar -tzf "$bundle")
if tar -tvzf "$bundle" | awk 'substr($1, 1, 1) != "-" && substr($1, 1, 1) != "d" { found=1 } END { exit !found }'; then
  printf '%s\n' 'error: archive contains a link or special file' >&2
  exit 1
fi

work_dir="$(mktemp -d)"
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT

tar --no-same-owner --no-same-permissions -xzf "$bundle" -C "$work_dir"
mapfile -t roots < <(find "$work_dir" -mindepth 1 -maxdepth 1 -type d -print)
((${#roots[@]} == 1)) || { printf 'error: expected one bundle root\n' >&2; exit 1; }
bundle_root="${roots[0]}"

checksum_manifest="$bundle_root/checksums/SHA256SUMS"
[[ -s "$checksum_manifest" ]] || {
  printf '%s\n' 'error: checksum manifest is missing or empty' >&2
  exit 1
}
if grep -Ev '^[0-9a-f]{64}  \./[A-Za-z0-9._/-]+$' "$checksum_manifest" | grep -q .; then
  printf '%s\n' 'error: checksum manifest contains an unsafe entry' >&2
  exit 1
fi
if grep -Eq '(^|/)\.\.(/|$)|  \./checksums/SHA256SUMS$' "$checksum_manifest"; then
  printf '%s\n' 'error: checksum manifest contains a non-canonical entry' >&2
  exit 1
fi
(
  cd "$bundle_root"
  sha256sum --check checksums/SHA256SUMS
)

manifest="$bundle_root/manifest/images.tsv"
[[ -s "$manifest" ]] || { printf '%s\n' 'error: image manifest is missing or empty' >&2; exit 1; }
expected_header=$'label\treference\tarchive\tsbom\tsignature'
[[ "$(head -n 1 "$manifest")" == "$expected_header" ]] || {
  printf '%s\n' 'error: image manifest header is invalid' >&2
  exit 1
}
image_count=0
declare -A seen_labels=()
while IFS=$'\t' read -r label reference archive sbom signature extra; do
  [[ "$label" != "label" ]] || continue
  [[ "$label" =~ ^[a-z0-9][a-z0-9._-]*$ && -n "$reference" && -z "${extra:-}" ]] || {
    printf 'error: invalid image manifest row for label: %s\n' "$label" >&2
    exit 1
  }
  [[ -z "${seen_labels[$label]:-}" ]] || {
    printf 'error: duplicate image manifest label: %s\n' "$label" >&2
    exit 1
  }
  seen_labels[$label]=1
  [[ "$reference" =~ @sha256:[0-9a-f]{64}$ ]] || {
    printf 'error: image reference is not digest-pinned: %s\n' "$reference" >&2
    exit 1
  }
  [[ "$archive" == "images/${label}.tar" && "$sbom" == "sbom/${label}.spdx.json" && "$signature" == "signatures/${label}.sigstore.json" ]] || {
    printf 'error: image manifest paths are not canonical for: %s\n' "$label" >&2
    exit 1
  }
  [[ -s "$bundle_root/$archive" && -s "$bundle_root/$sbom" && -s "$bundle_root/$signature" ]] || {
    printf 'error: image evidence is missing or empty for: %s\n' "$label" >&2
    exit 1
  }
  grep -q '"spdxVersion"[[:space:]]*:[[:space:]]*"SPDX-' "$bundle_root/$sbom" || {
    printf 'error: SBOM is not an SPDX JSON document for: %s\n' "$label" >&2
    exit 1
  }
  image_count=$((image_count + 1))
done < "$manifest"
((image_count > 0)) || { printf '%s\n' 'error: bundle contains no image records' >&2; exit 1; }
if [[ "$standalone" == "true" ]]; then
  ((image_count == 3)) || {
    printf '%s\n' 'error: standalone bundle requires exactly three image records' >&2
    exit 1
  }
  for required_label in gateway postgres valkey; do
    [[ -n "${seen_labels[$required_label]:-}" ]] || {
      printf 'error: standalone bundle is missing image label: %s\n' "$required_label" >&2
      exit 1
    }
  done
fi

compgen -G "$bundle_root/chart/portable-ai-gateway-*.tgz" >/dev/null || {
  printf '%s\n' 'error: packaged Helm chart is missing' >&2
  exit 1
}
[[ -s "$bundle_root/legal/LICENSE" && -s "$bundle_root/legal/THIRD_PARTY_NOTICES" ]] || {
  printf '%s\n' 'error: legal material is missing' >&2
  exit 1
}
compgen -G "$bundle_root/source/*" >/dev/null || {
  printf '%s\n' 'error: corresponding-source archive is missing' >&2
  exit 1
}
[[ -s "$bundle_root/deployment/compose.yaml" && -s "$bundle_root/deployment/provider-egress.yaml" ]] || {
  printf '%s\n' 'error: standalone Compose deployment material is missing' >&2
  exit 1
}
if grep -Eq '^[[:space:]]*build:' "$bundle_root/deployment/compose.yaml"; then
  printf '%s\n' 'error: air-gap Compose must use imported images and may not require a source build' >&2
  exit 1
fi
grep -q '\.\./config/development\.yaml:' "$bundle_root/deployment/compose.yaml" || {
  printf '%s\n' 'error: air-gap Compose does not bind the bundled development configuration' >&2
  exit 1
}
grep -q '\.\./config/on-prem\.yaml:' "$bundle_root/deployment/provider-egress.yaml" || {
  printf '%s\n' 'error: air-gap provider override does not bind the bundled on-prem configuration' >&2
  exit 1
}
compgen -G "$bundle_root/config/*.yaml" >/dev/null || {
  printf '%s\n' 'error: versioned configuration profiles are missing' >&2
  exit 1
}

if [[ "$checksums_only" == "false" ]]; then
  while IFS=$'\t' read -r label _ archive _ signature; do
    [[ "$label" != "label" ]] || continue
    cosign verify-blob --offline \
      --bundle "$bundle_root/$signature" \
      "${archive_identity_args[@]}" \
      --certificate-oidc-issuer "$archive_issuer" \
      "$bundle_root/$archive"
  done < "$manifest"
  printf '%s\n' 'Air-gap bundle checksum, structure and Sigstore verification passed.'
else
  printf '%s\n' 'Checksum-only structure verification passed; publisher authenticity was explicitly NOT verified.'
fi
