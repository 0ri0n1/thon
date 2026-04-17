---
name: Pineapple Recon
description: WiFi Pineapple Mk7 AP-survey recon — dispatches a canned collection script over SSH, pulls artifacts, renders a standardized report from template. Token-efficient: Thon invokes, the script collects, the template renders.
---

# Pineapple Recon: WiFi Pineapple Mk7 AP-Survey Skill

Get the lay of the land. Thon says "run recon" — a script runs on the Pineapple, a template renders the report, Thon reads the report.

> [!IMPORTANT]
> Operates under operator authority (Eddie). No programmatic scope gate — operator discipline via engagement naming is the control.

## When to use

Trigger this skill when the user asks for:
- "Run pineapple recon" / "canvas the RF environment" / "lay of the land"
- "What APs are around" / "wireless site survey"
- Start-of-engagement reconnaissance on the WiFi Pineapple Mk7

Do NOT use this skill for:
- Active attacks (deauth, Evil Twin, PineAP spoofing) — different skills, different authority gates
- Client-specific recon / association-trail analysis — separate skill
- Anything on hardware other than the Mk7

## Prerequisites

1. **Pineapple connected** (USB tether to host, default IP `172.16.42.1`)
2. **SSH credentials available**: set `PINEAPPLE_PASSWORD` env var
3. **Python deps** on the host running the skill:
   ```bash
   pip install paramiko jinja2 pyyaml
   ```
4. **Engagement name** chosen — output lands under `hak5/wifi-pineapple-mk7/engagements/<name>/recon/<timestamp>/`

## Phased Workflow

### Phase 1 — Intelligence gathering (MANDATORY, per `intelligence-first.mdc` rule)

Before invoking:
1. Confirm engagement context — what are we recon-ing and why?
2. Confirm Pineapple reachability: `ping 172.16.42.1` or user confirms it's connected
3. Confirm operator wants collection now (recon has RF emissions signature)
4. Decide: foreground (quick soak, blocking) or background (long soak, detachable)

### Phase 2 — Invocation

**Foreground (quick, blocks):**
```bash
PINEAPPLE_PASSWORD=<your-password> python .agents/skills/pineapple_recon/bin/pineapple_recon.py \
  run --engagement <name> --duration 300
```

**Background (long soak, returns run_id):**
```bash
PINEAPPLE_PASSWORD=<your-password> python .agents/skills/pineapple_recon/bin/pineapple_recon.py \
  start --engagement <name> --duration 1800
# → stdout: <run_id>
```

**Control a background run:**
```bash
# check status (running | complete | failed | stopped | unknown)
python .agents/skills/pineapple_recon/bin/pineapple_recon.py status <run_id>

# stop early — triggers artifact pull and report render
python .agents/skills/pineapple_recon/bin/pineapple_recon.py stop <run_id>

# list active and recent runs
python .agents/skills/pineapple_recon/bin/pineapple_recon.py list
```

### Phase 3 — Collection (automatic, zero agent tokens)

The remote script (`remote/pineapple_recon.sh`) is pushed to `/tmp/pineapple_recon.sh` on the Pineapple and executed with:
- `DURATION=<seconds>` (default 300)
- `IFACE=wlan1` (configurable)
- `BAND=both|2|5` (configurable)
- `OUT_DIR=/tmp/pineapple_recon_<run_id>`

On device, it:
1. Snapshots interface state (`iw dev`, `iwconfig`, `ifconfig`)
2. Puts `$IFACE` in monitor mode (`airmon-ng` or manual `iw` fallback)
3. Runs one-shot `iw scan` for cross-reference dataset
4. Runs `airodump-ng -w scan --output-format csv,pcap` for `$DURATION` seconds
5. Converts airodump CSV → normalized `manifest.json` via awk (no Python on Pineapple)
6. Computes SHA256 integrity on all artifacts
7. Restores interface to managed mode

### Phase 4 — Artifact retrieval (automatic)

The local CLI pulls `/tmp/pineapple_recon_<run_id>/` via tar-over-SSH (faster than per-file sftp), extracts to:
```
hak5/wifi-pineapple-mk7/engagements/<engagement>/recon/<YYYYMMDD-HHMMSSZ>/raw/
```
Then cleans up the remote directory.

### Phase 5 — Report rendering (deterministic template, zero agent tokens)

`manifest.json` is fed through `templates/recon_report.md.tmpl` (Jinja2). The template computes:
- Encryption breakdown (Open / WEP / WPA / WPA2 / WPA2-ENT / WPA3 / Unknown)
- 2.4 vs 5 GHz distribution
- Hidden SSID count
- Flagged findings: Open / WEP / Enterprise / Hidden
- Full AP table sorted by signal (dBm descending)
- Vendor lookup from `config/oui_small.json`
- Integrity block + run log tail

Output:
```
hak5/wifi-pineapple-mk7/engagements/<engagement>/recon/<timestamp>/report.md
```

### Phase 6 — Token preservation (Thon's job)

After the skill completes:
1. **Print the report path** to the user
2. **Do NOT** read the CSV, pcap, or full manifest
3. **Do NOT** narrate collection details
4. **If asked "what did you find"** — read `report.md` only, summarize findings
5. **If asked to correlate across runs** — that is a separate skill invocation

## Output contract

Every successful run produces:
```
hak5/wifi-pineapple-mk7/engagements/<engagement>/recon/<YYYYMMDD-HHMMSSZ>/
├── report.md                 ← agent reads this
├── run.log                   ← stderr/stdout tail for diagnostics
└── raw/                      ← preserved, do not parse directly
    ├── scan.csv
    ├── scan.pcap
    ├── iw-scan.txt
    ├── iface_info.txt
    ├── manifest.json
    └── sha256sums.txt
```

Run state (for background runs) lives at `~/.thon/pineapple_recon/state.json` — survives shell sessions so `stop`/`status` work across invocations.

## Failure modes and handling

| Symptom | Likely cause | Resolution |
|---|---|---|
| `SSH auth failed` | wrong password, no env var | set `PINEAPPLE_PASSWORD` |
| `SSH error: No route to host` | Pineapple not connected / wrong IP | verify USB tether, check `172.16.42.1` |
| `airodump-ng or iw missing` | firmware regression on device | SSH in manually, `opkg update && opkg install ...` |
| `interface wlan1 not present` | different iface on this firmware | check `iw dev`, pass `--iface wlan1mon` etc. |
| `manifest.json missing` | airodump crashed or zero captures | inspect `run.log` in artifact dir |
| `monitor_mode_failed` | driver quirk, iface already busy | check `iface_info.txt`, may need reboot |

## Security & operational notes

- **Passive collection only.** No deauth, no injection. Airodump in channel-hop mode does not transmit (beyond required hardware-level ACK behavior in promiscuous mode).
- **RF footprint:** The Pineapple's radios are relatively quiet in monitor mode. Nearby WIDS won't see meaningful emissions from this skill.
- **Data sensitivity:** `manifest.json` and `scan.pcap` contain BSSIDs and probe-request SSIDs from surrounding devices — treat as sensitive under GDPR/CCPA if operating in a regulated context.
- **Password hygiene:** Rotate the default password on first opportunity; prefer key auth and set `key_path` in `config/defaults.yaml`.

## See also

- Device README: `hak5/wifi-pineapple-mk7/README.md`
- Related skill (existing, broader): `hak5-usb-wifi` (Pineapple active attacks)
- Doctrine: operational layer 10 (Tactics) — reconnaissance phase selection
