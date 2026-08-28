"""Dropea el schema `mcp` — el estado del provider OAuth del MCP server.

QUÉ BORRA Y POR QUÉ
===================

    mcp.oauth_clients   registros DCR (RFC 7591) — los clientes que se dieron de alta
    mcp.oauth_codes     authorization codes (single-use, TTL 10 min)
    mcp.oauth_tokens    access tokens vivos (TTL 1h; borrar la fila = revocar el JWT)

El MCP server se apagó el 2026-08-28 (env vars fuera del `.env`, verificado con
`/mcp` → 404) y se borró entero ese mismo día: ninguna vista, job ni motor lo
consumía. Sin `api/mcp/oauth.py` no queda nadie que lea ni escriba estas tres
tablas — su único writer era ese archivo.

⚠️ **Dropear esto REVOCA todo**: si algún cliente todavía tuviera un token vivo,
deja de servir. Es lo que se quiere (el servidor ya no existe), pero conviene
saberlo antes que después.

POR QUÉ ES UN SCRIPT Y NO UNA LÍNEA EN schema.sql
=================================================

`scripts/apply_schema.py` es NO destructivo por diseño: no tiene un solo DROP.
Sacar los `CREATE TABLE` del archivo hace que no se vuelvan a crear, pero en una
base que ya los tiene siguen ocupando lugar.

LA RED DE SEGURIDAD (REGLA #4)
==============================

- **`--dry` es el default.** Sin `--aplicar` no toca nada.
- **Backup a CSV antes de cada DROP** (salvo `--sin-backup`), en
  `backups/schema_mcp_<fecha>/`. Si el backup falla, esa tabla NO se dropea.
- **Idempotente** (`DROP TABLE IF EXISTS`), y el `DROP SCHEMA` va **sin CASCADE**:
  si quedara algo adentro que no vimos, falla y lo dice en vez de arrastrarlo.
- **La lista es fija y vive acá.** No se pasa una tabla por parámetro.

Uso (Droplet, raíz):
    python -m scripts.drop_schema_mcp             # DRY-RUN: solo informa
    python -m scripts.drop_schema_mcp --aplicar   # backup + DROP
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

from core.postgres import connect

SCHEMA = "mcp"
TABLAS: tuple[tuple[str, str], ...] = (
    ("oauth_tokens", "access tokens vivos (TTL 1h)"),
    ("oauth_codes", "authorization codes single-use (TTL 10 min)"),
    ("oauth_clients", "registros DCR (RFC 7591)"),
)

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"


def _existe(cur, tabla: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        (SCHEMA, tabla),
    )
    return cur.fetchone() is not None


def _peso(cur, tabla: str) -> tuple[int, str]:
    cur.execute(f'SELECT count(*) FROM "{SCHEMA}"."{tabla}"')
    filas = int(cur.fetchone()[0])
    cur.execute("SELECT pg_size_pretty(pg_total_relation_size(%s))", (f"{SCHEMA}.{tabla}",))
    return filas, cur.fetchone()[0]


def _backup(cur, tabla: str, destino: Path) -> int:
    destino.parent.mkdir(parents=True, exist_ok=True)
    cur.execute(f'SELECT * FROM "{SCHEMA}"."{tabla}"')
    columnas = [d.name for d in cur.description]
    n = 0
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columnas)
        for fila in cur:
            w.writerow(fila)
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true",
                    help="hace el backup y el DROP. Sin esto es un dry-run que no toca nada.")
    ap.add_argument("--sin-backup", action="store_true",
                    help="NO vuelca a CSV antes de dropear.")
    args = ap.parse_args()

    carpeta = BACKUP_DIR / f"schema_mcp_{dt.datetime.now():%Y%m%d_%H%M}"
    print(f"drop_schema_mcp — modo {'APLICAR' if args.aplicar else 'DRY-RUN (no toca nada)'}\n")

    total = dropeadas = ausentes = fallidas = 0
    with connect() as conn:
        for tabla, para_que in TABLAS:
            nombre = f"{SCHEMA}.{tabla}"
            with conn.cursor() as cur:
                if not _existe(cur, tabla):
                    print(f"  · {nombre:<22} ya no está")
                    ausentes += 1
                    continue
                filas, tam = _peso(cur, tabla)
            total += filas
            print(f"  · {nombre:<22} {filas:>7} filas · {tam:>9}  — {para_que}")
            if not args.aplicar:
                continue

            if not args.sin_backup:
                destino = carpeta / f"{nombre}.csv"
                try:
                    with conn.cursor() as cur:
                        n = _backup(cur, tabla, destino)
                    print(f"      backup → {destino} ({n} filas)")
                except Exception as e:
                    print(f"      ✗ backup FALLÓ ({type(e).__name__}: {e}) — NO se dropea")
                    fallidas += 1
                    continue
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP TABLE IF EXISTS "{SCHEMA}"."{tabla}"')
                conn.commit()
                print("      ✓ dropeada")
                dropeadas += 1
            except Exception as e:
                conn.rollback()
                print(f"      ✗ DROP falló ({type(e).__name__}: {e})")
                fallidas += 1

        if args.aplicar and not fallidas:
            # Sin CASCADE a propósito: si algo quedó adentro, que falle y se vea.
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}"')
                conn.commit()
                print(f"\n  ✓ schema {SCHEMA} dropeado")
            except Exception as e:
                conn.rollback()
                print(f"\n  ✗ el schema {SCHEMA} NO se pudo dropear ({type(e).__name__}: {e})")
                print("    Quedó algo adentro que este script no conoce — miralo antes de forzar.")
                fallidas += 1

    print()
    if not args.aplicar:
        print(f"DRY-RUN: {len(TABLAS) - ausentes} tablas presentes, {total} filas. Nada se tocó.")
        print("Para hacerlo de verdad: python -m scripts.drop_schema_mcp --aplicar")
        return
    print(f"Listo: {dropeadas} dropeadas · {ausentes} ya no estaban · {fallidas} fallaron")
    if not args.sin_backup and dropeadas:
        print(f"Backups en {carpeta}")
    if fallidas:
        sys.exit(1)


if __name__ == "__main__":
    main()
