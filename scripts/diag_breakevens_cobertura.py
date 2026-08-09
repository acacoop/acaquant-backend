"""scripts/diag_breakevens_cobertura.py — ¿por qué un bono NO aparece en BREAKEVENS?

READ-ONLY (no escribe nada). Responde, con datos de prod, las tres preguntas que
hoy no se pueden contestar desde la vista:

  1. ¿Está FRESCO?      → última fila de `BreakevensHistorico` (fecha + updated_at)
                           y cuántos pares traen BE calculado vs. cuántos quedaron
                           sin número.
  2. ¿Está COMPLETO?    → replica `engines/breakevens.cargar_pares()` guardando el
                           MOTIVO de cada descarte. Un bono nuevo que no aparece cae
                           SIEMPRE en uno de estos: no está en `mercado.curvas`, no
                           tiene par CER dentro de ±20 días, se lo robó otro par en el
                           dedup, o lo filtró el plazo/IPC.
  3. ¿Está AL DÍA el master? → el vto y la emisión más recientes de `mercado.curvas`
                           por curva. Si la última Lecap del master venció hace meses,
                           el problema no es el motor: es que nadie dio de alta las nuevas.

El motor calcula el BE, pero la vista pública además FILTRA los pares excluidos a
mano en Manager → TÍTULOS → BREAKEVENS: eso también se lista acá, porque un par
"que no aparece" puede estar simplemente apagado.

Uso:
    python -m scripts.diag_breakevens_cobertura
    python -m scripts.diag_breakevens_cobertura --sin-mercado   # no lee precios (más rápido)
"""
from __future__ import annotations

import argparse
from datetime import date

from engines.breakevens import MAX_DIFF_DIAS, MIN_DIAS_PLAZO


def _vto(doc: dict) -> date | None:
    v = doc.get("fecha_vencimiento")
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _mes_inflacion(fecha_vto: date) -> str:
    """Mismo cálculo que el motor: el BE de un par que vence en M pricea el IPC de M−2."""
    y, m = fecha_vto.year, fecha_vto.month - 2
    if m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


# ── 1. Inventario del master ─────────────────────────────────────────────────

def inventario(grupos: dict[str, list[dict]], hoy: date) -> None:
    print("=" * 78)
    print("1. MASTER `mercado.curvas` — de acá salen los candidatos")
    print("=" * 78)
    print(f"{'curva':<14} {'n':>4}  {'vto más lejano':<14} {'emisión más nueva':<18}")
    print("-" * 78)
    for curva in sorted(grupos):
        docs = grupos[curva]
        vtos = [v for v in (_vto(d) for d in docs) if v]
        emis = sorted(str(d.get("fecha_emision"))[:10] for d in docs if d.get("fecha_emision"))
        print(f"{curva:<14} {len(docs):>4}  "
              f"{(max(vtos).isoformat() if vtos else '--'):<14} "
              f"{(emis[-1] if emis else '--'):<18}")

    for curva in ("tasa_fija", "cer"):
        docs = sorted(grupos.get(curva, []), key=lambda d: str(d.get("fecha_vencimiento") or ""))
        print(f"\n  ── {curva} ({len(docs)}) — campos que el BE necesita")
        print(f"  {'ticker':<10} {'vto':<12} {'días':>5}  {'flujo_vto':>12} {'cer_emision':>12} {'VN':>7}")
        for d in docs:
            v = _vto(d)
            dias = (v - hoy).days if v else None
            print(f"  {d.get('ticker_corto') or '?'!s:<10} "
                  f"{(v.isoformat() if v else '--'):<12} "
                  f"{(dias if dias is not None else '--'):>5}  "
                  f"{d.get('flujo_vencimiento') or '--'!s:>12} "
                  f"{d.get('cer_emision') or '--'!s:>12} "
                  f"{d.get('valor_nominal') or '--'!s:>7}")


# ── 2. Emparejamiento con motivos de descarte ────────────────────────────────

def emparejar(grupos: dict[str, list[dict]], hoy: date, ipc_mes: str | None) -> list[dict]:
    """Replica `cargar_pares()` + los filtros de `calcular_breakevens()`, pero
    guardando por qué se cayó cada Lecap/Boncap en vez de descartarlo en silencio."""
    lecaps = grupos.get("tasa_fija", [])
    cers = grupos.get("cer", [])

    filas: list[dict] = []
    for lecap in lecaps:
        v_lec = _vto(lecap)
        fila = {"lecap": lecap.get("ticker_corto"), "vto": v_lec, "cer": None,
                "diff": None, "estado": "", "motivo": ""}
        if v_lec is None:
            fila["estado"], fila["motivo"] = "FUERA", "sin fecha_vencimiento válida"
            filas.append(fila)
            continue

        mejor, mejor_diff = None, None
        for cer in cers:
            v_cer = _vto(cer)
            if v_cer is None:
                continue
            diff = abs((v_lec - v_cer).days)
            if mejor_diff is None or diff < mejor_diff:
                mejor, mejor_diff = cer, diff

        if mejor is None:
            fila["estado"] = "FUERA"
            fila["motivo"] = "no hay NINGÚN CER en el master"
        elif mejor_diff > MAX_DIFF_DIAS:
            fila["estado"] = "FUERA"
            fila["cer"] = mejor.get("ticker_corto")
            fila["diff"] = mejor_diff
            fila["motivo"] = (f"CER más cercano a {mejor_diff}d "
                              f"(tolerancia ±{MAX_DIFF_DIAS}d)")
        else:
            fila["cer"] = mejor.get("ticker_corto")
            fila["diff"] = mejor_diff
            fila["cer_emision"] = mejor.get("cer_emision")
            fila["vn_cer"] = mejor.get("valor_nominal") or 100
            fila["flujo_vto"] = lecap.get("flujo_vencimiento")
            fila["lecap_ticker"] = lecap.get("ticker")
            fila["cer_ticker"] = mejor.get("ticker")
            fila["estado"] = "PAR"
        filas.append(fila)

    # Dedup del motor: un CER solo puede estar en UN par (gana la menor diff).
    # OJO — el motor NO le busca al perdedor el segundo CER más cercano: lo tira.
    mejor_por_cer: dict[str, dict] = {}
    for f in filas:
        if f["estado"] != "PAR":
            continue
        c = f["cer"]
        if c not in mejor_por_cer or f["diff"] < mejor_por_cer[c]["diff"]:
            mejor_por_cer[c] = f
    ganadores = {id(f) for f in mejor_por_cer.values()}
    for f in filas:
        if f["estado"] == "PAR" and id(f) not in ganadores:
            f["estado"] = "FUERA"
            f["motivo"] = (f"dedup: el CER {f['cer']} se lo quedó "
                           f"{mejor_por_cer[f['cer']]['lecap']} (diff menor)")

    # Filtros que aplica calcular_breakevens() sobre los pares que sí sobrevivieron.
    for f in filas:
        if f["estado"] != "PAR":
            continue
        dias = (f["vto"] - hoy).days
        f["dias"] = dias
        f["mes_inflacion"] = _mes_inflacion(f["vto"])
        if dias < MIN_DIAS_PLAZO:
            f["estado"] = "FILTRADO"
            f["motivo"] = f"vto a {dias}d (mínimo {MIN_DIAS_PLAZO}d)"
        elif ipc_mes and f["mes_inflacion"] <= ipc_mes:
            f["estado"] = "FILTRADO"
            f["motivo"] = (f"pricea el IPC {f['mes_inflacion']}, "
                           f"ya publicado (último: {ipc_mes})")

    return filas


def imprimir_pares(filas: list[dict], cers: list[dict]) -> None:
    print()
    print("=" * 78)
    print("2. EMPAREJAMIENTO — un Lecap/Boncap por fila, con el motivo si no entra")
    print("=" * 78)
    print(f"  tolerancia ±{MAX_DIFF_DIAS}d entre vtos · plazo mínimo {MIN_DIAS_PLAZO}d")
    print()
    print(f"{'lecap':<10} {'vto':<12} {'CER':<10} {'diff':>5} {'estado':<10} motivo")
    print("-" * 78)
    for f in sorted(filas, key=lambda x: (x["vto"] or date.max)):
        print(f"{f['lecap'] or '?'!s:<10} "
              f"{(f['vto'].isoformat() if f['vto'] else '--'):<12} "
              f"{f['cer'] or '--'!s:<10} "
              f"{(f['diff'] if f['diff'] is not None else '--'):>5} "
              f"{f['estado']:<10} {f['motivo']}")

    n_par = sum(1 for f in filas if f["estado"] == "PAR")
    print(f"\n  → {n_par} pares vivos de {len(filas)} bonos tasa_fija en el master.")

    usados = {f["cer"] for f in filas if f["estado"] == "PAR"}
    libres = [d.get("ticker_corto") for d in cers if d.get("ticker_corto") not in usados]
    if libres:
        print(f"  → CER sin par: {', '.join(sorted(str(x) for x in libres))}")


# ── 3. Estado del dato publicado ─────────────────────────────────────────────

def estado_publicado() -> None:
    print()
    print("=" * 78)
    print("3. LO QUE VE LA VISTA — última fila de BreakevensHistorico")
    print("=" * 78)
    from api.services.mercado_hist_sql import breakevens_docs_raw
    docs = breakevens_docs_raw()
    if not docs:
        print("  ⚠ No hay NINGUNA fila de BreakevensHistorico. El motor nunca escribió.")
        return
    doc = docs[0]
    pares = doc.get("pares") or []
    con_be = [p for p in pares if p.get("breakeven_mensual") is not None]
    metodos: dict[str, int] = {}
    for p in con_be:
        metodos[p.get("metodo") or "?"] = metodos.get(p.get("metodo") or "?", 0) + 1

    print(f"  fecha del doc : {doc.get('fecha')}")
    print(f"  updated_at    : {doc.get('updated_at')}")
    print(f"  pares         : {len(pares)}  (con BE calculado: {len(con_be)})")
    print(f"  métodos       : {metodos or '--'}")
    if len(con_be) < len(pares):
        print("\n  Pares SIN número (el motor los emparejó pero no pudo calcular):")
        for p in pares:
            if p.get("breakeven_mensual") is None:
                falta = [k for k in ("precio_lecap", "precio_cer", "meses_pendientes")
                         if not p.get(k)]
                print(f"    {p.get('lecap'):<10} ↔ {p.get('cer'):<10} "
                      f"falta: {', '.join(falta) or 'revisar rango del BE'}")

    from api.services.breakevens_admin import get_excluidos
    excl = get_excluidos()
    print(f"\n  Excluidos a mano en Manager: {len(excl)}")
    for lecap, cer in sorted(excl):
        print(f"    {lecap} ↔ {cer}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sin-mercado", action="store_true",
                    help="omite la sección 3 (no lee el doc publicado)")
    args = ap.parse_args()

    hoy = date.today()
    from core import curvas_sql
    from engines.breakevens import ultimo_ipc_publicado

    grupos = curvas_sql.agrupado_por_curva()
    ipc_mes = ultimo_ipc_publicado()

    print(f"\nHOY: {hoy.isoformat()}   ·   último IPC publicado: {ipc_mes or '--'}\n")

    inventario(grupos, hoy)
    filas = emparejar(grupos, hoy, ipc_mes)
    imprimir_pares(filas, grupos.get("cer", []))
    if not args.sin_mercado:
        estado_publicado()
    print()


if __name__ == "__main__":
    main()
