"""diag_sql_frescura.py — auditoría de FRESCURA de TODAS las tablas SQL de dominio.

Read-only. Para cada tabla de los schemas de negocio (mercado/macro/operaciones/
portafolio/clientes/valuaciones/home/manager): conteo de filas + la fecha/timestamp
más reciente (detecta la columna de fecha sola, vía information_schema). Revela tablas
VACÍAS, STALE (última fecha vieja) o sin columna temporal — para saber qué writer no
está actualizando SQL.

    python -m scripts.diag_sql_frescura
"""
from __future__ import annotations

from datetime import date, datetime

from core.postgres import get_pool

_SCHEMAS = ("mercado", "macro", "operaciones", "portafolio", "clientes",
            "valuaciones", "home", "manager")
_HOY = None   # se setea en main (no usar Date.now en import)


def _edad_dias(v) -> int | None:
    if v is None or _HOY is None:
        return None
    d = v.date() if isinstance(v, datetime) else v
    if isinstance(d, date):
        return (_HOY - d).days
    return None


def main() -> int:
    global _HOY
    with get_pool().connection() as cn, cn.cursor() as cur:
        cur.execute("SELECT CURRENT_DATE")
        _HOY = cur.fetchone()[0]

        # Tablas de los schemas de dominio.
        cur.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema = ANY(%s) AND table_type = 'BASE TABLE' "
            "ORDER BY table_schema, table_name", (list(_SCHEMAS),))
        tablas = cur.fetchall()

        print(f"{'TABLA':<42} {'FILAS':>10}  {'ÚLT. FECHA':<12} {'EDAD':>6}  COL")
        print("─" * 90)
        for schema, tabla in tablas:
            full = f"{schema}.{tabla}"
            try:
                cur.execute(f"SELECT count(*) FROM {full}")
                n = cur.fetchone()[0]
            except Exception as e:
                print(f"{full:<42} ERROR count: {str(e).splitlines()[0][:30]}")
                continue
            # Columna temporal (la primera date/timestamp).
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s AND data_type IN "
                "('date','timestamp without time zone','timestamp with time zone') "
                "ORDER BY ordinal_position LIMIT 1", (schema, tabla))
            col_row = cur.fetchone()
            ult, edad, col = "—", "", ""
            if col_row:
                col = col_row[0]
                try:
                    cur.execute(f"SELECT max({col}) FROM {full}")
                    mx = cur.fetchone()[0]
                    if mx is not None:
                        ult = (mx.date() if isinstance(mx, datetime) else mx).isoformat()
                        d = _edad_dias(mx)
                        edad = f"{d}d" if d is not None else ""
                except Exception:
                    ult = "(err)"
            flag = ""
            if n == 0:
                flag = "  ⚠ VACÍA"
            elif edad and edad.rstrip("d").isdigit() and int(edad.rstrip("d")) > 7:
                flag = "  ⚠ STALE"
            print(f"{full:<42} {n:>10,}  {ult:<12} {edad:>6}  {col}{flag}")

    print("\n⚠ VACÍA = 0 filas · ⚠ STALE = última fecha > 7 días (puede ser normal en "
          "tablas de cierre si no hubo rueda; revisar caso por caso).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
