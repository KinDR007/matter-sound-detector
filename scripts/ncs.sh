#!/usr/bin/env bash
# Run a command inside the nRF Connect SDK toolchain environment.
# Usage: scripts/ncs.sh west build ...
#   NCS_DIR      SDK workspace (default /opt/nordic/ncs/$NCS_VERSION, as installed by nrfutil)
#   NCS_DIRECT=1 the toolchain is already on PATH (CI container): run the command as is
set -euo pipefail
NCS_VERSION="${NCS_VERSION:-v3.4.2}"
NCS_DIR="${NCS_DIR:-/opt/nordic/ncs/$NCS_VERSION}"
export ZEPHYR_BASE="$NCS_DIR/zephyr"
[ -d "$ZEPHYR_BASE" ] || { echo "NCS $NCS_VERSION not found in $NCS_DIR" >&2; exit 1; }
cd "$NCS_DIR"
if [ "${NCS_DIRECT:-0}" = 1 ]; then
	exec "$@"
fi
NRFUTIL="${NRFUTIL:-$(command -v nrfutil || echo "$HOME/.local/bin/nrfutil")}"
[ -x "$NRFUTIL" ] || { echo "nrfutil not found (or set NCS_DIRECT=1)" >&2; exit 1; }
exec "$NRFUTIL" sdk-manager toolchain launch --ncs-version "$NCS_VERSION" -- "$@"
