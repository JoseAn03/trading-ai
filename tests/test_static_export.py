from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from jobs.export_static import _risk_panel, export_static
from src.data.store import Store
from src.schemas.enums import Signal
from tests.conftest import make_signal

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def test_export_static_contains_latest_signal_and_pwa_assets(tmp_path):
    store = Store(tmp_path / "trading.db")
    store.init_schema()
    store.save_signal(make_signal(asset="BTC", ts=NOW))
    store.save_signal(
        make_signal(
            asset="ETH",
            signal=Signal.NO_TRADE,
            actionable=False,
            p_net=0.48,
            size=0.0,
            ts=NOW,
        )
    )
    store.audit("test", "report_generated", r"C:\Users\Andres\trading-ai\var\reports\reporte.md")

    out = tmp_path / "dist"
    reports = tmp_path / "reports"
    reports.mkdir()
    index = export_static(store=store, out_dir=out, reports_dir=reports, now=NOW)

    html = index.read_text(encoding="utf-8")
    assert "BTC" in html
    assert "ETH" in html
    assert "LONG" in html
    assert "NO TRADE" in html
    assert "history.replaceState" not in html
    assert "C:\\Users" not in html
    assert "reporte.md" in html
    assert "actions/workflows/daily.yml" in html
    assert "Run workflow" in html
    assert (out / "manifest.webmanifest").exists()
    assert (out / "sw.js").read_text(encoding="utf-8").startswith("\nconst CACHE")
    assert (out / "icon-192.png").read_bytes().startswith(b"\x89PNG")
    assert (out / ".nojekyll").exists()


def test_export_static_handles_empty_database(tmp_path):
    store = Store(tmp_path / "empty.db")
    store.init_schema()

    out = tmp_path / "dist"
    index = export_static(
        store=store,
        out_dir=out,
        reports_dir=tmp_path / "missing-reports",
        now=NOW,
    )

    html = index.read_text(encoding="utf-8")
    assert "Todavia no hay senales guardadas" in html
    assert "Sin operaciones cerradas" in html
    assert (out / "reports").is_dir()


def test_export_static_uses_signal_time_for_updated_kpi(tmp_path):
    store = Store(tmp_path / "trading.db")
    store.init_schema()
    store.save_signal(make_signal(asset="BTC", ts=NOW - timedelta(hours=3)))

    out = tmp_path / "dist"
    index = export_static(
        store=store,
        out_dir=out,
        reports_dir=tmp_path / "reports",
        now=NOW,
    )

    html = index.read_text(encoding="utf-8")
    assert '<div class="label">Actualizado</div><div class="value">22 Oct 09:00</div>' in html
    assert "Sitio generado 22 Oct 2026 12:00 UTC" in html


def test_export_static_copies_latest_report(tmp_path):
    store = Store(tmp_path / "trading.db")
    store.init_schema()
    reports = tmp_path / "reports"
    reports.mkdir()
    older = reports / "reporte-2026-10-21.md"
    newer = reports / "reporte-2026-10-22.md"
    older.write_text("viejo", encoding="utf-8")
    newer.write_text("nuevo", encoding="utf-8")
    # El archivo con fecha anterior tiene mtime mas nuevo: debe ganar el nombre.
    stamp = (NOW + timedelta(days=1)).timestamp()
    os.utime(older, (stamp, stamp))

    out = tmp_path / "dist"
    index = export_static(store=store, out_dir=out, reports_dir=reports, now=NOW)

    assert (out / "reports" / "reporte-2026-10-22.md").read_text(encoding="utf-8") == "nuevo"
    assert not (out / "reports" / "reporte-2026-10-21.md").exists()
    assert 'href="reports/reporte-2026-10-22.md"' in index.read_text(encoding="utf-8")



def test_risk_panel_shows_effective_data_age_tolerance() -> None:
    html = _risk_panel()
    assert '2160 min (36.0 h)' in html

