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

# Run the local stack (app + PostgreSQL) if you prefer Docker:
docker compose up --build
```

By default the app runs on SQLite locally; set `DATABASE_URL` for PostgreSQL.

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
