#!/usr/bin/env sh
# Download the newest release .ota, verify it against SHA256SUMS and copy it into DIR
# (the Matter Server OTA folder). Plain POSIX sh + curl, runs in the Home Assistant SSH app.
#
# Usage: ha_ota_fetch.sh DIR [OWNER/REPO]
#   e.g.  ha_ota_fetch.sh /addon_configs/core_matter_server/updates
# Then restart the Matter Server app; it imports the file and offers the update.
set -eu

DIR="${1:?usage: $0 DIR [OWNER/REPO]}"
REPO="${2:-KinDR007/matter-sound-detector}"
API="https://api.github.com/repos/$REPO/releases/latest"

command -v curl >/dev/null || { echo "curl not found" >&2; exit 1; }
if command -v sha256sum >/dev/null; then SHA="sha256sum"; else SHA="shasum -a 256"; fi
[ -d "$DIR" ] || mkdir -p "$DIR"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fsSL "$API" -o "$TMP/release.json" || { echo "cannot read $API" >&2; exit 1; }
urls="$(grep -o '"browser_download_url": *"[^"]*"' "$TMP/release.json" | sed 's/.*"\(https[^"]*\)"/\1/')"
ota_url="$(printf '%s\n' "$urls" | grep '\.ota$' | head -n 1)"
sums_url="$(printf '%s\n' "$urls" | grep '/SHA256SUMS$' | head -n 1)"
[ -n "$ota_url" ] || { echo "no .ota file in the latest release of $REPO" >&2; exit 1; }
[ -n "$sums_url" ] || { echo "no SHA256SUMS in the latest release of $REPO" >&2; exit 1; }

name="$(basename "$ota_url")"
curl -fsSL "$ota_url" -o "$TMP/$name"
curl -fsSL "$sums_url" -o "$TMP/SHA256SUMS"
want="$(grep " \*\{0,1\}$name\$" "$TMP/SHA256SUMS" | cut -d ' ' -f 1)"
have="$($SHA "$TMP/$name" | cut -d ' ' -f 1)"
[ -n "$want" ] && [ "$want" = "$have" ] || { echo "$name: checksum mismatch" >&2; exit 1; }

cp "$TMP/$name" "$DIR/$name"
echo "$DIR/$name ($have)"
echo "restart the Matter Server app to import it"
