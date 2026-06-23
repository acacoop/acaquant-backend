"""scripts/estado_sql.py — qué dominio LEE de SQL vs Mongo (según los flags del .env).

Lee los flags del .env del Droplet y muestra, por dominio, si está sirviendo desde Postgres
(SQL) o todavía de Mongo. Default de cada flag = Mongo. Las ESCRITURAS siguen casi todas en
Mongo hasta la Fase 2 (motores/jobs) — esto refleja las LECTURAS.

    python -m scripts.estado_sql
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv("/root/TradingAV/.env")
load_dotenv()  # fallback (.env local)

_FLAGS = [
    ("OPERACIONES_SQL", "OPERACIONES — /ops/* (movimientos, aranceles, agro)"),
    ("MOVIMIENTOS_SQL", "FLUJOS — /api/operaciones/flujos (CashFlow.Movimientos)"),
    ("ACREENCIAS_SQL",  "ACREENCIAS — /back-office/acreencias/* + /comercial/cobros-futuros"),
    ("VOLUMEN_AGRO_SQL", "SHARE AGRO — denominador de /ops/agro (CashFlow.VolumenMercadoAgro)"),
    ("COMERCIAL_SQL",   "COMERCIAL — /comercial/* (operadores, informes)"),
    ("PORTFOLIO_SQL",   "PORTFOLIO AuM — /aum, /fci-*, /total-*, /diff"),
    ("VALUACIONES_SQL", "VALUACIONES — /valuaciones (cierre, serie, mensual TEA/TWR, posiciones)"),
    ("CONTRAPARTES_SQL", "CONTRAPARTES — /manager/contrapartes (lecturas)"),
    ("PNL_SQL",         "PnL Títulos — /pnl"),
    ("NEWS_SQL",        "HOME/NEWS — /api/news/* (headlines, stats)"),
    ("MARKET_SQL",      "MARKET — /api/market/quotes + /calendar/economic"),
    ("REM_SQL",         "REM — /api/cotizaciones/rem* (expectativas IPC, breakeven acum)"),
    ("MACRO_SQL",       "MACRO — /cotizaciones/{badlar,cer,dolar} + /analitica/serie-macro (7 series)"),
    ("MERCADO_HIST_SQL", "HISTÓRICOS — /cotizaciones/historico/{breakevens,forwards,futuros-dlr,caucion}"),
    ("RENTA_FIJA_SQL",  "RENTA FIJA LIVE — /cotizaciones/renta-fija + snapshot-live + /analitica/listar-curva + historico/curva"),
    ("AGRO_SQL",        "AGRO — /derivados/agro/* (pase, opciones, simulador, cámara, mejoras-dispo)"),
    ("OPCIONES_SQL",    "OPCIONES — /derivados charts (chain, meta, histórico, VR-GGal, griegas, estrategia)"),
    ("SCANNER_SQL",     "RENTA VARIABLE — Scanner CEDEARs (/scanner/*: cedears, ADR, precios, day-trading)"),
    ("AUTH_SQL",        "AUTH (lecturas) — roles, matriz, scope de cuentas"),
    ("MANAGER_SQL",     "MANAGER infra (lecturas) — /jobs/history(/stats), /roles/audit, frescura jobs"),
    ("SNAPSHOT_SQL",    "MOTORES → market_snapshot live (dual-write a SQL)"),
    ("MERCADO_SQL_WRITE", "JOBS mercado → dual-write (series_macro, rem, cierres)"),
    ("MANAGER_SQL_WRITE", "MANAGER infra → dual-write (JobRuns→job_runs, RoleAudit→role_audit)"),
]


def main() -> int:
    print("Estado de lectura por dominio (flags del .env):\n")
    for env, desc in _FLAGS:
        on = os.getenv(env) == "1"
        marca = "🟢 SQL  " if on else "⚪ MONGO"
        print(f"  {marca}  {env:16} → {desc}")
    print("\n⚪ MONGO = sigue leyendo Mongo (default). 🟢 SQL = lee Postgres (con fallback a Mongo en auth).")
    print("Nota: las ESCRITURAS (motores, jobs, edición de roles/grupos/segmentación) siguen")
    print("en Mongo hasta la Fase 2. Esto refleja de dónde se LEE cada vista.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
