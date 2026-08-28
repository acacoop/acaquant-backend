"""Dropea lo que quedó del GATEWAY DE IA: dos tablas y dos columnas.

QUÉ BORRA Y POR QUÉ
===================

    ia.trazas               una fila por CADA llamada al modelo (el "job_runs"
                            de la IA). Writer: core/ai.py, borrado.
    ia.config               los topes diarios de tokens (global y por usuario).
    ia.research.destilado         \\ las dos columnas del destilado del research:
    ia.research.destilado_modelo  / jsonb {resumen, temas, hechos} + qué modelo.

El sistema dejó de tener IA el 2026-08-28. La última tarea que quedaba
(`research_destilar`, el resumen del mail diario de 1816) se dio de baja por
decisión del user: *«ese destilado no tiene sentido, no se usa en absoluto; el
research se guarda y se muestra así nomás»*. Y era exacto por partida doble — el
flag que lo activaba (`--destilar`) nunca estuvo en el cron, así que **nunca
corrió**, y **ninguna pantalla lo dibujaba**: el campo viajaba en el payload de
`/research1816/mails` y el front lo tiraba. Con esa tarea se fueron `core/ai.py`
y `core/llm.py`, que existían para servirla, y estas dos tablas con ellos.

⚠️ **`ia.research` NO se toca** — el mail CRUDO es la vista Research y sigue
siendo la fuente de verdad. Acá solo se le sacan las dos columnas del destilado,
que están **enteramente en NULL** (nunca se escribieron). El script lo VERIFICA
antes de dropearlas: si alguna tiene datos, NO borra y te lo dice.

POR QUÉ ES UN SCRIPT Y NO UNA LÍNEA EN schema.sql
=================================================

`scripts/apply_schema.py` es NO destructivo por diseño: no tiene un solo DROP.
Sacar las tablas del archivo hace que no se vuelvan a crear, pero en una base que
ya las tiene siguen ocupando lugar.

LA RED DE SEGURIDAD (REGLA #4)
==============================

- **`--dry` es el default.** Sin `--aplicar` no toca nada: cuenta filas y peso.
- **Backup a CSV antes de cada DROP** (salvo `--sin-backup`), en
  `backups/tablas_ia_<fecha>/`. Si el backup falla, esa tabla NO se dropea.
  `ia.trazas` puede tener bastante historia — mirá el dry-run antes.
- **Las columnas del destilado NO se backupean pero SÍ se verifican**: se cuenta
  cuántas filas tienen algo distinto de NULL, y con una sola el script se planta.
- **Idempotente** (`DROP ... IF EXISTS`), sin CASCADE.
- **La lista es fija y vive acá.** No se pasa nada por parámetro.

Uso (Droplet, raíz):
    python -m scripts.drop_tablas_ia                            # DRY-RUN: solo informa
    python -m scripts.drop_tablas_ia --aplicar                  # backup + DROP
    python -m scripts.drop_tablas_ia --aplicar --forzar-columnas  # + las columnas con datos
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

from core.postgres import connect

TABLAS: tuple[tuple[str, str, str], ...] = (
    ("ia", "trazas", "una fila por llamada al modelo (writer: core/ai.py, borrado)"),
    ("ia", "config", "topes diarios de tokens del gateway"),
)
# (schema, tabla, columna) — se dropean SOLO si están 100% en NULL
COLUMNAS: tuple[tuple[str, str, str], ...] = (
    ("ia", "research", "destilado"),
    ("ia", "research", "destilado_modelo"),
)

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"


def _existe_tabla(cur, schema: str, tabla: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
        (schema, tabla))
    return cur.fetchone() is not None


def _existe_columna(cur, schema: str, tabla: str, columna: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s AND column_name=%s",
        (schema, tabla, columna))
    return cur.fetchone() is not None


def _peso(cur, schema: str, tabla: str) -> tuple[int, str]:
    cur.execute(f'SELECT count(*) FROM "{schema}"."{tabla}"')
    filas = int(cur.fetchone()[0])
    cur.execute("SELECT pg_size_pretty(pg_total_relation_size(%s))", (f"{schema}.{tabla}",))
    return filas, cur.fetchone()[0]


def _backup(cur, schema: str, tabla: str, destino: Path) -> int:
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
                    help="hace el backup y el DROP. Sin esto es un dry-run.")
    ap.add_argument("--sin-backup", action="store_true", help="NO vuelca a CSV antes de dropear.")
    ap.add_argument("--forzar-columnas", action="store_true",
                    help="dropea las columnas del destilado AUNQUE tengan datos (los vuelca "
                         "a CSV primero). Sin esto el script se planta, que es el default "
                         "correcto para una premisa que resultó falsa.")
    args = ap.parse_args()

    carpeta = BACKUP_DIR / f"tablas_ia_{dt.datetime.now():%Y%m%d_%H%M}"
    print(f"drop_tablas_ia — modo {'APLICAR' if args.aplicar else 'DRY-RUN (no toca nada)'}\n")
    dropeadas = ausentes = fallidas = 0

    with connect() as conn:
        print("TABLAS")
        for schema, tabla, para_que in TABLAS:
            nombre = f"{schema}.{tabla}"
            with conn.cursor() as cur:
                if not _existe_tabla(cur, schema, tabla):
                    print(f"  · {nombre:<14} ya no está")
                    ausentes += 1
                    continue
                filas, tam = _peso(cur, schema, tabla)
            print(f"  · {nombre:<14} {filas:>9} filas · {tam:>9}  — {para_que}")
            if not args.aplicar:
                continue
            if not args.sin_backup:
                destino = carpeta / f"{nombre}.csv"
                try:
                    with conn.cursor() as cur:
                        n = _backup(cur, schema, tabla, destino)
                    print(f"      backup → {destino} ({n} filas)")
                except Exception as e:
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
                print(f"      ✗ DROP falló ({type(e).__name__}: {e})")
                fallidas += 1

        print("\nCOLUMNAS (solo si están 100% en NULL)")
        for schema, tabla, columna in COLUMNAS:
            nombre = f"{schema}.{tabla}.{columna}"
            with conn.cursor() as cur:
                if not _existe_columna(cur, schema, tabla, columna):
                    print(f"  · {nombre:<32} ya no está")
                    ausentes += 1
                    continue
                cur.execute(
                    f'SELECT count(*) FROM "{schema}"."{tabla}" WHERE "{columna}" IS NOT NULL')
                con_dato = int(cur.fetchone()[0])
            if con_dato:
                # No es un error del script: es que la premisa era falsa.
                print(f"  · {nombre:<32} ⚠️ {con_dato} filas CON DATO")
                with conn.cursor() as cur:
                    cur.execute(
                        f'SELECT id, fecha FROM "{schema}"."{tabla}" '
                        f'WHERE "{columna}" IS NOT NULL ORDER BY fecha LIMIT 10')
                    for rid, fecha in cur.fetchall():
                        print(f"      id={rid} · research del {fecha}")
                if not args.forzar_columnas:
                    print("      NO se dropea. Alguien corrió el job con --destilar alguna vez.")
                    print("      Si el destilado ya no se usa, borralas con:")
                    print("      python -m scripts.drop_tablas_ia --aplicar --forzar-columnas")
                    fallidas += 1
                    continue
                if args.aplicar and not args.sin_backup:
                    # Un DROP COLUMN no tiene backup propio: se vuelca ANTES o no vuelve.
                    destino = carpeta / f"{nombre}.csv"
                    try:
                        destino.parent.mkdir(parents=True, exist_ok=True)
                        with conn.cursor() as cur:
                            cur.execute(
                                f'SELECT id, fecha, "{columna}" FROM "{schema}"."{tabla}" '
                                f'WHERE "{columna}" IS NOT NULL ORDER BY fecha')
                            n = 0
                            with destino.open("w", newline="", encoding="utf-8") as fh:
                                w = csv.writer(fh)
                                w.writerow(["id", "fecha", columna])
                                for fila in cur:
                                    w.writerow(fila)
                                    n += 1
                        print(f"      backup → {destino} ({n} filas)")
                    except Exception as e:
                        print(f"      ✗ backup FALLÓ ({type(e).__name__}: {e}) — NO se dropea")
                        fallidas += 1
                        continue
                print("      --forzar-columnas: se dropea igual")
            else:
                print(f"  · {nombre:<32} 0 filas con dato")
            if not args.aplicar:
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute(f'ALTER TABLE "{schema}"."{tabla}" DROP COLUMN IF EXISTS "{columna}"')
                conn.commit()
                print("      ✓ columna dropeada")
                dropeadas += 1
            except Exception as e:
                conn.rollback()
                print(f"      ✗ DROP COLUMN falló ({type(e).__name__}: {e})")
                fallidas += 1

    print()
    if not args.aplicar:
        print("DRY-RUN: nada se tocó. Para hacerlo: python -m scripts.drop_tablas_ia --aplicar")
        return
    print(f"Listo: {dropeadas} dropeadas · {ausentes} ya no estaban · {fallidas} no se tocaron")
    if not args.sin_backup and dropeadas:
        print(f"Backups en {carpeta}")
    if fallidas:
        sys.exit(1)


if __name__ == "__main__":
    main()
