"""Dropea las 7 tablas que quedaron del copiloto y del asistente de negocio.

QUÉ SON Y POR QUÉ SE VAN
========================

El copiloto se dio de baja el 2026-08-19 (`docs/AV_AGENT.md` §0.k) y su código se
borró entero. Las TABLAS se dejaron a propósito, con un criterio correcto:
*borrar código es reversible con un `git revert`, borrar datos no*. Pasaron nueve
días, nadie las extrañó, y medido el 2026-08-28 **ninguna tiene una sola
referencia en el código** — ni un writer, ni un lector, ni una FK apuntándolas.
Existen solamente en su `CREATE TABLE` de `sql/schema.sql`, que este mismo cambio
saca.

    ia.triage_incidentes        el triage que corría cada 10' contra una tabla
    ia.triage_estado            que nadie abrió nunca (jobs/triage.py, borrado)
    ia.calidad_flags            el crítico de calidad de conversaciones del
    ia.calidad_estado           copiloto (jobs/ia_calidad.py, borrado)
    manager.asistente_mappings  la aduana PII (core/pii_gateway.py, borrado)
    manager.asistente_chats     el transcript del asistente de negocio
    manager.salud_diagnosticos  el cache del diagnóstico con IA del panel SALUD,
                                que salió del front el 2026-08-19

De yapa le saca ruido al AV AGENT: `agente/tablas.py::inventario()` **barre
pg_class**, o sea todas las tablas de la base, no una lista. Siete tablas sin
escritor caen justo en la pared `tabla_quieta · sin_escribir` que `core/escribe.py`
documenta como la más cara.

POR QUÉ ESTO ES UN SCRIPT Y NO UNA LÍNEA EN schema.sql
======================================================

`scripts/apply_schema.py` es NO destructivo por diseño: no tiene un solo DROP.
Sacar el `CREATE TABLE` del archivo hace que no se vuelva a crear, pero **no
borra nada en una base que ya la tiene**. Hace falta esto, corrido a mano.

LA RED DE SEGURIDAD (REGLA #4)
==============================

- **`--dry` es el default.** Sin `--aplicar` no toca nada: cuenta filas y peso,
  y dice exactamente qué haría. Correlo primero SIEMPRE.
- **Backup antes de cada DROP**, salvo `--sin-backup`. Cada tabla se vuelca a
  `backups/ia_huerfanas_<fecha>/<schema>.<tabla>.csv` con su header. Si una
  guardaba algo que importaba, está ahí — y si el backup falla, esa tabla NO se
  dropea.
- **Idempotente**: `DROP TABLE IF EXISTS`. Correrlo dos veces no rompe nada; la
  segunda vez informa que ya no están.
- **Se niega a dropear una tabla que no está en la lista.** La lista es fija y
  vive acá: el script no acepta un nombre por parámetro. Un script de drop con
  el nombre de tabla parametrizable es un accidente esperando la línea de
  comando equivocada.
- **No usa CASCADE.** Si aparece una dependencia que no vimos, el DROP falla y
  el script lo informa en vez de arrastrarse lo que cuelgue.
- Liviano y fuera de rueda igual: son tablas chicas y muertas, pero un DROP
  toma un lock.

Uso (Droplet, raíz):
    python -m scripts.drop_tablas_ia_huerfanas              # DRY-RUN: solo informa
    python -m scripts.drop_tablas_ia_huerfanas --aplicar    # backup + DROP
    python -m scripts.drop_tablas_ia_huerfanas --aplicar --sin-backup
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

from core.postgres import connect

# La lista es FIJA y vive acá. No se parametriza por línea de comando.
TABLAS: tuple[tuple[str, str, str], ...] = (
    ("ia", "triage_incidentes", "triage de incidentes (jobs/triage.py, borrado 2026-08-19)"),
    ("ia", "triage_estado", "watermark del triage"),
    ("ia", "calidad_flags", "crítico de calidad del copiloto (jobs/ia_calidad.py, borrado)"),
    ("ia", "calidad_estado", "watermark del crítico de calidad"),
    ("manager", "asistente_mappings", "aduana PII (core/pii_gateway.py, borrado)"),
    ("manager", "asistente_chats", "transcript del asistente de negocio"),
    ("manager", "salud_diagnosticos", "cache del diagnóstico con IA del panel SALUD"),
)

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"


def _existe(cur, schema: str, tabla: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        (schema, tabla),
    )
    return cur.fetchone() is not None


def _peso(cur, schema: str, tabla: str) -> tuple[int, str]:
    """(filas reales, tamaño legible). El COUNT es exacto a propósito: son tablas
    muertas y chicas, y acá el número es lo que decide si el backup importa."""
    cur.execute(f'SELECT count(*) FROM "{schema}"."{tabla}"')
    filas = int(cur.fetchone()[0])
    cur.execute("SELECT pg_size_pretty(pg_total_relation_size(%s))", (f"{schema}.{tabla}",))
    return filas, cur.fetchone()[0]


def _backup(cur, schema: str, tabla: str, destino: Path) -> int:
    """Vuelca la tabla entera a CSV con header. Devuelve las filas escritas."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    cur.execute(f'SELECT * FROM "{schema}"."{tabla}"')
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
                    help="NO vuelca a CSV antes de dropear. Solo si ya sabés que están vacías.")
    args = ap.parse_args()

    sello = dt.datetime.now().strftime("%Y%m%d_%H%M")
    carpeta = BACKUP_DIR / f"ia_huerfanas_{sello}"
    modo = "APLICAR" if args.aplicar else "DRY-RUN (no toca nada)"
    print(f"drop_tablas_ia_huerfanas — modo {modo}\n")

    total_filas = 0
    dropeadas = ausentes = fallidas = 0

    with connect() as conn:
        for schema, tabla, para_que in TABLAS:
            nombre = f"{schema}.{tabla}"
            with conn.cursor() as cur:
                if not _existe(cur, schema, tabla):
                    print(f"  · {nombre:<32} ya no está")
                    ausentes += 1
                    continue
                filas, tam = _peso(cur, schema, tabla)
            total_filas += filas
            print(f"  · {nombre:<32} {filas:>9,} filas · {tam:>9}  — {para_que}".replace(",", "."))

            if not args.aplicar:
                continue

            if not args.sin_backup:
                destino = carpeta / f"{nombre}.csv"
                try:
                    with conn.cursor() as cur:
                        n = _backup(cur, schema, tabla, destino)
                    print(f"      backup → {destino} ({n} filas)".replace(",", "."))
                except Exception as e:
                    # sin backup NO se dropea: el dato es lo único irreversible
                    print(f"      ✗ backup FALLÓ ({type(e).__name__}: {e}) — NO se dropea")
                    fallidas += 1
                    continue

            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP TABLE IF EXISTS "{schema}"."{tabla}"')
                conn.commit()
                print("      ✓ dropeada")
                dropeadas += 1
            except Exception as e:
                conn.rollback()
                print(f"      ✗ DROP falló ({type(e).__name__}: {e}) — queda como está")
                fallidas += 1

    print()
    if not args.aplicar:
        print(f"DRY-RUN: {len(TABLAS) - ausentes} tablas presentes, "
              f"{total_filas:,} filas en total. Nada se tocó.".replace(",", "."))
        print("Para hacerlo de verdad: python -m scripts.drop_tablas_ia_huerfanas --aplicar")
        return

    print(f"Listo: {dropeadas} dropeadas · {ausentes} ya no estaban · {fallidas} fallaron")
    if not args.sin_backup and dropeadas:
        print(f"Backups en {carpeta}")
    if fallidas:
        sys.exit(1)


if __name__ == "__main__":
    main()
