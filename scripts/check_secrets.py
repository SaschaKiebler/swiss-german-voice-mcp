#!/usr/bin/env python3
"""Refuse to commit secrets.

Run by ``.githooks/pre-commit`` on the staged files and by ``tests/test_no_secrets.py`` on every file git would
pick up (tracked plus untracked, minus .gitignore). It flags:

- files that hold secrets by name (.env, private keys),
- token shapes (Telegram bot token, Anthropic/OpenAI, GitHub, Hugging Face, AWS, private key blocks),
- the actual Telegram bot token of this machine, if one is configured, wherever it appears.

Findings name the file and the kind of secret, never the value.

    python3 scripts/check_secrets.py            # everything git would pick up
    python3 scripts/check_secrets.py --staged   # staged content only (pre-commit)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "Telegram bot token": re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{35}\b"),
    "Anthropic/OpenAI API key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
SECRET_FILE = re.compile(r"(?:^|/)(?:\.env(?:\..+)?|.+\.env|.+\.pem|.+\.key|id_(?:rsa|ed25519|ecdsa))$")
ALLOWED_FILE = re.compile(r"(?:^|/)\.env\.example$")


def local_secrets() -> list[str]:
    """Secrets configured on this machine, to catch them verbatim even when no pattern matches."""
    values = [os.environ.get("TELEGRAM_BOT_TOKEN", "")]
    state = os.environ.get("TELEGRAM_STATE_DIR") or os.path.join(
        os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "channels", "telegram"
    )
    try:
        for line in Path(state, ".env").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() and value.strip():
                values.append(value.strip())
    except OSError:
        pass
    return [v for v in values if len(v) >= 12]


def scan_text(name: str, text: str, secrets: list[str]) -> list[str]:
    findings = [f"{name}: {label}" for label, pattern in PATTERNS.items() if pattern.search(text)]
    if any(secret in text for secret in secrets):
        findings.append(f"{name}: a secret configured on this machine")
    return findings


def scan_name(name: str) -> list[str]:
    if SECRET_FILE.search(name) and not ALLOWED_FILE.search(name):
        return [f"{name}: file type that holds secrets"]
    return []


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def candidate_files(root: Path, staged: bool) -> list[str]:
    if staged:
        return _git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0")
    return _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")


def read(root: Path, name: str, staged: bool) -> str:
    if staged:  # the staged blob, not the working copy, is what gets committed
        data = subprocess.run(["git", "show", f":{name}"], cwd=root, capture_output=True, check=True).stdout
    else:
        data = (root / name).read_bytes()
    return data.decode("utf-8", errors="replace")


def scan(root: Path, staged: bool = False) -> list[str]:
    secrets = local_secrets()
    findings: list[str] = []
    for name in filter(None, candidate_files(root, staged)):
        findings += scan_name(name)
        if staged or (root / name).is_file():
            findings += scan_text(name, read(root, name, staged), secrets)
    return findings


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    findings = scan(root, staged="--staged" in sys.argv[1:])
    if findings:
        print("Secret check failed, nothing was committed:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        print("Remove the secret (or unstage the file) and commit again.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
