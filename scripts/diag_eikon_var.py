"""diag_eikon_var — ¿por qué VAR% / VAR NETA del tab REUTERS quedan en '—'?

Read-only, one-shot. Corré en el Droplet con el feed (PC oficina) prendido:

    python -m scripts.diag_eikon_var

Chequea, sobre TODO mercado.eikon_snapshot:
  - cuántas filas tienen last / var_pct / var_neta / prev_close poblados.
  - si var_pct/var_neta están null pero last y prev_close SÍ están → el fix es
    calcular la variación en el server (fallback last vs prev_close).
Se borra cuando cierre el tema (REGLA #5).
"""
from __future__ import annotations

from core.postgres import get_pool

CAMPOS = ["last", "var_pct", "var_neta", "prev_close"]


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), min(updated_at), max(updated_at) "
                    "FROM mercado.eikon_snapshot")
        n, ts_min, ts_max = cur.fetchone()
        print(f"mercado.eikon_snapshot: {n} filas · updated_at [{ts_min} → {ts_max}]")
        if not n:
            print("(sin filas — ¿el feed está corriendo en la PC de oficina?)")
            return

        # Cuántas filas tienen cada campo NO-null en el jsonb.
        sel = ", ".join(
            f"count(*) FILTER (WHERE data ? '{c}' AND data->>'{c}' <> '' "
            f"AND lower(data->>'{c}') <> 'nan') AS {c}" for c in CAMPOS
        )
        cur.execute(f"SELECT {sel} FROM mercado.eikon_snapshot")
        vals = cur.fetchone()
        print("\npoblados (no-null / no-NaN):")
        for c, v in zip(CAMPOS, vals):
            print(f"  {c:<12} {v}/{n}")

        # Filas donde last y prev_close están pero var_pct falta → candidatas al fallback.
        cur.execute(
            "SELECT count(*) FROM mercado.eikon_snapshot "
            "WHERE data->>'last' IS NOT NULL AND lower(data->>'last') <> 'nan' "
            "  AND data->>'prev_close' IS NOT NULL AND lower(data->>'prev_close') <> 'nan' "
            "  AND (NOT data ? 'var_pct' OR data->>'var_pct' IS NULL "
            "       OR lower(data->>'var_pct') = 'nan' OR data->>'var_pct' = '')"
        )
        (candidatas,) = cur.fetchone()
        print(f"\nfilas con last+prev_close pero SIN var_pct (fallback aplicable): {candidatas}")

        # Sample crudo de 12 filas para ver los valores reales.
        cur.execute(
            "SELECT ticker, data->>'last', data->>'prev_close', "
            "       data->>'var_pct', data->>'var_neta', updated_at "
            "FROM mercado.eikon_snapshot ORDER BY ticker LIMIT 12")
        print("\nsample (ticker · last · prev_close · var_pct · var_neta · updated_at):")
        for t, last, prev, vp, vn, ts in cur.fetchall():
            print(f"  {t:<7} last={str(last):<9} prev={str(prev):<9} "
                  f"var%={str(vp):<8} var_neta={str(vn):<8} {ts}")


if __name__ == "__main__":
    main()
