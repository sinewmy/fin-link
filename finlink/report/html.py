"""Self-contained static HTML from markdown-derived figures.

No server, no CDN, no JavaScript libraries, no external assets: charts are inline
SVG computed here. Open the file directly and it renders offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from html import escape
from pathlib import Path

from finlink.domain.exposure import ExposureReport
from finlink.domain.money import q
from finlink.domain.snapshots import Snapshot

PCT = Decimal("0.01")

DISCLAIMER = (
    "Not investment advice. Not for execution. Figures are computed by fin-link from "
    "your own records; interpretation only."
)


@dataclass(frozen=True)
class ChartSeries:
    name: str
    points: list[tuple[str, Decimal]]
    colour: str = "#3b7dd8"


def _scale_points(
    points: list[tuple[str, Decimal]], width: int, height: int, pad: int
) -> tuple[list[tuple[float, float]], Decimal, Decimal]:
    values = [v for _x, v in points]
    lo = min(values)
    hi = max(values)
    span = hi - lo
    if span == 0:
        span = Decimal("1")
    n = max(len(points) - 1, 1)
    scaled: list[tuple[float, float]] = []
    for i, (_label, v) in enumerate(points):
        x = pad + (width - 2 * pad) * (i / n)
        y = height - pad - (height - 2 * pad) * float((v - lo) / span)
        scaled.append((x, y))
    return scaled, lo, hi


def line_chart(
    series: list[ChartSeries], *, width: int = 640, height: int = 220, pad: int = 36
) -> str:
    if not series or not any(s.points for s in series):
        return "<p><em>No history yet — run <code>finlink snapshot</code> over time.</em></p>"

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" xmlns="http://www.w3.org/2000/svg">'
    ]
    parts.append(
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" '
        f'stroke="#d0d7de" stroke-width="1"/>'
    )
    all_values = [v for s in series for _l, v in s.points]
    lo, hi = min(all_values), max(all_values)
    if hi == lo:
        hi = lo + Decimal("1")

    for s in series:
        if not s.points:
            continue
        pts, _lo, _hi = _scale_points(s.points, width, height, pad)
        if len(pts) == 1:
            cx, cy = pts[0]
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" fill="{s.colour}"/>')
            continue
        d = " ".join(f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}" for i, (x, y) in enumerate(pts))
        parts.append(
            f'<path d="{d}" fill="none" stroke="{s.colour}" stroke-width="2" '
            f'stroke-linejoin="round"/>'
        )
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="{s.colour}"/>')

    parts.append(
        f'<text x="{pad}" y="{pad - 12}" font-size="12" fill="#57606a">{q(hi, PCT)}</text>'
    )
    parts.append(
        f'<text x="{pad}" y="{height - pad + 22}" font-size="12" fill="#57606a">{q(lo, PCT)}</text>'
    )
    first_labels = [s.points[0][0] for s in series if s.points]
    last_labels = [s.points[-1][0] for s in series if s.points]
    if first_labels:
        parts.append(
            f'<text x="{pad}" y="{height - 8}" font-size="11" '
            f'fill="#57606a">{escape(str(first_labels[0]))}</text>'
        )
    if last_labels:
        parts.append(
            f'<text x="{width - pad}" y="{height - 8}" font-size="11" fill="#57606a" '
            f'text-anchor="end">{escape(str(last_labels[-1]))}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def bar_chart(
    buckets: list[tuple[str, Decimal]],
    *,
    width: int = 640,
    bar_height: int = 26,
    colour: str = "#3b7dd8",
) -> str:
    if not buckets:
        return "<p><em>No exposure data.</em></p>"
    height = bar_height * len(buckets) + 12
    max_val = max((v for _n, v in buckets), default=Decimal("1")) or Decimal("1")
    label_w = 150
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" xmlns="http://www.w3.org/2000/svg">'
    ]
    for i, (name, value) in enumerate(buckets):
        y = i * bar_height + 6
        w = int((width - label_w - 90) * float(value / max_val)) if max_val else 0
        parts.append(
            f'<text x="8" y="{y + 15}" font-size="12" fill="#24292f">{escape(str(name))}</text>'
        )
        parts.append(
            f'<rect x="{label_w}" y="{y + 3}" width="{max(w, 1)}" height="16" '
            f'fill="{colour}" rx="2"/>'
        )
        parts.append(
            f'<text x="{label_w + max(w, 1) + 8}" y="{y + 16}" font-size="12" '
            f'fill="#24292f">{q(value, PCT)}%</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "<p><em>none</em></p>"
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render(
    *,
    exposure: ExposureReport | None = None,
    snapshots: list[Snapshot] | None = None,
    correlation_rows: list[tuple[str, list[str]]] | None = None,
    correlation_header: list[str] | None = None,
    alerts: list[tuple[str, str, str]] | None = None,
    title: str = "fin-link portfolio report",
    markdown_sections: list[tuple[str, str]] | None = None,
) -> str:
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(title)}</title>",
        "<style>",
        _CSS,
        "</style></head><body>",
        f"<h1>{escape(title)}</h1>",
        f'<p class="disclaimer">{escape(DISCLAIMER)}</p>',
    ]

    if exposure is not None:
        parts.append("<h2>Exposure</h2>")
        parts.append(
            "<p>Total positions (USD): "
            f"{escape(str(q(exposure.total_usd)))} · HHI "
            f"{escape(str(q(exposure.concentration_hhi, Decimal('0.0001'))))} · "
            f"effective positions {escape(str(q(exposure.effective_positions, PCT)))}</p>"
        )
        parts.append("<h3>By currency</h3>")
        parts.append(bar_chart([(b.name, b.weight_pct) for b in exposure.currencies]))
        parts.append("<h3>By sector</h3>")
        parts.append(
            bar_chart([(b.name, b.weight_pct) for b in exposure.sectors], colour="#2da44e")
        )
        parts.append("<h3>By country</h3>")
        parts.append(
            bar_chart([(b.name, b.weight_pct) for b in exposure.countries], colour="#bf8700")
        )
        parts.append(f"<p><strong>FX summary:</strong> {escape(exposure.fx_note)}</p>")

    if correlation_rows and correlation_header:
        parts.append("<h2>Correlation (daily returns)</h2>")
        parts.append(
            _table(correlation_header, [[name, *cells] for name, cells in correlation_rows])
        )

    if snapshots and len(snapshots) > 1:
        parts.append("<h2>History</h2>")
        parts.append("<h3>Concentration (HHI)</h3>")
        parts.append(
            line_chart(
                [
                    ChartSeries(
                        "HHI",
                        [(s.day.isoformat(), s.hhi * Decimal("10000")) for s in snapshots],
                    )
                ]
            )
        )
        parts.append("<h3>Cash %</h3>")
        parts.append(
            line_chart(
                [
                    ChartSeries(
                        "cash %", [(s.day.isoformat(), s.cash_pct) for s in snapshots], "#2da44e"
                    )
                ]
            )
        )
        tickers = sorted({t for s in snapshots for t in s.weights})
        if tickers:
            parts.append("<h3>Position weights</h3>")
            palette = ["#3b7dd8", "#2da44e", "#bf8700", "#cf222e", "#8250df"]
            parts.append(
                line_chart(
                    [
                        ChartSeries(
                            t,
                            [
                                (s.day.isoformat(), s.weights.get(t, Decimal("0")))
                                for s in snapshots
                            ],
                            palette[i % len(palette)],
                        )
                        for i, t in enumerate(tickers)
                    ]
                )
            )
    elif snapshots:
        parts.append(
            "<p><em>Only one snapshot recorded so far — history charts appear after "
            "the second run.</em></p>"
        )

    if alerts:
        parts.append("<h2>Open alerts</h2>")
        parts.append(_table(["rule", "scope", "detail"], [list(a) for a in alerts]))

    for heading, body in markdown_sections or []:
        parts.append(f"<h2>{escape(heading)}</h2>")
        parts.append(f"<pre>{escape(body)}</pre>")

    parts.append("</body></html>")
    return "\n".join(parts)


_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial,
       sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem;
       color: #24292f; line-height: 1.5; }
h1 { border-bottom: 2px solid #d0d7de; padding-bottom: .3rem; }
h2 { margin-top: 2rem; border-bottom: 1px solid #d0d7de; padding-bottom: .2rem; }
h3 { color: #57606a; font-size: 1rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: .9rem; }
th, td { border: 1px solid #d0d7de; padding: .35rem .6rem; text-align: left; }
th { background: #f6f8fa; }
svg { border: 1px solid #d0d7de; border-radius: 6px; margin: .5rem 0 1.5rem;
      background: #fff; }
pre { background: #f6f8fa; padding: 1rem; border-radius: 6px; overflow-x: auto;
      font-size: .85rem; }
.disclaimer { color: #57606a; font-size: .85rem; font-style: italic;
              border-left: 3px solid #d0d7de; padding-left: .8rem; }
"""


def write(path: Path, html: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
