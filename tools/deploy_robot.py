#!/usr/bin/env python3
"""Deploy robotctl client code + env to a robot over SSH (paramiko).

    .venv/bin/python tools/deploy_robot.py --host 192.168.42.10 \
        --user wheeltec --pass-file ~/.config/wheeltec-ssh-pass \
        --servers "http://192.168.1.142:8090 http://192.168.42.1:8090" \
        --id s2000-1

Does: recon running client (owner + env path) -> backup old files ->
upload client.py/navd.py/run_client.sh -> MERGE env (SERVER list + NAV=1,
token/ID preserved in-place, chmod 600) -> restart as the same user ->
verify banner + hub reachability + LAN probe. Never prints secrets.
"""
import argparse
import datetime
import re
import sys
import time

import paramiko


def run(ssh, cmd, timeout=60, show=True):
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    o = out.read().decode(errors="replace")
    e = err.read().decode(errors="replace")
    if show and (o or e):
        print((o + e).strip()[:1500])
    return o, e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", default="wheeltec")
    ap.add_argument("--pass-file", required=True,
                    help="path to file holding the login/sudo password")
    ap.add_argument("--servers", required=True,
                    help='space-separated hub URL list, best first')
    ap.add_argument("--id", default="s2000-1")
    args = ap.parse_args()
    pw = open(args.pass_file).read().strip()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(args.host, username=args.user, password=pw, timeout=15)
    print(f"ssh up -> {args.host}")

    # ---- recon: who runs the client, with which env?
    o, _ = run(ssh,
               "pgrep -f 'robotctl/client\\.py' | head -1 | "
               "xargs -r ps -o user=,cmd= -p", show=False)
    owner = o.split()[0] if o.strip() else args.user
    print("running client:", o.strip() or "(none — clean start)", "| user:", owner)
    o, _ = run(ssh, "ls ~/robotctl/robotctl.env ~/.config/wheeltec-robotctl.env 2>/dev/null",
               show=False)
    envs = o.split()
    base_env = "~/robotctl/robotctl.env"
    for cand in envs:
        if cand.endswith("robotctl.env"):
            base_env = cand
            break
    print("env base:", base_env, "candidates:", envs or "none")

    # ---- backup + upload
    ts = datetime.datetime.now().strftime("%H%M%S")
    run(ssh, f"cd ~/robotctl 2>/dev/null && for f in client.py navd.py run_client.sh; do "
             f"[ -f $f ] && cp -a $f $f.bak-{ts}; done; mkdir -p ~/robotctl; echo backed-up")
    sftp = ssh.open_sftp()
    for name in ("client.py", "navd.py", "run_client.sh"):
        sftp.put(f"robotctl/{name}", f"/home/{args.user}/robotctl/{name}")
    sftp.chmod(f"/home/{args.user}/robotctl/run_client.sh", 0o755)
    print("uploaded client.py navd.py run_client.sh")

    # ---- env merge ON THE ROBOT (token never leaves the robot)
    merge = r'''
import os, re, sys
servers, robot_id = sys.argv[1], sys.argv[2]
base = os.path.expanduser("%(base)s")
lines = open(base).read().splitlines() if os.path.exists(base) else []
out, seen = [], set()
for ln in lines:
    k = ln.split("=", 1)[0].strip()
    if k == "WEBCTL_SERVER":
        ln = 'WEBCTL_SERVER="' + servers + '"'; seen.add(k)
    elif k == "WEBCTL_ROBOT_ID":
        ln = "WEBCTL_ROBOT_ID=" + robot_id; seen.add(k)
    elif k == "WEBCTL_NAV":
        continue
    else:
        seen.add(k)
    out.append(ln)
for extra in (["WEBCTL_ROBOT_ID=" + robot_id] if "WEBCTL_ROBOT_ID" not in seen else []) \
        + (['WEBCTL_SERVER="' + servers + '"'] if "WEBCTL_SERVER" not in seen else []) \
        + ["WEBCTL_NAV=1"]:
    out.append(extra)
if not any(l.startswith("WEBCTL_ROBOT_TOKEN=") for l in out):
    sys.exit("ABORT: no WEBCTL_ROBOT_TOKEN anywhere — refusing to deploy a "
             "tokenless client (would 401-loop)")
dst = os.path.expanduser("~/robotctl/robotctl.env")
open(dst, "w").write("\n".join(out) + "\n")
os.chmod(dst, 0o600)
tok = [l for l in out if l.startswith("WEBCTL_ROBOT_TOKEN=")][0][len("WEBCTL_ROBOT_TOKEN="):]
print("env written:", dst, "| keys:", len(out), "| token len:", len(tok))
''' % {"base": base_env}
    from shlex import quote
    cmd = f"python3 - {quote(args.servers)} {quote(args.id)} <<'MERGE_EOF'\n{merge}\nMERGE_EOF"
    o, e = run(ssh, cmd, show=False)
    print((o + e).strip()[:400])
    if "ABORT" in o + e:
        sys.exit(1)

    # ---- restart as the SAME user the old client ran under
    sud = "" if owner == args.user else f"echo {quote(pw)} | sudo -S -p '' "
    start = ("setsid bash -c 'nohup bash ~/robotctl/run_client.sh "
             "~/robotctl/robotctl.env > ~/robotctl/webctl.log 2>&1 </dev/null &'")
    run(ssh, f"{sud}pkill -f 'robotctl/client\\.py'; sleep 1; {sud}{start}",
        show=False)
    time.sleep(7)
    print("--- pgrep:", end=" ")
    run(ssh, "pgrep -f 'robotctl/client\\.py' | wc -l")
    print("--- banner/log:")
    run(ssh, "grep -E 'robotctl|websocket|failover|ERROR' ~/robotctl/webctl.log | tail -6")

    # ---- LAN probe from the robot (VPN-free readiness)
    lan = args.servers.split()[0]
    o, _ = run(ssh, f"curl -s -m3 -o /dev/null -w 'LAN-probe {lan} -> %{{http_code}}' "
                    f"{lan}/api/healthz 2>&1 || echo LAN-probe unreachable")
    print(o.strip())
    ssh.close()


if __name__ == "__main__":
    main()
