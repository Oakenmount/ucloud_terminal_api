@AGENTS.md

## Developing this repo

- The package lives in `src/ucloud_api/` and uses only the standard library. Don't add runtime dependencies.
- Run the tests with `uv run python -m unittest discover -s tests`. They are offline and use a fake client.
- Live checks with `uv run ucloud doctor` / `products` / `balance` are free. `launch` costs allocation: use
  `cpu-amd-zen5-1-vcpu`, and `stop` the job right after.
- Keep the docs and the CLI help in step: `machines.py` (`CATALOG`, `CATALOG_HELP`), `README.md` and `AGENTS.md`.
