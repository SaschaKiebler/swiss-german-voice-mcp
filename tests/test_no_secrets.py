import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_secrets", ROOT / "scripts" / "check_secrets.py")
check_secrets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_secrets)


def _in_git_repo() -> bool:
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True)
    return result.returncode == 0 and Path(result.stdout.strip()) == ROOT


@pytest.mark.skipif(not _in_git_repo(), reason="needs the git checkout")
def test_repository_contains_no_secrets():
    assert check_secrets.scan(ROOT) == []


def test_scanner_flags_token_shapes():
    # Built at runtime, so no token-shaped literal sits in this file.
    telegram_token = "1234567890:" + "A" * 35
    assert check_secrets.scan_text("x.py", f"TOKEN = '{telegram_token}'", []) == ["x.py: Telegram bot token"]
    assert check_secrets.scan_text("x.md", "key " + "sk-ant-" + "b" * 30, []) == ["x.md: Anthropic/OpenAI API key"]
    assert check_secrets.scan_text("x.md", "nothing to see", []) == []


def test_scanner_flags_local_secret_verbatim():
    assert check_secrets.scan_text("notes.txt", "pasted: my-very-own-secret", ["my-very-own-secret"]) == [
        "notes.txt: a secret configured on this machine"
    ]


def test_scanner_flags_secret_files():
    assert check_secrets.scan_name(".env") == [".env: file type that holds secrets"]
    assert check_secrets.scan_name("config/prod.env") == ["config/prod.env: file type that holds secrets"]
    assert check_secrets.scan_name("deploy/server.pem") == ["deploy/server.pem: file type that holds secrets"]
    assert check_secrets.scan_name(".env.example") == []
    assert check_secrets.scan_name("src/environment.py") == []
