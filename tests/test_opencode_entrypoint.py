import json
import os
import stat
import subprocess
from pathlib import Path


def test_entrypoint_writes_opencode_config_model_from_env(tmp_path):
    config_dir = tmp_path / "config"
    state_dir = tmp_path / "state"
    bin_dir = tmp_path / "bin"
    args_file = tmp_path / "opencode-args.txt"
    config_dir.mkdir()
    bin_dir.mkdir()

    (config_dir / "opencode.json").write_text(json.dumps({"agent": {"build": {"tools": {"bash": True}}}}) + "\n")
    fake_opencode = bin_dir / "opencode"
    fake_opencode.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$OPENCODE_ARGS_FILE"\n')
    fake_opencode.chmod(fake_opencode.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', os.defpath)}"
    env["OPENCODE_ARGS_FILE"] = str(args_file)
    env["OPENCODE_CONFIG_DIR"] = str(config_dir)
    env["OPENCODE_STATE_DIR"] = str(state_dir)
    env["OPENCODE_MODEL"] = "zai-coding-plan/glm-5.1"

    script = Path(__file__).resolve().parents[1] / "entrypoint-opencode.sh"
    result = subprocess.run(
        ["/bin/sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    config = json.loads((config_dir / "opencode.json").read_text())
    assert config["model"] == "zai-coding-plan/glm-5.1"
    assert config["small_model"] == "zai-coding-plan/glm-5.1"
    assert config["agent"]["build"]["tools"]["bash"] is True
    assert config["permission"]["read"]["*"] == "allow"
    assert config["permission"]["read"][".env"] == "deny"
    assert config["permission"]["read"]["*.env"] == "deny"
    assert config["permission"]["read"][".env.*"] == "deny"
    assert config["permission"]["read"]["*.env.*"] == "deny"
    assert config["permission"]["read"][".env.example"] == "allow"
    assert config["permission"]["read"]["*.env.example"] == "allow"

    state = json.loads((state_dir / "model.json").read_text())
    assert state["variant"] == {"zai-coding-plan/glm-5.1": "default"}
    assert args_file.read_text().splitlines() == ["serve", "--port", "4096", "--hostname", "0.0.0.0"]


def test_entrypoint_preserves_existing_permissions_and_enforces_env_read_deny(tmp_path):
    config_dir = tmp_path / "config"
    state_dir = tmp_path / "state"
    bin_dir = tmp_path / "bin"
    args_file = tmp_path / "opencode-args.txt"
    config_dir.mkdir()
    bin_dir.mkdir()

    (config_dir / "opencode.json").write_text(
        json.dumps(
            {
                "permission": {
                    "bash": {"*": "allow", "rm *": "deny"},
                    "read": {"*": "ask", "README.md": "allow", "*.env.example": "deny"},
                }
            }
        )
        + "\n"
    )
    fake_opencode = bin_dir / "opencode"
    fake_opencode.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$OPENCODE_ARGS_FILE"\n')
    fake_opencode.chmod(fake_opencode.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', os.defpath)}"
    env["OPENCODE_ARGS_FILE"] = str(args_file)
    env["OPENCODE_CONFIG_DIR"] = str(config_dir)
    env["OPENCODE_STATE_DIR"] = str(state_dir)
    env.pop("OPENCODE_MODEL", None)

    script = Path(__file__).resolve().parents[1] / "entrypoint-opencode.sh"
    result = subprocess.run(
        ["/bin/sh", str(script)],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    config = json.loads((config_dir / "opencode.json").read_text())
    assert "model" not in config
    assert config["permission"]["bash"] == {"*": "allow", "rm *": "deny"}
    assert config["permission"]["read"]["*"] == "ask"
    assert config["permission"]["read"]["README.md"] == "allow"
    assert config["permission"]["read"][".env"] == "deny"
    assert config["permission"]["read"]["*.env.*"] == "deny"
    assert config["permission"]["read"]["*.env.example"] == "allow"
    assert not (state_dir / "model.json").exists()
