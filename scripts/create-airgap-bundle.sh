#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

usage() {
  cat <<'EOF'
Create a deterministic, self-contained air-gap bundle from local artifacts.
This script never invokes a container registry or downloads dependencies.

Usage:
  scripts/create-airgap-bundle.sh \
    --version 0.1.0-alpha \
    --image 'gateway|ghcr.io/owner/portable-ai-gateway@sha256:...|/path/gateway.oci.tar' \
    --sbom 'gateway|/path/gateway.spdx.json' \
    --signature 'gateway|/path/gateway.sigstore.json' \
    --source-archive /path/portable-ai-gateway-source.tar.gz \
    [--image 'postgres|docker.io/library/postgres:17.6-alpine@sha256:...|/path/postgres.tar'] \
    [--sbom 'postgres|/path/postgres.spdx.json'] \
    [--signature 'postgres|/path/postgres.sigstore.json'] \
    [--chart-dir deploy/helm/portable-ai-gateway] \
    [--chart-archive /path/portable-ai-gateway-VERSION.tgz] \
    [--config-dir deploy/config] \
    [--compose-file deploy/airgap/compose.yaml] \
    [--compose-override deploy/airgap/provider-egress.yaml] \
    [--output artifacts/portable-ai-gateway-airgap-0.1.0-alpha.tar.gz]

Each image label must have exactly one local SBOM and Sigstore bundle. OCI or
Docker archives are accepted as opaque files; the declared reference is stored
in the inventory. Supply PostgreSQL and Valkey archives for a fully standalone
Compose installation. Paths and labels may not contain '|'.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || die "required file not found: $1"
}

require_dir() {
  [[ -d "$1" ]] || die "required directory not found: $1"
}

version=""
chart_dir="deploy/helm/portable-ai-gateway"
chart_archive=""
config_dir="deploy/config"
compose_file="deploy/airgap/compose.yaml"
compose_override="deploy/airgap/provider-egress.yaml"
license_file="LICENSE"
notices_file="THIRD_PARTY_NOTICES.md"
source_archive=""
output=""
declare -a images=()
declare -a sboms=()
declare -a signatures=()

while (($#)); do
  case "$1" in
    --version)
      (($# >= 2)) || die "--version requires a value"
      version="$2"
      shift 2
      ;;
    --image)
      (($# >= 2)) || die "--image requires label|reference|path"
      images+=("$2")
      shift 2
      ;;
    --sbom)
      (($# >= 2)) || die "--sbom requires label|path"
      sboms+=("$2")
      shift 2
      ;;
    --signature)
      (($# >= 2)) || die "--signature requires label|path"
      signatures+=("$2")
      shift 2
      ;;
    --source-archive)
      (($# >= 2)) || die "--source-archive requires a path"
      source_archive="$2"
      shift 2
      ;;
    --chart-dir)
      (($# >= 2)) || die "--chart-dir requires a path"
      chart_dir="$2"
      shift 2
      ;;
    --chart-archive)
      (($# >= 2)) || die "--chart-archive requires a path"
      chart_archive="$2"
      shift 2
      ;;
    --config-dir)
      (($# >= 2)) || die "--config-dir requires a path"
      config_dir="$2"
      shift 2
      ;;
    --compose-file)
      (($# >= 2)) || die "--compose-file requires a path"
      compose_file="$2"
      shift 2
      ;;
    --compose-override)
      (($# >= 2)) || die "--compose-override requires a path"
      compose_override="$2"
      shift 2
      ;;
    --license)
      (($# >= 2)) || die "--license requires a path"
      license_file="$2"
      shift 2
      ;;
    --notices)
      (($# >= 2)) || die "--notices requires a path"
      notices_file="$2"
      shift 2
      ;;
    --output)
      (($# >= 2)) || die "--output requires a path"
      output="$2"
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

[[ -n "$version" ]] || die "--version is required"
[[ "$version" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || die "version contains unsafe characters"
((${#images[@]} > 0)) || die "at least one --image is required"
((${#images[@]} == ${#sboms[@]})) || die "every image requires one --sbom"
((${#images[@]} == ${#signatures[@]})) || die "every image requires one --signature"
[[ -n "$source_archive" ]] || die "--source-archive is required for AGPL corresponding source"

if [[ -n "$chart_archive" ]]; then
  require_file "$chart_archive"
else
  require_dir "$chart_dir"
fi
require_dir "$config_dir"
require_file "$compose_file"
require_file "$compose_override"
require_file "$license_file"
require_file "$notices_file"
require_file "$source_archive"
if [[ -z "$chart_archive" ]]; then
  command -v helm >/dev/null 2>&1 || die "helm is required to package the local chart"
fi
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"
command -v tar >/dev/null 2>&1 || die "tar is required"

if [[ -z "$output" ]]; then
  output="artifacts/portable-ai-gateway-airgap-${version}.tar.gz"
fi
mkdir -p "$(dirname "$output")"
output="$(cd "$(dirname "$output")" && pwd)/$(basename "$output")"

work_dir="$(mktemp -d)"
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT

bundle_root="$work_dir/portable-ai-gateway-airgap-${version}"
mkdir -p \
  "$bundle_root/chart" \
  "$bundle_root/checksums" \
  "$bundle_root/config" \
  "$bundle_root/deployment" \
  "$bundle_root/images" \
  "$bundle_root/legal" \
  "$bundle_root/manifest" \
  "$bundle_root/sbom" \
  "$bundle_root/signatures" \
  "$bundle_root/source"

printf 'label\treference\tarchive\tsbom\tsignature\n' > "$bundle_root/manifest/images.tsv"
declare -A image_labels=()

for entry in "${images[@]}"; do
  IFS='|' read -r label reference archive extra <<< "$entry"
  [[ -n "$label" && -n "$reference" && -n "$archive" && -z "${extra:-}" ]] \
    || die "invalid --image value: $entry"
  [[ "$label" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || die "invalid image label: $label"
  [[ "$reference" =~ @sha256:[0-9a-f]{64}$ ]] || die "image reference must be pinned by digest: $reference"
  [[ -z "${image_labels[$label]:-}" ]] || die "duplicate image label: $label"
  require_file "$archive"
  image_labels[$label]="$reference"
  cp -- "$archive" "$bundle_root/images/${label}.tar"
done

declare -A sbom_labels=()
for entry in "${sboms[@]}"; do
  IFS='|' read -r label path extra <<< "$entry"
  [[ -n "$label" && -n "$path" && -z "${extra:-}" ]] || die "invalid --sbom value: $entry"
  [[ -n "${image_labels[$label]:-}" ]] || die "SBOM label has no image: $label"
  [[ -z "${sbom_labels[$label]:-}" ]] || die "duplicate SBOM label: $label"
  require_file "$path"
  sbom_labels[$label]=1
  cp -- "$path" "$bundle_root/sbom/${label}.spdx.json"
done

declare -A signature_labels=()
for entry in "${signatures[@]}"; do
  IFS='|' read -r label path extra <<< "$entry"
  [[ -n "$label" && -n "$path" && -z "${extra:-}" ]] || die "invalid --signature value: $entry"
  [[ -n "${image_labels[$label]:-}" ]] || die "signature label has no image: $label"
  [[ -z "${signature_labels[$label]:-}" ]] || die "duplicate signature label: $label"
  require_file "$path"
  signature_labels[$label]=1
  cp -- "$path" "$bundle_root/signatures/${label}.sigstore.json"
done

while IFS= read -r label; do
  [[ -n "${sbom_labels[$label]:-}" ]] || die "missing SBOM for image label: $label"
  [[ -n "${signature_labels[$label]:-}" ]] || die "missing signature for image label: $label"
  printf '%s\t%s\timages/%s.tar\tsbom/%s.spdx.json\tsignatures/%s.sigstore.json\n' \
    "$label" "${image_labels[$label]}" "$label" "$label" "$label" \
    >> "$bundle_root/manifest/images.tsv"
done < <(printf '%s\n' "${!image_labels[@]}" | LC_ALL=C sort)

if [[ -n "$chart_archive" ]]; then
  cp -- "$chart_archive" "$bundle_root/chart/$(basename "$chart_archive")"
else
  chart_version="${version#v}"
  helm package "$chart_dir" \
    --version "$chart_version" \
    --app-version "$version" \
    --destination "$bundle_root/chart" >/dev/null
fi

cp -R -- "$config_dir"/. "$bundle_root/config/"
cp -- "$compose_file" "$bundle_root/deployment/compose.yaml"
cp -- "$compose_override" "$bundle_root/deployment/provider-egress.yaml"
cp -- "$license_file" "$bundle_root/legal/LICENSE"
cp -- "$notices_file" "$bundle_root/legal/THIRD_PARTY_NOTICES"
cp -- "$source_archive" "$bundle_root/source/$(basename "$source_archive")"

cat > "$bundle_root/manifest/README.txt" <<EOF
Portable AI Gateway air-gap bundle ${version}

1. Before extraction, verify the separately distributed top-level checksum and
   Sigstore bundle against the approved release identity.
2. Verify internal checksums with:
     (cd portable-ai-gateway-airgap-${version} && sha256sum --check checksums/SHA256SUMS)
3. Verify every image archive against its Sigstore bundle using the exact
   accepted-candidate signer identity documented by the distributor.
4. Import each images/*.tar into the destination registry or container engine.
5. Install chart/portable-ai-gateway-*.tgz with image.repository and image.digest
   set to the imported gateway image. Do not expose the control plane publicly.
   For standalone evaluation, deployment/compose.yaml is also included; set
   PAG_IMAGE, PAG_POSTGRES_IMAGE and PAG_VALKEY_IMAGE to the imported immutable
   references before starting it.

No registry contact or external download was performed by the bundle script.
EOF

(
  cd "$bundle_root"
  find . -type f ! -path './checksums/SHA256SUMS' -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > checksums/SHA256SUMS
)

tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
  -C "$work_dir" -czf "$output" "$(basename "$bundle_root")"

printf '%s\n' "$output"
