#!/bin/bash
# Install leds-valve-shim.ko via the modules updates/ override.
# Run as: sudo ./install.sh
set -euo pipefail

REL=$(uname -r)
HERE=$(cd "$(dirname "$0")" && pwd)
MODULE_NAME="leds-valve-shim"
SRC=$HERE/${MODULE_NAME}.ko
DST=/usr/lib/modules/$REL/updates/${MODULE_NAME}.ko

[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

build_module() {
	echo "Module not found at $SRC. Trying to build..."
	make -C "$HERE" clean >/dev/null 2>&1 || true
	make -C "$HERE" || {
		echo "build failed; install linux-headers for $REL and make/gcc first"
		exit 1
	}
}

[ -f "$SRC" ] || build_module

# Verify vermagic matches the running kernel.
VERMAGIC=$(modinfo -F vermagic "$SRC" | awk '{print $1}')
if [ "$VERMAGIC" != "$REL" ]; then
	echo "ERROR: module vermagic '$VERMAGIC' does not match running kernel '$REL'"
	echo "Rebuild the module on this kernel before installing."
	exit 1
fi
echo "vermagic OK: $VERMAGIC"

SRC=$(realpath "$SRC")

ROOTFS_WAS_READONLY=0
INSTALL_STARTED=0
INSTALL_OK=0
TMPD=$(mktemp -d)
PRIORITY_FILE=/usr/lib/depmod.d/10-updates.conf
MODULES_LOAD=/etc/modules-load.d/steamos-led-bar.conf

cleanup() {
	if [ "$INSTALL_STARTED" = 1 ] && [ "$INSTALL_OK" = 0 ]; then
		echo "install failed; restoring previous state" >&2
		if [ -f "$TMPD/original.ko" ]; then
			install -D -m644 "$TMPD/original.ko" "$DST"
		else
			rm -f "$DST"
		fi
		if [ -f "$TMPD/original-priority.conf" ]; then
			install -D -m644 "$TMPD/original-priority.conf" "$PRIORITY_FILE"
		else
			rm -f "$PRIORITY_FILE"
		fi
		depmod "$REL" || true
	fi
	rm -rf "$TMPD"
	if [ "$ROOTFS_WAS_READONLY" = 1 ]; then
		if command -v steamos-readonly >/dev/null 2>&1; then
			steamos-readonly enable || true
		fi
	fi
}
trap cleanup EXIT

# Disable SteamOS read-only root if needed.
if command -v steamos-readonly >/dev/null 2>&1; then
	if steamos-readonly status 2>/dev/null | grep -qi enabled; then
		steamos-readonly disable
		ROOTFS_WAS_READONLY=1
	fi
fi

# Backup existing overrides.
if [ -f "$DST" ]; then cp -a "$DST" "$TMPD/original.ko"; fi
if [ -f "$PRIORITY_FILE" ]; then cp -a "$PRIORITY_FILE" "$TMPD/original-priority.conf"; fi

INSTALL_STARTED=1
install -D -m644 "$SRC" "$DST"

mkdir -p /usr/lib/depmod.d
echo "search updates built-in" > "$PRIORITY_FILE"

depmod "$REL"

# Verify depmod picked the override.
RESOLVED=$(modinfo -k "$REL" -F filename "$MODULE_NAME")
echo "$MODULE_NAME now resolves to: $RESOLVED"
if [[ "$RESOLVED" != *"/updates/"* ]]; then
	echo "ERROR: updates/ override not winning -- aborting"
	rm -f "$DST"
	depmod "$REL"
	exit 1
fi

# Load now.
modprobe "$MODULE_NAME" || { echo "modprobe failed; check dmesg"; exit 1; }

# Persist for next boot.
mkdir -p /etc/modules-load.d
echo "$MODULE_NAME" > "$MODULES_LOAD"

INSTALL_OK=1
echo "OK -- $MODULE_NAME installed and loaded. /dev/valve-leds-shim should appear."
