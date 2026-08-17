"""¿Cuántos hallazgos de TASA se arreglan SIN preguntarle a 1816?

Nace del planteo del user (2026-08-17): *«hay casos como PARIDAD FUERA DE RANGO
que NO necesitan ir a 1816 a consultar — hay casos donde este agente podría
debuguear internamente, ya que hay precio y ya está el flujo cargado»*.

Tiene razón, y este script es lo que hay que medir ANTES de rediseñar nada
(REGLA #2). Hoy el agente sabe hacer **un solo arreglo** —traer el cronograma de
1816 y pisar los flujos— así que las 5 reglas de tasa pasan por la misma cadena y
las dos puertas de 1816 (`cuadro` y `precio`) la BLOQUEAN entera. La pregunta que
nadie contestó con números es **cuántos de los hallazgos de la última corrida se
resuelven con datos que YA tenemos en la base**.

⚠️ **NO ESCRIBE NADA Y NO GASTA UN SOLO CRÉDITO DE 1816.** Todo lo que lee está
en Postgres, incluido el catálogo de 1816 (`research.mkt_1816_instrumentos`, que
llena `jobs/mercado_1816_discovery --catalogo`). Se puede correr con la API de
1816 caída, un domingo, y en pleno rate limit — que es justo cuando importa.

Lo que imprime por bono:

  · los EJES y la RAMA (y si su tasa es EXTERNA, o sea que no la calculamos acá)
  · **Σ de las amortizaciones FUTURAS** del cuadro guardado — un cuadro sano ronda
    100 porque el bono cotiza por 100 de VN. Es la mitad de la división de la
    paridad, y sale sin red.
  · el PRECIO por sus tres fuentes locales, con su fecha: `market_snapshot` (live),
    `mercado.snapshots_cierre` (el cierre persistido) y la **evidencia congelada
    del propio hallazgo** — que hoy el modal tira a la basura y vuelve a leer en
    vivo, por eso fuera de rueda se queda sin nada que dividir.
  · las PATAS del bono (`mercado.especies`) contra la que tiene cargada la curva:
    la falla #4 del catálogo (`docs/SALUD_CURVAS.md`) es exactamente esto.
  · si tiene espejo en `portafolio.assets` y si tiene `cer_emision`.

Y al final, LA TABLA que decide el rediseño: cuántos hallazgos por CAUSA, y
cuántos de esos se arreglan sin salir a la red.

La clasificación es una SOSPECHA con la evidencia al lado, no un veredicto: el
script imprime los números para que la decisión la tome quien lee. Ningún renglón
de acá justifica pisar un dato por sí solo.

    python -m scripts.diag_av_agent_arreglos            # los hallazgos de tasa
    python -m scripts.diag_av_agent_arreglos --todos    # los 4 tipos
    python -m scripts.diag_av_agent_arreglos DHSGO CO3D7
"""

from __future__ import annotations

import sys

# Mismos límites que usa el diagnóstico local del agente. Se IMPORTAN en vez de
# copiarse: dos definiciones de "residual sano" terminarían diciendo cosas
# distintas sobre el mismo bono, que es el bug que este agente ya se comió tres
# veces (TZXM8, BADLAR, IGNORAR).
from api.services.av_agent_alta import _RESIDUAL_MAX, _RESIDUAL_MIN
from core.postgres import get_pool


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _n(v, dec: int = 2) -> str:
    """Un número o un guion. **El vacío es un dato**: «sin precio» es la mitad de
    lo que estamos midiendo, y mostrar 0,00 en su lugar lo escondería."""
    return "—" if not isinstance(v, int | float) else f"{float(v):,.{dec}f}"


# ── Lectura: pocas queries y grandes ─────────────────────────────────────────
#
# Cada roundtrip a Supabase paga ~8,5 ms de DISTANCIA (Droplet en nyc1 ↔ base en
# us-east-1), así que lo que importa es la CANTIDAD de queries, no su plan. Con
# 38 hallazgos, una query por bono serían 6 viajes × 38 = 228 roundtrips para un
# diagnóstico que se resuelve en 6.


def _hallazgos(tipos: tuple[str, ...], tickers: list[str]) -> tuple[list[dict], str]:
    """Los hallazgos de la ÚLTIMA corrida — la MISMA foto que muestra la pantalla.

    Se lee la corrida persistida y no se re-releva a propósito: relevar cuesta
    ~29 créditos, y además el objetivo es explicar las filas que el user está
    mirando, no otras parecidas.
    """
    sql = ("SELECT tipo, ticker, regla, severidad, motivo, evidencia, corrida_at "
           "FROM mercado.av_agent_hallazgos WHERE corrida_at = "
           "(SELECT max(corrida_at) FROM mercado.av_agent_hallazgos) "
           "AND tipo = ANY(%s)")
    params: list = [list(tipos)]
    if tickers:
        sql += " AND upper(btrim(ticker)) = ANY(%s)"
        params.append([t.strip().upper() for t in tickers])
    sql += " ORDER BY ticker, regla"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        filas = cur.fetchall()
    corrida = filas[0][6].isoformat() if filas else ""
    return [{"tipo": r[0], "ticker": r[1], "regla": r[2], "severidad": r[3],
             "motivo": r[4], "evidencia": r[5] or {}} for r in filas], corrida


def _snapshot(simbolos: list[str]) -> dict[str, dict]:
    """El live. `updated_at` viaja porque un precio viejo NO es lo mismo que uno
    de hoy, y la regla `tea_fuera_de_rango` sospecha justamente de eso (falla #5)."""
    if not simbolos:
        return {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, last_price, paridad, tea, duration, updated_at "
                    "FROM mercado.market_snapshot WHERE ticker = ANY(%s)", (simbolos,))
        return {r[0]: {"precio": _f(r[1]), "paridad": _f(r[2]), "tea": _f(r[3]),
                       "duration": _f(r[4]), "at": r[5]} for r in cur.fetchall()}


def _cierre(simbolos: list[str]) -> dict[str, dict]:
    """El cierre persistido — la fuente de precio LOCAL que el simulador no mira.

    ⚠️ Su upsert **solo avanza** y las ONs están FUERA del barrido (`CURVAS_V1` de
    `jobs/snapshot_cierre`), así que acá hay dos cosas distintas que el diag tiene
    que poder distinguir: que no haya fila, y que la haya con fecha vieja. Por eso
    la fecha se imprime SIEMPRE al lado del precio.
    """
    if not simbolos:
        return {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, last_price, fecha FROM mercado.snapshots_cierre "
                    "WHERE ticker = ANY(%s)", (simbolos,))
        return {r[0]: {"precio": _f(r[1]), "fecha": r[2]} for r in cur.fetchall()}


def _especies(tickers: list[str]) -> dict[str, list[dict]]:
    """Las PATAS de cada bono. Es la tabla que convierte «la paridad está rara» en
    «está cargada la pata equivocada», sin preguntarle nada a nadie."""
    if not tickers:
        return {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, simbolo, ticker_especie, especie, moneda, plazo, "
                    "es_default, activa, validado FROM mercado.especies "
                    "WHERE upper(btrim(ticker)) = ANY(%s) ORDER BY ticker, simbolo",
                    (tickers,))
        out: dict[str, list[dict]] = {}
        for r in cur.fetchall():
            out.setdefault((r[0] or "").strip().upper(), []).append(
                {"simbolo": r[1], "ticker_especie": r[2], "especie": r[3],
                 "moneda": r[4], "plazo": r[5], "default": r[6], "activa": r[7],
                 "validado": r[8]})
        return out


def _en_assets(tickers: list[str]) -> set[str]:
    if not tickers:
        return set()
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT upper(btrim(ticker)) FROM portafolio.assets "
                    "WHERE upper(btrim(ticker)) = ANY(%s)", (tickers,))
        return {r[0] for r in cur.fetchall()}


def _catalogo_1816(tickers: list[str]) -> dict[str, str]:
    """La curva que 1816 le asigna a cada bono, **desde la tabla local** (0
    créditos). Es lo que traduce a ejes con `curvas_ejes.desde_1816` — o sea que
    un `sin_ejes` se resuelve sin tocar la red, aunque hoy la cadena lo cuente
    como si dependiera de ella."""
    if not tickers:
        return {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT upper(btrim(ticker)), curva FROM "
                    "research.mkt_1816_instrumentos WHERE upper(btrim(ticker)) = ANY(%s)",
                    (tickers,))
        return {r[0]: (r[1] or "") for r in cur.fetchall()}


# ── La clasificación (la parte con criterio) ─────────────────────────────────
#
# UNA causa por hallazgo y en orden de aguas ARRIBA: si un bono no tiene cuadro,
# discutir su pata no tiene sentido. Cada causa dice qué arreglo le corresponde y
# —lo único que este script viene a contestar— si ese arreglo necesita la red.

# causa → (qué arreglo sería, ¿necesita 1816?)
ARREGLOS = {
    "sin_cuadro":       ("importar el cronograma de 1816", True),
    "sin_ejes":         ("escribir los ejes (la curva de 1816 ya está local)", False),
    "sin_ejes_sin_ficha": ("decidir los ejes a mano / ampliar EJES_1816", False),
    "escala_del_cuadro": ("reescalar las amortizaciones a base 100", False),
    "pata_equivocada":  ("apuntar `curvas.instrumento` a la pata correcta", False),
    "falta_cer":        ("cargar el `cer_emision` (dato manual)", False),
    "sin_espejo_assets": ("crear la fila en `portafolio.assets`", False),
    "tasa_externa":     ("ninguno: la tasa no la calculamos nosotros", False),
    "precio_sospechoso": ("verificar el precio (stale/ilíquido) o IGNORAR", False),
    "sin_precio":       ("ninguno hoy: no hay precio en ninguna fuente local", False),
    "sin_doc":          ("el ticker no está en mercado.curvas", False),
}


def _pata_cargada(simbolo: str, patas: list[dict]) -> dict | None:
    return next((p for p in patas if (p.get("simbolo") or "") == simbolo), None)


def _causa(*, doc: dict | None, hallazgo: dict, residual: float, n_fut: int,
           precio: float | None, patas: list[dict], en_assets: bool,
           curva_1816: str) -> tuple[str, str]:
    """Devuelve `(causa, detalle)`. **Sospecha con la evidencia al lado.**"""
    from api.services.acreencias import tiene_flujo_def
    from api.services.av_agent_alta import tasa_externa_de
    from core import curvas_ejes

    if not doc:
        return "sin_doc", "no está en mercado.curvas"

    from datetime import date
    if not tiene_flujo_def(doc, date.today()):
        return "sin_cuadro", "no hay cronograma cargado — esto SÍ lo tiene que traer 1816"

    ejes = curvas_ejes.ejes_de_doc(doc)
    if ejes is None:
        if curva_1816 and curvas_ejes.desde_1816(curva_1816):
            return "sin_ejes", f"1816 lo clasifica en «{curva_1816}» y eso YA traduce a ejes"
        return "sin_ejes_sin_ficha", (f"1816 dice «{curva_1816}»" if curva_1816
                                      else "1816 tampoco lo tiene en el catálogo local")

    regla = hallazgo["regla"]
    if regla == "sin_espejo_en_assets" and not en_assets:
        return "sin_espejo_assets", "la casa lo TIENE y no está en portafolio.assets"

    # La tasa EXTERNA se pregunta por el AJUSTE y no por la rama, que es el bug que
    # tiene hoy `_diagnostico_local`: un corporativo TAMAR devuelve rama `on`
    # (`rama_calculo` pregunta `corporativo` primero) y por eso el atajo «esto no lo
    # calculamos nosotros» nunca se dispara justo en el caso que lo motivó.
    fuente_tasa, job = tasa_externa_de(doc.get("ajuste"))
    externa = fuente_tasa == "1816"
    if externa and regla in ("sin_tea_con_precio", "tea_fuera_de_rango"):
        return "tasa_externa", f"ajuste «{doc.get('ajuste')}» — la tasa la trae {job or '1816'}"

    if residual and not (_RESIDUAL_MIN <= residual <= _RESIDUAL_MAX):
        return "escala_del_cuadro", (f"Σ amortizaciones futuras = {residual:,.2f} en "
                                     f"{n_fut} cupón/es (un cuadro sano ronda 100)")

    if doc.get("ajuste") == "cer" and not _f(doc.get("cer_emision")):
        return "falta_cer", "rama CER sin `cer_emision`: el cuadro no se puede escalar al VN"

    # El cuadro está sano → el que está en otra unidad es el PRECIO. Y de eso la
    # pata es la explicación #1 (falla #4 de docs/SALUD_CURVAS.md): un HD con la
    # pata peso cargada se divide por MEP y la paridad se va a cualquier lado.
    simbolo = (doc.get("ticker") or "").strip()
    cargada = _pata_cargada(simbolo, patas)
    moneda_eje = (doc.get("moneda_eje") or "").strip().upper()
    if cargada and moneda_eje:
        mon_pata = (cargada.get("moneda") or "").strip().upper()
        esp = (cargada.get("especie") or "").strip().lower()
        otras = [p for p in patas if p.get("simbolo") != simbolo and p.get("activa")]
        if moneda_eje == "USD" and mon_pata == "ARS" and otras:
            return "pata_equivocada", (f"eje USD con la pata en PESOS ({esp or '?'}) — "
                                       f"hay {len(otras)} pata/s más cargadas")
        if moneda_eje == "ARS" and mon_pata == "USD" and otras:
            return "pata_equivocada", (f"eje ARS con la pata en DÓLARES ({esp or '?'}) — "
                                       f"hay {len(otras)} pata/s más cargadas")

    if precio is None:
        return "sin_precio", "ninguna de las tres fuentes locales tiene precio"
    return "precio_sospechoso", f"cuadro sano (Σ={residual:,.2f}) y precio {precio:,.4f}"


# ── Salida ───────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    from api.services.av_agent_alta import _residual_vivo
    from core import curvas_ejes, curvas_sql
    from engines.curvas import rama_calculo

    tipos = (("falta_en_base", "sin_flujo", "tasa_sospechosa", "hueco_de_curva")
             if "--todos" in argv else ("tasa_sospechosa",))
    pedidos = [a for a in argv if not a.startswith("--")]

    filas, corrida = _hallazgos(tipos, pedidos)
    if not filas:
        print("No hay hallazgos en la última corrida con ese filtro. "
              "¿Corrió `python -m jobs.av_agent`?")
        return 0

    docs = {(d.get("ticker_corto") or "").strip().upper(): d
            for d in curvas_sql.cargar_todos()}
    tickers = sorted({(f["ticker"] or "").strip().upper() for f in filas})
    simbolos = sorted({(docs[t].get("ticker") or "").strip()
                       for t in tickers if t in docs and docs[t].get("ticker")})

    snap, cierre = _snapshot(simbolos), _cierre(simbolos)
    patas_de, assets = _especies(tickers), _en_assets(tickers)
    cat_1816 = _catalogo_1816(tickers)

    print(f"\nAV AGENT — ¿qué se arregla SIN 1816?   corrida {corrida}   "
          f"{len(filas)} hallazgo(s) · {len(tickers)} bono(s)")
    print("=" * 100)

    resumen: dict[str, int] = {}
    for h in filas:
        tk = (h["ticker"] or "").strip().upper()
        doc = docs.get(tk)
        ev = h.get("evidencia") or {}
        simbolo = (doc.get("ticker") or "").strip() if doc else ""
        rama = rama_calculo(doc) if doc else ""
        ejes = curvas_ejes.ejes_de_doc(doc) if doc else None
        residual, n_fut = _residual_vivo(doc, rama) if doc else (0.0, 0)

        s, c = snap.get(simbolo) or {}, cierre.get(simbolo) or {}
        px_ev = _f(ev.get("last_price"))
        # El orden es el mismo que debería tener el simulador: live → cierre →
        # la foto congelada del hallazgo. Las tres son LOCALES.
        precio = s.get("precio") or c.get("precio") or px_ev

        causa, detalle = _causa(doc=doc, hallazgo=h, residual=residual, n_fut=n_fut,
                                precio=precio, patas=patas_de.get(tk) or [],
                                en_assets=tk in assets,
                                curva_1816=cat_1816.get(tk, ""))
        arreglo, necesita = ARREGLOS.get(causa, ("?", True))
        resumen[causa] = resumen.get(causa, 0) + 1

        txt_ejes = ("SIN EJES" if not ejes else
                    f"{ejes.emisor_tipo} · {ejes.moneda} · {ejes.ajuste} · "
                    f"{ejes.ley or '—'}")
        print(f"\n{tk:<8} {h['regla']:<24} [{h['severidad']}]")
        print(f"  ejes      {txt_ejes}   rama {rama or '—'}   símbolo {simbolo or '—'}")
        print(f"  cuadro    Σ amort futuras {_n(residual)} en {n_fut} cupón/es"
              f"   cer_emision {_n(_f((doc or {}).get('cer_emision')), 4)}")
        print(f"  precio    live {_n(s.get('precio'), 4)} ({s.get('at') or 'sin fila'})"
              f"   cierre {_n(c.get('precio'), 4)} ({c.get('fecha') or 'sin fila'})"
              f"   hallazgo {_n(px_ev, 4)}")
        print(f"  paridad   live {_n(s.get('paridad'))}   del hallazgo "
              f"{_n(_f(ev.get('paridad')))}   tea live {_n(s.get('tea'), 4)}")
        patas = patas_de.get(tk) or []
        if patas:
            for p in patas:
                marca = "◀ CARGADA" if p["simbolo"] == simbolo else ""
                print(f"    pata    {p['simbolo']:<34} {p['ticker_especie']:<8} "
                      f"{(p['especie'] or '?'):<6} {(p['moneda'] or '?'):<4} "
                      f"{(p['plazo'] or '?'):<5} "
                      f"{'default' if p['default'] else '':<8}{marca}")
        else:
            print("    pata    (mercado.especies no tiene ninguna pata de este ticker)")
        print(f"  assets    {'sí' if tk in assets else 'NO'}"
              f"   catálogo 1816 «{cat_1816.get(tk, '') or '—'}»")
        print(f"  → CAUSA   {causa}: {detalle}")
        print(f"    arreglo {arreglo}   ¿necesita 1816? "
              f"{'SÍ' if necesita else 'NO'}")

    print("\n" + "=" * 100)
    print("RESUMEN POR CAUSA")
    sin_red = con_red = 0
    for causa, n in sorted(resumen.items(), key=lambda kv: -kv[1]):
        arreglo, necesita = ARREGLOS.get(causa, ("?", True))
        con_red, sin_red = (con_red + n, sin_red) if necesita else (con_red, sin_red + n)
        print(f"  {n:>3}  {causa:<20} {'1816' if necesita else 'LOCAL':<6} {arreglo}")
    total = sin_red + con_red
    print(f"\n  SE ARREGLAN SIN SALIR A LA RED: {sin_red}/{total}"
          f"   ·   NECESITAN 1816: {con_red}/{total}")
    print("\n⚠️ Esto es una SOSPECHA con la evidencia al lado, no un veredicto: "
          "ningún renglón\n   de acá alcanza por sí solo para pisar un dato.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
