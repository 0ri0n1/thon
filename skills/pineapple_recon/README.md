# pineapple_recon

Skill implementation: WiFi Pineapple Mk7 AP-survey recon.

- **Entrypoint for Thon:** [`SKILL.md`](SKILL.md)
- **Entrypoint for humans:** this file

## Quick start

```bash
# 1. Install local deps (on the host running Thon / the CLI)
pip install paramiko jinja2 pyyaml

# 2. Connect Pineapple via USB, confirm reachability
ping 172.16.42.1

# 3. Set auth (do NOT commit)
export PINEAPPLE_PASSWORD=<your-pineapple-password>
# (or edit config/defaults.yaml to point at an SSH key)

# 4. Run a 5-minute survey, report lands in engagement folder
python bin/pineapple_recon.py run --engagement dallas_home_survey --duration 300

# 5. Open the report
# → e:\Thon\hak5\wifi-pineapple-mk7\engagements\dallas_home_survey\recon\<ts>\report.md
```

## Components

```
pineapple_recon/
├── SKILL.md                         # agent-facing skill definition
├── README.md                        # this file
├── bin/pineapple_recon.py           # local CLI (run/start/stop/status/list)
├── remote/pineapple_recon.sh        # on-device collection (POSIX sh / busybox)
├── templates/recon_report.md.tmpl   # Jinja2 report template
└── config/
    ├── defaults.yaml                # host/iface/duration defaults (no secrets)
    └── oui_small.json               # minimal MAC-prefix → vendor table
```

## Architectural commitments

1. **Transport agnostic.** The CLI only needs SSH to reach `172.16.42.1` (or wherever `host` points). It doesn't care if the host is a Windows PC, a Pi, or anything else. The Pi/wardriving stack is a separate concern.
2. **Deterministic reports.** The agent never narrates collection. A template renders the report from normalized JSON. Thon's tokens are preserved for analysis, not narration.
3. **Controllable.** Foreground for quick canvases, background for long soaks. `stop` triggers a clean finalize + report render.
4. **Artifact preservation.** Raw CSV/pcap are always kept alongside the report. Every artifact gets a SHA256. Re-analysis is always possible.
5. **Idempotent remote push.** The Pineapple-side script is pushed fresh each run. No persistent on-device install.

## Configuration precedence

CLI flag → env var → `defaults.yaml` → hardcoded fallback

- Password: `--password` → `$PINEAPPLE_PASSWORD` → `defaults.yaml:password` → error
- Host:     `--host`     → `defaults.yaml:host`     → `172.16.42.1`
- Duration: `--duration` → `defaults.yaml:duration` → `300`

## State

Background-run state lives at `~/.thon/pineapple_recon/state.json`. If you wipe that file, background runs become un-pollable — but the remote process on the Pineapple will continue until `$DURATION` elapses, and artifacts can be pulled manually from `/tmp/pineapple_recon_<run_id>/`.

## Future work (NOT in scope for this skill)

### 1. Pi-based wardriving transport

Goal: Pineapple tethered to a Raspberry Pi; Pi gets internet from iPhone hotspot; operator drives around.

Pieces needed (document in `hak5/wifi-pineapple-mk7/documentation/pi_transport.md`):
- Pi network config: USB-0 upstream to iPhone, USB-1 downstream to Pineapple (or `eth0` / `usb1`)
- NAT/forwarding so Pineapple has internet if desired (usually NOT — air-gap the Pineapple)
- Tailscale on Pi for home-ward reachability
- `PINEAPPLE_PASSWORD` stored in Pi's env or Vaultwarden lookup

### 2. Remote Claude Code dispatch from iPhone

Goal: Cursor/Claude Code on iPhone → SSH to home desktop → start Claude Code session → session invokes skill → skill SSHes to Pi → Pi SSHes to Pineapple.

Pieces needed (document in `hak5/wifi-pineapple-mk7/documentation/remote_dispatch.md`):
- Tailscale SSH from iPhone to home
- `cursor-agent` or `claude` CLI in headless mode
- Session output streamed back or fetched on-demand

### 3. Additional collection surfaces

Currently the skill does AP survey only (per initial design decision). Natural extensions:
- Client enumeration from the same airodump CSV (it already captures this — the awk parser only consumes the AP section)
- Probe request harvesting (reveals known networks of unassociated clients)
- Bluetooth / BLE survey if hardware permits
- GPS tagging if a puck is attached to the Pi

Each should be a **separate mode** (`--mode ap-survey | clients | probes | full`) or a **separate skill**, not a flag avalanche.

## Testing status

**UNTESTED against hardware.** Pineapple was disconnected at time of writing. First hardware run will surface:
- Actual monitor-mode interface name (probably `wlan1mon` via airmon-ng, possibly `mon0`)
- Exact airodump-ng build on device (busybox + OpenWrt package)
- Whether `iw scan` works while airmon owns the iface (probably no — may need to skip)

Expect to iterate on `remote/pineapple_recon.sh` after first live run. The manifest schema and report template should remain stable.
