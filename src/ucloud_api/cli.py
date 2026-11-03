"""`ucloud` command-line interface for Terminal Ubuntu jobs on cloud.sdu.dk."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from typing import List, Optional

from .client import UCloudClient, UCloudError
from .terminal import JobInfo, TerminalJobs


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _fmt_remaining(info: JobInfo) -> str:
    rem = info.remaining_seconds
    if rem is None:
        return "-"
    rem = max(0, int(rem))
    return f"{rem // 3600}h{(rem % 3600) // 60:02d}m"


def _print_info(info: JobInfo, as_json: bool) -> None:
    if as_json:
        print(json.dumps(info.to_dict(), indent=2))
        return
    print(f"job        {info.id}  ({info.name or 'unnamed'})")
    print(f"state      {info.state}")
    print(f"machine    {info.machine} @ {info.provider}")
    print(f"remaining  {_fmt_remaining(info)}")
    print(f"workdir    {info.workdir}   mounts: {', '.join(info.mounts) or '-'}")
    print(f"ssh        {f'ssh {info.ssh.user}@{info.ssh.host} -p {info.ssh.port}' if info.ssh else '-'}")
    print(f"web        {info.url}")


def _parse_params(items: List[str]) -> dict:
    out = {}
    for it in items or []:
        name, _, raw = it.partition("=")
        out[name] = json.loads(raw)
    return out


def cmd_launch(tj: TerminalJobs, a: argparse.Namespace) -> int:
    machine = a.machine or os.environ.get("UCLOUD_MACHINE")
    if not machine:
        _err("--machine is required (or set $UCLOUD_MACHINE). See `ucloud products`.")
        return 2
    info = tj.launch(
        machine,
        hours=a.hours, minutes=a.minutes, workdir=a.workdir, init_script=a.init_script,
        env_dir=a.env_dir, modules_dir=a.modules_dir, mounts=a.mount, name=a.name,
        extra_args=a.extra_args, shell=a.shell, tmux=a.tmux, provider=a.provider,
        app_version=a.app_version, replicas=a.nodes, extra_parameters=_parse_params(a.param),
    )
    _err(f"submitted job {info.id} ({info.url})")
    if not a.no_wait:
        info = tj.wait(info.id, timeout=a.timeout, log=_err)
        if a.setup_script:
            _err(f"running setup script {a.setup_script} on {info.id} ...")
            r = tj.run_script(info.id, a.setup_script)
            sys.stderr.write(r.stdout + r.stderr)
            if r.returncode != 0:
                _err(f"setup script failed with exit code {r.returncode}")
                _print_info(info, a.json)
                return r.returncode
    _print_info(info, a.json)
    return 0


def cmd_status(tj: TerminalJobs, a: argparse.Namespace) -> int:
    info = tj.info(a.job)
    _print_info(info, a.json)
    if a.messages and not a.json:
        print("messages:")
        for m in info.messages:
            print("  " + m)
    return 0


def cmd_list(tj: TerminalJobs, a: argparse.Namespace) -> int:
    jobs = tj.list(include_finished=a.all, limit=a.limit)
    if a.json:
        print(json.dumps([j.to_dict() for j in jobs], indent=2))
        return 0
    if not jobs:
        print("no active terminal-ubuntu jobs" + ("" if a.all else " (use --all to include finished)"))
    for j in jobs:
        ssh = f"{j.ssh.host}:{j.ssh.port}" if j.ssh else "-"
        print(f"{j.id:>9}  {j.state:<9} {j.machine:<24} left={_fmt_remaining(j):<7} ssh={ssh:<24} {j.name or ''}")
    return 0


def cmd_wait(tj: TerminalJobs, a: argparse.Namespace) -> int:
    info = tj.wait(a.job, timeout=a.timeout, ssh_ready=not a.no_ssh, log=_err)
    _print_info(info, a.json)
    return 0


def cmd_extend(tj: TerminalJobs, a: argparse.Namespace) -> int:
    if a.if_less_than is not None:
        extended, info = tj.extend_if_needed(a.job, a.if_less_than, hours=a.hours, minutes=a.minutes)
        _err(("extended" if extended else "not extended") + f"; remaining {_fmt_remaining(info)}")
    else:
        info = tj.extend(a.job, hours=a.hours, minutes=a.minutes)
        _err(f"extended; remaining {_fmt_remaining(info)}")
    _print_info(info, a.json)
    return 0


def cmd_keepalive(tj: TerminalJobs, a: argparse.Namespace) -> int:
    """Keep a job alive: extend whenever remaining time drops below a threshold, until the job ends."""
    while True:
        extended, info = tj.extend_if_needed(a.job, a.if_less_than, hours=a.hours, minutes=a.minutes)
        if extended:
            _err(f"{time.strftime('%H:%M:%S')} extended {a.job}; remaining {_fmt_remaining(info)}")
        if info.is_final:
            _err(f"job {a.job} ended ({info.state})")
            return 0
        time.sleep(a.interval)


def cmd_stop(tj: TerminalJobs, a: argparse.Namespace) -> int:
    for j in a.job:
        tj.stop(j)
        _err(f"terminated {j}")
    return 0


def cmd_ssh(tj: TerminalJobs, a: argparse.Namespace) -> int:
    info = tj.info(a.job)
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    argv = tj.ssh_argv(info, cmd or None, key=a.key, cd=a.cd, tty=not cmd or a.tty)
    if a.print:
        import shlex
        print(shlex.join(argv))
        return 0
    os.execvp(argv[0], argv)
    return 0  # unreachable


def cmd_run_script(tj: TerminalJobs, a: argparse.Namespace) -> int:
    r = tj.run_script(a.job, a.script, key=a.key, cd=a.cd)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


def cmd_ssh_config(tj: TerminalJobs, a: argparse.Namespace) -> int:
    print(tj.ssh_config(tj.info(a.job), alias=a.alias, key=a.key), end="")
    return 0


def cmd_products(tj: TerminalJobs, a: argparse.Namespace) -> int:
    rows = []
    for item in tj.products():
        p = item["product"]
        if p.get("productType") not in (None, "COMPUTE") or p["name"] in ("terminal", "syncthing"):
            continue
        if a.filter and a.filter not in p["name"]:
            continue
        rows.append({"name": p["name"], "provider": p["category"]["provider"], "category": p["category"]["name"],
                     "cpu": p.get("cpu"), "memoryInGigs": p.get("memoryInGigs"), "gpu": p.get("gpu") or 0,
                     "gpuModel": p.get("gpuModel") or ""})
    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r['name']:<28} {r['provider']:<12} cpu={r['cpu']:<4} mem={r['memoryInGigs']:<5}G "
                  f"gpu={r['gpu']} {r['gpuModel']}")
    return 0


def cmd_ls(tj: TerminalJobs, a: argparse.Namespace) -> int:
    if not a.path:
        for d in tj.client.browse_drives():
            print(f"/{d['id']:<10} {d['specification']['title']}")
        return 0
    for f in tj.client.list_files(tj.resolve_path(a.path)):
        kind = "d" if f["status"]["type"] == "DIRECTORY" else "-"
        print(f"{kind} {f['id']}")
    return 0


def cmd_ssh_keys(tj: TerminalJobs, a: argparse.Namespace) -> int:
    if a.action == "add":
        from pathlib import Path
        key = Path(a.pubkey).expanduser().read_text().strip()
        title = a.title or f"{socket.gethostname()} ({Path(a.pubkey).name})"
        print(tj.client.add_ssh_key(title, key))
        return 0
    for k in tj.client.ssh_keys():
        parts = k["specification"]["key"].split()
        print(f"{k['id']:>6}  {k['specification']['title']:<20} {parts[0] if parts else ''} ...{parts[1][-16:] if len(parts) > 1 else ''}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ucloud", description="Launch and use UCloud Terminal Ubuntu jobs over SSH.")
    p.add_argument("--url", help="UCloud base URL (default $UCLOUD_URL or https://cloud.sdu.dk)")
    p.add_argument("--project", help="Project id header override (default: the token's workspace)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def job_cmd(name, fn, help):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(fn=fn)
        return sp

    s = job_cmd("launch", cmd_launch, "start a Terminal Ubuntu job with SSH enabled")
    s.add_argument("-m", "--machine", help="machine type, e.g. cpu-amd-zen5-4-vcpu (see `ucloud products`)")
    s.add_argument("--provider", help="provider, only needed if the machine name is ambiguous")
    s.add_argument("-H", "--hours", type=int, default=1)
    s.add_argument("--minutes", type=int, default=0)
    s.add_argument("-w", "--workdir", help="UCloud folder mounted as the work directory (e.g. Home/myproj)")
    s.add_argument("-i", "--init-script", help="UCloud path of a bash script run at job start (app 'Initialization')")
    s.add_argument("-e", "--env-dir", help="UCloud folder with an environment (venv/conda/...) mounted at /work/<name>")
    s.add_argument("--modules-dir", help="UCloud folder with EasyBuild environment modules (app 'Modules path')")
    s.add_argument("--mount", action="append", default=[], help="additional UCloud folder to mount (repeatable)")
    s.add_argument("--setup-script", help="LOCAL bash script piped over SSH once the job is reachable")
    s.add_argument("-n", "--name", help="job name")
    s.add_argument("--extra-args", help="arguments passed to the init script")
    s.add_argument("--shell", choices=["bash", "zsh", "fish"])
    s.add_argument("--tmux", action="store_true", default=None, help="start the web terminal in tmux")
    s.add_argument("--nodes", type=int, default=1, help="number of nodes (replicas)")
    s.add_argument("--app-version", help="app version (default: latest)")
    s.add_argument("--param", action="append", default=[],
                   help='raw app parameter NAME=JSON, e.g. slurm=\'{"type":"boolean","value":true}\'')
    s.add_argument("--no-wait", action="store_true", help="return right after submission")
    s.add_argument("--timeout", type=float, default=900, help="seconds to wait for SSH (default 900)")
    s.add_argument("--json", action="store_true")

    s = job_cmd("status", cmd_status, "show a job")
    s.add_argument("job")
    s.add_argument("--messages", action="store_true", help="show job status messages")
    s.add_argument("--json", action="store_true")

    s = job_cmd("list", cmd_list, "list terminal-ubuntu jobs")
    s.add_argument("--all", action="store_true", help="include finished jobs")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--json", action="store_true")

    s = job_cmd("wait", cmd_wait, "wait until a job is running and SSH works")
    s.add_argument("job")
    s.add_argument("--timeout", type=float, default=900)
    s.add_argument("--no-ssh", action="store_true", help="only wait for RUNNING")
    s.add_argument("--json", action="store_true")

    s = job_cmd("extend", cmd_extend, "extend a job's time allocation")
    s.add_argument("job")
    s.add_argument("-H", "--hours", type=int, default=1)
    s.add_argument("--minutes", type=int, default=0)
    s.add_argument("--if-less-than", type=int, metavar="MINUTES",
                   help="only extend if fewer than MINUTES remain (idempotent; safe for cron/agents)")
    s.add_argument("--json", action="store_true")

    s = job_cmd("keepalive", cmd_keepalive, "keep extending a job until it ends or you press Ctrl-C")
    s.add_argument("job")
    s.add_argument("--if-less-than", type=int, default=30, metavar="MINUTES")
    s.add_argument("-H", "--hours", type=int, default=1)
    s.add_argument("--minutes", type=int, default=0)
    s.add_argument("--interval", type=float, default=300, help="seconds between checks")

    s = job_cmd("stop", cmd_stop, "terminate job(s)")
    s.add_argument("job", nargs="+")

    s = job_cmd("ssh", cmd_ssh, "SSH into a job (interactive), or run a command: ucloud ssh JOB -- CMD")
    s.add_argument("job")
    s.add_argument("command", nargs="*", help="remote command; put it after `--`")
    s.add_argument("--cd", help="remote directory (default: the job's work directory)")
    s.add_argument("--key", help="private key (default: auto-detect a registered ~/.ssh key)")
    s.add_argument("-t", "--tty", action="store_true", help="force a TTY when running a command")
    s.add_argument("--print", action="store_true", help="print the ssh command instead of running it")

    s = job_cmd("run-script", cmd_run_script, "pipe a local bash script to a job and run it")
    s.add_argument("job")
    s.add_argument("script")
    s.add_argument("--cd")
    s.add_argument("--key")

    s = job_cmd("ssh-config", cmd_ssh_config, "print an ~/.ssh/config Host block for a job")
    s.add_argument("job")
    s.add_argument("--alias")
    s.add_argument("--key")

    s = job_cmd("products", cmd_products, "list machine types")
    s.add_argument("--filter")
    s.add_argument("--json", action="store_true")

    s = job_cmd("ls", cmd_ls, "list drives, or files in a UCloud folder (e.g. Home/myproj)")
    s.add_argument("path", nargs="?")

    s = job_cmd("ssh-keys", cmd_ssh_keys, "list or add SSH public keys on UCloud")
    s.add_argument("action", choices=["list", "add"], nargs="?", default="list")
    s.add_argument("pubkey", nargs="?")
    s.add_argument("--title")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        tj = TerminalJobs(UCloudClient(base_url=args.url, project=args.project))
        return args.fn(tj, args) or 0
    except (UCloudError, ValueError, TimeoutError) as e:
        _err(f"error: {e}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
