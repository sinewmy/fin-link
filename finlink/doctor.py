"""`finlink doctor` — schema validation for the whole workspace.

Fails loud. Never silently repairs: a schema that "fixes itself" hides exactly the
drift it exists to catch.
"""

from __future__ import annotations

from pathlib import Path

import click
from pydantic import BaseModel, ValidationError

from finlink.io.markdown import MarkdownError, read_document
from finlink.models import CashEntry, LedgerRow, PositionRow, ThesisFrontmatter
from finlink.workspace import Config, find_root


def run(root: Path) -> bool:
    errors: list[str] = []
    warnings: list[str] = []

    checks: list[tuple[str, type[BaseModel]]] = [
        ("portfolio/positions.md", PositionRow),
        ("portfolio/ledger.md", LedgerRow),
        ("portfolio/cash.md", CashEntry),
    ]
    for name, model in checks:
        path = root / name
        if not path.exists():
            errors.append(f"MISSING {name}")
            continue
        try:
            from finlink.io.markdown import parse_table

            for i, row in enumerate(parse_table(path.read_text(encoding="utf-8")), start=1):
                row = {k: ("" if v in ("", "-") else v) for k, v in row.items()}
                try:
                    model.model_validate(row)
                except ValidationError as exc:
                    for err in exc.errors():
                        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
                        errors.append(f"{name} row {i}: {loc}: {err['msg']}")
                except Exception as exc:  # pragma: no cover - defensive
                    errors.append(f"{name} row {i}: {exc}")
        except MarkdownError as exc:
            errors.append(f"{name}: {exc}")

    tdir = root / "theses"
    if not tdir.is_dir():
        warnings.append("no theses/ directory yet (expected after Phase 1)")
    else:
        slugs: dict[str, Path] = {}
        for f in sorted(tdir.glob("*.md")):
            try:
                doc = read_document(f)
            except MarkdownError as exc:
                errors.append(f"theses/{f.name}: {exc}")
                continue
            # Report every field problem at once; one error per run is tedious to fix.
            try:
                tf = ThesisFrontmatter.model_validate(doc.frontmatter)
            except ValidationError as exc:
                for err in exc.errors():
                    loc = ".".join(str(x) for x in err["loc"]) or "<root>"
                    errors.append(f"theses/{f.name}: {loc}: {err['msg']}")
                continue
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"theses/{f.name}: {exc}")
                continue
            if tf is None:  # pragma: no cover - defensive
                continue
            if tf.slug in slugs:
                errors.append(
                    f"theses/{f.name}: duplicate slug '{tf.slug}' (also in {slugs[tf.slug].name})"
                )
            slugs[tf.slug] = f
            expected = f"{tf.ticker}-{tf.slug}.md"
            if f.name != expected:
                warnings.append(f"theses/{f.name}: filename should be {expected}")
            ids = [h.id for h in tf.hypotheses]
            if len(ids) != len(set(ids)):
                errors.append(f"theses/{f.name}: duplicate hypothesis ids: {ids}")
            for h in tf.hypotheses:
                if h.parent and h.parent not in ids:
                    errors.append(
                        f"theses/{f.name}: hypothesis {h.id} has unknown parent {h.parent}"
                    )
            cores = [h for h in tf.hypotheses if h.kind == "core"]
            if len(cores) > 1:
                errors.append(f"theses/{f.name}: {len(cores)} core hypotheses; expected exactly 1")
            if not tf.invalidation_conditions:
                warnings.append(
                    f"theses/{f.name}: no invalidation_conditions — the product doc requires "
                    "'what would prove me wrong'"
                )

    # link check: position -> thesis
    try:
        from finlink.io.markdown import parse_table

        pfile = root / "portfolio" / "positions.md"
        if pfile.exists() and tdir.is_dir():
            for row in parse_table(pfile.read_text(encoding="utf-8")):
                slug = (row.get("thesis_slug") or "").strip()
                if slug in ("", "-"):
                    continue  # placeholder for "no linked thesis", not a dangling link
                if slug and slug not in slugs:
                    errors.append(
                        f"portfolio/positions.md: ticker {row.get('ticker')} links to "
                        f"thesis_slug '{slug}' which does not exist"
                    )
    except MarkdownError:
        pass

    cfg = Config.load(root)
    if cfg.base_currency != "USD":
        warnings.append(f"base_currency is {cfg.base_currency}; USD expected")
    for ccy in cfg.fx:
        if ccy not in ("USD", "HKD", "SEK"):
            errors.append(f"config/config.yaml: unsupported currency in fx: {ccy}")

    if warnings:
        click.echo("WARNINGS:")
        for w in warnings:
            click.echo(f"  - {w}")
    if errors:
        click.echo("ERRORS:")
        for problem in errors:
            click.echo(f"  - {problem}")
        click.echo(f"\ndoctor FAILED: {len(errors)} error(s)")
        return False

    click.echo(f"doctor OK — workspace {root}")
    return True


def main() -> None:
    root = find_root()
    raise SystemExit(0 if run(root) else 1)
