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
           "FROM agente.av_agent_hallazgos WHERE corrida_at = "
           "(SELECT max(corrida_at) FROM agente.av_agent_hallazgos) "
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
    "moneda_flujo_contradice": ("alinear `moneda_flujo` con los ejes", False),
    "escala_del_cuadro": ("reescalar las amortizaciones a base 100", False),
    "campo_de_amortizacion": ("el cuadro usa el campo de la OTRA rama", False),
    "pata_equivocada":  ("apuntar `curvas.instrumento` a la pata correcta", False),
    "falta_cer":        ("cargar el `cer_emision` (dato manual)", False),
    "sin_espejo_assets": ("crear la fila en `portafolio.assets`", False),
    "tasa_externa":     ("ninguno: la tasa no la calculamos nosotros", False),
    "paridad_fosil":    ("ninguno en el dato: limpiar la métrica vieja del snapshot", False),
    "paridad_del_motor": ("BUG del motor: el valor técnico CER ignora el residual vivo",
                          False),
    "precio_sospechoso": ("verificar el precio (stale/ilíquido) o IGNORAR", False),
    "sin_precio":       ("ninguno hoy: no hay precio en ninguna fuente local", False),
    "sin_doc":          ("el ticker no está en mercado.curvas", False),
}

# ── `moneda_flujo`: el vocabulario que la migración a EJES dejó atrás ────────
#
# `rama_calculo` se migró a los ejes el 2026-08-16, pero **la rama ON sigue
# despachando por `moneda_flujo`** (`engines/curvas.py:665`), que es un campo
# aparte y se carga a mano. Cuando los dos se contradicen no hay ningún error: el
# MOTOR usa el viejo y la VISTA muestra el nuevo, así que la paridad sale de una
# cuenta y la clasificación de otra.
#
# Verificado con la aritmética de la corrida del 2026-08-17:
#   OLC3O  (DL, `moneda_flujo` OK)  (137.280 / A3500) / 146.300 × 100 = 0,065 ✓
#   LOC6O  paridad 156.570 = precio / 100 × 100  → el motor NO convirtió nada,
#          o sea cayó en el `else` de ARS pese a tener eje USD.
def _moneda_flujo_esperada(ejes) -> str:
    """Qué debería decir `moneda_flujo` según los ejes — las MISMAS tres puertas
    que abre el `if` de la rama ON, ni una más."""
    if (ejes.ajuste or "") == "dolar_linked":
        return "DL"
    return "USD" if (ejes.moneda or "").upper() == "USD" else "ARS"


def _precio_como_el_motor(doc: dict, precio: float | None, mep: float | None,
                          a3500: float | None) -> tuple[float | None, str]:
    """El `precio_calc` que usaría HOY la rama ON. Es la copia FIEL del bloque de
    `engines/curvas.py` — si acá se convirtiera distinto, el diag hablaría de un
    precio que el motor nunca vio."""
    from engines.curvas import precio_soberano_a_usd

    if not precio or precio <= 0:
        return None, "sin precio"
    moneda = (doc.get("moneda_flujo") or "USD").upper()
    if moneda == "USD":
        px = precio_soberano_a_usd(precio, doc.get("ticker") or "", mep)
        if px is None:
            return None, "USD sin MEP → el motor sale sin TEA ni paridad"
        return px, ("as-is (sufijo D/C)" if abs(px - precio) < 1e-9 else "÷MEP")
    if moneda == "DL":
        if precio < 1000:
            return precio, "as-is (ya en escala USD)"
        if not a3500 or a3500 <= 0:
            return None, "DL en escala peso sin A3500 → el motor sale sin TEA ni paridad"
        return precio / a3500, "÷A3500"
    return precio, "as-is (ARS nativo)"


def _sumas_amortizacion(doc: dict) -> tuple[float, float]:
    """Σ de las amortizaciones FUTURAS por los DOS campos: `(amortizacion,
    amortizacion_pct)`.

    `_residual_vivo` elige uno según la rama —que es lo correcto, porque es lo que
    hace el motor— pero entonces un 0,00 no distingue «este bono ya no amortiza»
    de «el cuadro está cargado con el campo de la otra rama», y las dos cosas
    piden arreglos opuestos. Con los dos números al lado, la ambigüedad se va.
    """
    from datetime import date

    from engines.curvas import fecha_flujo

    hoy = date.today()
    abs_, pct = 0.0, 0.0
    for f in doc.get("flujos") or []:
        fd = fecha_flujo(f)
        if fd and fd > hoy:
            abs_ += float(f.get("amortizacion") or 0.0)
            pct += float(f.get("amortizacion_pct") or 0.0)
    return round(abs_, 6), round(pct, 6)


def _motor_escribe_paridad(doc: dict, rama: str) -> tuple[bool, str]:
    """¿El motor escribiría HOY una paridad para este bono?

    Si la respuesta es NO y el snapshot igual tiene una, **esa paridad es un
    fósil**: `market_snapshot` es un upsert PARCIAL, así que lo que el motor deja
    de calcular no se borra — se queda ahí sin fecha propia, indistinguible de un
    valor de hoy, y es lo que termina disparando el hallazgo.

    Las tres puertas son las del propio `engines/curvas.py`, no criterios nuevos.
    """
    if rama == "otros":
        return False, "la rama «otros» solo computa duration (curvas.py:733)"
    if rama == "cer" and not _f(doc.get("cer_emision")):
        return False, "CER sin `cer_emision` → sale en curvas.py:439"
    return True, ""


def _pata_cargada(simbolo: str, patas: list[dict]) -> dict | None:
    return next((p for p in patas if (p.get("simbolo") or "") == simbolo), None)


def _causa(*, doc: dict | None, hallazgo: dict, residual: float, n_fut: int,
           precio: float | None, patas: list[dict], en_assets: bool,
           curva_1816: str, rama: str = "", sumas: tuple[float, float] = (0.0, 0.0),
           par_calc: float | None = None,
           par_guardada: float | None = None) -> tuple[str, str]:
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

    # ⚠️ **PRIMERO `moneda_flujo`, y no la pata.** Es lo que decide si el motor
    # convierte el precio, y una pata "rara" con `moneda_flujo` bien no rompe nada
    # (el sufijo D/C del símbolo ya la resuelve). Al revés sí rompe: con
    # `moneda_flujo` en ARS el precio entra crudo y la paridad sale ×MEP de más.
    # Si esto se clasificara después, el arreglo propuesto sería cambiar la PATA —
    # o sea pisar el dato equivocado para tapar el síntoma del otro.
    if rama == "on":
        esperada = _moneda_flujo_esperada(ejes)
        actual = (doc.get("moneda_flujo") or "").strip().upper() or "(vacío)"
        if actual != esperada:
            return "moneda_flujo_contradice", (
                f"`moneda_flujo`={actual} pero los ejes dicen {ejes.moneda}/"
                f"{ejes.ajuste} → debería ser {esperada}. El motor despacha por "
                f"`moneda_flujo`, así que el precio entra sin convertir.")

    # ⚠️ El CER va ANTES que la forma del cuadro: sin `cer_emision` la rama sale en
    # la línea 439 de `engines/curvas.py` **sin escribir TEA ni paridad**, así que
    # todo lo que se vea guardado es de otra época y discutir el cuadro es discutir
    # un síntoma que el motor ni siquiera produjo.
    if (doc.get("ajuste") or "") == "cer" and not _f(doc.get("cer_emision")):
        return "falta_cer", ("rama CER sin `cer_emision`: el motor sale sin calcular "
                             "NADA (curvas.py:439), así que la paridad guardada es un "
                             "fósil de antes")

    s_abs, s_pct = sumas
    if not residual and (s_abs or s_pct):
        otro = "amortizacion" if residual == s_pct else "amortizacion_pct"
        return "campo_de_amortizacion", (
            f"la rama «{rama}» lee su campo y da 0, pero el cuadro tiene "
            f"Σ {otro} = {(s_abs or s_pct):,.2f}: está cargado con el campo de la otra")

    if residual and not (_RESIDUAL_MIN <= residual <= _RESIDUAL_MAX):
        return "escala_del_cuadro", (f"Σ amortizaciones futuras = {residual:,.2f} en "
                                     f"{n_fut} cupón/es (un cuadro sano ronda 100)")

    # ⚠️ **UN BONO CER YA AMORTIZADO NO TIENE LA PARIDAD MAL: LA TIENE MAL EL MOTOR.**
    # `curvas.py:457` arma el valor técnico con `valor_nominal` (estático, default
    # 100) en vez del residual VIVO, así que un bono que ya amortizó el 80% muestra
    # una paridad 5 veces más chica. TX26: 727,30 / (100 × ratio) × 100 = 20,07;
    # con el residual real (20) da 100,4 — o sea perfectamente normal.
    #
    # Es la MISMA lección que E2.u (GD46: «el residual vivo sale del cuadro»), que
    # se arregló en la rama ON y quedó pendiente en la CER. Se reporta aparte
    # porque su arreglo NO es escribir un dato: es tocar el motor.
    if rama == "cer" and 0 < residual < 99:
        vn = _f(doc.get("valor_nominal")) or 100.0
        if vn > residual * 1.05:
            return "paridad_del_motor", (
                f"el bono ya amortizó: residual vivo {residual:,.2f} contra "
                f"`valor_nominal` {vn:,.2f}. El motor arma el valor técnico con el "
                f"segundo → la paridad sale ×{vn / residual:,.1f} más chica de lo real")

    # La pata SIGUE siendo una causa real (falla #4), pero recién acá: un HD con la
    # pata peso y `moneda_flujo` bien se convierte solo, así que solo importa
    # cuando el símbolo cargado NO tiene sufijo D/C y encima no hay MEP.
    simbolo = (doc.get("ticker") or "").strip()
    cargada = _pata_cargada(simbolo, patas)
    moneda_eje = (doc.get("moneda_eje") or "").strip().upper()
    if cargada and moneda_eje:
        mon_pata = (cargada.get("moneda") or "").strip().upper()
        esp = (cargada.get("especie") or "").strip().lower()
        otras = [p for p in patas if p.get("simbolo") != simbolo and p.get("activa")]
        if moneda_eje == "ARS" and mon_pata == "USD" and otras:
            return "pata_equivocada", (f"eje ARS con la pata en DÓLARES ({esp or '?'}) — "
                                       f"hay {len(otras)} pata/s más cargadas")

    # ⚠️ **LA MÉTRICA GUARDADA PUEDE SER UN FÓSIL.** `market_snapshot` es un upsert
    # PARCIAL: cuando el motor sale por una de sus puertas de emergencia (sin MEP,
    # sin A3500, XIRR fuera de rango) escribe SOLO `duration` — y la TEA y la
    # paridad de la última vez que sí calculó **se quedan ahí, sin fecha propia y
    # sin forma de distinguirlas de un valor de hoy**. Un hallazgo disparado por un
    # fósil no habla del bono: habla de un día viejo.
    if (par_calc is not None and par_guardada is not None
            and abs(par_calc - par_guardada) > max(1.0, abs(par_calc) * 0.02)):
        return "paridad_fosil", (f"la paridad guardada es {par_guardada:,.2f} pero "
                                 f"rehaciendo HOY la cuenta del motor da {par_calc:,.2f}")

    if not precio:
        return "sin_precio", "ninguna fuente local tiene un precio > 0"
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

    # Los DOS tipos de cambio que puede necesitar la rama ON, pedidos UNA vez. Sin
    # ellos no se puede rehacer la cuenta del motor — y que FALTEN es en sí mismo
    # un diagnóstico: es la puerta de emergencia por la que el bono sale sin TEA.
    mep = a3500 = None
    try:
        from api.services.macro import get_ultimo_mep
        mep = (get_ultimo_mep() or {}).get("mep")
    except Exception as e:
        print(f"  ⚠ sin MEP ({type(e).__name__})")
    try:
        from engines.curvas import cargar_a3500_actual
        a3500 = cargar_a3500_actual()
    except Exception as e:
        print(f"  ⚠ sin A3500 ({type(e).__name__})")
    print(f"TC del día: MEP {_n(mep, 2)} · A3500 {_n(a3500, 2)}"
          + ("   ⚠ sin A3500 los dólar-linked en escala peso salen sin TEA ni paridad"
             if not a3500 else ""))

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

        # Σ por los DOS campos. `_residual_vivo` elige uno según la rama, así que un
        # 0,00 es ambiguo: puede ser «no amortiza más» o «el cuadro está cargado con
        # el campo de la otra rama». Con los dos números al lado deja de serlo.
        sumas = _sumas_amortizacion(doc) if doc else (0.0, 0.0)

        s, c = snap.get(simbolo) or {}, cierre.get(simbolo) or {}
        px_ev = _f(ev.get("last_price"))
        # El orden es el mismo que debería tener el simulador: live → cierre →
        # la foto congelada del hallazgo. Las tres son LOCALES.
        precio = s.get("precio") or c.get("precio") or px_ev

        # La cuenta del motor, rehecha HOY: sirve para separar un problema REAL del
        # bono de una métrica vieja que quedó pegada en el snapshot.
        px_calc, nota_px = (_precio_como_el_motor(doc, precio, mep, a3500)
                            if doc and rama == "on" else (None, ""))
        par_calc = (round(px_calc / residual * 100, 4)
                    if px_calc and residual > 0 else None)

        causa, detalle = _causa(doc=doc, hallazgo=h, residual=residual, n_fut=n_fut,
                                precio=precio, patas=patas_de.get(tk) or [],
                                en_assets=tk in assets, rama=rama, sumas=sumas,
                                par_calc=par_calc, par_guardada=s.get("paridad"),
                                curva_1816=cat_1816.get(tk, ""))
        arreglo, necesita = ARREGLOS.get(causa, ("?", True))
        resumen[causa] = resumen.get(causa, 0) + 1

        txt_ejes = ("SIN EJES" if not ejes else
                    f"{ejes.emisor_tipo} · {ejes.moneda} · {ejes.ajuste} · "
                    f"{ejes.ley or '—'}")
        print(f"\n{tk:<8} {h['regla']:<24} [{h['severidad']}]")
        print(f"  ejes      {txt_ejes}   rama {rama or '—'}   símbolo {simbolo or '—'}")
        mf = ((doc or {}).get("moneda_flujo") or "").strip().upper() or "(vacío)"
        esperada = _moneda_flujo_esperada(ejes) if ejes else "?"
        print(f"  moneda_flujo {mf}   (los ejes piden {esperada})"
              + ("   ⚠ CONTRADICE" if rama == "on" and mf != esperada else ""))
        print(f"  cuadro    Σ amort futuras {_n(residual)} en {n_fut} cupón/es"
              f"   [abs {_n(sumas[0])} · pct {_n(sumas[1])}]"
              f"   valor_nominal {_n(_f((doc or {}).get('valor_nominal')))}"
              f"   cer_emision {_n(_f((doc or {}).get('cer_emision')), 4)}")
        print(f"  precio    live {_n(s.get('precio'), 4)} ({s.get('at') or 'sin fila'})"
              f"   cierre {_n(c.get('precio'), 4)} ({c.get('fecha') or 'sin fila'})"
              f"   hallazgo {_n(px_ev, 4)}")
        escribe, por_que = _motor_escribe_paridad(doc, rama) if doc else (True, "")
        print(f"  paridad   guardada {_n(s.get('paridad'))}   REHECHA HOY "
              f"{_n(par_calc)}   del hallazgo {_n(_f(ev.get('paridad')))}"
              f"   tea guardada {_n(s.get('tea'), 4)}")
        if not escribe and s.get("paridad") is not None:
            print(f"    ⚠ FÓSIL: el motor NO escribe paridad para este bono "
                  f"({por_que}), así que la guardada es de otra época")
        if nota_px:
            print(f"  motor     precio_calc {_n(px_calc, 4)} ({nota_px})")
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

    # ── EL CENSO: ¿cuántos bonos MÁS están así sin haber disparado un hallazgo?
    #
    # Los detectores solo ven lo que se sale de un rango. Un `moneda_flujo` mal
    # puesto en un bono cuya paridad, por casualidad, cae adentro de [40, 160] no
    # dispara nada **y está igual de mal valuado**. La pregunta no es cuántos
    # hallazgos hay: es cuántos bonos tienen el defecto.
    print("\n" + "=" * 100)
    print("CENSO sobre TODO mercado.curvas (no solo los que dispararon hallazgo)")
    mal, revisados, ejemplos = 0, 0, []
    for d in docs.values():
        ej = curvas_ejes.ejes_de_doc(d)
        if ej is None or rama_calculo(d) != "on":
            continue
        revisados += 1
        actual = (d.get("moneda_flujo") or "").strip().upper() or "(vacío)"
        if actual != _moneda_flujo_esperada(ej):
            mal += 1
            if len(ejemplos) < 12:
                ejemplos.append(f"{(d.get('ticker_corto') or '?')}"
                                f"({actual}→{_moneda_flujo_esperada(ej)})")
    print(f"  `moneda_flujo` que contradice a los ejes: {mal} de {revisados} bonos "
          f"de la rama ON")
    if ejemplos:
        print("  " + " · ".join(ejemplos) + (" …" if mal > len(ejemplos) else ""))
    print("\n⚠️ Esto es una SOSPECHA con la evidencia al lado, no un veredicto: "
          "ningún renglón\n   de acá alcanza por sí solo para pisar un dato.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
