"""scripts/diag_cuentas_informe.py — ¿por qué el gráfico "CUENTAS POR SEGMENTO"
(NEGOCIO → OPERADORES → INFORME) muestra menos cuentas de las que hay?

READ-ONLY. Mide el universo real de cuentas y reconstruye, paso a paso, el embudo
que aplica `api/services/comercial_sql.py::informe_cuentas_por_segmento`:

    FROM clientes.comitentes
    WHERE estado = 'Activa'                  ← (1) descarta todo lo que no diga exacto 'Activa'
      AND fecha_alta_legajo <= <corte>       ← (2) en SQL, NULL <= fecha es NULL ⇒ TAMBIÉN descarta
                                                   las cuentas SIN fecha de alta cargada
Además chequea si esas cuentas excluidas son "fantasmas" o cuentas REALES:
¿operaron alguna vez? ¿tienen tenencia (AuM) hoy? ¿están segmentadas?

Uso:
    python -m scripts.diag_cuentas_informe
    python -m scripts.diag_cuentas_informe --fecha 2026-08-03   # corte explícito
"""
from __future__ import annotations

import argparse
from datetime import date

from core.postgres import get_pool


def _q(sql: str, p: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, p or {})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _titulo(t: str) -> None:
    print(f"\n{'─' * 78}\n{t}\n{'─' * 78}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Diag del universo de cuentas del Informe.")
    ap.add_argument("--fecha", help="corte ISO YYYY-MM-DD (default: hoy).")
    args = ap.parse_args()
    corte = date.fromisoformat(args.fecha) if args.fecha else date.today()
    p = {"corte": corte}
    print(f"Corte usado: {corte}")

    _titulo("1) UNIVERSO CRUDO")
    for r in _q(
        "SELECT (SELECT count(*) FROM clientes.cuentas)    AS en_cuentas, "
        "       (SELECT count(*) FROM clientes.comitentes) AS en_comitentes"
    ):
        print(f"clientes.cuentas     (padrón bruto): {r['en_cuentas']:>6}")
        print(f"clientes.comitentes  (con ficha)   : {r['en_comitentes']:>6}")
    huerfanas = _q(
        "SELECT count(*) AS n FROM clientes.cuentas c "
        "WHERE NOT EXISTS (SELECT 1 FROM clientes.comitentes m WHERE m.id_cuenta = c.id_cuenta)"
    )[0]["n"]
    print(f"→ en `cuentas` pero SIN fila en `comitentes`: {huerfanas} "
          "(invisibles para el informe, no tienen segmento ni operador)")

    _titulo("2) BREAKDOWN POR `estado` (el filtro #1 del gráfico)")
    for r in _q(
        "SELECT COALESCE(estado, '(NULL)') AS estado, count(*) AS n "
        "FROM clientes.comitentes GROUP BY COALESCE(estado, '(NULL)') ORDER BY n DESC"
    ):
        marca = "  ✅ ENTRA" if r["estado"] == "Activa" else "  ❌ QUEDA AFUERA"
        print(f"{r['estado']:<28} {r['n']:>6}{marca}")

    _titulo("3) EMBUDO DEL GRÁFICO (paso a paso)")
    r = _q(
        "SELECT count(*) FILTER (WHERE estado = 'Activa') AS activas, "
        "       count(*) FILTER (WHERE estado = 'Activa' AND fecha_alta_legajo IS NULL) AS activas_sin_alta, "
        "       count(*) FILTER (WHERE estado = 'Activa' AND fecha_alta_legajo > %(corte)s) AS activas_alta_futura, "
        "       count(*) FILTER (WHERE estado = 'Activa' AND fecha_alta_legajo <= %(corte)s) AS en_grafico "
        "FROM clientes.comitentes", p,
    )[0]
    print(f"estado = 'Activa'                          : {r['activas']:>6}")
    print(f"  ├─ SIN fecha_alta_legajo (NULL)          : {r['activas_sin_alta']:>6}  ❌ se pierden (NULL <= fecha = NULL)")
    print(f"  ├─ alta POSTERIOR al corte               : {r['activas_alta_futura']:>6}  ❌ se pierden")
    print(f"  └─ alta <= corte  →  LO QUE MUESTRA EL Q1: {r['en_grafico']:>6}  ✅")

    _titulo("4) LAS EXCLUIDAS, ¿SON CUENTAS REALES?")
    w_excl = ("(estado IS DISTINCT FROM 'Activa' OR fecha_alta_legajo IS NULL "
              "OR fecha_alta_legajo > %(corte)s)")
    r = _q(
        f"SELECT count(*) AS excluidas, "
        f"  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM operaciones.negocio_movimientos nm "
        f"                                 WHERE nm.id_cuenta = m.id_cuenta)) AS con_operaciones, "
        f"  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM portafolio.tenencia t "
        f"                                 WHERE t.id_cuenta = m.id_cuenta AND t.aum = 'si')) AS con_tenencia, "        f"  count(*) FILTER (WHERE nivel_1 IS NOT NULL) AS segmentadas "
        f"FROM clientes.comitentes m WHERE {w_excl}", p,
    )[0]
    print(f"Excluidas del gráfico            : {r['excluidas']:>6}")
    print(f"  ├─ operaron alguna vez         : {r['con_operaciones']:>6}  ← cuentas REALES que no se cuentan")
    print(f"  ├─ tuvieron tenencia (AuM) alguna vez: {r['con_tenencia']:>6}")
    print(f"  └─ tienen segmento (nivel_1)   : {r['segmentadas']:>6}")

    _titulo("5) EXCLUIDAS — DETALLE POR MOTIVO × SEGMENTO")
    for row in _q(
        f"SELECT CASE WHEN estado IS DISTINCT FROM 'Activa' "
        f"              THEN 'estado=' || COALESCE(estado, '(NULL)') "
        f"            WHEN fecha_alta_legajo IS NULL THEN 'sin fecha_alta_legajo' "
        f"            ELSE 'alta posterior al corte' END AS motivo, "
        f"       COALESCE(nivel_1, '(sin segmentar)') AS segmento, count(*) AS n "
        f"FROM clientes.comitentes m WHERE {w_excl} "
        f"GROUP BY 1, 2 ORDER BY n DESC", p,
    ):
        print(f"{row['motivo']:<34} {row['segmento']:<24} {row['n']:>5}")

    _titulo("6) MUESTRA (20 excluidas que SÍ operaron) — para validar a ojo")
    for row in _q(
        f"SELECT m.id_cuenta, COALESCE(m.estado, '(NULL)') AS estado, m.fecha_alta_legajo, "
        f"       COALESCE(m.nivel_1, '(sin segmentar)') AS nivel_1, m.operador_email "
        f"FROM clientes.comitentes m WHERE {w_excl} "
        f"  AND EXISTS (SELECT 1 FROM operaciones.negocio_movimientos nm WHERE nm.id_cuenta = m.id_cuenta) "
        f"ORDER BY m.id_cuenta LIMIT 20", p,
    ):
        print(f"{row['id_cuenta']:<8} {row['estado']:<12} alta={row['fecha_alta_legajo']!s:<12} "
              f"{row['nivel_1']:<22} {row['operador_email'] or '(sin operador)'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
