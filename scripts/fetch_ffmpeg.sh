#!/usr/bin/env bash
# Fetch signed Intel macOS FFmpeg/FFprobe release binaries.
# The Evermeet Intel builds are documented as compatible with macOS 10.13+,
# so they are suitable candidates for a macOS 11 Big Sur target.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$ROOT/binaries"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fetch-ffmpeg.XXXXXX")"
VERSION="${FFMPEG_VERSION:-9.0.2}"
BASE_URL="https://evermeet.cx/ffmpeg"
EXPECTED_FINGERPRINT="20F6EA3E0CFD6B4C53447A73476C4B611A660874"

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

for cmd in curl unzip shasum file lipo gpg; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Required command not found: $cmd"
done

[[ "$(uname -s)" == "Darwin" ]] || fail "Run this script on macOS (GitHub Actions macOS runner)."

mkdir -p "$BIN_DIR"

echo "Importing FFmpeg publisher signing key..."
curl --fail --location --silent --show-error --retry 4 \
  "$BASE_URL/0x1A660874.asc" -o "$TMP_DIR/evermeet-signing-key.asc"

gpg --batch --import "$TMP_DIR/evermeet-signing-key.asc" >/dev/null 2>&1

ACTUAL_FINGERPRINT="$(
  gpg --batch --with-colons --fingerprint "$EXPECTED_FINGERPRINT" 2>/dev/null |
  awk -F: '$1 == "fpr" { print toupper($10); exit }'
)"
[[ "$ACTUAL_FINGERPRINT" == "$EXPECTED_FINGERPRINT" ]] ||
  fail "Publisher signing-key fingerprint mismatch. Got: ${ACTUAL_FINGERPRINT:-none}"

download_tool() {
  local tool="$1"
  local archive="$TMP_DIR/${tool}-${VERSION}.zip"
  local signature="${archive}.sig"
  local extract_dir="$TMP_DIR/${tool}-extract"
  local candidate
  local archs

  echo
  echo "Downloading ${tool} ${VERSION} for macOS Intel..."
  curl --fail --location --silent --show-error --retry 4 \
    "$BASE_URL/${tool}-${VERSION}.zip" -o "$archive"

  curl --fail --location --silent --show-error --retry 4 \
    "$BASE_URL/${tool}-${VERSION}.zip.sig" -o "$signature"

  [[ -s "$archive" && -s "$signature" ]] || fail "Missing or empty ${tool} archive/signature."
  unzip -tq "$archive" >/dev/null || fail "${tool} ZIP archive is corrupt."

  echo "Verifying publisher GPG signature..."
  gpg --batch --verify "$signature" "$archive" ||
    fail "GPG signature verification failed for ${tool}."

  mkdir -p "$extract_dir"
  unzip -oq "$archive" -d "$extract_dir"

  candidate="$(find "$extract_dir" -type f -name "$tool" -print -quit)"
  [[ -n "$candidate" ]] || fail "Could not find executable '${tool}' in archive."
  chmod +x "$candidate"

  archs="$(lipo -archs "$candidate" 2>/dev/null || true)"
  [[ " $archs " == *" x86_64 "* ]] ||
    fail "${tool} is not an Intel x86_64 Mach-O binary (architectures: ${archs:-unknown})."

  install -m 755 "$candidate" "$BIN_DIR/$tool"
  echo "Installed: $BIN_DIR/$tool"
  echo "SHA256: $(shasum -a 256 "$BIN_DIR/$tool" | awk '{print $1}')"
  file "$BIN_DIR/$tool"
}

download_tool ffmpeg
download_tool ffprobe

echo
echo "Checking executable versions..."
"$BIN_DIR/ffmpeg" -version | head -n 1
"$BIN_DIR/ffprobe" -version | head -n 1

CHECKER="$ROOT/scripts/check_macho.py"
[[ -f "$CHECKER" ]] || fail "Missing $CHECKER; cannot check macOS deployment target."
command -v python3 >/dev/null 2>&1 || fail "python3 is required for check_macho.py."

echo
echo "Checking minimum macOS version (must be <= 11.0)..."
python3 "$CHECKER" "$BIN_DIR" --min 11.0 --verbose

echo
echo "SUCCESS: Signed Intel FFmpeg and FFprobe passed the macOS 11.0 compatibility check."
