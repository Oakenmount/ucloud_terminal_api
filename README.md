# ucloud-api

A Python library and `ucloud` CLI for running **Terminal Ubuntu** jobs on [UCloud](https://cloud.sdu.dk).
UCloud is the DeiC Interactive HPC platform run by SDU. The tool launches a job with SSH enabled, waits until you
can log in, runs commands over SSH, and extends or stops the job. It needs only the Python standard library.

It talks to the same REST API that the UCloud web interface uses. It is meant for people and for AI agents.
Agents should read [AGENTS.md](AGENTS.md).

```
your machine ──HTTPS + API token──▶ cloud.sdu.dk /api/jobs   (launch, status, extend, stop)
your machine ──SSH + your key────▶ ssh.cloud.sdu.dk:<port>  (shell / commands inside the job)
```

## Setup (one time)

1. **API token.** Open <https://cloud.sdu.dk/app/api-tokens> and create a token with provider **UCloud**. Create it in
   the workspace (personal or project) that should pay for the jobs. Save it:
   ```sh
   mkdir -p ~/.config/ucloud && pbpaste > ~/.config/ucloud/token && chmod 600 ~/.config/ucloud/token
   ```
   You can also set `$UCLOUD_TOKEN` or `$UCLOUD_TOKEN_FILE`. A token acts with your full permissions and expires on
   the date you chose. After that, create a new one.
2. **Install.** Run `uv sync` and then use `uv run ucloud …`. To put `ucloud` on your PATH, run `uv tool install -e .`.
3. **SSH key.** The tool uses the first `~/.ssh/*.pub` that is registered on UCloud and has a matching private key.
   To register a key, run `ucloud ssh-keys add ~/.ssh/id_ed25519.pub`. To pick a key yourself, set `$UCLOUD_SSH_KEY`.
4. **Check.** Run `ucloud doctor`. It checks the token, the app, your allocations, the SSH client, the SSH key and
   your active jobs.

## Everyday use

```sh
ucloud products --usable                      # machine types you can launch, with cost and queue status
ucloud balance                                # remaining allocation per machine category

ucloud launch -m cpu-amd-zen5-8-vcpu -H 4 \
    --workdir Home/myproject \
    --init-script Home/myproject/start.sh \
    --env-dir Home/envs/py311 \
    --name my-run --json

ucloud list                                   # active jobs
ucloud status JOB [--messages] [--json]
ucloud ssh JOB                                # interactive shell in the work directory
ucloud ssh JOB -- 'nvidia-smi && python train.py'   # run a command; its exit code is returned
ucloud logs JOB [-f]                          # console output (startup + init script)
ucloud run-script JOB ./script.sh             # pipe a local script to the job
ucloud ssh-config JOB --alias gpu >> ~/.ssh/config  # for VS Code Remote / rsync / scp
ucloud extend JOB -H 2                        # add 2 hours
ucloud extend JOB --if-less-than 30 -H 1      # add 1 hour only if < 30 min remain (idempotent)
ucloud keepalive JOB --if-less-than 30        # loop that extends until the job ends / Ctrl-C
ucloud stop JOB                               # stop the job (and the billing)
ucloud ls Home/myproject                      # browse UCloud folders
```

`ucloud --help` and `ucloud <command> --help` show every option. `launch --help` also lists the machine types.

### Launch options

| Option             | What it does                                                                                    |
|--------------------|-------------------------------------------------------------------------------------------------|
| `-m/--machine`     | Machine type (see below). Required, or set `$UCLOUD_MACHINE`.                                   |
| `-H/--hours`, `--minutes` | Time allocation. The job is killed when it runs out, unless you `extend` it.             |
| `-w/--workdir`     | UCloud folder mounted at `/work/<name>`. `ucloud ssh` sessions and commands start here.         |
| `-i/--init-script` | UCloud file used as the app's "Initialization" script. It runs at job start, with cwd `/work`.  |
| `--extra-args`     | Arguments passed to the init script.                                                            |
| `-e/--env-dir`     | Extra UCloud folder (venv, conda, …) mounted at `/work/<name>`.                                 |
| `--modules-dir`    | The app's "Modules path" setting: an EasyBuild module tree, added to `module use`.              |
| `--mount`          | More folders to mount. Repeatable.                                                              |
| `--setup-script`   | A **local** script piped over SSH once the job is reachable.                                    |
| `--nodes N`        | Multi-node job, with a Slurm cluster if you pass `--param slurm=…`. Only on provider `ucloud`.  |
| `--param NAME=JSON`| Any raw app parameter, e.g. `--param numWeb='{"type":"integer","value":1}'`.                    |
| `--no-wait`        | Return right after submission. Use `ucloud wait JOB` later.                                     |
| `--force`          | Skip the allocation and cost check that runs before submission.                                 |

Paths are written as `<Drive title>/sub/dir` (e.g. `Home/myproject`) or `/<drive id>/sub/dir`. Run `ucloud ls` to see
your drives.

## Machine types

Pick the machine with `-m`. Run **`ucloud products`** for the live list, which shows which machines your workspace
has an allocation for, the cost per hour, and the queue status (AVAILABLE, BUSY or FULL). The table below comes from
the [UCloud docs](https://docs.cloud.sdu.dk/guide/resources-products.html) and the live catalog as of November 2026.

| Provider | Machine names | Per unit of size | Billed in |
|---|---|---|---|
| `ucloud`: DeiC Interactive HPC (SDU/K8s) | `cpu-amd-zen5-{1,2,4,8,16,32,64,128}-vcpu` | 1 vCPU, 3 GB RAM (AMD EPYC 9635/9535) | core-hours |
| `ucloud` | `gpu-nvidia-b200-{1..8}-gpu` | 1x NVIDIA B200 192 GB, 48 vCPU, 288 GB RAM | GPU-hours |
| `ucloud` | `gpu-nvidia-b200-{1..4}-mig.1g` | 1/7 of a B200 (23 GB), 6 vCPU, 36 GB RAM | 1/7 GPU-hour each |
| `sdu-odense` (SDU-affiliated users only) | `cpu-amd-zen4-{1,2,4,8,16,32,64,128}` | 1 vCPU, 6 GB RAM (AMD EPYC 9534) | core-hours |
| `sdu-odense` | `gpu-nvidia-h100-{1..4}` | 1x H100 SXM5 80 GB, 48 vCPU, 192 GB RAM | GPU-hours |
| `sdu-odense` | `gpu-nvidia-rtx6000-{1..4}` | 1x RTX PRO 6000, 24 vCPU, 128 GB RAM | GPU-hours |
| `sdu-odense` (research groups) | `u1-dfm-*`, `u1-madlab-*`, `u1-medrob-*`, `u2-madlab-*` | dedicated group hardware | varies |

- **Cost.** The cost per hour equals the number in the name. `cpu-amd-zen5-8-vcpu` costs 8 core-hours per hour, and
  `--nodes 2` doubles that. `launch` prints an estimate before it submits.
- **Allocation.** You can only launch a machine whose category (e.g. `cpu-amd-zen5`) has an allocation in the active
  workspace. `launch` checks this first and refuses with a clear message. To get more resources, apply at
  <https://cloud.sdu.dk/app/grants>, or create a token in a project that has them.
- **Size.** Large machines can wait longer in the queue. Start small and scale up.

## What happens inside the job

- **User and tools.** You log in as `ucloud`, with passwordless `sudo`. The image is Ubuntu, with `apt`, Python 3,
  git, tmux, Slurm and Lmod/EasyBuild (`module avail`).
- **Mounts.** Each mounted folder appears at `/work/<folder name>`. Changes are written straight to UCloud storage.
- **What is kept.** Anything else you write under `/work` is saved to the job's output folder,
  `Home/Jobs/Terminal Ubuntu/<job id>` (see `ucloud status`). **Anything outside `/work` is lost when the job ends.**
  That includes `apt install`s, `~/.local`, `~/.cache` and `/tmp`. Keep venvs, conda envs and checkpoints in a
  mounted folder, or reinstall them from the init script.
- **Non-interactive commands.** `ucloud ssh JOB -- CMD` doesn't apply interactive `.bashrc` settings, so `module`
  and `MODULEPATH` are missing. Prefix the command with `source /opt/lmod/lmod/init/bash && module use …`, and
  activate venvs explicitly.
- **Console log.** `/work/stdout-0.log` holds the startup output and the init/batch script output. Read it with
  `ucloud logs JOB`.
- **SSH.** SSH starts before the init script finishes, so `launch` can return while the init script is still running.
  Check `ucloud logs JOB` if you depend on it.
- **Billing.** The job is billed for the whole time it runs, even when idle. Stop it when you are done.

## Python

```python
from ucloud_api import TerminalJobs

tj = TerminalJobs()   # token from ~/.config/ucloud/token or $UCLOUD_TOKEN
print([m.name for m in tj.machines() if m.usable])

job = tj.launch("cpu-amd-zen5-4-vcpu", hours=2, workdir="Home/myproject",
                init_script="Home/myproject/start.sh", env_dir="Home/envs/py311",
                wait=True, log=print)          # log receives the cost estimate and warnings
print(job.ssh, job.workdir, job.output_folder, job.remaining_seconds)

r = tj.run(job.id, "python -c 'import os; print(os.cpu_count())'")
print(r.returncode, r.stdout)

tj.extend_if_needed(job.id, min_remaining_minutes=30, hours=1)
tj.stop(job.id)
```

## JSON output (`--json`)

`launch`, `status`, `wait`, `extend` and `list` print job objects with these fields:

| Field | Meaning |
|---|---|
| `id` | Job id. Pass it to the other commands. |
| `state` | `IN_QUEUE`, `RUNNING`, `SUCCESS`, `FAILURE`, `EXPIRED` or `SUSPENDED`. The last four mean the job has ended. |
| `ssh_command`, `ssh` | e.g. `ssh ucloud@ssh.cloud.sdu.dk -p 2413`. Null until SSH is up. |
| `workdir` | Directory inside the job where `ucloud ssh` starts (`/work/<workdir name>` or `/work`). |
| `mounts` | UCloud paths mounted into the job. |
| `output_folder` | UCloud folder that receives `/work` (logs, files written outside mounts). |
| `remaining_seconds`, `expires_at` | Time left; `expires_at` is epoch milliseconds. |
| `machine`, `provider`, `name`, `url`, `messages` | Machine type, provider, job name, web UI link, status messages. |

`products --json` and `balance --json` print machine and allocation rows.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No API token found` / HTTP 401 / 403 | The token is missing, expired or revoked. Create a new one (Setup step 1). |
| `No allocation for category …` | Pick a machine from `ucloud products --usable`, or use a token for a project that has that allocation. |
| `No local SSH key … registered` | Run `ucloud ssh-keys add ~/.ssh/<key>.pub`, or set `$UCLOUD_SSH_KEY`. Keys must be registered **before** launch. |
| Launch waits forever in `IN_QUEUE` | The machine is busy (see the QUEUE column in `ucloud products`). Try a smaller machine. |
| Job ended unexpectedly | Run `ucloud status JOB --messages`. `EXPIRED` means the time ran out; use `extend` or `keepalive` next time. |
| Init script failed | Run `ucloud logs JOB`. The script runs with cwd `/work`, so use absolute paths like `/work/myproject/...`. |
| `Host key verification failed` | Remove `~/.config/ucloud/known_hosts/<job id>`. Each job gets its own known-hosts file. |

## How it maps to the UCloud API

| Action        | Endpoint                                                                                                 |
|---------------|----------------------------------------------------------------------------------------------------------|
| launch        | `POST /api/jobs` `{"items":[JobSpecification]}` with `sshEnabled: true`                                   |
| status / list | `GET /api/jobs/retrieve?id=…&includeUpdates=true`, `GET /api/jobs/browse?filterApplication=terminal-ubuntu` |
| extend        | `POST /api/jobs/extend` `{"items":[{"jobId", "requestedTime":{hours,minutes,seconds}}]}`                  |
| stop          | `POST /api/jobs/terminate` `{"items":[{"id"}]}`                                                           |
| SSH endpoint  | read from the job update `"SSH: Connected! Available at: ssh ucloud@ssh.cloud.sdu.dk -p <port>"`          |
| app params    | `GET /api/hpc/apps/byNameAndVersion?appName=terminal-ubuntu`                                              |
| machine types | `GET /api/jobs/retrieveProducts` (with `support.queueStatus`)                                             |
| allocations   | `GET /api/accounting/v2/browseWallets?filterType=COMPUTE`                                                 |
| files         | `GET /api/files/collections/browse`, `GET /api/files/browse?path=…`                                       |
| SSH keys      | `GET /api/ssh/browse`, `POST /api/ssh`                                                                    |

The schemas come from UCloud's open-source code
(`provider-integration/shared/pkg/orchestrators/jobs.go` in SDU-eScience/UCloud). The code lives in `src/ucloud_api/`:

| File | Contents |
|---|---|
| `client.py` | Thin REST client: auth, endpoints. |
| `terminal.py` | Job logic: spec building, wait, extend, SSH. |
| `machines.py` | Machine catalog, and the live merge of products, allocations and queue status. |
| `cli.py` | The command line. |
