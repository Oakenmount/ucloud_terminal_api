# Agent guide: running work on UCloud with `ucloud`

Use this tool to get a Linux machine (CPU or GPU) on UCloud (cloud.sdu.dk), run commands on it over SSH, and shut it
down. Jobs cost the user's compute allocation for every minute they run, so **always stop jobs you start.**

Run the CLI with `uv run ucloud …` from this repo, or `ucloud …` if it is installed. Add `--json` when you need to
parse the output. Progress messages go to stderr and results to stdout.

## Standard workflow

```sh
ucloud doctor                                   # 1. check setup; stop and tell the user if anything FAILs
ucloud products --usable --json                 # 2. choose a machine you can actually launch
ucloud launch -m cpu-amd-zen5-4-vcpu -H 2 -w Home/<project> --name <task> --json   # 3. blocks until SSH works
ucloud ssh <id> -- '<command>'                  # 4. run work (cwd = /work/<project>); exit code is forwarded
ucloud extend <id> --if-less-than 30 -H 1       # 5. before long steps; safe to repeat
ucloud stop <id>                                # 6. ALWAYS, also after failures
```

## Rules

- **Machine choice.** Use the smallest machine that fits. Only machines with an allocation can launch: `usable: true`
  in `products --json`. The cost per hour is `price_per_hour` core-hours or GPU-hours, so `cpu-amd-zen5-8-vcpu`
  costs 8 core-hours per hour. `launch` refuses jobs that would overrun the allocation. Don't pass `--force` unless
  the user asks.
- **Time.** `-H` is the time allocation. The job is killed when it runs out. For long runs, start small and call
  `extend --if-less-than` from time to time, or use a `keepalive` process. Don't request huge allocations up front.
- **Persistence.** Only data under `/work` survives the job. Mounted folders (`-w`, `-e`, `--mount`) are written in
  place. Other files under `/work` go to the job's `output_folder`. `apt install`s, `~/.local`, `~/.cache`, `pip
  install --user` and `/tmp` are lost. Put venvs and checkpoints inside the mounted work directory.
- **Commands.** `ucloud ssh <id> -- '<cmd>'` runs non-interactively (BatchMode, no TTY), so interactive `.bashrc`
  settings don't apply. Activate environments explicitly, for example
  `ucloud ssh <id> -- 'source .venv/bin/activate && python run.py'`. Environment modules work the same way:
  `source /opt/lmod/lmod/init/bash && module use $(grep -oP 'MODULEPATH=\K.*' ~/.bashrc | tail -1) && module load …`.
  To run a long job that survives a disconnect, use
  `nohup … > run.log 2>&1 &` or `tmux new -d -s run '…'`, then check on it with further `ssh` calls.
- **Root.** `sudo` works without a password. Packages you install that way are temporary (see Persistence).
- **Local scripts.** Use `ucloud run-script <id> ./local.sh`, or `--setup-script ./local.sh` at launch. These pipe the
  local file over SSH. `--init-script` takes a file **on UCloud**, not a local path.
- **Debugging.** `ucloud status <id> --messages` shows the job's lifecycle. `ucloud logs <id>` shows the console and
  init-script output.
- **Paths.** UCloud paths are `<Drive>/<dir>` (e.g. `Home/myproj`) or `/<drive id>/<dir>`. Inside the job, each
  mounted folder is at `/work/<last path component>`. Use `ucloud ls` / `ucloud ls Home` to browse.
- **Cleanup.** Before you finish, run `ucloud list` and stop every job you started. Leave jobs you didn't start alone.
- **Secrets.** Never print, log or commit the API token (`~/.config/ucloud/token`).

## Failure handling

| Exit / message | Meaning | Action |
|---|---|---|
| exit 1 `No allocation for category` | The machine can't be paid for | Choose from `products --usable` |
| exit 1 `exceeds the remaining allocation` | Too many hours or too big a machine | Lower `-H` or the machine size |
| exit 1 `ended in state FAILURE/EXPIRED` | The job died while you waited | `status --messages`, `logs`, then relaunch |
| exit 1 `not ready after …s` | Still queued | `ucloud wait <id> --timeout 1800`, or stop it and try a smaller machine |
| exit 1 HTTP 401/403 | Token invalid or expired | Ask the user to create a new token (README, Setup) |
| exit 255 from `ssh` | SSH connection failed | Check `status` (is it RUNNING?) and retry once |
| any other non-zero from `ssh … --` | Your remote command failed | Read its stderr |

## Python API (same capabilities)

```python
from ucloud_api import TerminalJobs
tj = TerminalJobs()
job = tj.launch("cpu-amd-zen5-4-vcpu", hours=1, workdir="Home/proj", wait=True)
try:
    r = tj.run(job.id, "python train.py", timeout=3600)   # CompletedProcess(returncode, stdout, stderr)
finally:
    tj.stop(job.id)
```
