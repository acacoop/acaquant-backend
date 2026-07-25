"""drop_tablas_telegram.py — one-shot: borra las tablas huérfanas del decomiso Telegram.

Al eliminar jobs/watchdog.py y jobs/informe_salud.py (2026-07-25, su única
salida era Telegram y nada de la API las leía) quedaron huérfanas:

    manager.watchdog_alertas   (cooldown de alertas del watchdog)
    manager.health_reports     (snapshots del informe de salud)

Este script las dropea. Idempotente (IF EXISTS): re-correrlo no rompe nada.

Uso (Droplet):
    python -m scripts.drop_tablas_telegram          # muestra qué hay y pide confirmar
    python -m scripts.drop_tablas_telegram --apply  # dropea
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

TABLAS = ("manager.watchdog_alertas", "manager.health_reports")


def main() -> int:
    from core.postgres import get_pool

    apply = "--apply" in sys.argv
    with get_pool().connection() as conn, conn.cursor() as cur:
        for t in TABLAS:
            schema, nombre = t.split(".")
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s)", (schema, nombre))
            existe = cur.fetchone()[0]
            if not existe:
                print(f"· {t}: no existe (nada que hacer)")
                continue
            cur.execute(f"SELECT count(*) FROM {t}")
            n = cur.fetchone()[0]
            if apply:
                cur.execute(f"DROP TABLE IF EXISTS {t}")
                print(f"· {t}: {n} filas → DROPPED")
            else:
                print(f"· {t}: {n} filas → se borraría (correr con --apply)")
        if apply:
            conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
