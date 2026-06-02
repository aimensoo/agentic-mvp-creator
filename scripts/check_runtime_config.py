from __future__ import annotations

from pathlib import Path


REQUIRED_FOR_E2E = {
    "WEBHOOK_SECRET": {"", "change-me"},
    "TELEGRAM_BOT_TOKEN": {""},
    "OPENCLAW_API_URL": {""},
    "OPENCLAW_MODEL": {""},
    "GITHUB_TOKEN": {""},
    "GITHUB_REPO_OWNER": {""},
    "GITHUB_REPO_NAME": {""},
}


def main() -> int:
    env_path = Path(".env")
    if not env_path.exists():
        print(".env is missing. Create it with: cp .env.example .env")
        return 1

    values = _load_env(env_path)
    invalid = []
    for key, placeholder_values in REQUIRED_FOR_E2E.items():
        value = values.get(key, "").strip()
        if value in placeholder_values:
            invalid.append(key)

    if invalid:
        print("Missing or placeholder values:")
        for key in invalid:
            print(f"- {key}")
        return 1

    print("Runtime config looks ready for E2E. Secret values were not printed.")
    return 0


def _load_env(path: Path) -> dict[str, str]:
    result = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
