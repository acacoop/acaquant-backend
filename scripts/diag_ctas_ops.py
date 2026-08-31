"""READ-ONLY. ¿Cuánto cambia CTAS OPS si el número sale de `operaciones.operaciones`?

Herramienta: diag · Una sola pasada, un solo mes, cero escrituras.

EL PUNTO
========

En NEGOCIO → OPERADORES → INFORME, la columna **CTAS OPS** («cuentas que
operaron en el mes») sale hoy de `operaciones.negocio_movimientos` filtrando por
SEIS categorías (`comercial._CATS_VOLUMEN`). Y esa tabla **no tiene a las cuentas
OTC**: `api/services/aunesa_negocio._excluir()` las tira EN LA INGESTA por
substring en el nombre de la cuenta, así que no es que no cuenten — es que sus
movimientos no existen.

La salida propuesta es que CTAS OPS pase a leer `operaciones.operaciones` con el
MISMO predicado que ya usa DÍAS SIN OPERAR (`comercial_sql._ULT_OP_WHERE` =
`anulado_en IS NULL`, cualquier boleto). Esa tabla sí tiene a las OTC — de ahí
sale el arancel que el informe ya les muestra.

Pero **el número sube para todos, no solo para las OTC**, y cuánto no se puede
saber sin medirlo. Eso es lo que contesta este script, para el mes que le pases:

  · cuántas CTAS OPS hay HOY y cuántas habría con el criterio nuevo
  · QUIÉNES entran, con el motivo de por qué hoy no están
  · quiénes SALDRÍAN (si sale alguna, el cambio no es gratis y hay que verlo)
  · el impacto POR OPERADOR — el ranking del informe se ordena por eso
  · las OTC que van a seguir sin aparecer (no tienen boletos en `operaciones`
    tampoco: los `NDF OTC` y `Opciones OTC` no se ingestan desde 2026-06-08)

Los dos predicados se IMPORTAN del código que sirve la vista, no se copian: si
mañana cambian, este diag cambia con ellos.

Uso:
    python -m scripts.diag_ctas_ops                    # mes corriente (ART)
    python -m scripts.diag_ctas_ops --mes 2026-08      # un mes puntual
    python -m scripts.diag_ctas_ops --mes 2026-08 --top 40
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta

from api.services.comercial import _CATS_VOLUMEN
from api.services.comercial_sql import _ULT_OP_WHERE
from core.postgres import get_job_pool

# Substrings que `aunesa_negocio.EXCLUIR_SUBSTRINGS` y `jobs/_aum_filters` usan
# para decir "esto es OTC/CDC". Acá sirven SOLO para etiquetar el motivo en la
# salida — el diag no filtra por ellos.
_MARCAS_OTC = ("OTC", "CDC")


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _rango(mes: str | None) -> tuple[date, date, str]:
    """'YYYY-MM' → (primer día, último día, label). Sin mes: el corriente en ART."""
    hoy = _hoy_art()
    ym = mes or f"{hoy.year:04d}-{hoy.month:02d}"
    y, m = int(ym[:4]), int(ym[5:7])
    ini = date(y, m, 1)
    fin = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    # El mes en curso se corta HOY: contar hasta el 31 cuando estamos a 5 no es
    # el número que muestra la pantalla.
    if fin > hoy:
        fin = hoy
    return ini, fin, ym


def _plata(v: float) -> str:
    """ARS legible. El arancel se guarda siempre en ARS (no se dolariza acá:
    este diag compara cuentas, no valúa)."""
    a = abs(v)
    if a >= 1_000_000_000:
        return f"${v / 1_000_000_000:,.1f} MM".replace(",", ".")
    if a >= 1_000_000:
        return f"${v / 1_000_000:,.1f} M".replace(",", ".")
    return f"${v:,.0f}".replace(",", ".")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compara CTAS OPS actual vs. leyendo operaciones.operaciones.")
    ap.add_argument("--mes", help="mes YYYY-MM (default: el corriente en ART)")
    ap.add_argument("--top", type=int, default=25,
                    help="cuántas cuentas listar en cada detalle (default 25)")
    args = ap.parse_args()

    ini, fin, ym = _rango(args.mes)
    p = {"ini": ini, "fin": fin, "cats": list(_CATS_VOLUMEN)}

    print(f"\n{'=' * 78}")
    print(f"  CTAS OPS — {ym}   ventana {ini.strftime('%d/%m/%Y')} → {fin.strftime('%d/%m/%Y')}")
    print(f"{'=' * 78}\n")

    # ── Universo: el MISMO que el informe (comitentes activas). Una cuenta que
    #    no está acá no aparece en la vista, opere lo que opere.
    universo = {r["id_cuenta"]: r for r in _q(
        "SELECT c.id_cuenta, u.denominacion, c.operador_email, "
        "       o.nombre AS operador_nombre "
        "FROM comitentes c "
        "LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
        "LEFT JOIN operadores o ON o.email = c.operador_email "
        "WHERE c.estado = 'Activa'")}
    print(f"  Universo (comitentes estado='Activa'): {len(universo)} cuentas\n")

    # ── (A) CRITERIO DE HOY — negocio_movimientos, 6 categorías, sin USDL.
    hoy_set = {r["id_cuenta"] for r in _q(
        "SELECT DISTINCT id_cuenta FROM negocio_movimientos "
        "WHERE fecha >= %(ini)s AND fecha <= %(fin)s "
        "  AND categoria = ANY(%(cats)s) AND anulado_en IS NULL "
        "  AND unidad IS DISTINCT FROM 'USDL' AND id_cuenta IS NOT NULL", p)}
    hoy_set &= set(universo)

    # ── (B) CRITERIO PROPUESTO — operaciones.operaciones, cualquier boleto no
    #        anulado. El WHERE se importa: es el mismo de DÍAS SIN OPERAR.
    ops = {r["id_cuenta"]: r for r in _q(
        f"SELECT id_cuenta, count(*) AS n_boletos, "
        f"  COALESCE(SUM(CASE WHEN arancel > 0 AND etapa IS DISTINCT FROM 'solicitud' "
        f"                    THEN arancel END), 0) AS arancel "
        f"FROM operaciones "
        f"WHERE concertacion >= %(ini)s AND concertacion <= %(fin)s "
        f"  AND {_ULT_OP_WHERE} AND id_cuenta IS NOT NULL "
        f"GROUP BY id_cuenta", p)}
    nuevo_set = set(ops) & set(universo)

    entran = nuevo_set - hoy_set
    salen = hoy_set - nuevo_set

    print(f"  {'CTAS OPS hoy (negocio_movimientos, 6 categorías)':<52} {len(hoy_set):>6}")
    print(f"  {'CTAS OPS con operaciones.operaciones':<52} {len(nuevo_set):>6}")
    print(f"  {'-' * 59}")
    delta = len(nuevo_set) - len(hoy_set)
    pct = (delta / len(hoy_set) * 100) if hoy_set else 0.0
    print(f"  {'DIFERENCIA':<52} {delta:>+6}  ({pct:+.1f}%)")
    print(f"  {'ENTRAN':<52} {len(entran):>6}")
    print(f"  {'SALEN':<52} {len(salen):>6}\n")

    # ── Por qué HOY no están: qué tiene cada cuenta en negocio_movimientos ese
    #    mes, sin filtrar por categoría. Si no tiene NADA, la ingesta la excluyó.
    nm: dict[str, dict] = {}
    for r in _q("SELECT id_cuenta, categoria, unidad, count(*) AS n "
                "FROM negocio_movimientos "
                "WHERE fecha >= %(ini)s AND fecha <= %(fin)s "
                "  AND anulado_en IS NULL AND id_cuenta IS NOT NULL "
                "GROUP BY 1, 2, 3", p):
        d = nm.setdefault(r["id_cuenta"], {"cats": set(), "unidades": set()})
        d["cats"].add(r["categoria"])
        d["unidades"].add(r["unidad"])

    cats_ok = set(_CATS_VOLUMEN)

    def _motivo(idc: str) -> str:
        nombre = (universo[idc].get("denominacion") or "").upper()
        marca = next((m for m in _MARCAS_OTC if m in nombre), None)
        d = nm.get(idc)
        if d is None:
            if marca:
                return f"{marca} — excluida en la ingesta por el nombre de la cuenta"
            return "sin movimientos en negocio_movimientos"
        if marca:
            return f"{marca} — solo entró lo que la ingesta no tira (diferencias diarias)"
        if d["cats"] & cats_ok and d["unidades"] <= {"USDL"}:
            return "futuros DLR (unidad USDL, excluida a propósito)"
        fuera = sorted(c for c in d["cats"] if c and c not in cats_ok)
        if fuera:
            return "categorías fuera de las 6: " + ", ".join(fuera[:3])
        return "revisar a mano"

    if entran:
        print(f"  {'─' * 74}")
        print("  ENTRAN — por motivo\n")
        por_motivo: dict[str, int] = {}
        for idc in entran:
            k = _motivo(idc).split(" — ")[0].split(":")[0]
            por_motivo[k] = por_motivo.get(k, 0) + 1
        for k, n in sorted(por_motivo.items(), key=lambda x: -x[1]):
            print(f"    {k:<58} {n:>5}")

        filas = sorted(entran, key=lambda i: -float(ops[i]["arancel"] or 0))
        print(f"\n  DETALLE — las {min(args.top, len(filas))} de mayor arancel del mes\n")
        print(f"    {'CUENTA':<44} {'ARANCEL MES':>13} {'BOL':>5}  MOTIVO")
        for idc in filas[:args.top]:
            u = universo[idc]
            nom = f"[{idc}] {(u.get('denominacion') or '—')}"[:44]
            print(f"    {nom:<44} {_plata(float(ops[idc]['arancel'] or 0)):>13} "
                  f"{int(ops[idc]['n_boletos']):>5}  {_motivo(idc)}")
        if len(filas) > args.top:
            print(f"    … y {len(filas) - args.top} más (subí --top para verlas)")
        tot_ar = sum(float(ops[i]["arancel"] or 0) for i in entran)
        print(f"\n    Arancel del mes que aportan las que entran: {_plata(tot_ar)}")
        print()

    # ── SALEN: si hay alguna, el cambio PIERDE información y hay que mirarlo
    #    antes de tocar nada. La causa esperable es un boleto sin id_cuenta.
    if salen:
        print(f"  {'─' * 74}")
        print("  ⚠ SALEN — estas cuentas HOY cuentan y con el criterio nuevo NO.\n"
              "    Revisar antes de aplicar: lo esperable es 0.\n")
        print(f"    {'CUENTA':<44}  CATEGORÍAS QUE TENÍA")
        for idc in sorted(salen)[:args.top]:
            u = universo[idc]
            nom = f"[{idc}] {(u.get('denominacion') or '—')}"[:44]
            cats = sorted((nm.get(idc) or {}).get("cats") or [])
            print(f"    {nom:<44}  {', '.join(c for c in cats if c)[:60]}")
        if len(salen) > args.top:
            print(f"    … y {len(salen) - args.top} más")
        print()
    else:
        print("  ✔ No sale ninguna cuenta: el cambio solo SUMA.\n")

    # ── Impacto por operador: el ranking del informe se ordena por acá.
    def _por_operador(s: set[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for idc in s:
            k = (universo[idc].get("operador_nombre")
                 or universo[idc].get("operador_email") or "(sin operador)")
            out[k] = out.get(k, 0) + 1
        return out

    a, b = _por_operador(hoy_set), _por_operador(nuevo_set)
    cambian = {k: (a.get(k, 0), b.get(k, 0)) for k in set(a) | set(b)
               if a.get(k, 0) != b.get(k, 0)}
    if cambian:
        print(f"  {'─' * 74}")
        print("  IMPACTO POR OPERADOR (solo los que cambian)\n")
        print(f"    {'OPERADOR':<40} {'HOY':>6} {'NUEVO':>7} {'DIF':>6}")
        for k, (v0, v1) in sorted(cambian.items(), key=lambda x: -(x[1][1] - x[1][0])):
            print(f"    {k[:40]:<40} {v0:>6} {v1:>7} {v1 - v0:>+6}")
        print()

    # ── El caveat: OTC que van a seguir sin aparecer. `operaciones` no ingesta
    #    los NDF OTC ni las Opciones OTC (decisión 2026-06-08) → una cuenta que
    #    solo opere eso no aparece con NINGUNO de los dos criterios.
    otc_universo = {i for i, u in universo.items()
                    if any(m in (u.get("denominacion") or "").upper() for m in _MARCAS_OTC)}
    otc_sin = sorted(otc_universo - nuevo_set)
    print(f"  {'─' * 74}")
    print(f"  OTC/CDC en el universo: {len(otc_universo)}  ·  "
          f"entran con el criterio nuevo: {len(otc_universo & nuevo_set)}  ·  "
          f"siguen sin aparecer: {len(otc_sin)}")
    if otc_sin:
        print("\n    Sin boletos en `operaciones` este mes. Puede ser que no operaron,\n"
              "    o que solo operaron NDF OTC / Opciones OTC (no se ingestan).\n")
        for idc in otc_sin[:args.top]:
            print(f"      [{idc}] {(universo[idc].get('denominacion') or '—')[:60]}")
        if len(otc_sin) > args.top:
            print(f"      … y {len(otc_sin) - args.top} más")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
