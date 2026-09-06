"""Git helpers. Every mutating pipeline run commits, so damage is one diff from undone.

Design rule: a commit failure must never lose or half-apply data. The write has
ALREADY happened by the time we commit, so we warn loudly and let the caller
finish — the alternative (raising) leaves the user with changed files and a
traceback, which is worse than an uncommitted but correct change.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click


def _run(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=root, capture_output=True, text=True, check=False
    )


def is_repo(root: Path) -> bool:
    """True only if git actually works here — a .git dir alone is not enough."""
    if not (root / ".git").exists():
        return False
    return _run(root, ["git", "rev-parse", "--git-dir"]).returncode == 0


def commit(root: Path, message: str, paths: list[Path] | None = None) -> str | None:
    """Commit if this is a working git repo. Returns short sha, or None."""
    if not is_repo(root):
        click.echo(
            "note: not a usable git repo — change written but NOT committed. "
            "Run `git init` to enable history.",
            err=True,
        )
        return None

    add = _run(root, ["git", "add", *( [str(p) for p in paths] if paths else ["-A"] )])
    if add.returncode != 0:
        click.echo(
            f"warning: `git add` failed ({add.stderr.strip()}) — data was written "
            f"but is NOT committed.",
            err=True,
        )
        return None

    if _run(root, ["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return None  # nothing staged

    res = _run(root, ["git", "commit", "-m", message, "--no-verify"])
    if res.returncode != 0:
        click.echo(
            f"warning: `git commit` failed ({res.stderr.strip()}) — data was written "
            f"but is NOT committed.",
            err=True,
        )
        return None

    sha = _run(root, ["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    return sha or None
