#!/bin/bash
# SteamOS LED → WLED Bridge — Uninstaller
# Run as: sudo ./uninstall.sh
set -euo pipefail

SERVICE_NAME="steamos-led-wled"
MODULE_NAME="leds-valve-shim"
SERVER_INSTALL_DIR="/opt/steamos-led-wled"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CONF_FILE="/etc/steamos-led-wled.conf"
MODULES_LOAD="/etc/modules-load.d/steamos-led-bar.conf"
REL=$(uname -r)
MODULE_PATH="/usr/lib/modules/$REL/updates/${MODULE_NAME}.ko"
PRIORITY_FILE="/usr/lib/depmod.d/10-updates.conf"

[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo ./uninstall.sh"; exit 1; }

# ── SteamOS read-only rootfs ───────────────────────────────────
ROOTFS_WAS_READONLY=0
if command -v steamos-readonly >/dev/null 2>&1; then
    if steamos-readonly status 2>/dev/null | grep -qi enabled; then
        echo "Disabling read-only filesystem..."
        steamos-readonly disable
        ROOTFS_WAS_READONLY=1
    fi
fi

restore_readonly() {
    if [ "$ROOTFS_WAS_READONLY" = 1 ]; then
        echo "Re-enabling read-only filesystem..."
        steamos-readonly enable || true
    fi
}
trap restore_readonly EXIT

# ── 1. Stop and remove service ─────────────────────────────────
echo ""
echo "======================================"
echo "  Step 1: Removing systemd service"
echo "======================================"

if systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1; then
    systemctl stop "$SERVICE_NAME"
    echo "Service stopped"
fi

if systemctl is-enabled "$SERVICE_NAME" >/dev/null 2>&1; then
    systemctl disable "$SERVICE_NAME"
    echo "Service disabled"
fi

if [ -f "$SERVICE_FILE" ]; then
    rm -f "$SERVICE_FILE"
    echo "Removed $SERVICE_FILE"
fi

systemctl daemon-reload

# ── 2. Remove server files ─────────────────────────────────────
echo ""
echo "======================================"
echo "  Step 2: Removing LED server"
echo "======================================"

if [ -d "$SERVER_INSTALL_DIR" ]; then
    rm -rf "$SERVER_INSTALL_DIR"
    echo "Removed $SERVER_INSTALL_DIR"
else
    echo "Server directory not found (already removed)"
fi

# ── 3. Remove config ──────────────────────────────────────────
echo ""
echo "======================================"
echo "  Step 3: Removing configuration"
echo "======================================"

if [ -f "$CONF_FILE" ]; then
    read -rp "Remove config file $CONF_FILE? [Y/n]: " RM_CONF
    if [[ "${RM_CONF,,}" != "n" && "${RM_CONF,,}" != "no" ]]; then
        rm -f "$CONF_FILE"
        echo "Removed $CONF_FILE"
    else
        echo "Kept $CONF_FILE"
    fi
else
    echo "Config file not found (already removed)"
fi

# ── 4. Unload and remove kernel module ─────────────────────────
echo ""
echo "======================================"
echo "  Step 4: Removing kernel module"
echo "======================================"

if lsmod | grep -q "$MODULE_NAME"; then
    rmmod "$MODULE_NAME" 2>/dev/null || modprobe -r "$MODULE_NAME" 2>/dev/null || true
    echo "Module unloaded"
fi

if [ -f "$MODULE_PATH" ]; then
    rm -f "$MODULE_PATH"
    echo "Removed $MODULE_PATH"
else
    echo "Module file not found (already removed)"
fi

if [ -f "$MODULES_LOAD" ]; then
    rm -f "$MODULES_LOAD"
    echo "Removed $MODULES_LOAD"
fi

if [ -f "$PRIORITY_FILE" ]; then
    rm -f "$PRIORITY_FILE"
    echo "Removed $PRIORITY_FILE"
fi

depmod "$REL"
echo "depmod updated"

# ── Done ───────────────────────────────────────────────────────
echo ""
echo "======================================"
echo "  Uninstall complete!"
echo "======================================"
echo ""
echo "  All components have been removed."
echo ""
