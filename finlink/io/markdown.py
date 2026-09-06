"""Markdown table + frontmatter reading and writing.

SAFETY CONTRACT (see TECHNICAL_DESIGN §4.1):
  * Validations are APPENDED as new sections. Bodies are never rewritten.
  * Frontmatter changes go through set_frontmatter_key() — named key only.
  * All writes are atomic (temp file + os.replace).
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class MarkdownError(RuntimeError):
    pass


@dataclass
class Doc:
    frontmatter: dict[str, object]
    body: str
    path: Path

    def section(self, heading: str) -> str | None:
        return extract_section(self.body, heading)


def parse_document(text: str, path: Path | None = None) -> Doc:
    m = FM_RE.match(text)
    if not m:
        raise MarkdownError(
            f"{path or '<text>'}: no YAML frontmatter found (expected --- fenced block at top)"
        )
    try:
        fm: dict[str, object] = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise MarkdownError(f"{path or '<text>'}: invalid YAML frontmatter: {e}") from e
    if not isinstance(fm, dict):
        raise MarkdownError(f"{path or '<text>'}: frontmatter must be a mapping")
    return Doc(frontmatter=fm, body=m.group(2), path=path or Path("<text>"))


def render_document(doc: Doc) -> str:
    fm = yaml.safe_dump(doc.frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm}\n---\n\n{doc.body}"


def read_document(path: Path) -> Doc:
    return parse_document(path.read_text(encoding="utf-8"), path)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def set_frontmatter_key(path: Path, key: str, value: object) -> None:
    """Mutate ONE named frontmatter key. Never touches the body."""
    doc = read_document(path)
    doc.frontmatter[key] = value
    write_atomic(path, render_document(doc))


def append_section(path: Path, heading: str, content: str) -> None:
    """Append a new `## <heading>` section. Existing content is preserved byte-for-byte."""
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    text += f"\n## {heading}\n\n{content.strip()}\n"
    write_atomic(path, text)


def extract_section(body: str, heading: str) -> str | None:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
    )
    m = pattern.search(body)
    return m.group(1).strip() if m else None


def parse_table(text: str) -> list[dict[str, str]]:
    """Parse the first GitHub-style markdown table in `text`."""
    rows = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 2:
        return []

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]
    header = cells(rows[0])
    out: list[dict[str, str]] = []
    for line in rows[2:]:  # skip separator row
        vals = cells(line)
        if len(vals) != len(header):
            raise MarkdownError(
                f"table row has {len(vals)} cells, expected {len(header)}: {line!r}"
            )
        out.append(dict(zip(header, vals, strict=True)))
    return out


def render_table(rows: list[dict[str, str]], headers: list[str]) -> str:
    def esc(v: str) -> str:
        return str(v).replace("|", "\\|")
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        out.append("| " + " | ".join(esc(r.get(h, "")) for h in headers) + " |")
    return "\n".join(out)
