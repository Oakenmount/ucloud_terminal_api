# ucloud-api

A Python library and `ucloud` CLI for running **Terminal Ubuntu** jobs on [UCloud](https://cloud.sdu.dk). It launches the
job with SSH enabled, waits until you can log in, connects over SSH, and extends or stops the job. It needs only the
Python standard library.

It talks to the same UCloud/Core REST API that the web interface uses (`/api/jobs`, `/api/jobs/extend`, …),
and authenticates with a platform API token.

## Setup

1. Create a token at <https://cloud.sdu.dk/app/api-tokens> with provider **UCloud**, in the workspace that should pay
   for the jobs. Save it:
   ```sh
   mkdir -p ~/.config/ucloud && pbpaste > ~/.config/ucloud/token && chmod 600 ~/.config/ucloud/token
   ```
   You can also set `$UCLOUD_TOKEN` or `$UCLOUD_TOKEN_FILE`. Tokens expire on the date you chose, so make a new one
   when that happens.
2. Install: `uv sync` (then `uv run ucloud …`), or `uv tool install -e .` to get `ucloud` on your PATH.
3. SSH key: the tool picks the first `~/.ssh/*.pub` that is registered on UCloud and has a matching private key.
   To override that, set `$UCLOUD_SSH_KEY` or pass `--key`. To register a key, run `ucloud ssh-keys add ~/.ssh/id_ed25519.pub`.

## CLI

```sh
ucloud products --filter zen5                 # machine types
ucloud ls Home                                # browse UCloud folders ("Home/..." or "/123456/...")

# Launch, wait until SSH works, print connection info
ucloud launch -m cpu-amd-zen5-8-vcpu -H 4 \
    --workdir Home/myproject \
    --init-script Home/myproject/start.sh \
    --extra-args "--foo bar" \
    --env-dir Home/envs/py311 \
    --modules-dir Home/easybuild \
    --setup-script ./local_setup.sh \
    --name my-run --json
```

| Launch option   | What it does                                                                                   |
|-----------------|------------------------------------------------------------------------------------------------|
| `--workdir`     | UCloud folder mounted at `/work/<name>`. `ucloud ssh` sessions and commands start here.        |
| `--init-script` | UCloud file used as the app's "Initialization" script. It runs at job start with cwd `/work`.  |
| `--extra-args`  | Arguments passed to the init script.                                                           |
| `--env-dir`     | Extra UCloud folder (venv, conda, …) mounted at `/work/<name>`.                                |
| `--modules-dir` | The app's "Modules path" setting (an EasyBuild module tree).                                   |
| `--setup-script`| A **local** script piped over SSH once the job is reachable.                                   |
| `--mount`       | More folders to mount; repeatable.                                                             |

```sh
ucloud list                                   # active jobs
ucloud status JOB [--messages] [--json]
ucloud ssh JOB                                # interactive shell in the work directory
ucloud ssh JOB -- 'nvidia-smi && python train.py'   # run a command, exit code is forwarded
ucloud run-script JOB ./script.sh             # pipe a local script to the job
ucloud ssh-config JOB --alias gpu >> ~/.ssh/config  # for VS Code Remote / rsync / scp
ucloud extend JOB -H 2                        # add 2 hours
ucloud extend JOB --if-less-than 30 -H 1      # add 1 hour only if < 30 min remain (idempotent)
ucloud keepalive JOB --if-less-than 30        # loop that extends until the job ends / Ctrl-C
ucloud stop JOB
```

Other app parameters can be passed raw, for example
`--param slurm='{"type":"boolean","value":true}' --param numWeb='{"type":"integer","value":1}'`.
For multi-node jobs, use `--nodes N`.

## Python

```python
from ucloud_api import TerminalJobs

tj = TerminalJobs()   # token from ~/.config/ucloud/token or $UCLOUD_TOKEN
job = tj.launch("cpu-amd-zen5-4-vcpu", hours=2, workdir="Home/myproject",
                init_script="Home/myproject/start.sh", env_dir="Home/envs/py311", wait=True)
print(job.ssh, job.workdir, job.remaining_seconds)

r = tj.run(job.id, "python -c 'import os; print(os.cpu_count())'")
print(r.returncode, r.stdout)

tj.extend_if_needed(job.id, min_remaining_minutes=30, hours=1)
tj.stop(job.id)
```

## Notes for agents

- `ucloud launch … --json` blocks until SSH accepts a login. It then prints JSON with `id`, `state`, `ssh_command`,
  `workdir` and `remaining_seconds`. Use `--no-wait` to return right after submission, and `ucloud wait JOB` to block later.
- `ucloud ssh JOB -- CMD` runs `CMD` in the work directory with `BatchMode`, so it never prompts. It returns the remote
  exit code.
- Before a long task, run `ucloud extend JOB --if-less-than <minutes> -H <hours>`. It is safe to call repeatedly.
- The job runs until its time allocation ends, even when idle. Call `ucloud stop JOB` when you are done.
- Each job gets its own known-hosts file in `~/.config/ucloud/known_hosts/<job id>`, because UCloud reuses SSH ports
  across jobs.

## How it maps to the UCloud API

| Action        | Endpoint                                                                                                 |
|---------------|----------------------------------------------------------------------------------------------------------|
| launch        | `POST /api/jobs` `{"items":[JobSpecification]}` with `sshEnabled: true`                                   |
| status / list | `GET /api/jobs/retrieve?id=…&includeUpdates=true`, `GET /api/jobs/browse?filterApplication=terminal-ubuntu` |
| extend        | `POST /api/jobs/extend` `{"items":[{"jobId", "requestedTime":{hours,minutes,seconds}}]}`                  |
| stop          | `POST /api/jobs/terminate` `{"items":[{"id"}]}`                                                           |
| SSH endpoint  | read from the job update `"SSH: Connected! Available at: ssh ucloud@ssh.cloud.sdu.dk -p <port>"`          |
| app params    | `GET /api/hpc/apps/byNameAndVersion?appName=terminal-ubuntu`                                              |
| machine types | `GET /api/jobs/retrieveProducts`                                                                          |

The schemas come from UCloud's open-source code
(`provider-integration/shared/pkg/orchestrators/jobs.go` in SDU-eScience/UCloud).
