"""Exporta el panel a HTML estatico para GitHub Pages (uso movil / PWA).

El sitio se genera desde la misma SQLite que usa el panel local; no calcula
senales ni ejecuta nada. GitHub Actions corre la ingesta y este exportador una
vez al dia, y publica el resultado como sitio estatico.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from src.config.settings import ROOT, Settings
from src.data.store import Store
from src.paper_trading.shadow import equity_points, paper_stats
from src.quant.risk import RiskLimits

CSS = """
:root {
  --bg: #0b0f14;
  --card: #121821;
  --line: #213040;
  --text: #e6edf3;
  --muted: #8b949e;
  --green: #3fb950;
  --red: #f85149;
  --amber: #d29922;
  --blue: #58a6ff;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 1000px; margin: 0 auto; padding: 16px 16px 40px; }
header { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; justify-content: space-between; }
h1 { font-size: 1.45rem; margin: 4px 0; letter-spacing: -.02em; }
h2 { font-size: 1.1rem; margin: 22px 0 10px; }
h3 { font-size: .95rem; margin: 0 0 6px; color: var(--muted); font-weight: 600; }
p { line-height: 1.5; }
a { color: var(--blue); }
.muted { color: var(--muted); }
.small { font-size: .82rem; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.tabs {
  display: flex; gap: 6px; overflow-x: auto; padding: 10px 0;
  position: sticky; top: 0; background: rgba(11,15,20,.96); z-index: 10;
  border-bottom: 1px solid var(--line); margin-bottom: 12px;
  scrollbar-width: none;
}
.tabs::-webkit-scrollbar { display: none; }
.tab-btn {
  background: var(--card); color: var(--muted); border: 1px solid var(--line);
  padding: 8px 14px; border-radius: 999px; font-size: .88rem; white-space: nowrap;
  cursor: pointer;
}
.tab-btn.active { color: #0b0f14; background: var(--green); border-color: var(--green); font-weight: 700; }
.tab-panel { display: none; }
.tab-panel.active { display: block; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.card {
  background: var(--card); border: 1px solid var(--line);
  border-radius: 14px; padding: 14px;
}
.kpi .label { color: var(--muted); font-size: .75rem; text-transform: uppercase; letter-spacing: .06em; }
.kpi .value { font-size: 1.55rem; font-weight: 700; margin-top: 4px; }
.signal-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; }
.signal-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.asset { font-size: 1.15rem; font-weight: 700; }
.badge { padding: 3px 9px; border-radius: 999px; font-size: .72rem; font-weight: 700; letter-spacing: .04em; }
.badge.long { background: rgba(63,185,80,.15); color: var(--green); border: 1px solid rgba(63,185,80,.4); }
.badge.short, .badge.no { background: rgba(248,81,73,.12); color: var(--red); border: 1px solid rgba(248,81,73,.35); }
.badge.warn { background: rgba(210,153,34,.12); color: var(--amber); border: 1px solid rgba(210,153,34,.35); }
.badge.llm { background: rgba(88,166,255,.12); color: var(--blue); border: 1px solid rgba(88,166,255,.35); }
.row { display: flex; justify-content: space-between; gap: 10px; padding: 5px 0; border-bottom: 1px solid rgba(33,48,64,.5); }
.row:last-child { border-bottom: 0; }
.pos { color: var(--green); }
.neg { color: var(--red); }
.warn { color: var(--amber); }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; }
table { width: 100%; border-collapse: collapse; font-size: .84rem; min-width: 560px; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); font-weight: 600; background: rgba(255,255,255,.02); }
tr:last-child td { border-bottom: 0; }
.chart { width: 100%; height: auto; display: block; background: var(--card); border: 1px solid var(--line); border-radius: 14px; }
.notice { border-left: 3px solid var(--amber); background: rgba(210,153,34,.08); padding: 10px 12px; border-radius: 8px; }
.ok { border-left-color: var(--green); background: rgba(63,185,80,.08); }
.links { display: flex; flex-wrap: wrap; gap: 8px; }
.links a { background: var(--card); border: 1px solid var(--line); padding: 7px 12px; border-radius: 10px; text-decoration: none; font-size: .84rem; }
details { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; margin-top: 10px; }
summary { cursor: pointer; color: var(--muted); }
ul { padding-left: 20px; }
li { margin: 4px 0; line-height: 1.4; }
footer { margin-top: 28px; padding-top: 14px; border-top: 1px solid var(--line); color: var(--muted); font-size: .78rem; }
@media (max-width: 600px) {
  .wrap { padding: 12px 12px 32px; }
  h1 { font-size: 1.25rem; }
  .kpi .value { font-size: 1.3rem; }
  table { font-size: .78rem; }
}
"""

JS = """
(function () {
  const buttons = Array.from(document.querySelectorAll('.tab-btn'));
  const panels = Array.from(document.querySelectorAll('.tab-panel'));
  function show(name) {
    buttons.forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    panels.forEach(p => p.classList.toggle('active', p.id === name));
  }
  buttons.forEach(b => b.addEventListener('click', () => show(b.dataset.tab)));
  const initial = (location.hash || '#hoy').slice(1);
  show(document.getElementById(initial) ? initial : 'hoy');
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('./sw.js').catch(() => {});
  }
})();
"""


def _safe(value: object) -> str:
    return html.escape(str(value), quote=True)


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_ts(value: str | None, fmt: str = "%d %b %Y %H:%M") -> str:
    stamp = _ts(value)
    return stamp.strftime(fmt) if stamp else "—"


def _pct(value: float | None, digits: int = 2, signed: bool = True) -> str:
    if value is None:
        return "—"
    fmt = f"{{:+.{digits}f}}%" if signed else f"{{:.{digits}f}}%"
    return fmt.format(value * 100)


def _money(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _signal_label(value: str) -> str:
    return {"LONG_BIAS": "LONG", "RISK_OFF": "RISK OFF", "NO_TRADE": "NO TRADE"}.get(
        value, value.replace("_", " ")
    )


def _signal_class(value: str) -> str:
    if value == "LONG_BIAS":
        return "long"
    if value in {"RISK_OFF", "NO_TRADE"}:
        return "no"
    return "warn"


def _payload(row: dict) -> dict:
    raw = row.get("payload")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _latest_by_asset(store: Store) -> list[dict]:
    rows = store.latest_signals(limit=200)
    latest: dict[str, dict] = {}
    for row in rows:
        current = latest.get(row["asset"])
        if current is None or str(row["timestamp"]) > str(current["timestamp"]):
            latest[row["asset"]] = row
    return sorted(latest.values(), key=lambda r: (r["rank_position"], r["asset"]))


def _sma(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        result.append(running / window if index >= window - 1 else None)
    return result


def _segments(values: list[float | None], xs: list[float], ys: list[float]) -> list[str]:
    paths: list[str] = []
    points: list[str] = []
    for value, x, y in zip(values, xs, ys, strict=False):
        if value is None:
            if len(points) > 1:
                paths.append(" ".join(points))
            points = []
            continue
        points.append(f"{x:.1f},{y:.1f}")
    if len(points) > 1:
        paths.append(" ".join(points))
    return paths


def _chart_svg(
    series: list[tuple[str, str, list[float | None]]],
    labels: list[datetime],
    *,
    value_fmt: str = "{:,.2f}",
    height: int = 240,
) -> str:
    width = 680
    pad_l, pad_r, pad_t, pad_b = 54, 12, 16, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    values = [v for _, _, points in series for v in points if v is not None]
    if len(values) < 2:
        return (
            '<div class="card muted small">Sin datos suficientes para el grafico.</div>'
        )

    low, high = min(values), max(values)
    if high == low:
        high = low + 1.0
    margin = (high - low) * 0.08
    low -= margin
    high += margin
    count = max(len(labels), 2)
    xs = [pad_l + plot_w * (i / (count - 1)) for i in range(count)]

    def y_of(value: float) -> float:
        return pad_t + plot_h * (1 - (value - low) / (high - low))

    grid: list[str] = []
    for step in range(5):
        value = low + (high - low) * step / 4
        y = y_of(value)
        grid.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" y2="{y:.1f}" '
            f'stroke="#213040" stroke-width="1" />'
            f'<text x="{pad_l - 6}" y="{y + 4:.1f}" fill="#8b949e" font-size="10" '
            f'text-anchor="end">{_safe(value_fmt.format(value))}</text>'
        )

    lines: list[str] = []
    legend: list[str] = []
    for name, color, points in series:
        ys = [y_of(v) if v is not None else 0.0 for v in points]
        for path in _segments(points, xs, ys):
            lines.append(
                f'<polyline points="{path}" fill="none" stroke="{color}" '
                f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />'
            )
        legend.append(
            f'<span style="display:inline-flex;align-items:center;gap:5px;'
            f'font-size:11px;color:#8b949e;margin-right:10px">'
            f'<i style="width:10px;height:3px;background:{color};display:inline-block"></i>'
            f"{_safe(name)}</span>"
        )

    x_labels = ""
    if labels:
        label_fmt = "%b %y" if labels[0].year != labels[-1].year else "%d %b"
        x_labels = (
            f'<text x="{pad_l}" y="{height - 8}" fill="#8b949e" font-size="10">'
            f"{labels[0].strftime(label_fmt)}</text>"
            f'<text x="{pad_l + plot_w}" y="{height - 8}" fill="#8b949e" font-size="10" '
            f'text-anchor="end">{labels[-1].strftime(label_fmt)}</text>'
        )

    return (
        '<div class="card" style="padding:8px">'
        f'<div style="padding:2px 4px 4px">{"".join(legend)}</div>'
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Grafico de series temporales">'
        f"{''.join(grid)}{''.join(lines)}{x_labels}</svg></div>"
    )


def _market_chart(candles: list, asset: str) -> str:
    if len(candles) < 2:
        return f'<div class="card muted small">{_safe(asset)}: sin velas guardadas.</div>'
    closes = [c.close for c in candles]
    series: list[tuple[str, str, list[float | None]]] = [
        ("Cierre", "#58a6ff", [float(v) for v in closes])
    ]
    if len(closes) >= 30:
        series.append(("SMA 30", "#3fb950", _sma(closes, 30)))
    if len(closes) >= 200:
        series.append(("SMA 200", "#d29922", _sma(closes, 200)))
    return _chart_svg(series, [c.ts for c in candles])


def _equity_chart(store: Store) -> str:
    points = equity_points(store)
    if len(points) < 2:
        return '<div class="card muted small">Sin operaciones cerradas: la curva esta plana.</div>'
    return _chart_svg(
        [("Capital", "#3fb950", [point["equity"] for point in points])],
        [point["at"] for point in points],
        value_fmt="{:.3f}",
        height=220,
    )


def _latest_backtest(reports_dir: Path) -> tuple[dict | None, str | None]:
    files = sorted(reports_dir.glob("backtest-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None, None
    try:
        return json.loads(files[0].read_text(encoding="utf-8")), files[0].name
    except (OSError, json.JSONDecodeError):
        return None, None


def _copy_reports(reports_dir: Path, out_dir: Path) -> list[dict[str, str]]:
    target = out_dir / "reports"
    target.mkdir(parents=True, exist_ok=True)
    specs = [
        ("reporte-*.md", "Reporte diario"),
        ("backtest-*.md", "Backtest walk-forward"),
        ("barrido-*.md", "Barrido de horizontes"),
        ("comparacion-*.md", "Comparacion de features"),
    ]
    links: list[dict[str, str]] = []
    for pattern, label in specs:
        files = sorted(reports_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            continue
        shutil.copy2(files[0], target / files[0].name)
        links.append({"label": label, "href": f"reports/{files[0].name}"})
    return links


def _icon_png(size: int) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (size, size), "#0b0f14")
    draw = ImageDraw.Draw(image)
    unit = size / 16
    draw.rounded_rectangle(
        (unit, unit, size - unit, size - unit),
        radius=int(unit * 2),
        outline="#213040",
        width=max(1, int(unit / 3)),
    )
    bars = [(3.5, 10.5), (6.5, 8.5), (9.5, 6.0)]
    for index, (x, top) in enumerate(bars):
        draw.rounded_rectangle(
            (x * unit, top * unit, (x + 1.8) * unit, 13.5 * unit),
            radius=int(unit / 2),
            fill="#3fb950" if index == 2 else "#238636",
        )
    draw.line(
        [(3.5 * unit, 9.5 * unit), (7.0 * unit, 7.5 * unit), (10.5 * unit, 5.0 * unit)],
        fill="#58a6ff",
        width=max(2, int(unit / 2)),
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _manifest() -> str:
    return json.dumps(
        {
            "name": "Trading AI - panel personal",
            "short_name": "Trading AI",
            "description": "Senales, paper trading y backtests. Solo lectura.",
            "start_url": "./",
            "scope": "./",
            "display": "standalone",
            "background_color": "#0b0f14",
            "theme_color": "#0b0f14",
            "icons": [
                {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
                {
                    "src": "icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _service_worker(version: str) -> str:
    return f"""
const CACHE = 'trading-ai-{version}';
const ASSETS = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png'];
self.addEventListener('install', event => {{
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
}});
self.addEventListener('activate', event => {{
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
}});
self.addEventListener('fetch', event => {{
  if (event.request.method !== 'GET') return;
  if (event.request.mode === 'navigate') {{
    event.respondWith(
      fetch(event.request)
        .then(response => {{
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put('./index.html', copy));
          return response;
        }})
        .catch(() => caches.match('./index.html'))
    );
    return;
  }}
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request).then(response => {{
      const copy = response.clone();
      caches.open(CACHE).then(cache => cache.put(event.request, copy));
      return response;
    }}))
  );
}});
"""


def _kpi(label: str, value: str, extra: str = "") -> str:
    suffix = f'<div class="small muted">{_safe(extra)}</div>' if extra else ""
    return (
        f'<div class="card kpi"><div class="label">{_safe(label)}</div>'
        f'<div class="value">{_safe(value)}</div>{suffix}</div>'
    )


def _signal_card(row: dict) -> str:
    payload = _payload(row)
    signal = str(row["signal"])
    badge = _signal_class(signal)
    reasons = payload.get("reasons") or []
    invalidators = payload.get("invalidators") or []
    risk = payload.get("risk") or {}
    size = float(risk.get("suggested_size_pct") or 0.0)
    p_net = float(row["probability_net"])
    expected = float(row["expected_ret_net"])
    quality = str(row["data_quality"])

    detail = "".join(
        [
            f'<div class="row"><span class="muted">Prob. neta</span><b>{_pct(p_net, 1)}</b></div>',
            f'<div class="row"><span class="muted">Ret. neto est.</span>'
            f'<b class="{("pos" if expected > 0 else "neg")}">{_pct(expected)}</b></div>',
            f'<div class="row"><span class="muted">Confianza</span><b>{_safe(row["confidence"])}</b></div>',
            f'<div class="row"><span class="muted">Tamano sugerido</span><b>{size:.2f}%</b></div>',
            f'<div class="row"><span class="muted">Calidad dato</span>'
            f'<b class="{("pos" if quality == "valid" else "warn")}">{_safe(quality)}</b></div>',
        ]
    )
    reasons_html = (
        "<h3>Razones</h3><ul>" + "".join(f"<li>{_safe(r)}</li>" for r in reasons) + "</ul>"
        if reasons
        else ""
    )
    invalidators_html = (
        "<h3>Invalidadores</h3><ul>"
        + "".join(f"<li>{_safe(r)}</li>" for r in invalidators)
        + "</ul>"
        if invalidators
        else ""
    )
    return (
        f'<div class="card"><div class="signal-head"><span class="asset">{_safe(row["asset"])}</span>'
        f'<span class="badge {badge}">{_safe(_signal_label(signal))}</span></div>'
        f'<div class="small muted">Horizonte {_safe(row["horizon"])} · '
        f'dato {_safe(_fmt_ts(row.get("data_timestamp"), "%d %b %H:%M"))}</div>'
        f'<div style="margin-top:8px">{detail}</div>{reasons_html}{invalidators_html}</div>'
    )


def _paper_panel(store: Store) -> str:
    stats = paper_stats(store)
    open_rows = store.open_paper_trades()
    closed_rows = store.paper_trades(status="closed", limit=30)

    kpis = "".join(
        [
            _kpi("Capital", _money(stats.equity), "base 1,0"),
            _kpi("Retorno", f"{stats.total_return_pct:+.2f}%"),
            _kpi("Cerradas", str(stats.trades)),
            _kpi("Abiertas", str(stats.open_trades)),
            _kpi("Acierto", f"{stats.win_rate * 100:.1f}%"),
            _kpi("Max DD", f"{stats.max_drawdown_pct:.2f}%"),
        ]
    )

    if open_rows:
        open_html = (
            '<div class="table-wrap"><table><thead><tr>'
            "<th>Activo</th><th>Entrada</th><th>Precio</th><th>Tamano</th><th>Vence</th>"
            "</tr></thead><tbody>"
            + "".join(
                f"<tr><td>{_safe(r['asset'])}</td>"
                f"<td>{_safe(_fmt_ts(r['opened_at'], '%d %b %H:%M'))}</td>"
                f"<td>{_safe(_money(r['entry_price'], 2))}</td>"
                f"<td>{r['size_pct']:.2f}%</td>"
                f"<td>{_safe(_fmt_ts(r.get('due_at'), '%d %b %H:%M'))}</td></tr>"
                for r in open_rows
            )
            + "</tbody></table></div>"
        )
    else:
        open_html = '<div class="card muted small">Sin posiciones abiertas.</div>'

    if closed_rows:
        closed_html = (
            '<div class="table-wrap"><table><thead><tr>'
            "<th>Activo</th><th>Cerrada</th><th>Entrada</th><th>Salida</th>"
            "<th>Bruto</th><th>Neto</th><th>PnL capital</th></tr></thead><tbody>"
            + "".join(
                f"<tr><td>{_safe(r['asset'])}</td>"
                f"<td>{_safe(_fmt_ts(r.get('closed_at'), '%d %b %H:%M'))}</td>"
                f"<td>{_safe(_money(r['entry_price'], 2))}</td>"
                f"<td>{_safe(_money(r.get('exit_price'), 2))}</td>"
                f"<td>{_pct(r.get('gross_return'))}</td>"
                f"<td class=\"{'pos' if (r.get('net_return') or 0) > 0 else 'neg'}\">"
                f"{_pct(r.get('net_return'))}</td>"
                f"<td>{(r.get('net_return') or 0) * r['size_pct']:+.3f}%</td></tr>"
                for r in closed_rows
            )
            + "</tbody></table></div>"
        )
    else:
        closed_html = '<div class="card muted small">Sin operaciones cerradas todavia.</div>'

    return (
        '<div class="notice">Modo sombra: posiciones simuladas, sin capital real, '
        "sin claves y sin ordenes.</div>"
        f'<div class="kpis" style="margin-top:10px">{kpis}</div>'
        "<h2>Curva de capital</h2>"
        f"{_equity_chart(store)}"
        f"<h2>Posiciones abiertas</h2>{open_html}"
        f"<h2>Operaciones cerradas</h2>{closed_html}"
    )


def _backtest_panel(reports_dir: Path, links: list[dict[str, str]]) -> str:
    payload, filename = _latest_backtest(reports_dir)
    links_html = ""
    if links:
        links_html = '<div class="links">' + "".join(
            f'<a href="{_safe(item["href"])}">{_safe(item["label"])}</a>' for item in links
        ) + "</div>"

    if not payload:
        return (
            '<div class="card muted small">Sin reportes de backtest en el repositorio.</div>'
            f"{links_html}"
        )

    assets = payload.get("assets") or {}
    rows: list[str] = []
    beats_baseline = 0
    profitable = 0
    for asset, data in assets.items():
        metrics = data.get("metrics") or {}
        ll = float(metrics.get("log_loss") or 0.0)
        base = float(metrics.get("log_loss_baseline") or 0.6931)
        net = float(metrics.get("net_return") or 0.0)
        if ll < base:
            beats_baseline += 1
        if net > 0:
            profitable += 1
        rows.append(
            f"<tr><td>{_safe(asset)}</td>"
            f"<td>{ll:.4f}</td><td>{base:.4f}</td>"
            f"<td class=\"{'pos' if metrics.get('ece', 1) < 0.05 else 'neg'}\">"
            f"{float(metrics.get('ece') or 0):.4f}</td>"
            f"<td class=\"{'pos' if net > 0 else 'neg'}\">{_pct(net)}</td>"
            f"<td>{int(float(metrics.get('trades') or 0))}</td>"
            f"<td>{_pct(metrics.get('max_drawdown'), 1, signed=False)}</td></tr>"
        )

    verdict_class = "ok" if beats_baseline == len(assets) and profitable == len(assets) else ""
    verdict = (
        "El backtest no muestra ventaja demostrada: no promocionar a capital real."
        if beats_baseline == 0
        else f"Resultado mixto: {beats_baseline}/{len(assets)} activos superan el baseline."
    )
    summary = (
        f"<b>Ventanas:</b> train {int(payload.get('windows', {}).get('train', 0))} d, "
        f"test {int(payload.get('windows', {}).get('test', 0))} d · "
        f"<b>Costo:</b> {float(payload.get('cost_rate', 0)) * 100:.2f}% ida y vuelta · "
        f"<b>Umbral:</b> {float(payload.get('threshold', 0)):.2f}"
    )

    return (
        f'<div class="notice {verdict_class}">{_safe(verdict)}</div>'
        f'<div class="card small muted" style="margin-top:10px">{summary}</div>'
        '<div class="table-wrap" style="margin-top:10px"><table><thead><tr>'
        "<th>Activo</th><th>Log loss</th><th>Baseline</th><th>ECE</th>"
        "<th>Retorno neto</th><th>Trades</th><th>Max DD</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        f'<div class="small muted" style="margin-top:8px">Archivo: {_safe(filename or "")}</div>'
        f"{links_html}"
    )


def _market_panel(store: Store, signals: list[dict], now: datetime) -> str:
    quality = {row["asset"]: row["data_quality"] for row in signals}
    parts: list[str] = []
    for asset in sorted(quality):
        candles = store.load_candles(asset, limit=365, source="binance")
        if not candles:
            candles = store.load_candles(asset, limit=365)
        last = candles[-1] if candles else None
        age = ""
        if last:
            hours = (now - last.ts).total_seconds() / 3600
            age = f"ultima vela {last.ts:%d %b %H:%M} · {hours:.1f} h"
        parts.append(
            f'<h2>{_safe(asset)} '
            f'<span class="badge {("long" if quality.get(asset) == "valid" else "warn")}">'
            f'{_safe(quality.get(asset, "sin dato"))}</span></h2>'
            f'<div class="small muted" style="margin-bottom:6px">{_safe(age)}</div>'
            f"{_market_chart(candles, asset)}"
        )
    return "".join(parts) or '<div class="card muted">Sin datos de mercado.</div>'


def _risk_panel() -> str:
    limits = RiskLimits()
    rows = [
        ("Riesgo por operacion", f"{limits.max_risk_per_trade_pct:.2f}%"),
        ("Exposicion por activo", f"{limits.max_exposure_per_asset_pct:.2f}%"),
        ("Perdida diaria maxima", f"{limits.max_daily_loss_pct:.2f}%"),
        ("Perdida semanal maxima", f"{limits.max_weekly_loss_pct:.2f}%"),
        ("Drawdown maximo", f"{limits.max_drawdown_pct:.2f}%"),
        ("Operaciones por dia", str(limits.max_trades_per_day)),
        ("Correlacion maxima", f"{limits.max_correlation:.2f}"),
        ("Antiguedad maxima del dato", f"{limits.max_data_age_minutes} min"),
    ]
    body = "".join(
        f'<div class="row"><span class="muted">{_safe(label)}</span><b>{_safe(value)}</b></div>'
        for label, value in rows
    )
    return (
        '<div class="card">' + body + "</div>"
        '<div class="notice" style="margin-top:12px">Los limites los aplica el codigo antes de '
        "publicar una senal. Ningun LLM ni agente puede modificarlos. El kill switch es manual.</div>"
    )


def _clean_detail(value: object) -> str:
    text = str(value)
    if "\\" in text or "/" in text:
        text = text.replace("\\", "/").rsplit("/", 1)[-1]
    return text[:120]


def _audit_panel(store: Store) -> str:
    try:
        events = store.recent_audit(15)
    except Exception:  # noqa: BLE001 - base sin esquema
        events = []
    if not events:
        return '<div class="card muted small">Sin eventos de auditoria.</div>'
    rows = "".join(
        f"<tr><td>{_safe(_fmt_ts(row['at'], '%d %b %H:%M'))}</td>"
        f"<td>{_safe(row['actor'])}</td><td>{_safe(row['action'])}</td>"
        f"<td>{_safe(_clean_detail(row['detail']))}</td></tr>"
        for row in events
    )
    return (
        '<div class="table-wrap"><table><thead><tr><th>Fecha</th><th>Actor</th>'
        "<th>Accion</th><th>Detalle</th></tr></thead><tbody>"
        + rows
        + "</tbody></table></div>"
    )


def _explanation_panel(store: Store) -> str:
    try:
        record = store.latest_explanation()
    except Exception:  # noqa: BLE001 - base sin esquema
        record = None
    if not record:
        return (
            '<div class="card muted small">Todavia no hay explicaciones guardadas. '
            "Se generan al final de cada corrida diaria.</div>"
        )
    try:
        body = json.loads(record["body"])
    except (TypeError, json.JSONDecodeError):
        body = {}
    kind = "LLM" if record["kind"] == "llm" else "determinista"
    badge = "llm" if record["kind"] == "llm" else "warn"
    opportunities = body.get("top_opportunities") or []
    notes = body.get("no_trade_notes") or []
    opp_html = "".join(
        f"<li><b>{_safe(item.get('asset', '?'))}</b> ({_safe(item.get('signal', '?'))}): "
        f"{_safe(item.get('why', ''))}</li>"
        for item in opportunities
    )
    notes_html = "".join(f"<li>{_safe(note)}</li>" for note in notes)
    return (
        f'<div class="small muted">Explicacion <span class="badge {badge}">{_safe(kind)}</span> · '
        f'{_safe(record["model"])} · prompt {_safe(record["prompt_version"])} · '
        f'{_safe(_fmt_ts(record["generated_at"], "%d %b %H:%M"))}</div>'
        f'<h2>{_safe(body.get("subject", "Explicacion"))}</h2>'
        f'<p>{_safe(body.get("executive_summary", ""))}</p>'
        + (f"<h3>Oportunidades</h3><ul>{opp_html}</ul>" if opp_html else "")
        + (f"<h3>Por que no se opera</h3><ul>{notes_html}</ul>" if notes_html else "")
        + f'<div class="card small muted">{_safe(body.get("data_health_note", ""))}</div>'
        + f'<p class="small muted">{_safe(body.get("disclaimer", ""))}</p>'
    )


def export_static(*, store: Store, out_dir: Path, reports_dir: Path, now: datetime | None = None) -> Path:
    stamp = now or datetime.now(UTC)
    store.init_schema()
    signals = _latest_by_asset(store)
    actionable = [row for row in signals if row["is_actionable"]]
    stats = paper_stats(store)
    explanation = _explanation_panel(store)
    links = _copy_reports(reports_dir, out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "reports").mkdir(parents=True, exist_ok=True)

    latest_data = max(
        (_ts(row.get("data_timestamp")) for row in signals if row.get("data_timestamp")),
        default=None,
    )
    latest_signal = max(
        (_ts(row.get("timestamp")) for row in signals if row.get("timestamp")),
        default=None,
    )
    updated_at = latest_signal or stamp
    data_age = ""
    stale = False
    if latest_data:
        hours = (stamp - latest_data).total_seconds() / 3600
        data_age = f"Dato de {latest_data:%d %b %H:%M} UTC ({hours:.1f} h)"
        stale = hours > 36

    status = (
        f"{len(actionable)} senal(es) accionable(s)"
        if actionable
        else "Sin operacion: ninguna senal supera el umbral"
    )
    status_class = "ok" if actionable else ""

    kpis = "".join(
        [
            _kpi("Activos", str(len(signals))),
            _kpi("Accionables", str(len(actionable))),
            _kpi("Paper equity", _money(stats.equity)),
            _kpi("Actualizado", f"{updated_at:%d %b %H:%M}", "UTC"),
        ]
    )

    signals_html = (
        '<div class="signal-grid">' + "".join(_signal_card(row) for row in signals) + "</div>"
        if signals
        else '<div class="card muted">Todavia no hay senales guardadas.</div>'
    )

    report_links = ""
    if links:
        report_links = (
            "<h2>Reportes completos</h2><div class=\"links\">"
            + "".join(
                f'<a href="{_safe(item["href"])}">{_safe(item["label"])}</a>' for item in links
            )
            + "</div>"
        )

    body = f"""
<div class="wrap">
  <header>
    <div>
      <h1>Trading AI</h1>
      <div class="small muted">Panel personal · solo lectura · sin ejecucion real</div>
    </div>
    <div class="small muted">{_safe(data_age)}</div>
  </header>

  <nav class="tabs" aria-label="Secciones">
    <button class="tab-btn active" data-tab="hoy">Hoy</button>
    <button class="tab-btn" data-tab="explicacion">Explicacion</button>
    <button class="tab-btn" data-tab="paper">Paper</button>
    <button class="tab-btn" data-tab="backtest">Backtest</button>
    <button class="tab-btn" data-tab="mercado">Mercado</button>
    <button class="tab-btn" data-tab="riesgo">Riesgo</button>
    <button class="tab-btn" data-tab="auditoria">Auditoria</button>
  </nav>

  <main>
    <section id="hoy" class="tab-panel active">
      <div class="notice {status_class}">{_safe(status)}</div>
      {'<div class="notice" style="margin-top:8px">El ultimo dato tiene mas de 36 h. Revisar la corrida diaria antes de leerlo.</div>' if stale else ''}
      <div class="kpis" style="margin-top:10px">{kpis}</div>
      <div class="small muted" style="margin-top:10px">Paper en sombra y sin ventaja demostrada en el backtest. Esto es apoyo a decisiones, no una recomendacion de compra o venta.</div>
      <h2>Senales de hoy</h2>
      {signals_html}
      {report_links}
    </section>

    <section id="explicacion" class="tab-panel">{explanation}</section>
    <section id="paper" class="tab-panel">{_paper_panel(store)}</section>
    <section id="backtest" class="tab-panel">{_backtest_panel(reports_dir, links)}</section>
    <section id="mercado" class="tab-panel">{_market_panel(store, signals, stamp)}</section>
    <section id="riesgo" class="tab-panel">{_risk_panel()}</section>
    <section id="auditoria" class="tab-panel">{_audit_panel(store)}</section>
  </main>

  <footer>
    Sitio generado {_safe(stamp.strftime("%d %b %Y %H:%M UTC"))} desde SQLite.
    No es asesoria financiera. Sin claves, sin ordenes, sin capital real.
    <br>En el celular: menu del navegador → "Agregar a la pantalla de inicio".
  </footer>
</div>
"""

    html_doc = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Trading AI — panel movil</title>
<meta name="theme-color" content="#0b0f14">
<meta name="description" content="Panel personal de senales, paper trading y backtests. Solo lectura.">
<link rel="manifest" href="./manifest.webmanifest">
<link rel="apple-touch-icon" href="./icon-180.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Trading AI">
<style>{CSS}</style>
</head>
<body>
{body}
<script>{JS}</script>
</body>
</html>
"""

    (out_dir / "index.html").write_text(html_doc, encoding="utf-8")
    (out_dir / "manifest.webmanifest").write_text(_manifest(), encoding="utf-8")
    (out_dir / "sw.js").write_text(
        _service_worker(stamp.strftime("%Y%m%d%H%M")), encoding="utf-8"
    )
    (out_dir / "icon-192.png").write_bytes(_icon_png(192))
    (out_dir / "icon-512.png").write_bytes(_icon_png(512))
    (out_dir / "icon-180.png").write_bytes(_icon_png(180))
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return out_dir / "index.html"


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta el panel a HTML estatico")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--reports", type=Path, default=ROOT / "var" / "reports")
    args = parser.parse_args()

    settings = Settings.from_env()
    path = export_static(
        store=Store(args.db or settings.db_path),
        out_dir=args.out,
        reports_dir=args.reports,
    )
    print(f"Panel estatico: {path}")


if __name__ == "__main__":
    main()
