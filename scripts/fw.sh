#!/usr/bin/env bash
# Usage: [MODEL=int8|float] scripts/fw.sh build|pristine|flash [HEX]|package DIR|monitor
#   flash [HEX]  flash the build (MCUboot + signed app + factory data) or one merged .hex
#   package DIR  release files: merged .hex, matter.ota, dfu_application.zip, SHA256SUMS
# Build uses nRF Connect SDK (scripts/ncs.sh), flashing uses OpenOCD over the
# XIAO's onboard CMSIS-DAP (no J-Link needed).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HERE/firmware"
# MODEL=int8 (default): int8 CMSIS-NN model; MODEL=float: float reference model
MODEL="${MODEL:-int8}"
case "$MODEL" in
float) BUILD="$HERE/build"; EXTRA=() ;;
int8) BUILD="$HERE/build_int8"; EXTRA=(-DEXTRA_CONF_FILE=int8.conf) ;;
*) echo "MODEL must be float or int8" >&2; exit 2 ;;
esac
BOARD="xiao_nrf54lm20a/nrf54lm20a/cpuapp"
NCS="$HERE/scripts/ncs.sh"
OPENOCD_DIR="${OPENOCD_DIR:-$HOME/.platformio/packages/tool-openocd}"
OPENOCD_CFG="$HERE/scripts/nrf54lm20a.cfg"
PORT="${PORT:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)}"

# "1.0.0" from firmware/VERSION, also written into the matter.ota header (NCS default "1.0.0+0")
VERSION_STR="$(awk -F' = ' '/^VERSION_MAJOR/{a=$2} /^VERSION_MINOR/{b=$2} /^PATCHLEVEL/{c=$2} /^EXTRAVERSION/{e=$2}
	END{printf "%s.%s.%s%s", a, b, c, (e != "" ? "-" e : "")}' "$APP/VERSION")"
# MCUboot signing key: MCUBOOT_KEY or ~/.config/matter-sound-detector/mcuboot-ed25519.pem;
# without it the public NCS development key is used (anyone can sign images for the device)
MCUBOOT_KEY="${MCUBOOT_KEY:-$HOME/.config/matter-sound-detector/mcuboot-ed25519.pem}"
if [ -f "$MCUBOOT_KEY" ]; then
	EXTRA+=(-DSB_CONFIG_BOOT_SIGNATURE_KEY_FILE="\"$MCUBOOT_KEY\"")
else
	echo "warning: $MCUBOOT_KEY not found, signing with the public NCS development key" >&2
fi

build() {
	"$NCS" west build -b "$BOARD" -d "$BUILD" "$APP" "$@" -- -DBOARD_ROOT="$APP" \
		-DCHIP_OTA_IMAGE_EXTRA_ARGS="--version-str=$VERSION_STR" "${EXTRA[@]}"
}

case "${1:-}" in
build) build ;;
pristine) build -p always ;;
flash)
	HEXES=("$BUILD/mcuboot/zephyr/zephyr.hex" "$BUILD/firmware/zephyr/zephyr.signed.hex"
		"$BUILD/matter_factory_data/zephyr/factory_data.hex")
	[ -n "${2:-}" ] && HEXES=("$2")
	ARGS=(-c "init" -c "reset halt")
	for h in "${HEXES[@]}"; do
		[ -f "$h" ] || { echo "missing $h, build first" >&2; exit 1; }
		ARGS+=(-c "nrf54lm20a-load {$h}" -c "verify_image {$h}")
	done
	"$OPENOCD_DIR/bin/openocd" -s "$OPENOCD_DIR/openocd/scripts" -f "$OPENOCD_CFG" \
		"${ARGS[@]}" -c "reset run" -c "shutdown"
	;;
package)
	mkdir -p "${2:?usage: $0 package DIR}"
	OUT="$(cd "$2" && pwd)" # absolute: ncs.sh runs in the SDK directory
	NAME="matter-sound-detector-$VERSION_STR"
	for f in "$BUILD/mcuboot/zephyr/zephyr.hex" "$BUILD/firmware/zephyr/zephyr.signed.hex" \
		"$BUILD/matter_factory_data/zephyr/factory_data.hex" "$BUILD/matter.ota" "$BUILD/dfu_application.zip"; do
		[ -f "$f" ] || { echo "missing $f, build first" >&2; exit 1; }
	done
	"$NCS" python3 zephyr/scripts/build/mergehex.py -o "$OUT/$NAME.hex" \
		"$BUILD/mcuboot/zephyr/zephyr.hex" "$BUILD/firmware/zephyr/zephyr.signed.hex" \
		"$BUILD/matter_factory_data/zephyr/factory_data.hex"
	cp "$BUILD/matter.ota" "$OUT/$NAME.ota"
	cp "$BUILD/dfu_application.zip" "$OUT/$NAME-dfu.zip"
	# sha256sum on Linux; shasum (Perl) clashes with the Perl of the CI toolchain image
	if command -v sha256sum >/dev/null; then SUM=(sha256sum); else SUM=(shasum -a 256); fi
	(cd "$OUT" && "${SUM[@]}" "$NAME".* "$NAME"-dfu.zip >SHA256SUMS)
	ls -l "$OUT"
	;;
monitor)
	[ -n "$PORT" ] || { echo "no serial port, set PORT=" >&2; exit 1; }
	exec "$HOME/.platformio/penv/bin/python" -m serial.tools.miniterm "$PORT" 115200
	;;
*) echo "Usage: $0 build|pristine|flash [HEX]|package DIR|monitor" >&2; exit 2 ;;
esac
