"""diag_fci_negocio.py — READ-ONLY: FCI duplicado/inflado en la tabla OPERACIONES
del panel PORTAFOLIO (vista NEGOCIO → OPERADORES).

Síntoma reportado (cuenta 199, 03/08/2026, ticker CAFCI1578-5055): la tabla
muestra TRES filas "Susc FCI" del mismo fondo el mismo día, con cantidades
0 / 269.626,35 / 269.626.346,91 (la tercera es ~1000× la segunda) e importes
-$275 k / -$275 M. El usuario afirma que esa operación de miles de millones no
existe en la cuenta real.

Este diag responde, SIN suponer nada, cuál de estas es la causa:

  A) Son 3 comprobantes DISTINTOS que Aunesa realmente devuelve (solicitud +
     liquidación + otra etapa) → el problema es de PRESENTACIÓN (mostramos las
     3 patas de un mismo flujo como si fueran 3 operaciones).
  B) Es 1 comprobante cuyas líneas se sumaron mal en `agrupar_boletos`
     (cantidad = suma de líneas de títulos) → problema de CONSOLIDACIÓN.
  C) La cantidad/importe está mal ESCALADA (×1000) porque una línea trae el
     monto en unidades distintas (cuotapartes vs. pesos) → problema de PARSEO.
  D) La fila no existe en Aunesa hoy (quedó de una ingesta vieja que Aunesa
     después corrigió y nuestro upsert no borra) → problema de INGESTA.

Uso:
    python -m scripts.diag_fci_negocio
    python -m scripts.diag_fci_negocio --cuenta 199 --desde 2026-07-28 --hasta 2026-08-04
    python -m scripts.diag_fci_negocio --ticker CAFCI1578-5055
    python -m scripts.diag_fci_negocio --aunesa          # además re-pega a Aunesa

No escribe nada (sólo SELECT + un GET a Aunesa si se pasa --aunesa).
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _titulo(n: int, txt: str) -> None:
    print(f"\n{'─' * 78}\n{n}. {txt}\n{'─' * 78}")


def _num(x) -> str:
    if x is None:
        return "—"
    return f"{float(x):,.4f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _fecha(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


# ─── 1. Filas crudas en negocio_movimientos ─────────────────────────────────


def _filas_negocio(cuenta: str, desde: date, hasta: date, ticker: str | None) -> list[dict]:
    _titulo(1, f"operaciones.negocio_movimientos — cuenta {cuenta} · {desde} → {hasta}")
    sql = (
        "SELECT fecha, comprobante, categoria, op, ticker, cantidad, precio, importe, "
        "       moneda, unidad, plazo, lugar, estado, informacion, arancel, ingestado_en "
        "  FROM operaciones.negocio_movimientos "
        " WHERE id_cuenta = %(c)s AND fecha BETWEEN %(d)s AND %(h)s "
    )
    p: dict = {"c": cuenta, "d": desde, "h": hasta}
    if ticker:
        sql += " AND ticker = %(t)s "
        p["t"] = ticker
    sql += " ORDER BY fecha DESC, comprobante DESC"
    rows = _q(sql, p)
    if not rows:
        print("  (sin filas — ¿id_cuenta correcto? ver sección 2)")
        return rows
    for r in rows:
        print(
            f"\n  {r['fecha']}  comp={r['comprobante']}  cat={r['categoria']}  "
            f"estado={r['estado']}  lugar={r['lugar']}  unidad={r['unidad']}"
        )
        print(
            f"     op={r['op']!r}  ticker={r['ticker']!r}  moneda={r['moneda']!r}  "
            f"plazo={r['plazo']!r}"
        )
        print(
            f"     cantidad={_num(r['cantidad'])}  precio={_num(r['precio'])}  "
            f"importe={_num(r['importe'])}  arancel={_num(r['arancel'])}"
        )
        print(f"     ingestado_en={r['ingestado_en']}")
        print(f"     informacion={r['informacion']!r}")
    print(f"\n  TOTAL: {len(rows)} filas")
    return rows


# ─── 2. ¿Cómo se llama la cuenta? ───────────────────────────────────────────


def _resolver_cuenta(cuenta: str) -> None:
    _titulo(2, f"¿Cómo aparece la cuenta {cuenta} en la base?")
    rows = _q(
        "SELECT DISTINCT cuenta FROM operaciones.negocio_movimientos "
        " WHERE id_cuenta = %(c)s LIMIT 5",
        {"c": cuenta},
    )
    for r in rows:
        print(f"  negocio_movimientos.cuenta: {r['cuenta']!r}")


# ─── 3. Los "trillizos": misma fecha + mismo ticker ─────────────────────────


def _grupos_sospechosos(cuenta: str, desde: date, hasta: date) -> None:
    _titulo(3, "Grupos (fecha, ticker) con MÁS DE UNA fila — candidatos a duplicado")
    rows = _q(
        "SELECT fecha, ticker, COUNT(*) AS n, "
        "       array_agg(comprobante ORDER BY comprobante) AS comps, "
        "       array_agg(categoria  ORDER BY comprobante) AS cats, "
        "       array_agg(cantidad   ORDER BY comprobante) AS cants, "
        "       array_agg(importe    ORDER BY comprobante) AS imps, "
        "       MIN(cantidad) AS min_c, MAX(cantidad) AS max_c "
        "  FROM operaciones.negocio_movimientos "
        " WHERE id_cuenta = %(c)s AND fecha BETWEEN %(d)s AND %(h)s AND ticker IS NOT NULL "
        " GROUP BY fecha, ticker HAVING COUNT(*) > 1 "
        " ORDER BY fecha DESC",
        {"c": cuenta, "d": desde, "h": hasta},
    )
    if not rows:
        print("  (ningún grupo repetido en el rango)")
        return
    for r in rows:
        print(f"\n  {r['fecha']}  {r['ticker']}  ×{r['n']}")
        for comp, cat, cant, imp in zip(r["comps"], r["cats"], r["cants"], r["imps"]):
            print(f"     {comp:<20} {cat:<26} cant={_num(cant):>22}  imp={_num(imp):>22}")
        # El ratio delata un problema de ESCALA (×1000) vs. patas distintas de un flujo.
        mn, mx = r["min_c"], r["max_c"]
        if mn and float(mn) != 0:
            print(f"     ratio max/min cantidad = {float(mx) / float(mn):,.4f}")


# ─── 4. Lo que devuelve el endpoint (mismo filtro que el service) ───────────

_CATS_OPERACIONES = (
    "compra", "venta",
    "suscripcion_fci", "solicitud_suscripcion_fci",
    "caucion_tom_ap", "caucion_col_ap",
    "rescate_fci", "solicitud_rescate_fci",
)


def _endpoint(cuenta: str, desde: date, hasta: date) -> None:
    _titulo(4, "Filtro EXACTO del endpoint /operaciones/comercial/operaciones")
    rows = _q(
        "SELECT fecha, comprobante, categoria, op, ticker, cantidad, precio, importe, moneda "
        "  FROM operaciones.negocio_movimientos "
        " WHERE id_cuenta = %(c)s AND categoria = ANY(%(cats)s) "
        "   AND unidad IS DISTINCT FROM 'USDL' AND fecha BETWEEN %(d)s AND %(h)s "
        " ORDER BY fecha DESC, comprobante DESC",
        {"c": cuenta, "cats": list(_CATS_OPERACIONES), "d": desde, "h": hasta},
    )
    print(f"  {len(rows)} filas pasan el filtro del endpoint en el rango:")
    for r in rows:
        print(
            f"     {r['fecha']}  {r['ticker']!s:<18} {r['categoria']:<26} "
            f"cant={_num(r['cantidad']):>22} precio={_num(r['precio']):>10} "
            f"imp={_num(r['importe']):>20} {r['moneda']}"
        )


# ─── 5. Cruce con operaciones.operaciones (vista MOVIMIENTOS) ───────────────


def _cruce_operaciones(cuenta: str, desde: date, hasta: date) -> None:
    _titulo(5, "Cruce con operaciones.operaciones (fuente de la vista MOVIMIENTOS)")
    rows = _q(
        "SELECT boleto, concertacion, operacion, etapa, instrumento, moneda, "
        "       bruto, cantidad, es_cierre "
        "  FROM operaciones.operaciones "
        " WHERE id_cuenta = %(c)s AND concertacion BETWEEN %(d)s AND %(h)s "
        " ORDER BY concertacion DESC, boleto DESC",
        {"c": cuenta, "d": desde, "h": hasta},
    )
    if not rows:
        print("  (sin filas — la cuenta no tiene boletos en esta tabla en el rango)")
        return
    for r in rows:
        print(
            f"     {r['concertacion']}  {r['boleto']!s:<18} {r['operacion']!s:<22} "
            f"etapa={r['etapa']!s:<12} instr={str(r['instrumento'])[:24]:<24} "
            f"bruto={_num(r['bruto']):>20} {r['moneda']}"
        )
    print(f"\n  TOTAL: {len(rows)} boletos")


# ─── 6. Aunesa en vivo: las líneas CRUDAS del día ───────────────────────────


def _aunesa(cuenta: str, dia: date, ticker: str | None) -> None:
    _titulo(6, f"Aunesa EN VIVO — ¿qué comprobantes de {dia} SIGUEN existiendo?")
    import requests

    from api.services import aunesa_negocio as svc

    # Fetch CRUDO (sin `_excluir`) para que un comprobante filtrado por nuestras
    # reglas no se confunda con uno que Aunesa dio de baja.
    dia_str = dia.strftime("%d/%m/%Y")
    resp = requests.get(
        svc.OPS_URL,
        params={
            "tiposCuenta": "Comitente",
            "concertacionDesde": dia_str,
            "concertacionHasta": dia_str,
        },
        headers=svc._autenticar(),
        timeout=180,
    )
    resp.raise_for_status()
    crudo = resp.json() if (resp.text or "").strip() else []
    comps_aunesa = {str(r.get("comprobante")) for r in crudo if r.get("comprobante")}
    print(f"  Aunesa devuelve HOY {len(crudo)} líneas / {len(comps_aunesa)} comprobantes para {dia}")

    en_base = _q(
        "SELECT comprobante, id_cuenta, cuenta, ticker, categoria, cantidad, importe "
        "  FROM operaciones.negocio_movimientos WHERE fecha = %(d)s",
        {"d": dia},
    )
    fantasmas = [r for r in en_base if str(r["comprobante"]) not in comps_aunesa]
    print(f"  En NUESTRA base hay {len(en_base)} comprobantes para {dia}")
    print(f"  → FANTASMAS (en base, Aunesa ya NO los devuelve): {len(fantasmas)}")
    for r in fantasmas[:40]:
        print(
            f"     {r['comprobante']:<20} cta={r['id_cuenta']!s:<8} "
            f"{r['ticker']!s:<18} {r['categoria']!s:<24} "
            f"cant={_num(r['cantidad']):>22} imp={_num(r['importe']):>22}"
        )
    if len(fantasmas) > 40:
        print(f"     … y {len(fantasmas) - 40} más")

    comps_base = {str(r["comprobante"]) for r in en_base}
    faltan = comps_aunesa - comps_base
    print(f"  → FALTANTES (Aunesa los da, la base no los tiene): {len(faltan)}"
          "  (normal: los que filtramos por informacion/OTC/USDL)")

    cons = svc.fetch_y_consolidar(fecha=dia)
    boletos = [
        b for b in cons["boletos"]
        if str(b.get("cuenta") or "").strip().startswith(f"[{cuenta}]")
    ]
    if ticker:
        boletos = [b for b in boletos if (b.get("ticker") or "") == ticker]
    print(f"\n  Detalle: {len(boletos)} boletos consolidados de la cuenta {cuenta}"
          + (f" con ticker {ticker}" if ticker else ""))
    for b in boletos:
        print(
            f"\n  ── comprobante={b['comprobante']}  cat={b['categoria']}  "
            f"n_lineas={b['n_lineas']}"
        )
        print(f"     informacion={b['informacion']!r}")
        print(
            f"     CONSOLIDADO → cantidad={_num(b['cantidad'])}  precio={_num(b['precio'])}  "
            f"importe={_num(b['importe'])}  moneda={b['moneda']}"
        )
        print("     LÍNEAS RAW (lo que Aunesa devuelve, sin tocar):")
        for ln in b["lineas"]:
            crudo = {
                k: v for k, v in ln.items()
                if not k.startswith("_") and k != "lineas"
            }
            print(f"       · {json.dumps(crudo, ensure_ascii=False, default=str)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cuenta", default="199")
    ap.add_argument("--desde", default=None, help="YYYY-MM-DD (default: hasta - 10d)")
    ap.add_argument("--hasta", default=None, help="YYYY-MM-DD (default: hoy)")
    ap.add_argument("--ticker", default=None, help="filtra la sección 1 por ticker exacto")
    ap.add_argument("--aunesa", action="store_true", help="re-pega a Aunesa el día --dia")
    ap.add_argument("--dia", default="2026-08-03", help="día a re-pedir a Aunesa")
    args = ap.parse_args()

    hasta = _fecha(args.hasta) if args.hasta else date.today()
    desde = _fecha(args.desde) if args.desde else hasta - timedelta(days=10)

    _filas_negocio(args.cuenta, desde, hasta, args.ticker)
    _resolver_cuenta(args.cuenta)
    _grupos_sospechosos(args.cuenta, desde, hasta)
    _endpoint(args.cuenta, desde, hasta)
    _cruce_operaciones(args.cuenta, desde, hasta)
    if args.aunesa:
        _aunesa(args.cuenta, _fecha(args.dia), args.ticker)

    print("\n✅ diag terminado (read-only).")


if __name__ == "__main__":
    main()
