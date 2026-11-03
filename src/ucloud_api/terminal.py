"""High-level API for launching and driving UCloud "Terminal Ubuntu" jobs over SSH."""

from __future__ import annotations

import glob
import os
import re
import shlex
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .client import UCloudClient, UCloudError
from .machines import Machine, live_machines

APP_NAME = "terminal-ubuntu"
SSH_USER_DEFAULT = "ucloud"
FINAL_STATES = {"SUCCESS", "FAILURE", "EXPIRED"}
KNOWN_HOSTS_DIR = Path("~/.config/ucloud/known_hosts").expanduser()

# "SSH: Connected! Available at: ssh ucloud@ssh.cloud.sdu.dk -p 2413"
_SSH_RE = re.compile(r"ssh\s+(?P<user>[\w.-]+)@(?P<host>[\w.-]+)\s+-p\s+(?P<port>\d+)")


@dataclass
class SshEndpoint:
    user: str
    host: str
    port: int

    def __str__(self) -> str:
        return f"{self.user}@{self.host}:{self.port}"


@dataclass
class JobInfo:
    id: str
    state: str
    name: Optional[str]
    application: str
    machine: str
    provider: str
    created_at: Optional[int]          # epoch millis
    started_at: Optional[int]
    expires_at: Optional[int]
    ssh: Optional[SshEndpoint]
    workdir: str                       # path inside the container
    mounts: List[str] = field(default_factory=list)   # UCloud paths mounted into the job
    messages: List[str] = field(default_factory=list)
    url: str = ""
    # UCloud folder that receives everything written to /work outside the mounted folders.
    output_folder: Optional[str] = None

    @property
    def remaining_seconds(self) -> Optional[float]:
        if not self.expires_at:
            return None
        return self.expires_at / 1000.0 - time.time()

    @property
    def is_final(self) -> bool:
        return self.state in FINAL_STATES

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["remaining_seconds"] = None if self.remaining_seconds is None else int(self.remaining_seconds)
        d["ssh_command"] = (f"ssh {self.ssh.user}@{self.ssh.host} -p {self.ssh.port}" if self.ssh else None)
        return d


def parse_ssh_endpoint(updates: Sequence[Dict]) -> Optional[SshEndpoint]:
    found = None
    for u in updates or []:
        m = _SSH_RE.search(u.get("status") or "")
        if m:
            found = SshEndpoint(m["user"], m["host"], int(m["port"]))
    return found


def container_path(ucloud_path: str) -> str:
    """UCloud mounts each selected folder at /work/<folder name>."""
    return "/work/" + ucloud_path.rstrip("/").rsplit("/", 1)[-1]


class TerminalJobs:
    """Launch, inspect, extend, stop and SSH into Terminal Ubuntu jobs."""

    def __init__(self, client: Optional[UCloudClient] = None, app_name: str = APP_NAME):
        self.client = client or UCloudClient()
        self.app_name = app_name
        self._drives: Optional[List[Dict]] = None
        self._products: Optional[List[Dict]] = None

    # ------------------------------------------------------------------ resolution helpers

    def resolve_path(self, path: str) -> str:
        """Accept '/123456/folder' (native) or 'Home/folder' (drive title prefix)."""
        p = path.strip()
        parts = [x for x in p.split("/") if x]
        if not parts:
            raise ValueError(f"Empty UCloud path: {path!r}")
        if parts[0].isdigit():
            return "/" + "/".join(parts)
        if self._drives is None:
            self._drives = self.client.browse_drives()
        matches = [d for d in self._drives if d["specification"]["title"].lower() == parts[0].lower()]
        if len(matches) != 1:
            titles = ", ".join(f"{d['specification']['title']} (/{d['id']})" for d in self._drives)
            raise ValueError(f"Cannot resolve drive {parts[0]!r} in {path!r}. Known drives: {titles}")
        return "/" + "/".join([matches[0]["id"]] + parts[1:])

    def products(self) -> List[Dict]:
        if self._products is None:
            self._products = self.client.job_products()
        return self._products

    def machines(self) -> List[Machine]:
        """Live machine types merged with this workspace's allocations and queue status."""
        return live_machines(self.products(), self.client.wallets())

    def preflight(self, spec: Dict, check_allocation: bool = True) -> List[str]:
        """Check the allocation and estimate the cost before submitting. Returns warnings, or raises."""
        prod, t = spec["product"], spec["timeAllocation"]
        hours = t["hours"] + t["minutes"] / 60
        nodes = spec.get("replicas", 1)
        m = next((x for x in self.machines() if x.name == prod["id"] and x.provider == prod["provider"]), None)
        if m is None:
            return []
        cost = m.price_per_hour * hours * nodes
        unit = {"Core": "core-hours", "GPU": "GPU-hours"}.get(m.unit, f"{m.unit}-hours")
        warnings = [f"estimated cost: {cost:g} {unit} ({m.price_per_hour:g}/h x {hours:g} h x {nodes} node(s))"]
        if m.remaining_units is not None:
            warnings[0] += f"; remaining allocation for {m.category}: {m.remaining_units:,.0f} {unit}"
        if check_allocation and not m.usable:
            usable = sorted({x.category for x in self.machines() if x.usable})
            raise UCloudError(402, f"No allocation for category '{m.category}' ({m.provider}) in this workspace. "
                                   f"Categories you can use: {usable or 'none'}. See `ucloud balance`.")
        if check_allocation and m.remaining_units is not None and cost > m.remaining_units:
            raise UCloudError(402, f"Estimated cost {cost:g} {unit} exceeds the remaining allocation "
                                   f"{m.remaining_units:,.0f} {unit} for {m.category}.")
        if m.queue in ("BUSY", "FULL"):
            warnings.append(f"queue status for {m.name} is {m.queue}; the job may wait in the queue")
        return warnings

    def resolve_product(self, machine: str, provider: Optional[str] = None) -> Dict[str, str]:
        cands = [p["product"] for p in self.products()
                 if p["product"]["name"] == machine
                 and (provider is None or p["product"]["category"]["provider"] == provider)]
        if not cands:
            names = sorted({p["product"]["name"] for p in self.products()})
            raise ValueError(f"Unknown machine type {machine!r}. Available: {', '.join(names)}")
        if len(cands) > 1:
            provs = [c["category"]["provider"] for c in cands]
            raise ValueError(f"Machine type {machine!r} exists at several providers {provs}; pass provider=")
        c = cands[0]
        return {"id": c["name"], "category": c["category"]["name"], "provider": c["category"]["provider"]}

    # ------------------------------------------------------------------ launch

    def build_spec(
        self,
        machine: str,
        hours: int = 1,
        minutes: int = 0,
        workdir: Optional[str] = None,
        init_script: Optional[str] = None,
        env_dir: Optional[str] = None,
        modules_dir: Optional[str] = None,
        mounts: Sequence[str] = (),
        name: Optional[str] = None,
        extra_args: Optional[str] = None,
        shell: Optional[str] = None,
        tmux: Optional[bool] = None,
        provider: Optional[str] = None,
        app_version: Optional[str] = None,
        replicas: int = 1,
        extra_parameters: Optional[Dict[str, Dict]] = None,
    ) -> Dict:
        app = self.client.retrieve_application(self.app_name, app_version)
        inv = app["invocation"]
        if (inv.get("ssh") or {}).get("mode", "DISABLED") == "DISABLED":
            raise UCloudError(400, f"{self.app_name} does not support SSH")
        declared = {p["name"]: p for p in inv.get("parameters", [])}

        def file_value(p: str) -> Dict:
            return {"type": "file", "path": self.resolve_path(p), "readOnly": False}

        params: Dict[str, Dict] = {}
        if init_script:
            params["initScript"] = file_value(init_script)
        if modules_dir:
            params["modules"] = file_value(modules_dir)
        if extra_args:
            params["extraArgs"] = {"type": "text", "value": extra_args}
        if shell:
            params["shell"] = {"type": "text", "value": shell}
        if tmux is not None:
            params["enableTmux"] = {"type": "boolean", "value": bool(tmux)}
        params.update(extra_parameters or {})
        unknown = set(params) - set(declared)
        if unknown:
            raise ValueError(f"Parameters {sorted(unknown)} not declared by {self.app_name} "
                             f"{app['metadata']['version']}; available: {sorted(declared)}")

        # Folder mounts. The work directory goes first so we can find it again from the job spec.
        folders = [f for f in [workdir, env_dir, *mounts] if f]
        resources = [file_value(f) for f in folders]

        spec: Dict[str, Any] = {
            "application": {"name": app["metadata"]["name"], "version": app["metadata"]["version"]},
            "product": self.resolve_product(machine, provider),
            "parameters": params,
            "resources": resources,
            "replicas": replicas,
            "timeAllocation": {"hours": hours, "minutes": minutes, "seconds": 0},
            "sshEnabled": True,
            "allowDuplicateJob": True,
        }
        if name:
            spec["name"] = name
        return spec

    def launch(self, machine: str, wait: bool = False, wait_timeout: float = 900,
               check_allocation: bool = True, log=None, **kwargs: Any) -> JobInfo:
        """Submit a Terminal Ubuntu job with SSH enabled. See build_spec for arguments.

        Before submitting, it checks that the machine's category has enough allocation (disable with
        check_allocation=False). Warnings, such as the cost estimate, go to ``log`` if one is given.
        """
        spec = self.build_spec(machine, **kwargs)
        for w in self.preflight(spec, check_allocation=check_allocation):
            if log:
                log(w)
        ids = self.client.create_jobs([spec])
        if not ids:
            raise UCloudError(500, "UCloud did not return a job id")
        if wait:
            return self.wait(ids[0], timeout=wait_timeout)
        return self.info(ids[0])

    # ------------------------------------------------------------------ inspection

    def info(self, job_id: str) -> JobInfo:
        return self._to_info(self.client.retrieve_job(job_id))

    def list(self, include_finished: bool = False, limit: int = 50) -> List[JobInfo]:
        jobs = [self._to_info(j) for j in self.client.browse_jobs(application=self.app_name, limit=limit)]
        return jobs if include_finished else [j for j in jobs if not j.is_final]

    def _to_info(self, job: Dict) -> JobInfo:
        spec, status = job["specification"], job["status"]
        mounts = [r["path"] for r in spec.get("resources") or [] if r.get("type") == "file"]
        return JobInfo(
            id=job["id"],
            state=status["state"],
            name=spec.get("name"),
            application=f"{spec['application']['name']}@{spec['application']['version']}",
            machine=spec["product"]["id"],
            provider=spec["product"]["provider"],
            created_at=job.get("createdAt"),
            started_at=status.get("startedAt"),
            expires_at=status.get("expiresAt"),
            ssh=parse_ssh_endpoint(job.get("updates") or []),
            workdir=container_path(mounts[0]) if mounts else "/work",
            mounts=mounts,
            messages=[u["status"] for u in job.get("updates") or [] if u.get("status")],
            url=f"{self.client.base_url}/app/jobs/properties/{job['id']}",
            output_folder=(job.get("output") or {}).get("outputFolder"),
        )

    def wait(self, job_id: str, timeout: float = 900, poll: float = 5.0, ssh_ready: bool = True,
             key: Optional[str] = None, log=None) -> JobInfo:
        """Block until the job is RUNNING and (by default) accepts SSH logins."""
        deadline = time.time() + timeout
        last_state = None
        while True:
            info = self.info(job_id)
            if info.state != last_state and log:
                log(f"job {job_id}: {info.state}")
            last_state = info.state
            if info.is_final:
                raise UCloudError(409, f"Job {job_id} ended in state {info.state}: {info.messages[-3:]}")
            if info.state == "RUNNING" and (not ssh_ready or (info.ssh and self._ssh_ok(info, key))):
                return info
            if time.time() > deadline:
                raise TimeoutError(f"Job {job_id} not ready after {timeout}s (state={info.state}, ssh={info.ssh})")
            time.sleep(poll)

    def _ssh_ok(self, info: JobInfo, key: Optional[str]) -> bool:
        assert info.ssh
        try:
            with socket.create_connection((info.ssh.host, info.ssh.port), timeout=5):
                pass
        except OSError:
            return False
        r = subprocess.run(self.ssh_argv(info, ["true"], key=key, batch=True),
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        return r.returncode == 0

    # ------------------------------------------------------------------ lifetime

    def extend(self, job_id: str, hours: int = 1, minutes: int = 0) -> JobInfo:
        self.client.extend_job(job_id, hours=hours, minutes=minutes)
        return self.info(job_id)

    def extend_if_needed(self, job_id: str, min_remaining_minutes: int = 30,
                         hours: int = 1, minutes: int = 0) -> tuple:
        """Extend only if less than ``min_remaining_minutes`` remain. Returns (extended, JobInfo)."""
        info = self.info(job_id)
        rem = info.remaining_seconds
        if info.is_final or rem is None or rem >= min_remaining_minutes * 60:
            return False, info
        return True, self.extend(job_id, hours=hours, minutes=minutes)

    def stop(self, job_id: str) -> None:
        self.client.terminate_job(job_id)

    # ------------------------------------------------------------------ ssh

    def find_ssh_key(self) -> str:
        """Private key to use: $UCLOUD_SSH_KEY, else a ~/.ssh key whose .pub is registered on UCloud."""
        env = os.environ.get("UCLOUD_SSH_KEY")
        if env:
            return str(Path(env).expanduser())
        registered = set()
        for k in self.client.ssh_keys():
            parts = k["specification"]["key"].split()
            if len(parts) >= 2:
                registered.add(parts[1])
        for pub in sorted(glob.glob(os.path.expanduser("~/.ssh/*.pub"))):
            parts = Path(pub).read_text().split()
            priv = pub[:-4]
            if len(parts) >= 2 and parts[1] in registered and os.path.exists(priv):
                return priv
        raise UCloudError(400, "No local SSH key in ~/.ssh matches a key registered on UCloud. "
                               "Register one with `ucloud ssh-keys add ~/.ssh/<key>.pub` or set $UCLOUD_SSH_KEY.")

    def ssh_argv(self, info: JobInfo, command: Optional[Sequence[str]] = None, key: Optional[str] = None,
                 cd: Optional[str] = None, tty: bool = False, batch: bool = False) -> List[str]:
        if info.is_final:
            raise UCloudError(409, f"Job {info.id} has ended (state={info.state})")
        if not info.ssh:
            raise UCloudError(409, f"Job {info.id} has no SSH endpoint yet (state={info.state})")
        key = key or self.find_ssh_key()
        KNOWN_HOSTS_DIR.mkdir(parents=True, exist_ok=True)
        argv = [
            "ssh", "-p", str(info.ssh.port), "-i", key,
            "-o", "IdentitiesOnly=yes",
            # Ports are reused across jobs, so pin host keys per job (trust on first use).
            "-o", f"UserKnownHostsFile={KNOWN_HOSTS_DIR / info.id}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ServerAliveInterval=30",
            "-o", "ConnectTimeout=15",
            "-o", "LogLevel=ERROR",
        ]
        if batch:
            argv += ["-o", "BatchMode=yes"]
        if tty:
            argv.append("-t")
        argv.append(f"{info.ssh.user}@{info.ssh.host}")
        cd = info.workdir if cd is None else cd
        if command:
            remote = " ".join(command)
            argv.append(f"cd {shlex.quote(cd)} && {remote}" if cd else remote)
        elif cd:
            argv.append(f"cd {shlex.quote(cd)} && exec \"$SHELL\" -l")
        return argv

    def run(self, job_id: str, command: str, key: Optional[str] = None, cd: Optional[str] = None,
            check: bool = False, timeout: Optional[float] = None, input: Optional[str] = None
            ) -> subprocess.CompletedProcess:
        """Run a shell command on the job over SSH and capture its output (for agents)."""
        info = self.info(job_id)
        argv = self.ssh_argv(info, [command], key=key, cd=cd, batch=True)
        return subprocess.run(argv, input=input, capture_output=True, text=True, check=check, timeout=timeout,
                              stdin=None if input is not None else subprocess.DEVNULL)

    def run_script(self, job_id: str, script_path: str, key: Optional[str] = None,
                   cd: Optional[str] = None, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        """Pipe a local bash script to the job and execute it in the work directory."""
        return self.run(job_id, "bash -s", key=key, cd=cd, timeout=timeout,
                        input=Path(script_path).expanduser().read_text())

    def ssh_config(self, info: JobInfo, alias: Optional[str] = None, key: Optional[str] = None) -> str:
        if not info.ssh:
            raise UCloudError(409, f"Job {info.id} has no SSH endpoint yet (state={info.state})")
        key = key or self.find_ssh_key()
        return "\n".join([
            f"Host {alias or 'ucloud-' + info.id}",
            f"    HostName {info.ssh.host}",
            f"    Port {info.ssh.port}",
            f"    User {info.ssh.user}",
            f"    IdentityFile {key}",
            "    IdentitiesOnly yes",
            f"    UserKnownHostsFile {KNOWN_HOSTS_DIR / info.id}",
            "    StrictHostKeyChecking accept-new",
            "    ServerAliveInterval 30",
            "",
        ])
