#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"

REQUIRED_GITIGNORE_SNIPPETS = (
    ".claude/*",
    "!.claude/skills/",
    "!.claude/skills/**",
)


def fail(message: str) -> None:
    print(f"[ai-assets] ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def ensure_file_exists(path: Path, description: str) -> None:
    if not path.exists():
        fail(f"{description} is missing: {path.relative_to(ROOT)}")


def ensure_symlink() -> None:
    ensure_file_exists(AGENTS, "canonical AGENTS.md")
    if not CLAUDE.exists():
        fail("CLAUDE.md is missing")
    if CLAUDE.is_symlink():
        target = Path(CLAUDE.readlink())
        if target != Path("AGENTS.md"):
            fail(f"CLAUDE.md must point to AGENTS.md, found: {target}")
        return

    # Git for Windows materializes symlinks as plain files when core.symlinks=false.
    if CLAUDE.read_text(encoding="utf-8").strip() != "AGENTS.md":
        fail("CLAUDE.md must be a symlink to AGENTS.md or its Windows plain-file representation")

    result = subprocess.run(
        ["git", "ls-files", "--stage", "--", "CLAUDE.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    if not result.stdout.startswith("120000 "):
        fail("CLAUDE.md plain-file representation is only valid when tracked as a Git symlink")


def ensure_gitignore_rules() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for snippet in REQUIRED_GITIGNORE_SNIPPETS:
        if snippet not in gitignore:
            fail(f".gitignore is missing required AI asset rule: {snippet}")


def ensure_no_tracked_claude_artifacts() -> None:
    result = subprocess.run(
        ["git", "ls-files", "--", ".claude"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    allowed_prefixes = (".claude/skills/",)
    for path in tracked:
        if path.startswith(allowed_prefixes):
            continue
        fail(f"tracked .claude artifact outside skills/: {path}")


def main() -> None:
    ensure_symlink()
    ensure_gitignore_rules()
    ensure_no_tracked_claude_artifacts()
    print("[ai-assets] OK")


if __name__ == "__main__":
    main()
