# Contributing

Thanks for your interest in improving NIM Key Manager! Contributions of all
kinds are welcome: bug reports, docs, tests and features.

## Ground rules

- Be respectful — see the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Never commit secrets or real API keys.** Only `.env.example` (with
  placeholders) belongs in the repo.
- Keep changes focused; open an issue first for larger features.

## Development setup

Requires Python 3.10+ (the project targets 3.12).

```bash
git clone https://github.com/BySergiMM/nim-key-manager.git
cd nim-key-manager
pip install -e ".[dev]"

nimkm up                    # the CLI is installed with the package
docker compose up --build   # or the full stack with PostgreSQL
```

By default the app runs on SQLite; set `DATABASE_URL` for PostgreSQL. Configuration is
read from the environment first, then `./.env`, then the generated `config.env` in
`NIMKM_HOME` — so exporting a variable always wins while you are developing.

### Layout worth knowing

- `app/mcp/stdio.py` — the transport clients actually use. **Nothing may print to
  stdout** in that code path; it carries JSON-RPC. Logging goes to stderr.
- `app/mcp/clients.py` — discovery and editing of client configuration files. It touches
  user data, so: back up first, refuse to rewrite what does not parse, preserve every
  unrelated key, write atomically.
- `app/cli.py` — the `nimkm` command (stdlib only; heavy imports stay inside commands).
  No user-facing string names Python, uv, uvicorn or FastAPI.
- `app/migrations/` — Alembic revisions, **inside the package** so the wheel can migrate
  itself. `alembic.ini` at the root only exists for `alembic revision --autogenerate`.
- `install.sh` / `install.ps1` — the one-command installers, exercised on every platform
  by `.github/workflows/installers.yml`.

Anything that changes installation or the MCP surface must keep those workflows green;
they are the contract behind the README's first command. To try your checkout against a
real client: `nimkm mcp setup --scope project`.

## Quality gates (must pass before a PR is merged)

CI runs exactly these — run them locally first:

```bash
ruff check .
mypy app
pytest --cov=app         # coverage gate: 85% (keep it high)
```

- **Typed code**: full type hints; `mypy` must pass.
- **Style**: `ruff` (lint + import order); line length 100.
- **Tests**: add/extend tests for any behavior change. New code should be covered.
- **Clean Architecture**: keep dependencies pointing inward — the `application`
  layer must not import FastAPI/MCP; access NVIDIA through the `KeyValidator` port.

## Pull requests

1. Fork and create a branch: `git checkout -b feat/short-description`.
2. Make the change with tests and docs.
3. Ensure the quality gates above pass.
4. Open a PR describing **what** and **why**. Link any related issue.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
where practical (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).

## Reporting bugs / requesting features

Use the issue templates. For security issues, follow [SECURITY.md](SECURITY.md)
instead of opening a public issue.
