"""The domain-isolation rule (TECHNICAL_DESIGN §2.2 / standard #4).

domain/ must never import llm/, ingest/, or api/. Programs own facts and math;
the LLM owns understanding and expression.
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN = {"llm", "ingest", "api"}
ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def test_domain_never_imports_llm_ingest_or_api():
    domain = ROOT / "finlink" / "domain"
    assert domain.is_dir(), "finlink/domain missing"
    violations: list[str] = []
    for f in domain.rglob("*.py"):
        bad = _imports(f) & FORBIDDEN
        if bad:
            violations.append(f"{f.relative_to(ROOT)} imports {sorted(bad)}")
    assert not violations, "domain/ must stay pure:\n" + "\n".join(violations)


def test_no_float_conversion_in_domain_money_paths():
    """Guard: money code must never round-trip through float."""
    for name in ["pnl.py", "portfolio.py", "money.py"]:
        path = ROOT / "finlink" / "domain" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # strip docstrings so prose like "1 SEK = 0.095 USD" is not flagged
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "float", f"{name}: float() is forbidden in money code"
