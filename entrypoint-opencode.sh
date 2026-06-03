#!/bin/sh
set -e

OPENCODE_CONFIG_DIR="${OPENCODE_CONFIG_DIR:-/root/.config/opencode}"
OPENCODE_STATE_DIR="${OPENCODE_STATE_DIR:-/root/.local/state/opencode}"
export OPENCODE_CONFIG_DIR OPENCODE_STATE_DIR
mkdir -p "$OPENCODE_CONFIG_DIR" "$OPENCODE_STATE_DIR"

python3 - <<'PY'
import json
import os
from pathlib import Path

model = os.environ.get("OPENCODE_MODEL")
config_dir = Path(os.environ["OPENCODE_CONFIG_DIR"])
state_dir = Path(os.environ["OPENCODE_STATE_DIR"])
config_path = config_dir / "opencode.json"

try:
    config = json.loads(config_path.read_text())
except FileNotFoundError:
    config = {}

config["$schema"] = config.get("$schema", "https://opencode.ai/config.json")

if model:
    config["model"] = model
    config["small_model"] = model

permission = config.get("permission")
if isinstance(permission, str):
    permission = {"*": permission}
elif not isinstance(permission, dict):
    permission = {}

read_rules = permission.get("read")
if isinstance(read_rules, str):
    read_rules = {"*": read_rules}
elif isinstance(read_rules, dict):
    read_rules = dict(read_rules)
else:
    read_rules = {"*": "allow"}

managed_env_rules = (
    ".env",
    "*.env",
    ".env.*",
    "*.env.*",
    ".env.example",
    "*.env.example",
)
for pattern in managed_env_rules:
    read_rules.pop(pattern, None)

read_rules[".env"] = "deny"
read_rules["*.env"] = "deny"
read_rules[".env.*"] = "deny"
read_rules["*.env.*"] = "deny"
read_rules[".env.example"] = "allow"
read_rules["*.env.example"] = "allow"

permission["read"] = read_rules
config["permission"] = permission

config_path.write_text(json.dumps(config, indent=2) + "\n")

if model:
    (state_dir / "model.json").write_text(
        json.dumps({"recent": [], "favorite": [], "variant": {model: "default"}}, separators=(",", ":")) + "\n"
    )
PY

exec opencode serve --port 4096 --hostname 0.0.0.0
