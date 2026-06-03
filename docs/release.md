# Release Notes

## Versioning

The project uses semantic versioning:

```text
MAJOR.MINOR.PATCH
```

Current package metadata is stored in `pyproject.toml`.

## Release Checklist

Before tagging a release:

- replace placeholder `pyproject.toml` project URLs with the final GitHub repository URL;
- run `make lint`;
- run `make test`;
- review `CHANGELOG.md`;
- check that `.env` files, generated workspaces, logs, caches, and OpenCode state are not present;
- confirm `README.md` and `docs/` match the current commands;
- create GitHub release notes from the changelog entry.
