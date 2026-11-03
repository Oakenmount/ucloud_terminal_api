"""`ucloud` command-line interface for Terminal Ubuntu jobs on cloud.sdu.dk."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import List, Optional

from .client import UCloudClient, UCloudError
from .machines import CATALOG_HELP
from .terminal import JobInfo, TerminalJobs

QUICKSTART = """\
quick start:
  ucloud doctor                                   check token, SSH key and allocations
  ucloud products --usable                        machine types you can launch right now
  ucloud launch -m cpu-amd-zen5-4-vcpu -H 2 -w Home/myproj --json
  ucloud ssh JOB -- 'python train.py'             run a command in the work directory
  ucloud extend JOB --if-less-than 30 -H 1        top up time only when needed
  ucloud stop JOB                                 stop billing

paths: UCloud folders are written as "<Drive title>/sub/dir" (e.g. Home/myproj) or "/<drive id>/sub/dir".
       Inside the job, each mounted folder is at /work/<folder name>.

exit codes: 0 ok, 1 UCloud/validation error, 2 bad CLI usage, 130 interrupted.
            `ssh JOB -- CMD`, `run-script` and `launch --setup-script` return the remote exit code.
"""

LAUNCH_EPILOG = CATALOG_HELP + """
persistence:
  Folders you mount are read and written in place. Everything else written under /work goes
  to the job's output folder (Home/Jobs/Terminal Ubuntu/<job id>). Anything outside /work
  (apt packages, ~/.cache, /tmp) is lost when the job ends.

examples:
  ucloud launch -m cpu-amd-zen5-8-vcpu -H 4 -w Home/myproj -e Home/envs/py311 --json
  ucloud launch -m gpu-nvidia-b200-1-mig.1g -H 1 -w Home/myproj -i Home/myproj/start.sh
  ucloud launch -m cpu-amd-zen5-4-vcpu --setup-script ./bootstrap.sh --no-wait
"""


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
    print(f"output     {info.output_folder or '-'}")
    print(f"ssh        {f'ssh {info.ssh.user}@{info.ssh.host} -p {info.ssh.port}' if info.ssh else '-'}")
    print(f"web        {info.url}")


def _parse_params(items: List[str]) -> dict:
    out = {}
    for it in items or []:
        name, sep, raw = it.partition("=")
        if not sep:
            raise ValueError(f"--param must be NAME=JSON, got {it!r}")
        out[name] = json.loads(raw)
    return out


def _unit_name(unit: str) -> str:
    return {"Core": "core-h", "GPU": "GPU-h"}.get(unit, f"{unit}-h")


# ---------------------------------------------------------------------------------------------- commands

def cmd_launch(tj: TerminalJobs, a: argparse.Namespace) -> int:
    machine = a.machine or os.environ.get("UCLOUD_MACHINE")
    if not machine:
        _err("--machine is required (or set $UCLOUD_MACHINE). See `ucloud products --usable`.")
        return 2
    info = tj.launch(
        machine, check_allocation=not a.force, log=_err,
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
        print(shlex.join(argv))
        return 0
    os.execvp(argv[0], argv)
    return 0  # unreachable


def cmd_logs(tj: TerminalJobs, a: argparse.Namespace) -> int:
    """Job console output (startup, init script, batch script) = /work/stdout-<rank>.log."""
    info = tj.info(a.job)
    tail = f"tail -n {a.lines}" + (" -F" if a.follow else "")
    argv = tj.ssh_argv(info, [f"{tail} /work/stdout-{a.rank}.log"], key=a.key, cd="", batch=True)
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


def cmd_products(tj: Optional[TerminalJobs], a: argparse.Namespace) -> int:
    if a.catalog:
        print(CATALOG_HELP, end="")
        return 0
    rows = [m for m in tj.machines()
            if (not a.usable or m.usable) and (not a.filter or a.filter in m.name)]
    if a.json:
        print(json.dumps([m.to_dict() for m in rows], indent=2))
        return 0
    print(f"{'NAME':<28} {'PROVIDER':<11} {'CPU':>4} {'MEM':>6} {'GPU':<22} {'COST/H':<12} {'QUEUE':<9} ALLOCATION")
    for m in rows:
        gpu = f"{m.gpu}x {m.gpu_model}" if m.gpu else "-"
        alloc = (f"{m.remaining_units:,.0f} {_unit_name(m.unit)} left" if m.usable and m.remaining_units is not None
                 else ("free" if m.usable else "none"))
        print(f"{m.name:<28} {m.provider:<11} {m.cpu:>4} {str(m.memory_gb) + 'G':>6} {gpu[:22]:<22} "
              f"{m.price_per_hour:>5.3g} {_unit_name(m.unit):<6} {(m.queue or '-'):<9} {alloc}")
    if not a.usable:
        _err("\n(only machines with ALLOCATION != none can be launched; use --usable to hide the rest)")
    return 0


def cmd_balance(tj: TerminalJobs, a: argparse.Namespace) -> int:
    from .machines import _PER_HOUR
    rows = []
    for w in tj.client.wallets(a.type):
        pc = w["paysFor"]
        per_h = _PER_HOUR.get(pc.get("accountingFrequency", "PERIODIC_MINUTE"), 60)
        unit = pc["accountingUnit"]["name"]
        rows.append({
            "category": pc["name"], "provider": pc["provider"], "unit": f"{unit}-hours",
            "quota": (w.get("quota") or 0) / per_h, "used": (w.get("totalUsage") or 0) / per_h,
            "remaining": (w.get("maxUsable") or 0) / per_h,
        })
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no allocations in this workspace; apply for resources at https://cloud.sdu.dk/app/grants")
    print(f"{'CATEGORY':<22} {'PROVIDER':<11} {'QUOTA':>12} {'USED':>12} {'REMAINING':>12}  UNIT")
    for r in rows:
        print(f"{r['category']:<22} {r['provider']:<11} {r['quota']:>12,.1f} {r['used']:>12,.1f} "
              f"{r['remaining']:>12,.1f}  {r['unit']}")
    return 0


def cmd_doctor(tj: TerminalJobs, a: argparse.Namespace) -> int:
    """Check that everything an agent or user needs is in place."""
    ok = True

    def check(label: str, fn):
        nonlocal ok
        try:
            msg = fn()
            print(f"[ok]   {label}: {msg}")
        except Exception as e:  # noqa: BLE001 - doctor reports everything
            ok = False
            print(f"[FAIL] {label}: {e}")

    check("API token", lambda: f"valid for {tj.client.base_url}; drives: "
          + ", ".join(d["specification"]["title"] for d in tj.client.browse_drives()))
    check("app", lambda: "terminal-ubuntu " + tj.client.retrieve_application("terminal-ubuntu")["metadata"]["version"])

    def _alloc():
        usable = sorted({f"{m.category} ({m.remaining_units:,.0f} {_unit_name(m.unit)})"
                         for m in tj.machines() if m.usable and m.remaining_units is not None})
        if not usable:
            raise RuntimeError("no compute allocation in this workspace")
        return ", ".join(usable)
    check("compute allocations", _alloc)
    check("ssh client", lambda: shutil.which("ssh") or (_ for _ in ()).throw(RuntimeError("`ssh` not on PATH")))
    check("SSH key", lambda: f"{tj.find_ssh_key()} (registered on UCloud)")
    check("active jobs", lambda: ", ".join(f"{j.id} {j.state} {_fmt_remaining(j)} left"
                                           for j in tj.list()) or "none")
    return 0 if ok else 1


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
        if not a.pubkey:
            _err("usage: ucloud ssh-keys add PATH_TO_PUBLIC_KEY")
            return 2
        key = Path(a.pubkey).expanduser().read_text().strip()
        title = a.title or f"{socket.gethostname()} ({Path(a.pubkey).name})"
        print(tj.client.add_ssh_key(title, key))
        return 0
    for k in tj.client.ssh_keys():
        parts = k["specification"]["key"].split()
        tail = parts[1][-16:] if len(parts) > 1 else ""
        print(f"{k['id']:>6}  {k['specification']['title']:<20} {parts[0] if parts else ''} ...{tail}")
    return 0


# ---------------------------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    p = argparse.ArgumentParser(
        prog="ucloud", formatter_class=fmt, epilog=QUICKSTART,
        description="Launch UCloud (cloud.sdu.dk) Terminal Ubuntu jobs with SSH, run commands, extend and stop them.")
    p.add_argument("--url", help="UCloud base URL (default $UCLOUD_URL or https://cloud.sdu.dk)")
    p.add_argument("--project", help="project id override (default: the API token's workspace)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def job_cmd(name, fn, help, epilog=None, description=None):
        sp = sub.add_parser(name, help=help, description=description or help, epilog=epilog, formatter_class=fmt)
        sp.set_defaults(fn=fn)
        return sp

    s = job_cmd("launch", cmd_launch, "start a Terminal Ubuntu job with SSH enabled (waits for SSH by default)",
                epilog=LAUNCH_EPILOG)
    s.add_argument("-m", "--machine", help="machine type, e.g. cpu-amd-zen5-4-vcpu (see list below / `ucloud products`)")
    s.add_argument("--provider", help="provider, only needed if the machine name is ambiguous")
    s.add_argument("-H", "--hours", type=int, default=1, help="time allocation in hours (default 1)")
    s.add_argument("--minutes", type=int, default=0)
    s.add_argument("-w", "--workdir", help="UCloud folder mounted as the work directory (e.g. Home/myproj)")
    s.add_argument("-i", "--init-script",
                   help="UCloud path of a bash script run at job start (app 'Initialization'; cwd /work)")
    s.add_argument("-e", "--env-dir", help="UCloud folder with an environment (venv/conda/...) mounted at /work/<name>")
    s.add_argument("--modules-dir", help="UCloud folder with EasyBuild environment modules (app 'Modules path')")
    s.add_argument("--mount", action="append", default=[], help="additional UCloud folder to mount (repeatable)")
    s.add_argument("--setup-script", help="LOCAL bash script piped over SSH once the job is reachable")
    s.add_argument("-n", "--name", help="job name")
    s.add_argument("--extra-args", help="arguments passed to the init script")
    s.add_argument("--shell", choices=["bash", "zsh", "fish"], help="shell for the web terminal")
    s.add_argument("--tmux", action="store_true", default=None, help="start the web terminal in tmux")
    s.add_argument("--nodes", type=int, default=1, help="number of nodes (multi-node only on provider ucloud)")
    s.add_argument("--app-version", help="app version (default: latest)")
    s.add_argument("--param", action="append", default=[],
                   help='raw app parameter NAME=JSON, e.g. slurm=\'{"type":"boolean","value":true}\'')
    s.add_argument("--force", action="store_true", help="skip the allocation check before submitting")
    s.add_argument("--no-wait", action="store_true", help="return right after submission")
    s.add_argument("--timeout", type=float, default=900, help="seconds to wait for SSH (default 900)")
    s.add_argument("--json", action="store_true", help="print the job as JSON on stdout")

    s = job_cmd("status", cmd_status, "show a job (state, remaining time, SSH endpoint, output folder)")
    s.add_argument("job")
    s.add_argument("--messages", action="store_true", help="show job status messages")
    s.add_argument("--json", action="store_true")

    s = job_cmd("list", cmd_list, "list active terminal-ubuntu jobs")
    s.add_argument("--all", action="store_true", help="include finished jobs")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--json", action="store_true")

    s = job_cmd("wait", cmd_wait, "wait until a job is RUNNING and accepts SSH logins")
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

    s = job_cmd("stop", cmd_stop, "terminate job(s) and stop billing")
    s.add_argument("job", nargs="+")

    s = job_cmd("ssh", cmd_ssh, "SSH into a job (interactive), or run a command: ucloud ssh JOB -- CMD",
                epilog="examples:\n  ucloud ssh JOB\n  ucloud ssh JOB -- 'nvidia-smi && python train.py'\n"
                       "  ucloud ssh JOB --cd /work -- ls -la\n  ucloud ssh JOB --print   # show the ssh command")
    s.add_argument("job")
    s.add_argument("command", nargs="*", help="remote command; put it after `--`")
    s.add_argument("--cd", help="remote directory (default: the job's work directory)")
    s.add_argument("--key", help="private key (default: auto-detect a registered ~/.ssh key)")
    s.add_argument("-t", "--tty", action="store_true", help="force a TTY when running a command")
    s.add_argument("--print", action="store_true", help="print the ssh command instead of running it")

    s = job_cmd("logs", cmd_logs, "show the job's console output (startup + init/batch script output)")
    s.add_argument("job")
    s.add_argument("-n", "--lines", type=int, default=100)
    s.add_argument("-f", "--follow", action="store_true")
    s.add_argument("--rank", type=int, default=0, help="node rank for multi-node jobs")
    s.add_argument("--key")

    s = job_cmd("run-script", cmd_run_script, "pipe a local bash script to a job and run it in the work directory")
    s.add_argument("job")
    s.add_argument("script")
    s.add_argument("--cd")
    s.add_argument("--key")

    s = job_cmd("ssh-config", cmd_ssh_config, "print an ~/.ssh/config Host block for a job (VS Code, rsync, scp)")
    s.add_argument("job")
    s.add_argument("--alias")
    s.add_argument("--key")

    s = job_cmd("products", cmd_products, "list machine types with cost, queue status and your allocation",
                epilog=CATALOG_HELP)
    s.add_argument("--usable", action="store_true", help="only machines this workspace has an allocation for")
    s.add_argument("--filter", help="substring filter on the machine name")
    s.add_argument("--catalog", action="store_true", help="print the built-in catalog (offline, no token needed)")
    s.add_argument("--json", action="store_true")

    s = job_cmd("balance", cmd_balance, "show the workspace's allocations (quota, used, remaining)")
    s.add_argument("--type", default="COMPUTE", choices=["COMPUTE", "STORAGE", "LICENSE", "INGRESS", "NETWORK_IP"])
    s.add_argument("--json", action="store_true")

    job_cmd("doctor", cmd_doctor, "check token, app, allocations, SSH key and active jobs")

    s = job_cmd("ls", cmd_ls, "list drives, or the files in a UCloud folder (e.g. Home/myproj)")
    s.add_argument("path", nargs="?")

    s = job_cmd("ssh-keys", cmd_ssh_keys, "list or add SSH public keys on UCloud")
    s.add_argument("action", choices=["list", "add"], nargs="?", default="list")
    s.add_argument("pubkey", nargs="?")
    s.add_argument("--title")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.fn is cmd_products and args.catalog:
            return cmd_products(None, args)
        tj = TerminalJobs(UCloudClient(base_url=args.url, project=args.project))
        return args.fn(tj, args) or 0
    except (UCloudError, ValueError, TimeoutError) as e:
        _err(f"error: {e}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
