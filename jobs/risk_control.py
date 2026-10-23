"""Activa o desactiva el kill switch persistente (sin LLM y sin red).

Uso:
    python -m jobs.risk_control status
    python -m jobs.risk_control on --reason "drawdown manual"
    python -m jobs.risk_control off --reason "revisado"
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from src.config.settings import Settings
from src.data.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Kill switch del paper trading")
    parser.add_argument("action", choices=["on", "off", "status"])
    parser.add_argument("--reason", default="", help="motivo para audit_events")
    args = parser.parse_args()

    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init_schema()

    if args.action == "status":
        active = store.control_flag("kill_switch")
        print(f"kill_switch={'on' if active else 'off'} db={settings.db_path}")
        return

    enabled = args.action == "on"
    store.set_control_flag("kill_switch", enabled)
    reason = args.reason.strip() or "sin motivo indicado"
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    store.audit("operator", "kill_switch", f"enabled={enabled} at={stamp} reason={reason}")
    print(f"kill_switch={'on' if enabled else 'off'} db={settings.db_path}")


if __name__ == "__main__":
    main()
