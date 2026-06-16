# Release Notes

## Versioning

The project uses semantic versioning:

```text
MAJOR.MINOR.PATCH
```

Current package metadata is stored in `pyproject.toml`.

## Release Checklist

Before tagging a release:

- run `make lint`;
- run `make test`;
- review `CHANGELOG.md`;
- check that `.env` files, generated workspaces, logs, caches, and OpenCode state are not present;
- confirm `README.md` and `docs/` match the current commands;
- confirm GitHub topics describe the project and match the package metadata;
- create GitHub release notes from the changelog entry.
