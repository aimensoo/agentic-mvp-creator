#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required" >&2
  exit 1
fi

for migration in migrations/*.sql; do
  echo "Applying ${migration}"
  psql "$DATABASE_URL" -f "$migration"
done
