"""Diag read-only: ¿se puede alimentar CONTABILIDAD desde `movimientos_propias`?

El paso previo obligatorio a cambiarle la FUENTE al informe contable. Hoy las
compras/ventas salen de `operaciones.operaciones` (importe = `bruto`, título =
`instrumento`, punta = catálogo `tipos_operacion`, plazo = `condiciones`). Pasar
a `operaciones.movimientos_propias` cambia LAS CUATRO cosas a la vez, y cada una
puede romper el informe **sin que falle nada**: una fila que no cruza no explota,
cae en «sin conciliar» y el mes queda en cero con la pantalla en verde.

Seis preguntas que NO se pueden suponer (REGLA #2). Las dos primeras son
BLOQUEANTES: si cualquiera da mal, el cambio no se hace como está pensado.

  1. **LAS CUENTAS.** Las del ABM de hoy contra las que existen de verdad en
     `movimientos_propias`. Si no se pisan, restringir el ABM deja la vista sin
     cuentas.
  2. **EL JOIN DEL TÍTULO.** `movimientos_propias.unidad` de una línea de
     TÍTULO, ¿es la misma `unidad` de `portafolio.assets` que usa el mapping
     `unidad → clave`? Si no, NINGUNA fila cruza contra la tenencia y TODO el
     informe cae en «sin conciliar». Se mide contra la MISMA vara que la fuente
     actual (`operaciones.instrumento`), así el número se puede comparar.
  3. **EL IMPORTE.** Filtrando a líneas de título queda la CANTIDAD, pero la
     PLATA vive en la línea de dinero del mismo comprobante (`total` de la línea
     de título son los NOMINALES, no pesos). Se cuenta cuántas líneas de título
     tienen hermana de dinero, cuántas no (= las administrativas, que el user
     declaró VÁLIDAS: mueven cantidad y no llevan importe) y si `cantidad ×
     precio` reproduce ese importe o hace falta el divisor de paridad.
  4. **LA COBERTURA.** Los comprobantes que ve cada fuente para la misma cuenta
     y mes: cuántos comparten, cuántos aparecen SOLO en la nueva y cuántos SOLO
     en la vieja. Cambiar de fuente sin este número es no saber qué se pierde.
  5. **LA PUNTA.** Qué `categoria` traen las líneas de título y cuántas resuelven
     compra/venta con las constantes que ya usa el informe.
  6. **EL PLAZO.** Las grafías de `plazo` pasadas por `plazo_habiles`: el corte
     de mes es por LIQUIDACIÓN, así que una grafía que no se entiende manda el
     boleto al mes equivocado en silencio. Más el mapa provisional/final del FCI,
     que hoy se empareja por `tipo_operacion` y en la tabla nueva no existe.

NO escribe nada: solo SELECT.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_contabilidad_propias --mes 2026-08
    python -m scripts.diag_contabilidad_propias --mes 2026-08 --cuenta 1839
    python -m scripts.diag_contabilidad_propias --mes 2026-08 --top 25
"""
from __future__ import annotations

import argparse
from collections import Counter

from api.services._sql import _f, _q
from api.services.contabilidad_sql import (
    _CATS_COMPRA,
    _CATS_VENTA,
    _clasificar,
    plazo_habiles,
)

# Las que el user marcó como NO-título: la línea de dinero del boleto.
MONEDAS = ("ARS", "USD", "USDL", "USDC")
_TIT = "upper(btrim(COALESCE(unidad,''))) <> ALL(%(mon)s)"


def _hdr(n: int, t: str) -> None:
    print(f"\n{'='*78}\n{n}. {t}\n{'='*78}")


# ── 1. las cuentas ───────────────────────────────────────────────────────────
def cuentas(mes: str) -> list[str]:
    _hdr(1, "LAS CUENTAS — el ABM de hoy contra lo que existe en movimientos_propias")
    abm = {r["id_cuenta"]: r["etiqueta"] for r in
           _q("SELECT id_cuenta, etiqueta FROM operaciones.contabilidad_cuentas")}
    reales = _q(
        "SELECT id_cuenta, max(cuenta) AS cuenta, count(*) AS n, "
        "       count(*) FILTER (WHERE " + _TIT + ") AS n_titulos, "
        "       min(fecha) AS desde, max(fecha) AS hasta "
        "  FROM operaciones.movimientos_propias "
        " WHERE anulado_en IS NULL AND id_cuenta IS NOT NULL "
        " GROUP BY id_cuenta ORDER BY count(*) DESC", {"mon": list(MONEDAS)})
    print(f"\n  ABM actual (operaciones.contabilidad_cuentas): {len(abm)}")
    for c, e in sorted(abm.items()):
        marca = "✔ está en propias" if any(r["id_cuenta"] == c for r in reales) else "❌ NO está"
        print(f"    [{c:>6}] {str(e)[:42]:<42} {marca}")
    print(f"\n  En movimientos_propias: {len(reales)} cuentas")
    for r in reales:
        marca = "✔ en el ABM" if r["id_cuenta"] in abm else "→ NUEVA (hoy no está en el ABM)"
        print(f"    [{r['id_cuenta']:>6}] {str(r['cuenta'] or '')[:38]:<38} "
              f"{r['n']:>6} líneas ({r['n_titulos']:>5} de título) "
              f"{r['desde']}..{r['hasta']}  {marca}")
    solo_abm = sorted(set(abm) - {r["id_cuenta"] for r in reales})
    print()
    if solo_abm:
        print(f"  🚨 {len(solo_abm)} cuenta(s) del ABM NO aparecen en movimientos_propias: "
              f"{solo_abm}\n     Restringir el ABM a la tabla nueva las SACA del informe. "
              "Confirmar antes.")
    else:
        print("  ✅ Todas las del ABM existen en movimientos_propias: restringir no saca ninguna.")
    return [r["id_cuenta"] for r in reales]


# ── 2. el join del título (BLOQUEANTE) ───────────────────────────────────────
def join_titulo(ids: list[str], mes: str, top: int) -> None:
    _hdr(2, "EL JOIN DEL TÍTULO — ¿`unidad` cruza contra portafolio.assets?")
    from api.services.pnl_sql import _mapas_assets
    u2m = _mapas_assets()["unidad_to_match"]
    print(f"\n  catálogo `portafolio.assets`: {len(u2m)} unidades mapeadas a una clave")

    nuevas = _q(
        "SELECT unidad, count(*) AS n FROM operaciones.movimientos_propias "
        " WHERE anulado_en IS NULL AND id_cuenta = ANY(%(ids)s) "
        f"   AND to_char(fecha,'YYYY-MM') = %(mes)s AND {_TIT} "
        " GROUP BY unidad ORDER BY count(*) DESC",
        {"ids": ids, "mes": mes, "mon": list(MONEDAS)})
    viejas = _q(
        "SELECT instrumento AS unidad, count(*) AS n FROM operaciones.operaciones "
        " WHERE anulado_en IS NULL AND id_cuenta = ANY(%(ids)s) "
        "   AND to_char(concertacion,'YYYY-MM') = %(mes)s "
        "   AND etapa IS DISTINCT FROM 'solicitud' AND COALESCE(es_cierre,false) = false "
        " GROUP BY instrumento ORDER BY count(*) DESC", {"ids": ids, "mes": mes})

    for etiqueta, filas in (("movimientos_propias (líneas de TÍTULO)", nuevas),
                            ("operaciones.operaciones (fuente ACTUAL)", viejas)):
        tot = sum(r["n"] for r in filas)
        ok = sum(r["n"] for r in filas if (r["unidad"] or "") in u2m)
        pct = f"{ok*100//tot}%" if tot else "—"
        print(f"\n  {etiqueta}")
        print(f"    {len(filas)} unidades distintas · {tot} líneas · "
              f"{ok} cruzan el catálogo ({pct})")
        huerfanas = [r for r in filas if (r["unidad"] or "") not in u2m]
        if huerfanas:
            print(f"    ❌ {len(huerfanas)} unidad(es) SIN clave — estas filas caen en «sin "
                  "conciliar»:")
            for r in huerfanas[:top]:
                print(f"       {r['n']:>5}  {str(r['unidad'])[:66]!r}")
            if len(huerfanas) > top:
                print(f"       … y {len(huerfanas)-top} más")
    print()
    tn = sum(r["n"] for r in nuevas); on = sum(r["n"] for r in nuevas if (r["unidad"] or "") in u2m)
    tv = sum(r["n"] for r in viejas); ov = sum(r["n"] for r in viejas if (r["unidad"] or "") in u2m)
    if tn and on == 0:
        print("  🚨 VEREDICTO: NINGUNA línea de título cruza el catálogo. La `unidad` de "
              "movimientos_propias\n     NO es la de `portafolio.assets` — hace falta un "
              "mapeo nuevo (¿por el ticker parseado?)\n     ANTES de tocar el informe. "
              "Como está, el mes entero saldría «sin conciliar».")
    elif tn and tv and (on * 100 // tn) < (ov * 100 // max(tv, 1)) - 5:
        print(f"  ⚠️  VEREDICTO: la fuente nueva cruza PEOR ({on*100//tn}% vs {ov*100//tv}%). "
              "Migrar así\n     empeora el cuadre. Ver las unidades huérfanas de arriba.")
    elif tn:
        print(f"  ✅ VEREDICTO: cruza {on*100//tn}% (la fuente actual, {ov*100//max(tv,1)}%). "
              "El mapping sirve tal cual.")
    else:
        print("  ⚠️  No hay líneas de título en ese mes para esas cuentas: probá otro --mes.")


# ── 3. el importe (BLOQUEANTE) ───────────────────────────────────────────────
def importe(ids: list[str], mes: str, top: int) -> None:
    _hdr(3, "EL IMPORTE — la plata NO está en la línea de título")
    filas = _q(
        "SELECT t.comprobante, t.unidad, t.categoria, t.cantidad, t.precio, "
        "       t.importe AS importe_titulo, t.informacion, "
        "       (SELECT count(*) FROM operaciones.movimientos_propias d "
        "         WHERE d.fecha = t.fecha AND d.comprobante = t.comprobante "
        f"          AND d.anulado_en IS NULL AND NOT ({_TIT.replace('unidad', 'd.unidad')})) "
        "         AS n_dinero, "
        "       (SELECT sum(d.importe) FROM operaciones.movimientos_propias d "
        "         WHERE d.fecha = t.fecha AND d.comprobante = t.comprobante "
        f"          AND d.anulado_en IS NULL AND NOT ({_TIT.replace('unidad', 'd.unidad')})) "
        "         AS importe_dinero "
        "  FROM operaciones.movimientos_propias t "
        " WHERE t.anulado_en IS NULL AND t.id_cuenta = ANY(%(ids)s) "
        f"   AND to_char(t.fecha,'YYYY-MM') = %(mes)s AND {_TIT.replace('unidad', 't.unidad')} "
        " ORDER BY t.fecha, t.comprobante",
        {"ids": ids, "mes": mes, "mon": list(MONEDAS)})
    if not filas:
        print("\n  (sin líneas de título en el mes)")
        return
    con_dinero = [r for r in filas if (r["n_dinero"] or 0) > 0]
    sin_dinero = [r for r in filas if not (r["n_dinero"] or 0)]
    print(f"\n  Líneas de TÍTULO: {len(filas)}")
    print(f"    con línea de dinero hermana (mismo comprobante): {len(con_dinero)}")
    print(f"    SIN línea de dinero  → mueven CANTIDAD y no llevan plata: {len(sin_dinero)}")
    print(f"    con `precio` cargado: {sum(1 for r in filas if _f(r['precio']))}")
    print(f"    con `cantidad` cargada: {sum(1 for r in filas if _f(r['cantidad']))}")

    print(f"\n  ¿`cantidad × precio` reproduce el importe de la hermana? (muestra de {top})")
    print(f"    {'comprobante':<16}{'cant×precio':>16}{'/100':>16}{'dinero':>16}  cuál pega")
    pega_directo = pega_cien = ninguno = 0
    for r in con_dinero:
        q, px = _f(r["cantidad"]) or 0.0, _f(r["precio"]) or 0.0
        din = abs(_f(r["importe_dinero"]) or 0.0)
        directo, cien = abs(q * px), abs(q * px / 100)
        cual = ("directo" if din and abs(directo - din) / din < 0.02 else
                "÷100" if din and abs(cien - din) / din < 0.02 else "ninguno")
        pega_directo += cual == "directo"; pega_cien += cual == "÷100"; ninguno += cual == "ninguno"
        if pega_directo + pega_cien + ninguno <= top:
            print(f"    {str(r['comprobante'])[:15]:<16}{directo:>16,.2f}{cien:>16,.2f}"
                  f"{din:>16,.2f}  {cual}")
    print(f"\n    directo: {pega_directo} · ÷100 (paridad): {pega_cien} · ninguno: {ninguno}")
    if ninguno > max(pega_directo, pega_cien):
        print("    🚨 Ni `cantidad × precio` ni ÷100 reproducen la plata → el importe TIENE "
              "que salir\n       de la línea de dinero hermana, no calcularse. (Es lo que "
              "recomiendo igual.)")
    elif pega_cien and pega_directo:
        print("    ⚠️  Conviven las dos escalas (paridad y directo) → calcular el importe "
              "exigiría\n       el divisor por CARTERA. Leer la hermana lo evita entero.")

    if sin_dinero:
        print(f"\n  Los ADMINISTRATIVOS (sin plata, con cantidad) — {len(sin_dinero)}, "
              f"top {top} por texto:")
        for t, n in Counter(str(r["informacion"] or "")[:70] for r in sin_dinero).most_common(top):
            print(f"    {n:>5}  {t}")


# ── 4. cobertura contra la fuente actual ─────────────────────────────────────
def cobertura(ids: list[str], mes: str, top: int) -> None:
    _hdr(4, "LA COBERTURA — qué comprobantes ve cada fuente")
    nueva = {str(r["comprobante"]): r for r in _q(
        "SELECT comprobante, count(*) AS n, max(informacion) AS info "
        "  FROM operaciones.movimientos_propias "
        " WHERE anulado_en IS NULL AND id_cuenta = ANY(%(ids)s) "
        f"   AND to_char(fecha,'YYYY-MM') = %(mes)s AND {_TIT} AND comprobante IS NOT NULL "
        " GROUP BY comprobante", {"ids": ids, "mes": mes, "mon": list(MONEDAS)})}
    vieja = {str(r["boleto"]): r for r in _q(
        "SELECT boleto, max(tipo_operacion) AS tipo, max(instrumento) AS instrumento "
        "  FROM operaciones.operaciones "
        " WHERE anulado_en IS NULL AND id_cuenta = ANY(%(ids)s) "
        "   AND to_char(concertacion,'YYYY-MM') = %(mes)s "
        "   AND etapa IS DISTINCT FROM 'solicitud' AND COALESCE(es_cierre,false) = false "
        "   AND boleto IS NOT NULL GROUP BY boleto", {"ids": ids, "mes": mes})}
    print(f"\n  movimientos_propias (títulos): {len(nueva)} comprobantes")
    print(f"  operaciones.operaciones      : {len(vieja)} boletos")
    # Los IDs pueden traer prefijo ("BOL 2026069919" vs "2026069919"): se compara
    # también por la parte numérica, si no el solape daría 0 por una convención.
    def _num(s: str) -> str:
        return "".join(ch for ch in s if ch.isdigit())
    n_num = {_num(k): k for k in nueva if _num(k)}
    v_num = {_num(k): k for k in vieja if _num(k)}
    comunes = set(n_num) & set(v_num)
    print("\n  comparando por la parte NUMÉRICA del id:")
    print(f"    en las DOS        : {len(comunes)}")
    print(f"    SOLO en propias   : {len(n_num) - len(comunes)}  ← los que hoy el informe NO ve")
    print(f"    SOLO en operaciones: {len(v_num) - len(comunes)}  ← los que se PERDERÍAN")
    solo_v = sorted(set(v_num) - comunes)[:top]
    if solo_v:
        print(f"\n    Muestra de los que se perderían (top {top}):")
        for k in solo_v:
            r = vieja[v_num[k]]
            print(f"      {v_num[k]:<16} {str(r['tipo'])[:30]:<30} {str(r['instrumento'])[:34]}")
    solo_n = sorted(set(n_num) - comunes)[:top]
    if solo_n:
        print(f"\n    Muestra de los que se ganarían (top {top}):")
        for k in solo_n:
            print(f"      {n_num[k]:<16} {str(nueva[n_num[k]]['info'])[:64]}")


# ── 5. la punta ──────────────────────────────────────────────────────────────
def punta(ids: list[str], mes: str, top: int) -> None:
    _hdr(5, "LA PUNTA — qué categorías traen las líneas de título")
    filas = _q(
        "SELECT categoria, op, count(*) AS n FROM operaciones.movimientos_propias "
        " WHERE anulado_en IS NULL AND id_cuenta = ANY(%(ids)s) "
        f"   AND to_char(fecha,'YYYY-MM') = %(mes)s AND {_TIT} "
        " GROUP BY categoria, op ORDER BY count(*) DESC",
        {"ids": ids, "mes": mes, "mon": list(MONEDAS)})
    tot = sum(r["n"] for r in filas)
    resuelve = sum(r["n"] for r in filas
                   if r["categoria"] in _CATS_COMPRA or r["categoria"] in _CATS_VENTA)
    print(f"\n  {tot} líneas de título · {resuelve} resuelven compra/venta con las "
          f"constantes del informe ({resuelve*100//tot if tot else 0}%)")
    print(f"\n    {'categoria':<26}{'op':<40}{'n':>6}  punta")
    for r in filas[:top * 2]:
        cat = r["categoria"] or "(null)"
        # La MISMA función que usa el informe: si acá dice `ajuste`, en el
        # informe mueve nominales y no plata.
        p = _clasificar(cat if cat != "(null)" else "", 1.0, None if cat else 1.0) or "—"
        if cat in _CATS_COMPRA:
            p = "compra"
        elif cat in _CATS_VENTA:
            p = "venta"
        print(f"    {cat[:25]:<26}{str(r['op'] or '')[:39]:<40}{r['n']:>6}  {p}")


# ── 6. el plazo y el FCI ─────────────────────────────────────────────────────
def plazo_y_fci(ids: list[str], mes: str, top: int) -> None:
    _hdr(6, "EL PLAZO (corte de mes) y el FCI provisional/final")
    filas = _q(
        "SELECT plazo, count(*) AS n FROM operaciones.movimientos_propias "
        " WHERE anulado_en IS NULL AND id_cuenta = ANY(%(ids)s) "
        f"   AND to_char(fecha,'YYYY-MM') = %(mes)s AND {_TIT} "
        " GROUP BY plazo ORDER BY count(*) DESC",
        {"ids": ids, "mes": mes, "mon": list(MONEDAS)})
    print("\n  Grafías de `plazo` pasadas por plazo_habiles() — 0 = contado inmediato:")
    sospechosas = 0
    for r in filas[:top * 2]:
        g = r["plazo"]
        d = plazo_habiles(g)
        aviso = ""
        if d == 0 and g and "inm" not in str(g).lower() and "contado" not in str(g).lower():
            aviso = "  ⚠️ cae en 0 sin decir 'contado': el corte de mes puede estar mal"
            sospechosas += r["n"]
        print(f"    {str(g if g is not None else '(null)')[:28]:<30}{r['n']:>6} → "
              f"{d} hábil(es){aviso}")
    if sospechosas:
        print(f"\n  🚨 {sospechosas} líneas con una grafía que plazo_habiles NO entiende. "
              "El boleto\n     se imputa al mes de CONCERTACIÓN y puede caer en el mes "
              "equivocado, en silencio.")
    else:
        print("\n  ✅ Todas las grafías se entienden o dicen contado explícitamente.")

    print("\n  FCI — cómo se ve el par provisional/final en esta tabla:")
    fci = _q(
        "SELECT categoria, op, count(*) AS n FROM operaciones.movimientos_propias "
        " WHERE anulado_en IS NULL AND id_cuenta = ANY(%(ids)s) "
        f"   AND to_char(fecha,'YYYY-MM') = %(mes)s AND {_TIT} "
        "   AND (categoria ILIKE '%%fci%%' OR op ILIKE '%%suscri%%' OR op ILIKE '%%rescate%%') "
        " GROUP BY categoria, op ORDER BY count(*) DESC",
        {"ids": ids, "mes": mes, "mon": list(MONEDAS)})
    if not fci:
        print("    (sin FCI en el mes — no se puede concluir; probá otro mes)")
    for r in fci[:top]:
        print(f"    {str(r['categoria'])[:26]:<28}{str(r['op'] or '')[:40]:<42}{r['n']:>5}")
    print("\n    ⚠️ El emparejador actual busca 'provisional'/'final' en `tipo_operacion`, "
          "que\n       esta tabla NO tiene. Si arriba conviven `solicitud_*_fci` y "
          "`*_fci`, el mismo\n       movimiento entra DOS veces y el mes se duplica.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mes", required=True, help="YYYY-MM a auditar")
    p.add_argument("--cuenta", help="id_cuenta; default: todas las de movimientos_propias")
    p.add_argument("--top", type=int, default=15)
    args = p.parse_args()

    print(f"DIAG CONTABILIDAD ← movimientos_propias · mes {args.mes}")
    ids = cuentas(args.mes)
    if args.cuenta:
        ids = [args.cuenta]
        print(f"\n(acotado a --cuenta {args.cuenta})")
    if not ids:
        print("\n→ sin cuentas que auditar.")
        return 2
    join_titulo(ids, args.mes, args.top)
    importe(ids, args.mes, args.top)
    cobertura(ids, args.mes, args.top)
    punta(ids, args.mes, args.top)
    plazo_y_fci(ids, args.mes, args.top)
    print(f"\n{'='*78}\nLISTO. Pegá la salida y decidimos con números: si el join cruza, de "
          f"dónde sale\nel importe, qué comprobantes se pierden y cómo se corta el mes.\n{'='*78}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
