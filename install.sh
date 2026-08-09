#!/bin/bash
# SteamOS LED → WLED Bridge — Installer
# Installs leds-valve-shim kernel module + WLED bridge service.
# Run as: sudo ./install.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SHIM_DIR="$HERE/leds-valve-shim"
SERVER_DIR="$HERE/server"
SERVICE_NAME="steamos-led-wled"
SERVER_INSTALL_DIR="/opt/steamos-led-wled"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CONF_FILE="/etc/steamos-led-wled.conf"
REL=$(uname -r)

[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo ./install.sh"; exit 1; }

# ── Detect distro ─────────────────────────────────────────────
DISTRO="unknown"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    case "$ID" in
        steamos|holo) DISTRO="steamos" ;;
        arch|manjaro|endeavouros|garuda|cachyos) DISTRO="arch" ;;
        ubuntu|debian|linuxmint|pop) DISTRO="debian" ;;
        fedora|nobara|bazzite) DISTRO="fedora" ;;
        *) DISTRO="$ID" ;;
    esac
fi
echo "Detected distro: $DISTRO"

# ── SteamOS read-only rootfs ───────────────────────────────────
ROOTFS_WAS_READONLY=0
restore_readonly() {
    if [ "$ROOTFS_WAS_READONLY" = 1 ]; then
        if command -v steamos-readonly >/dev/null 2>&1; then
            steamos-readonly enable || true
        fi
    fi
}
trap restore_readonly EXIT

if command -v steamos-readonly >/dev/null 2>&1; then
    if steamos-readonly status 2>/dev/null | grep -qi enabled; then
        steamos-readonly disable
        ROOTFS_WAS_READONLY=1
    fi
fi

# ── Install build dependencies ─────────────────────────────────
echo ""
echo "======================================"
echo "  Step 0: Installing dependencies"
echo "======================================"

install_deps_arch() {
    local NEEDED=()
    command -v make >/dev/null 2>&1 || NEEDED+=(make)
    command -v gcc >/dev/null 2>&1 || NEEDED+=(gcc)
    [ -d "/usr/lib/modules/$REL/build" ] || NEEDED+=(linux-headers)
    if [ ${#NEEDED[@]} -gt 0 ]; then
        echo "Installing: ${NEEDED[*]}"
        pacman -Sy --noconfirm --needed base-devel "${NEEDED[@]}" 2>/dev/null || \
        pacman -Sy --noconfirm --needed "${NEEDED[@]}"
    else
        echo "All build dependencies already installed"
    fi
}

install_deps_debian() {
    local NEEDED=()
    command -v make >/dev/null 2>&1 || NEEDED+=(build-essential)
    command -v gcc >/dev/null 2>&1 || NEEDED+=(build-essential)
    [ -d "/usr/lib/modules/$REL/build" ] || NEEDED+=("linux-headers-$REL")
    if [ ${#NEEDED[@]} -gt 0 ]; then
        echo "Installing: ${NEEDED[*]}"
        apt-get update -qq
        apt-get install -y "${NEEDED[@]}"
    else
        echo "All build dependencies already installed"
    fi
}

install_deps_fedora() {
    local NEEDED=()
    command -v make >/dev/null 2>&1 || NEEDED+=(make)
    command -v gcc >/dev/null 2>&1 || NEEDED+=(gcc)
    [ -d "/usr/lib/modules/$REL/build" ] || NEEDED+=("kernel-devel-$REL")
    if [ ${#NEEDED[@]} -gt 0 ]; then
        echo "Installing: ${NEEDED[*]}"
        dnf install -y "${NEEDED[@]}"
    else
        echo "All build dependencies already installed"
    fi
}

case "$DISTRO" in
    steamos|arch)
        install_deps_arch ;;
    debian)
        install_deps_debian ;;
    fedora)
        install_deps_fedora ;;
    *)
        echo "Unknown distro '$DISTRO'. Ensure you have make, gcc, and linux headers."
        read -rp "Continue anyway? [y/N]: " CONT
        [[ "${CONT,,}" == "y" ]] || exit 1
        ;;
esac

# ── Discover or ask for WLED IP ────────────────────────────────
echo ""
echo "======================================"
echo "  Step 1: WLED device configuration"
echo "======================================"

discover_wled() {
    # Try mDNS discovery first (WLED advertises as _wled._tcp or _http._tcp)
    if command -v avahi-browse >/dev/null 2>&1; then
        echo "Scanning network for WLED devices (5 seconds)..."
        local FOUND=""
        FOUND=$(timeout 5 avahi-browse -rpt _http._tcp 2>/dev/null | \
            grep -i "wled" | grep "=;" | head -5 | \
            awk -F';' '{print $8}' | sort -u) || true
        if [ -n "$FOUND" ]; then
            echo ""
            echo "Found WLED device(s):"
            local IDX=1
            local -a ADDRS=()
            while IFS= read -r addr; do
                [ -z "$addr" ] && continue
                ADDRS+=("$addr")
                echo "  $IDX) $addr"
                IDX=$((IDX + 1))
            done <<< "$FOUND"
            if [ ${#ADDRS[@]} -gt 0 ]; then
                echo ""
                read -rp "Select a device [1]: " SEL
                SEL=${SEL:-1}
                if [[ "$SEL" =~ ^[0-9]+$ ]] && [ "$SEL" -ge 1 ] && [ "$SEL" -le ${#ADDRS[@]} ]; then
                    WLED_HOST="${ADDRS[$((SEL - 1))]}"
                    return 0
                fi
            fi
        fi
    fi

    # Try scanning common subnets with a quick UDP probe
    echo "Trying UDP probe on local network..."
    local SUBNET
    SUBNET=$(ip -4 route show default 2>/dev/null | awk '{print $3}' | head -1)
    if [ -n "$SUBNET" ]; then
        local BASE
        BASE=$(echo "$SUBNET" | sed 's/\.[0-9]*$//')
        for i in $(seq 1 254); do
            local IP="${BASE}.${i}"
            # Send a quick HTTP request to check for WLED
            local RESP
            RESP=$(timeout 0.3 bash -c "echo -e 'GET /json/info HTTP/1.0\r\nHost: ${IP}\r\n\r\n' | \
                nc -w 1 ${IP} 80 2>/dev/null" || true)
            if echo "$RESP" | grep -qi "wled"; then
                local WLED_NAME
                WLED_NAME=$(echo "$RESP" | grep -oP '"name"\s*:\s*"\K[^"]+' || echo "WLED")
                echo "  Found: $IP ($WLED_NAME)"
                read -rp "Use this device? [Y/n]: " USE_IT
                if [[ "${USE_IT,,}" != "n" && "${USE_IT,,}" != "no" ]]; then
                    WLED_HOST="$IP"
                    return 0
                fi
            fi
        done &
        local SCAN_PID=$!
        # Wait max 30 seconds for scan
        local WAIT=0
        while kill -0 "$SCAN_PID" 2>/dev/null && [ "$WAIT" -lt 30 ]; do
            sleep 1
            WAIT=$((WAIT + 1))
        done
        kill "$SCAN_PID" 2>/dev/null || true
        wait "$SCAN_PID" 2>/dev/null || true
    fi

    return 1
}

WLED_HOST=""
WLED_PORT=21324

if ! discover_wled || [ -z "$WLED_HOST" ]; then
    echo ""
    echo "Could not auto-discover WLED device."
    read -rp "Enter WLED IP address: " WLED_HOST
    if [ -z "$WLED_HOST" ]; then
        echo "ERROR: WLED IP is required."
        exit 1
    fi
fi

# Validate WLED is reachable
echo ""
echo "Verifying WLED at $WLED_HOST..."
if timeout 3 bash -c "echo -e 'GET /json/info HTTP/1.0\r\nHost: ${WLED_HOST}\r\n\r\n' | \
    nc -w 2 ${WLED_HOST} 80 2>/dev/null" | grep -qi "wled"; then
    echo "WLED device confirmed at $WLED_HOST"
else
    echo "WARNING: Could not verify WLED at $WLED_HOST. Continuing anyway."
fi

read -rp "WLED UDP port? [$WLED_PORT]: " PORT_INPUT
WLED_PORT=${PORT_INPUT:-$WLED_PORT}
echo "Using WLED at $WLED_HOST:$WLED_PORT"

# ── Prompt for number of LEDs ──────────────────────────────────
echo ""
read -rp "How many LEDs does your WLED strip have? [8]: " NUM_LEDS
NUM_LEDS=${NUM_LEDS:-8}
if ! [[ "$NUM_LEDS" =~ ^[0-9]+$ ]] || [ "$NUM_LEDS" -lt 1 ] || [ "$NUM_LEDS" -gt 17 ]; then
    echo "ERROR: Invalid number of LEDs (must be 1-17)"
    exit 1
fi
echo "Configuring for $NUM_LEDS LEDs"

# ── Prompt for overlay features ────────────────────────────────
echo ""
echo "  Overlay features (enhance LED bar beyond Game Mode):"
echo "    temp   - Color bar by CPU/GPU temperature"
echo "    notify - Flash on Steam achievements/messages"
echo "    audio  - VU meter driven by system audio"
echo ""

TEMP_OVERLAY="true"
NOTIFY_OVERLAY="true"
AUDIO_OVERLAY="true"

read -rp "Enable temperature overlay? [Y/n]: " OPT_TEMP
if [[ "${OPT_TEMP,,}" == "n" || "${OPT_TEMP,,}" == "no" ]]; then
    TEMP_OVERLAY="false"
fi
read -rp "Enable notification overlay? [Y/n]: " OPT_NOTIFY
if [[ "${OPT_NOTIFY,,}" == "n" || "${OPT_NOTIFY,,}" == "no" ]]; then
    NOTIFY_OVERLAY="false"
fi
read -rp "Enable audio reactive overlay? [Y/n]: " OPT_AUDIO
if [[ "${OPT_AUDIO,,}" == "n" || "${OPT_AUDIO,,}" == "no" ]]; then
    AUDIO_OVERLAY="false"
fi

echo "Overlays: temp=$TEMP_OVERLAY notify=$NOTIFY_OVERLAY audio=$AUDIO_OVERLAY"

# ── 2. Install kernel module ──────────────────────────────────
echo ""
echo "======================================"
echo "  Step 2: Kernel module (leds-valve-shim)"
echo "======================================"

if [ -f "$SHIM_DIR/install.sh" ]; then
    bash "$SHIM_DIR/install.sh"
else
    echo "ERROR: leds-valve-shim/install.sh not found"
    exit 1
fi

# ── 3. Install LED server ─────────────────────────────────────
echo ""
echo "======================================"
echo "  Step 3: LED server"
echo "======================================"

mkdir -p "$SERVER_INSTALL_DIR"
install -m755 "$SERVER_DIR/led_server.py" "$SERVER_INSTALL_DIR/led_server.py"
echo "Installed led_server.py to $SERVER_INSTALL_DIR"

# ── 4. Generate config file ──────────────────────────────────
echo ""
echo "======================================"
echo "  Step 4: Configuration"
echo "======================================"

sed -e "s/__WLED_HOST__/$WLED_HOST/g" \
    -e "s/__WLED_PORT__/$WLED_PORT/g" \
    -e "s/__NUM_LEDS__/$NUM_LEDS/g" \
    -e "s/__TEMP_OVERLAY__/$TEMP_OVERLAY/g" \
    -e "s/__NOTIFY_OVERLAY__/$NOTIFY_OVERLAY/g" \
    -e "s/__AUDIO_OVERLAY__/$AUDIO_OVERLAY/g" \
    "$SERVER_DIR/steamos-led-wled.conf" > "$CONF_FILE"

echo "Configuration written to $CONF_FILE"

# ── 5. Install systemd service ────────────────────────────────
echo ""
echo "======================================"
echo "  Step 5: Systemd service"
echo "======================================"

# Stop existing service if running
systemctl stop "$SERVICE_NAME" 2>/dev/null || true

# Detect desktop user
DESK_USER=$(awk -F: '$3 >= 1000 && $3 < 60000 { print $1; exit }' /etc/passwd)
DESK_UID=$(id -u "$DESK_USER" 2>/dev/null || echo "1000")
echo "Service will run as user: $DESK_USER (UID $DESK_UID)"

sed -e "s/__USER__/$DESK_USER/g" \
    -e "s/__UID__/$DESK_UID/g" \
    "$SERVER_DIR/steamos-led-wled.service" > "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

echo "Service $SERVICE_NAME enabled and started"

# ── Done ───────────────────────────────────────────────────────
echo ""
echo "======================================"
echo "  Installation complete!"
echo "======================================"
echo ""
echo "  WLED device:  $WLED_HOST:$WLED_PORT (UDP)"
echo "  Output LEDs:  $NUM_LEDS"
echo "  Config file:  $CONF_FILE"
echo "  Server:       $SERVER_INSTALL_DIR/led_server.py"
echo "  Service:      $SERVICE_NAME (running)"
echo ""
echo "  Check status: sudo systemctl status $SERVICE_NAME"
echo "  View logs:    sudo journalctl -u $SERVICE_NAME -f"
echo "  Edit config:  sudo nano $CONF_FILE"
echo "  Restart:      sudo systemctl restart $SERVICE_NAME"
echo ""
