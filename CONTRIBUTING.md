# Contributing

This repository accepts focused fixes and improvements that keep the pipeline runnable, documented, and safe to operate.

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Fill `.env` with local endpoints and tokens. Do not commit real `.env` files, API keys, OpenCode auth state, generated workspaces, logs, or caches.

## Checks

Run the same checks before opening or updating a pull request:

```bash
make lint
make format-check
make test
```

For runtime changes, also verify the relevant Docker Compose path and migrations.

## Pull Request Quality

Keep changes scoped. A pull request should describe:

- what changed;
- why it changed;
- how it was verified;
- risks, migration notes, or breaking changes.

Generated MVP workspaces under `workspaces/`, local OpenCode state, dependency folders, and build artifacts must stay out of git.

## Security

Report security issues privately. See [SECURITY.md](SECURITY.md).
