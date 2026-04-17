#!/usr/bin/env python3
"""pineapple_recon — local CLI for WiFi Pineapple Mk7 AP-survey recon.

Dispatches the remote collection script over SSH, tracks runs, and renders a
standardized report from the captured artifacts. Transport-agnostic: works
identically whether the Pineapple is USB-tethered to this host or reached via
a Pi pivot, provided SSH to the Pineapple resolves.

Commands:
  run     foreground — blocks until collection completes, renders report
  start   background — returns run_id, dispatches remote script
  status  query a run_id (running | complete | failed | unknown)
  stop    terminate a running run_id, pull artifacts, render report
  list    list active and recent runs

Dependencies:
  paramiko >= 3.0
  jinja2 >= 3.0
  pyyaml >= 6.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import paramiko
except ImportError:
    sys.stderr.write("missing dependency: paramiko (pip install paramiko)\n")
    sys.exit(2)

try:
    import yaml
except ImportError:
    sys.stderr.write("missing dependency: pyyaml (pip install pyyaml)\n")
    sys.exit(2)

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError:
    sys.stderr.write("missing dependency: jinja2 (pip install jinja2)\n")
    sys.exit(2)


SKILL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_ROOT.parent.parent.parent  # .../Thon
DEFAULT_CONFIG = SKILL_ROOT / "config" / "defaults.yaml"
TEMPLATE_DIR = SKILL_ROOT / "templates"
REMOTE_SCRIPT = SKILL_ROOT / "remote" / "pineapple_recon.sh"

STATE_DIR = Path.home() / ".thon" / "pineapple_recon"
STATE_FILE = STATE_DIR / "state.json"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class Run:
    run_id: str
    engagement: str
    host: str
    user: str
    iface: str
    band: str
    duration: int
    remote_out_dir: str
    remote_pid: int | None
    local_out_dir: str
    started_utc: str
    ended_utc: str | None = None
    status: str = "running"  # running | complete | failed | stopped
    error: str | None = None


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"runs": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"runs": {}}


def _save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _put_run(run: Run) -> None:
    state = _load_state()
    state["runs"][run.run_id] = asdict(run)
    _save_state(state)


def _get_run(run_id: str) -> Run | None:
    state = _load_state()
    data = state["runs"].get(run_id)
    if not data:
        return None
    return Run(**data)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def _ssh_connect(host: str, user: str, password: str | None, key_path: str | None,
                 port: int = 22, timeout: int = 15) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict[str, Any] = {
        "hostname": host,
        "username": user,
        "port": port,
        "timeout": timeout,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if key_path:
        kwargs["key_filename"] = key_path
    elif password:
        kwargs["password"] = password
    client.connect(**kwargs)
    return client


def _ssh_exec(client: paramiko.SSHClient, cmd: str, timeout: int | None = None) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def _sftp_put(client: paramiko.SSHClient, local: Path, remote: str) -> None:
    sftp = client.open_sftp()
    try:
        sftp.put(str(local), remote)
        sftp.chmod(remote, 0o755)
    finally:
        sftp.close()


def _sftp_get_dir(client: paramiko.SSHClient, remote_dir: str, local_dir: Path) -> None:
    """Pull the remote artifact dir via tar-over-ssh (faster than per-file sftp)."""
    local_dir.mkdir(parents=True, exist_ok=True)
    tar_remote = f"/tmp/{Path(remote_dir).name}.tar.gz"
    rc, _, err = _ssh_exec(client, f"cd {remote_dir} && tar czf {tar_remote} .")
    if rc != 0:
        raise RuntimeError(f"remote tar failed: {err}")

    sftp = client.open_sftp()
    local_tar = local_dir / "artifacts.tar.gz"
    try:
        sftp.get(tar_remote, str(local_tar))
    finally:
        sftp.close()
    _ssh_exec(client, f"rm -f {tar_remote}")

    with tarfile.open(local_tar, "r:gz") as tf:
        tf.extractall(local_dir)
    local_tar.unlink()


# ---------------------------------------------------------------------------
# Core dispatch
# ---------------------------------------------------------------------------

def _preflight(client: paramiko.SSHClient, iface: str) -> None:
    rc, out, _ = _ssh_exec(client, "command -v airodump-ng && command -v iw")
    if rc != 0:
        raise RuntimeError("airodump-ng or iw missing on Pineapple")
    rc, out, _ = _ssh_exec(client, f"iw dev {iface} info")
    if rc != 0:
        raise RuntimeError(f"interface {iface} not present (iw dev shows: see manual)")


def _push_script(client: paramiko.SSHClient) -> str:
    remote_path = "/tmp/pineapple_recon.sh"
    _sftp_put(client, REMOTE_SCRIPT, remote_path)
    return remote_path


def _engagement_outdir(engagement: str, stamp: str) -> Path:
    root = REPO_ROOT / "hak5" / "wifi-pineapple-mk7" / "engagements" / engagement / "recon" / stamp
    root.mkdir(parents=True, exist_ok=True)
    return root


def _dispatch(args: argparse.Namespace, cfg: dict, *, background: bool) -> Run:
    host = args.host or cfg.get("host", "172.16.42.1")
    user = args.user or cfg.get("user", "root")
    password = args.password or os.environ.get("PINEAPPLE_PASSWORD") or cfg.get("password")
    key = args.key or cfg.get("key_path")
    iface = args.iface or cfg.get("iface", "wlan1")
    band = args.band or cfg.get("band", "both")
    duration = args.duration or cfg.get("duration", 300)

    run_id = uuid.uuid4().hex[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    local_out = _engagement_outdir(args.engagement, stamp)

    print(f"[+] run_id={run_id} engagement={args.engagement} host={host} iface={iface} band={band} duration={duration}s", file=sys.stderr)

    client = _ssh_connect(host, user, password, key)
    try:
        _preflight(client, iface)
        remote_script = _push_script(client)
        remote_out = f"/tmp/pineapple_recon_{run_id}"

        env_prefix = (
            f"DURATION={duration} IFACE={iface} BAND={band} OUT_DIR={remote_out}"
        )

        if background:
            cmd = f"{env_prefix} nohup sh {remote_script} >/tmp/pineapple_recon_{run_id}.log 2>&1 & echo $!"
            rc, out, err = _ssh_exec(client, cmd)
            if rc != 0:
                raise RuntimeError(f"remote dispatch failed rc={rc}: {err}")
            pid = int(out.strip().splitlines()[-1])
            run = Run(
                run_id=run_id, engagement=args.engagement, host=host, user=user,
                iface=iface, band=band, duration=duration,
                remote_out_dir=remote_out, remote_pid=pid,
                local_out_dir=str(local_out),
                started_utc=datetime.now(timezone.utc).isoformat(),
                status="running",
            )
            _put_run(run)
            print(f"[+] dispatched background pid={pid}", file=sys.stderr)
            print(run_id)
            return run
        else:
            cmd = f"{env_prefix} sh {remote_script}"
            print(f"[+] foreground run, blocking for ~{duration}s", file=sys.stderr)
            rc, out, err = _ssh_exec(client, cmd, timeout=duration + 120)
            if rc != 0:
                raise RuntimeError(f"remote script exit rc={rc}: {err or out}")
            run = Run(
                run_id=run_id, engagement=args.engagement, host=host, user=user,
                iface=iface, band=band, duration=duration,
                remote_out_dir=remote_out, remote_pid=None,
                local_out_dir=str(local_out),
                started_utc=datetime.now(timezone.utc).isoformat(),
                status="running",
            )
            _put_run(run)
            _finalize(run, client)
            return run
    finally:
        client.close()


def _finalize(run: Run, client: paramiko.SSHClient | None = None) -> None:
    """Pull artifacts, render report, update state to complete/failed."""
    own_client = False
    if client is None:
        state = _load_state()
        data = state["runs"][run.run_id]
        client = _ssh_connect(run.host, run.user,
                              os.environ.get("PINEAPPLE_PASSWORD") or
                              _load_config(DEFAULT_CONFIG).get("password"),
                              _load_config(DEFAULT_CONFIG).get("key_path"))
        own_client = True
    try:
        local_raw = Path(run.local_out_dir) / "raw"
        try:
            _sftp_get_dir(client, run.remote_out_dir, local_raw)
        except Exception as exc:
            run.status = "failed"
            run.error = f"artifact pull failed: {exc}"
            run.ended_utc = datetime.now(timezone.utc).isoformat()
            _put_run(run)
            raise

        # Move run.log up one level for visibility
        log_src = local_raw / "run.log"
        if log_src.exists():
            shutil.copy(log_src, Path(run.local_out_dir) / "run.log")

        manifest_path = local_raw / "manifest.json"
        if not manifest_path.exists():
            run.status = "failed"
            run.error = "manifest.json missing from artifacts"
            run.ended_utc = datetime.now(timezone.utc).isoformat()
            _put_run(run)
            return

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _render_report(run, manifest, Path(run.local_out_dir))

        # Cleanup remote
        _ssh_exec(client, f"rm -rf {run.remote_out_dir}")

        run.status = "complete"
        run.ended_utc = datetime.now(timezone.utc).isoformat()
        _put_run(run)
        print(f"[+] report: {Path(run.local_out_dir) / 'report.md'}", file=sys.stderr)
    finally:
        if own_client:
            client.close()


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _classify_encryption(privacy: str, auth: str) -> str:
    p = (privacy or "").strip().upper()
    a = (auth or "").strip().upper()
    if not p or p == "OPN":
        return "OPEN"
    if "WPA3" in p:
        return "WPA3"
    if "WPA2" in p and ("MGT" in a or "802.1X" in a):
        return "WPA2-ENT"
    if "WPA2" in p:
        return "WPA2"
    if "WPA" in p:
        return "WPA"
    if "WEP" in p:
        return "WEP"
    return p or "UNKNOWN"


def _vendor_oui(bssid: str, oui_table: dict[str, str]) -> str:
    if not bssid or len(bssid) < 8:
        return "?"
    prefix = bssid.upper().replace("-", ":")[:8]
    return oui_table.get(prefix, "?")


def _render_report(run: Run, manifest: dict, out_dir: Path) -> None:
    aps_raw = manifest.get("access_points", [])

    # Load minimal OUI table if shipped
    oui_path = SKILL_ROOT / "config" / "oui_small.json"
    oui_table: dict[str, str] = {}
    if oui_path.exists():
        try:
            oui_table = json.loads(oui_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    aps = []
    ch_2g = 0
    ch_5g = 0
    enc_counts: dict[str, int] = {}
    hidden = 0
    for ap in aps_raw:
        try:
            ch = int(str(ap.get("channel", "0")).strip() or 0)
        except ValueError:
            ch = 0
        try:
            sig = int(str(ap.get("signal_dbm", "0")).strip() or 0)
        except ValueError:
            sig = 0
        enc = _classify_encryption(ap.get("privacy", ""), ap.get("auth", ""))
        enc_counts[enc] = enc_counts.get(enc, 0) + 1
        if ap.get("hidden"):
            hidden += 1
        if 1 <= ch <= 14:
            ch_2g += 1
        elif ch >= 36:
            ch_5g += 1
        aps.append({
            "bssid": ap.get("bssid", ""),
            "essid": ap.get("essid") or "<hidden>",
            "channel": ch,
            "encryption": enc,
            "signal_dbm": sig,
            "beacons": ap.get("beacons", ""),
            "vendor": _vendor_oui(ap.get("bssid", ""), oui_table),
            "hidden": ap.get("hidden", False),
        })

    aps.sort(key=lambda a: a["signal_dbm"], reverse=True)

    flagged = {
        "open": [a for a in aps if a["encryption"] == "OPEN"],
        "wep": [a for a in aps if a["encryption"] == "WEP"],
        "enterprise": [a for a in aps if a["encryption"] == "WPA2-ENT"],
        "hidden": [a for a in aps if a["hidden"]],
    }

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),
                      undefined=StrictUndefined,
                      trim_blocks=True, lstrip_blocks=True)
    tmpl = env.get_template("recon_report.md.tmpl")

    raw_dir = out_dir / "raw"
    sha_path = raw_dir / "sha256sums.txt"
    integrity = sha_path.read_text(encoding="utf-8") if sha_path.exists() else "(missing)"

    log_tail = ""
    log_path = out_dir / "run.log"
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        log_tail = "\n".join(lines[-20:])

    rendered = tmpl.render(
        run=asdict(run),
        manifest=manifest,
        aps=aps,
        ap_count=len(aps),
        ch_2g=ch_2g,
        ch_5g=ch_5g,
        hidden_count=hidden,
        enc_counts=enc_counts,
        flagged=flagged,
        integrity=integrity,
        log_tail=log_tail,
        generated_utc=datetime.now(timezone.utc).isoformat(),
    )
    (out_dir / "report.md").write_text(rendered, encoding="utf-8")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load_config(Path(args.config) if args.config else DEFAULT_CONFIG)
    _dispatch(args, cfg, background=False)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    cfg = _load_config(Path(args.config) if args.config else DEFAULT_CONFIG)
    _dispatch(args, cfg, background=True)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    run = _get_run(args.run_id)
    if run is None:
        print("unknown")
        return 1

    if run.status == "running" and run.remote_pid:
        cfg = _load_config(DEFAULT_CONFIG)
        try:
            client = _ssh_connect(run.host, run.user,
                                  os.environ.get("PINEAPPLE_PASSWORD") or cfg.get("password"),
                                  cfg.get("key_path"), timeout=10)
            rc, _, _ = _ssh_exec(client, f"kill -0 {run.remote_pid}")
            client.close()
            if rc != 0:
                run.status = "complete"
                _put_run(run)
                try:
                    _finalize(run)
                except Exception as exc:  # finalization failure shouldn't mask status
                    print(f"(finalize error: {exc})", file=sys.stderr)
        except (socket.error, paramiko.SSHException) as exc:
            print(f"(status check ssh failed: {exc})", file=sys.stderr)

    print(run.status)
    if args.verbose:
        print(json.dumps(asdict(run), indent=2))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    run = _get_run(args.run_id)
    if run is None:
        print("unknown run_id", file=sys.stderr)
        return 1
    if run.status != "running":
        print(f"run not running (status={run.status})", file=sys.stderr)
        return 1
    cfg = _load_config(DEFAULT_CONFIG)
    client = _ssh_connect(run.host, run.user,
                          os.environ.get("PINEAPPLE_PASSWORD") or cfg.get("password"),
                          cfg.get("key_path"))
    try:
        if run.remote_pid:
            _ssh_exec(client, f"kill -TERM {run.remote_pid} 2>/dev/null; sleep 2; kill -KILL {run.remote_pid} 2>/dev/null")
        # Wait briefly for finalization on-device
        for _ in range(10):
            rc, _, _ = _ssh_exec(client, f"test -f {run.remote_out_dir}/status")
            if rc == 0:
                break
            time.sleep(1)
        _finalize(run, client)
    finally:
        client.close()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    state = _load_state()
    runs = [Run(**v) for v in state["runs"].values()]
    runs.sort(key=lambda r: r.started_utc, reverse=True)
    if not runs:
        print("(no runs)")
        return 0
    for r in runs[: args.limit]:
        print(f"{r.run_id}  {r.status:9s}  {r.engagement:30s}  {r.started_utc}  host={r.host}")
    return 0


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", help="Pineapple IP or hostname (default: 172.16.42.1)")
    p.add_argument("--user", help="SSH user (default: root)")
    p.add_argument("--password", help="SSH password. Prefer PINEAPPLE_PASSWORD env var.")
    p.add_argument("--key", help="SSH private key path")
    p.add_argument("--iface", help="Wireless interface (default: wlan1)")
    p.add_argument("--band", choices=["2", "5", "both"], help="Band to scan")
    p.add_argument("--duration", type=int, help="Seconds (default: 300)")
    p.add_argument("--engagement", required=True, help="Engagement name (output folder)")
    p.add_argument("--config", help="Override defaults.yaml path")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pineapple-recon",
                                     description="WiFi Pineapple Mk7 AP-survey recon")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="foreground run")
    _add_common(p_run)
    p_run.set_defaults(func=cmd_run)

    p_start = sub.add_parser("start", help="background start, returns run_id")
    _add_common(p_start)
    p_start.set_defaults(func=cmd_start)

    p_status = sub.add_parser("status", help="query run status")
    p_status.add_argument("run_id")
    p_status.add_argument("-v", "--verbose", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_stop = sub.add_parser("stop", help="stop and finalize a background run")
    p_stop.add_argument("run_id")
    p_stop.set_defaults(func=cmd_stop)

    p_list = sub.add_parser("list", help="list runs")
    p_list.add_argument("-n", "--limit", type=int, default=20)
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except paramiko.AuthenticationException:
        print("SSH auth failed. Set PINEAPPLE_PASSWORD env or use --password/--key.", file=sys.stderr)
        return 3
    except (socket.error, paramiko.SSHException) as exc:
        print(f"SSH error: {exc}", file=sys.stderr)
        return 4
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
