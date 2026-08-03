"""validacion_cruzada_mav.py — READ-ONLY: cruza los boletos de MERCADO MAV
(`operaciones.operaciones`) contra sus movimientos en `operaciones.negocio_movimientos`
para traer el PRECIO, que sólo existe del lado de negocio.

Por qué existe
--------------
`operaciones.operaciones` es la fuente de la vista MOVIMIENTOS: tiene el boleto, el
volumen (`bruto`) y el arancel, pero NO tiene precio. El dato de precio vive en
`operaciones.negocio_movimientos` (la tabla de cost-basis), que se cruza por
`boleto == comprobante`. Este script junta las dos y emite un CSV para validar a mano.

EL PRECIO DE MAV ES UNA TASA, Y NO ESTÁ EN LA COLUMNA `precio` (medido 2026-08-03)
---------------------------------------------------------------------------------
La columna `negocio_movimientos.precio` está poblada al 100% en las categorías
compra / venta / suscripcion_fci / rescate_fci / caucion_*, y al 0% en `otro`.
**El 100% de los boletos MAV cae en `otro`** → para MAV `precio` es SIEMPRE NULL.
No es un hueco de datos: un pagaré o un cheque no se negocia a precio unitario,
se negocia A TASA. (Se descartó por medición que el ratio importe/cantidad fuera
el precio: da un rango de 0,0003 a 1484 — no es un precio.)

La tasa está embebida en el texto de `informacion`, después del `@`:

    'Compra [#UAC140770002] 100.000,00@6% (ARS 24hs)'      → 6
    'Compra [*ACI120300125] 27.000.000,00@39,5% (ARS ...)' → 39,5
    'Subasta [#UCO310770001] 30.000,00@7% (ARS Inm)'       → 7

Formato: `<Operación> [<código>] <nominal>@<TASA>% (<moneda> <plazo>)`, con números
en formato argentino (punto de miles, coma decimal). El script parsea eso y lo
expone en la columna **`tasa_pct`**, que es el precio a los fines de esta
validación. Se conserva `tasa_raw` (el token crudo) para poder auditar el parseo,
y `nominal_info` para cruzarlo contra `cantidad`.

Qué emite
---------
Una fila por (boleto × movimiento de negocio que le matchea). Un boleto puede tener
0, 1 o N movimientos:
  - SIN_MOVIMIENTO → el boleto no tiene contraparte en negocio (hueco a mirar).
  - OK             → exactamente 1 movimiento.
  - MULTIPLE       → N movimientos (el boleto se abrió en varias patas/títulos);
                     las columnas `*_total_boleto` traen la suma para comparar
                     contra el `bruto`/`cantidad` del boleto sin sumar a mano.

Lógica de operaciones REPLICADA de `api/services/operaciones_sql.py::_ops_where`
(para que los números aten con lo que se ve en la app):
  - `COALESCE(es_cierre, false) = false` — los cierres no cuentan como volumen.
    NULL = NO es cierre (FCI bilateral) → se incluye. Levantable con --incluir-cierres.
  - guarda anti-doble-conteo de FCI bilateral (Suscripción+liquidacion /
    Rescate+solicitud).
Si esa lógica cambia en el service, actualizar acá también.

Uso:
    python -m scripts.validacion_cruzada_mav                          # año actual
    python -m scripts.validacion_cruzada_mav --desde 2026-01-01 --hasta 2026-06-30
    python -m scripts.validacion_cruzada_mav --mercado MAV            # literal exacto
    python -m scripts.validacion_cruzada_mav --salida /root/mav.csv
    python -m scripts.validacion_cruzada_mav --todo                   # sin filtro de fecha

No escribe NADA en la base (sólo SELECT). Si el mercado pedido no matchea ningún
valor real, NO genera archivo: lista los valores disponibles y corta.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date

from psycopg.rows import dict_row

from core.postgres import get_pool

# ── Parseo de `informacion` ──────────────────────────────────────────────────
# 'Compra [#UAC140770002] 100.000,00@6% (ARS 24hs)'
#          └─ código ──┘  └ nominal ┘ └tasa┘
# El parseo se hace en Python (no en SQL) por dos razones: el '%' literal
# obligaría a escaparlo como '%%' para psycopg —trampa fácil de romper— y acá
# un texto con formato inesperado devuelve None en vez de tumbar la corrida.
_RE_TASA = re.compile(r"@\s*([\d.,]+)\s*%")
_RE_COD = re.compile(r"\[([^\]]+)\]")
_RE_NOMINAL = re.compile(r"\]\s*([\d.,]+)\s*@")


def _num_ar(s: str | None) -> float | None:
    """Número en formato argentino → float. '39,5'→39.5, '27.000.000,00'→27000000.0

    Sin coma la cadena es ambigua ('39.5' puede ser 39,5 o 395): se asume punto
    decimal sólo si hay UN punto con 1-2 dígitos detrás; si no, es separador de
    miles. Los datos vistos usan siempre coma decimal, pero un 10x silencioso
    por adivinar mal sería peor que devolver None.
    """
    t = (s or "").strip()
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") == 1 and len(t.split(".")[1]) <= 2:
        pass  # ya es decimal con punto
    else:
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def _parse_informacion(info: str | None) -> dict:
    """Extrae tasa (= el precio para MAV), código de instrumento y nominal."""
    if not info:
        return {"tasa_pct": None, "tasa_raw": "", "cod_instrumento": "", "nominal_info": None}
    m_tasa = _RE_TASA.search(info)
    m_cod = _RE_COD.search(info)
    m_nom = _RE_NOMINAL.search(info)
    raw = m_tasa.group(1) if m_tasa else ""
    return {
        "tasa_pct": _num_ar(raw),
        "tasa_raw": raw,
        "cod_instrumento": m_cod.group(1) if m_cod else "",
        "nominal_info": _num_ar(m_nom.group(1)) if m_nom else None,
    }

# Columnas del CSV. Prefijo `nm_` = viene de negocio_movimientos.
CAMPOS = [
    # ── boleto (operaciones.operaciones) ──
    "boleto", "concertacion", "id_cuenta", "denominacion", "mercado", "moneda",
    "operacion", "tipo_operacion", "instrumento", "segmento", "nivel_3", "etapa",
    "es_cierre", "cantidad_ops", "bruto", "arancel", "mep",
    # ── resultado del cruce ──
    "estado_match", "n_movimientos",
    # ── EL PRECIO: para MAV es la tasa embebida en `informacion` ──
    "tasa_pct",             # ← el dato que se viene a buscar (ej. 6 = 6%)
    "tasa_raw",             # token crudo, para auditar el parseo
    "cod_instrumento",      # [#UAC140770002] / [*ACI120300125]
    "nominal_info",         # nominal del texto — cruzar contra nm_cantidad
    # ── movimiento (operaciones.negocio_movimientos) ──
    "nm_fecha", "nm_ticker", "nm_categoria", "nm_op", "nm_cantidad",
    "nm_precio",            # columna nativa: SIEMPRE vacía en MAV (ver docstring)
    "nm_importe", "nm_moneda", "nm_plazo", "nm_estado", "nm_cuenta",
    "nm_informacion",       # texto de origen de la tasa
    # ── totales por boleto (iguales en todas las filas del mismo boleto) ──
    "nm_cantidad_total_boleto", "nm_importe_total_boleto",
]

# El WHERE canónico de la vista MOVIMIENTOS (ver docstring).
_W_CIERRE = "COALESCE(o.es_cierre, false) = false"
_W_FCI = (
    "NOT ((COALESCE(o.operacion,'') = 'Suscripción' AND COALESCE(o.etapa,'') = 'liquidacion') "
    "OR (COALESCE(o.operacion,'') = 'Rescate' AND COALESCE(o.etapa,'') = 'solicitud'))"
)


def _mercados_disponibles() -> list[str]:
    """Valores reales de `mercado`. Es la red contra hardcodear un literal que no existe."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT mercado, count(*) AS n FROM operaciones.operaciones "
            "WHERE mercado IS NOT NULL AND mercado <> '' "
            "GROUP BY mercado ORDER BY n DESC"
        )
        return [(r["mercado"], r["n"]) for r in cur.fetchall()]


def _resolver_mercado(pedido: str | None) -> list[str]:
    """Devuelve los valores de `mercado` a usar.

    Sin --mercado busca los que contengan 'mav' (case-insensitive), porque el
    literal exacto lo definen los datos, no este script. Con --mercado usa ese
    valor tal cual (igualdad exacta, como el filtro de la app).
    """
    disponibles = _mercados_disponibles()
    if not disponibles:
        print("❌ `operaciones.operaciones` no tiene ningún valor de `mercado`.")
        sys.exit(1)

    if pedido:
        exactos = [m for m, _ in disponibles if m == pedido]
        if exactos:
            return exactos
        print(f"❌ No existe el mercado {pedido!r}. Valores reales:\n")
        for m, n in disponibles:
            print(f"     {m!r:<28} {n:>9,} boletos")
        sys.exit(1)

    candidatos = [m for m, _ in disponibles if "mav" in m.lower()]
    if not candidatos:
        print("❌ Ningún valor de `mercado` contiene 'MAV'. Valores reales:\n")
        for m, n in disponibles:
            print(f"     {m!r:<28} {n:>9,} boletos")
        print("\n   Elegí uno con --mercado '<valor exacto>'.")
        sys.exit(1)
    return candidatos


def _construir_query(mercados: list[str], desde: str | None, hasta: str | None,
                     moneda: str | None, incluir_cierres: bool) -> tuple[str, dict]:
    """LEFT JOIN boleto → movimientos. LEFT (no INNER) a propósito: los boletos SIN
    contraparte en negocio son justamente lo que hay que ver."""
    conds = ["o.mercado = ANY(%(mercados)s)", _W_FCI]
    params: dict = {"mercados": mercados}
    if not incluir_cierres:
        conds.append(_W_CIERRE)
    if desde:
        conds.append("o.concertacion >= %(desde)s")   # ix_ops_concertacion
        params["desde"] = desde
    if hasta:
        conds.append("o.concertacion <= %(hasta)s")
        params["hasta"] = hasta
    if moneda:
        conds.append("o.moneda = %(moneda)s")
        params["moneda"] = moneda

    # Los totales por boleto salen con window functions (una sola pasada) en vez de
    # una segunda query + merge en Python.
    sql = f"""
        SELECT
            o.boleto, o.concertacion, o.id_cuenta, o.denominacion, o.mercado, o.moneda,
            o.operacion, o.tipo_operacion, o.instrumento, o.segmento, o.nivel_3, o.etapa,
            o.es_cierre, o.cantidad AS cantidad_ops, o.bruto, o.arancel, o.mep,
            n.fecha       AS nm_fecha,
            n.ticker      AS nm_ticker,
            n.categoria   AS nm_categoria,
            n.op          AS nm_op,
            n.cantidad    AS nm_cantidad,
            n.precio      AS nm_precio,
            n.importe     AS nm_importe,
            n.moneda      AS nm_moneda,
            n.plazo       AS nm_plazo,
            n.estado      AS nm_estado,
            n.cuenta      AS nm_cuenta,
            n.informacion AS nm_informacion,
            count(n.comprobante) OVER (PARTITION BY o.boleto) AS n_movimientos,
            sum(n.cantidad)      OVER (PARTITION BY o.boleto) AS nm_cantidad_total_boleto,
            sum(n.importe)       OVER (PARTITION BY o.boleto) AS nm_importe_total_boleto
        FROM operaciones.operaciones o
        LEFT JOIN operaciones.negocio_movimientos n
               ON n.comprobante = o.boleto          -- ix_nm_comprobante
        WHERE {' AND '.join(conds)}
        ORDER BY o.concertacion DESC, o.boleto, n.ticker
    """
    return sql, params


def _fmt(v):
    """Decimal/date → algo que Excel lea sin pelear. None → ''."""
    if v is None:
        return ""
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return "SI" if v else "NO"
    return v


def main() -> None:
    hoy = date.today()
    ap = argparse.ArgumentParser(description="Cruza boletos MAV con negocio_movimientos (precio).")
    ap.add_argument("--mercado", default=None,
                    help="valor EXACTO de `mercado`. Sin esto, busca los que contengan 'MAV'.")
    ap.add_argument("--desde", default=f"{hoy.year}-01-01", help="ISO YYYY-MM-DD (default: 1-ene de este año)")
    ap.add_argument("--hasta", default=hoy.isoformat(), help="ISO YYYY-MM-DD (default: hoy)")
    ap.add_argument("--todo", action="store_true", help="sin filtro de fecha (lee TODO el histórico)")
    ap.add_argument("--moneda", default=None, help="ARS | USD. Default: todas.")
    ap.add_argument("--incluir-cierres", action="store_true",
                    help="incluye los boletos de cierre (por default se excluyen, como la app)")
    ap.add_argument("--salida", default=None, help="path del CSV (default: ./validacion_cruzada_mav_<rango>.csv)")
    args = ap.parse_args()

    desde, hasta = (None, None) if args.todo else (args.desde, args.hasta)

    mercados = _resolver_mercado(args.mercado)
    print(f"\nMercado(s): {', '.join(repr(m) for m in mercados)}")
    print(f"Rango:      {'TODO el histórico' if args.todo else f'{desde} → {hasta}'}")
    print(f"Moneda:     {args.moneda or 'todas'}")
    print(f"Cierres:    {'incluidos' if args.incluir_cierres else 'excluidos (como la app)'}")

    salida = args.salida or (
        f"validacion_cruzada_mav_{'historico' if args.todo else f'{desde}_a_{hasta}'}.csv"
    )

    sql, params = _construir_query(mercados, desde, hasta, args.moneda, args.incluir_cierres)

    filas = 0
    boletos: set[str] = set()
    sin_mov: set[str] = set()
    multiples: set[str] = set()
    con_precio = 0        # columna nativa `precio` (vacía en MAV)
    con_tasa = 0          # tasa parseada de `informacion` — el precio real acá
    sin_parsear: list[str] = []  # hay informacion pero no se pudo sacar la tasa

    # Cursor server-side: con --todo el resultado puede ser grande y no queremos
    # materializarlo entero en RAM.
    with get_pool().connection() as conn:
        with conn.cursor(name="valid_cruzada_mav", row_factory=dict_row) as cur:
            cur.itersize = 5000
            cur.execute(sql, params)
            with open(salida, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=CAMPOS, extrasaction="ignore")
                w.writeheader()
                for r in cur:
                    n = r["n_movimientos"] or 0
                    r["estado_match"] = "SIN_MOVIMIENTO" if n == 0 else ("OK" if n == 1 else "MULTIPLE")
                    boletos.add(r["boleto"])
                    if n == 0:
                        sin_mov.add(r["boleto"])
                    elif n > 1:
                        multiples.add(r["boleto"])
                    if r.get("nm_precio") is not None:
                        con_precio += 1
                    r.update(_parse_informacion(r.get("nm_informacion")))
                    if r["tasa_pct"] is not None:
                        con_tasa += 1
                    elif r.get("nm_informacion"):
                        # Hay texto pero no se pudo sacar la tasa: es un formato
                        # distinto al conocido. Se guarda para mostrarlo — si esto
                        # se ignorara, el CSV saldría con la columna vacía y
                        # parecería que la operación no tiene tasa.
                        if len(sin_parsear) < 5:
                            sin_parsear.append(str(r["nm_informacion"])[:70])
                    w.writerow({k: _fmt(r.get(k)) for k in CAMPOS})
                    filas += 1

    print(f"\n📄 {salida}")
    print(f"   filas escritas          {filas:>9,}")
    print(f"   boletos distintos       {len(boletos):>9,}")
    print(f"   · CON TASA (tasa_pct)   {con_tasa:>9,}  ← el precio de MAV")
    print(f"   · con precio nativo     {con_precio:>9,}  (columna `precio`; 0 es lo esperado en MAV)")
    print(f"   · SIN_MOVIMIENTO        {len(sin_mov):>9,}  (boleto sin contraparte en negocio)")
    print(f"   · MULTIPLE              {len(multiples):>9,}  (boleto con N movimientos)")

    if not filas:
        print("\n⚠️  Cero filas. El mercado existe pero no hay boletos en ese rango de fechas.")
        print("    Probá con --todo, o ampliá --desde/--hasta.")
        return

    if sin_parsear:
        print("\n⚠️  Hay filas con `informacion` de las que NO se pudo extraer la tasa.")
        print("    Formato distinto al conocido. Muestra:")
        for s in sin_parsear:
            print(f"      {s!r}")
        print("    Pasame estas líneas y ajusto el parseo.")
    if sin_mov:
        pct = 100.0 * len(sin_mov) / len(boletos)
        print(f"\n⚠️  {pct:.1f}% de los boletos no tiene movimiento en negocio. Filtrá por")
        print("    estado_match=SIN_MOVIMIENTO en el CSV para verlos.")


if __name__ == "__main__":
    main()
