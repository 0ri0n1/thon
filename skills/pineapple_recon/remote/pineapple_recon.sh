#!/bin/sh
# pineapple_recon.sh — on-device AP survey collection
#
# Runs on the WiFi Pineapple Mk7 (busybox / OpenWrt). POSIX sh only.
# Invoked by bin/pineapple_recon.py after being pushed via SSH.
#
# Arguments (env):
#   DURATION    - seconds to run airodump (default 300)
#   IFACE       - base interface to put in monitor mode (default wlan1)
#   OUT_DIR     - where to write artifacts on device (default /tmp/pineapple_recon_$(date +%s))
#   BAND        - '2' (2.4GHz) | '5' (5GHz) | 'both' (default 'both')
#
# Output (inside OUT_DIR):
#   scan.csv            airodump-ng CSV (full capture)
#   scan.pcap           beacon/probe pcap
#   iw-scan.txt         one-shot `iw <iface> scan` snapshot
#   iface_info.txt      `iw dev`, `iwconfig`, `ifconfig` snapshot
#   manifest.json       normalized AP data (rendered by this script)
#   sha256sums.txt      integrity hashes
#   run.log             stderr/stdout tee

set -u

DURATION="${DURATION:-300}"
IFACE="${IFACE:-wlan1}"
BAND="${BAND:-both}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${OUT_DIR:-/tmp/pineapple_recon_${STAMP}}"

mkdir -p "$OUT_DIR"
cd "$OUT_DIR" || exit 2

LOG="$OUT_DIR/run.log"
exec 3>&1 4>&2
exec >>"$LOG" 2>&1

echo "=== pineapple_recon start $(date -u +%FT%TZ) ==="
echo "host=$(hostname) uname=$(uname -a)"
echo "DURATION=$DURATION IFACE=$IFACE BAND=$BAND OUT_DIR=$OUT_DIR"

# --- 1. Environment snapshot -----------------------------------------------
{
  echo "### iw dev ###"
  iw dev 2>&1 || echo "iw dev failed"
  echo
  echo "### iwconfig ###"
  iwconfig 2>&1 || echo "iwconfig failed"
  echo
  echo "### ifconfig ###"
  ifconfig 2>&1 || echo "ifconfig failed"
} > iface_info.txt

# --- 2. Monitor mode -------------------------------------------------------
MON_IFACE=""
if command -v airmon-ng >/dev/null 2>&1; then
  airmon-ng check kill >/dev/null 2>&1 || true
  airmon-ng start "$IFACE" >airmon.out 2>&1 || true
  # Pineapple/OpenWrt airmon sometimes produces mon0 or wlanNmon
  for candidate in "${IFACE}mon" "mon0" "${IFACE}"; do
    if iw dev "$candidate" info >/dev/null 2>&1; then
      MON_IFACE="$candidate"
      break
    fi
  done
fi

# Fallback: manual monitor mode via iw
if [ -z "$MON_IFACE" ]; then
  echo "airmon-ng path failed, attempting manual iw monitor setup"
  ip link set "$IFACE" down 2>/dev/null || ifconfig "$IFACE" down 2>/dev/null
  iw dev "$IFACE" set type monitor 2>/dev/null
  ip link set "$IFACE" up 2>/dev/null || ifconfig "$IFACE" up 2>/dev/null
  if iw dev "$IFACE" info 2>/dev/null | grep -q monitor; then
    MON_IFACE="$IFACE"
  fi
fi

if [ -z "$MON_IFACE" ]; then
  echo "FATAL: could not establish monitor mode on $IFACE" >&4
  echo "monitor_mode_failed" > status
  exit 3
fi
echo "monitor interface: $MON_IFACE"

# --- 3. One-shot iw scan (cross-reference dataset) -------------------------
iw dev "$MON_IFACE" scan 2>/dev/null > iw-scan.txt || echo "iw scan skipped (monitor mode)"

# --- 4. airodump-ng capture ------------------------------------------------
AIRODUMP_ARGS="-w scan --output-format csv,pcap --write-interval 5 --background 1"
case "$BAND" in
  2)    AIRODUMP_ARGS="$AIRODUMP_ARGS --band b" ;;
  5)    AIRODUMP_ARGS="$AIRODUMP_ARGS --band a" ;;
  both) AIRODUMP_ARGS="$AIRODUMP_ARGS --band abg" ;;
esac

echo "Starting airodump-ng for $DURATION seconds on $MON_IFACE"
# shellcheck disable=SC2086
airodump-ng $AIRODUMP_ARGS "$MON_IFACE" &
AIRO_PID=$!
echo "$AIRO_PID" > airodump.pid

# Allow external stop via signal on parent; airodump dies with parent group
# The Python caller may also send SIGTERM to this script for early stop.
trap 'echo "stop requested"; kill -INT "$AIRO_PID" 2>/dev/null; wait "$AIRO_PID" 2>/dev/null; STOP_REQUESTED=1' TERM INT

STOP_REQUESTED=0
i=0
while [ "$i" -lt "$DURATION" ]; do
  if ! kill -0 "$AIRO_PID" 2>/dev/null; then
    echo "airodump exited early at t=$i"
    break
  fi
  if [ "$STOP_REQUESTED" = "1" ]; then break; fi
  sleep 1
  i=$((i + 1))
done

kill -INT "$AIRO_PID" 2>/dev/null || true
wait "$AIRO_PID" 2>/dev/null || true
echo "airodump completed"

# Airodump writes scan-01.csv / scan-01.cap — normalize names
[ -f scan-01.csv ] && mv scan-01.csv scan.csv
[ -f scan-01.cap ] && mv scan-01.cap scan.pcap
[ -f scan-01.kismet.csv ] && rm -f scan-01.kismet.csv
[ -f scan-01.kismet.netxml ] && rm -f scan-01.kismet.netxml

# --- 5. Restore interface --------------------------------------------------
if command -v airmon-ng >/dev/null 2>&1 && [ "$MON_IFACE" != "$IFACE" ]; then
  airmon-ng stop "$MON_IFACE" >>airmon.out 2>&1 || true
else
  ip link set "$IFACE" down 2>/dev/null || ifconfig "$IFACE" down 2>/dev/null
  iw dev "$IFACE" set type managed 2>/dev/null
  ip link set "$IFACE" up 2>/dev/null || ifconfig "$IFACE" up 2>/dev/null
fi

# --- 6. Normalize to manifest.json ----------------------------------------
# Airodump CSV has two sections separated by a blank line: APs, then clients.
# We only consume the AP section here.
#
# Columns (airodump-ng):
# BSSID, First time seen, Last time seen, channel, Speed, Privacy, Cipher,
# Authentication, Power, # beacons, # IV, LAN IP, ID-length, ESSID, Key

awk -v stamp="$STAMP" -v dur="$DURATION" -v host="$(hostname)" -v iface="$MON_IFACE" '
BEGIN {
  FS=","
  in_ap = 1
  count = 0
  printf "{\n"
  printf "  \"schema\": \"pineapple_recon.manifest/v1\",\n"
  printf "  \"timestamp_utc\": \"%s\",\n", stamp
  printf "  \"duration_seconds\": %d,\n", dur
  printf "  \"host\": \"%s\",\n", host
  printf "  \"interface\": \"%s\",\n", iface
  printf "  \"access_points\": [\n"
}
/^$/ { in_ap = 0; next }
NR == 1 { next }  # header line
in_ap && NF >= 14 {
  bssid = trim($1)
  if (bssid == "" || bssid == "BSSID") next
  first = trim($2); last = trim($3); ch = trim($4); speed = trim($5)
  privacy = trim($6); cipher = trim($7); auth = trim($8)
  power = trim($9); beacons = trim($10); ivs = trim($11)
  lanip = trim($12); idlen = trim($13); essid = trim($14)
  gsub(/"/, "\\\"", essid)
  if (count > 0) printf ",\n"
  printf "    {"
  printf "\"bssid\":\"%s\",", bssid
  printf "\"essid\":\"%s\",", essid
  printf "\"channel\":\"%s\",", ch
  printf "\"speed\":\"%s\",", speed
  printf "\"privacy\":\"%s\",", privacy
  printf "\"cipher\":\"%s\",", cipher
  printf "\"auth\":\"%s\",", auth
  printf "\"signal_dbm\":\"%s\",", power
  printf "\"beacons\":\"%s\",", beacons
  printf "\"ivs\":\"%s\",", ivs
  printf "\"first_seen\":\"%s\",", first
  printf "\"last_seen\":\"%s\",", last
  printf "\"hidden\":%s", (essid == "" || idlen == "0" ? "true" : "false")
  printf "}"
  count++
}
END {
  printf "\n  ],\n"
  printf "  \"ap_count\": %d\n", count
  printf "}\n"
}
function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
' scan.csv > manifest.json 2>manifest.err || {
  echo "manifest generation failed, see manifest.err"
  echo "{\"schema\":\"pineapple_recon.manifest/v1\",\"error\":\"manifest_generation_failed\",\"access_points\":[],\"ap_count\":0}" > manifest.json
}

# --- 7. Integrity ----------------------------------------------------------
for f in scan.csv scan.pcap iw-scan.txt iface_info.txt manifest.json run.log; do
  [ -f "$f" ] && sha256sum "$f" 2>/dev/null || true
done > sha256sums.txt

echo "ok" > status
echo "=== pineapple_recon end $(date -u +%FT%TZ) ==="
echo "$OUT_DIR" >&3
