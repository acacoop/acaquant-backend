"""api/services/av_agent_alta.py — E2: SIMULAR un alta antes de escribirla.

Doc madre: **`docs/AV_AGENT.md`**.

**Qué resuelve.** Hasta acá, contestar «alta» guardaba la decisión y nada más.
Esto la ejecuta: baja el cuadro de flujos de 1816, lo convierte a NUESTRA shape,
**calcula la TEA que TENDRÍA el bono sin escribir nada**, y recién con ese número
a la vista se aplica.

**El simulador ES el guardrail.** Un flujo mal escalado no da error: da una TEA
absurda o ninguna, y si se escribiera igual rompería el chart entero (la escala
del eje) y contaminaría el AuM vía el join con `portafolio.assets`. Al simular
primero, ese error se ve ANTES y el alta no se aplica.

## Alcance: qué ramas se pueden dar de alta hoy, y por qué no todas

| Rama | ¿Alta automática? | Por qué |
|---|---|---|
| `tasa_fija` bullet (LECAP/BONCAP) | **sí** | un solo pago: `flujo_vencimiento` |
| `tasa_fija` con cupón | **sí** | `amortizacion` + `interes`, montos absolutos = lo que manda 1816 |
| `soberanos` (bonares/globales/BCRA) | **sí** | `amortizacion_pct` + `cupon_sobre_residual`, y en esta rama `cupon_sobre_residual` **es un monto por 100** — o sea, exactamente lo que manda 1816 |
| `cer` | **no** | acá `cupon_sobre_residual` es una **TASA que se multiplica por el residual vivo**, no un monto. Además exige `cer_emision`. |
| `tamar` / `dual` / otras | **no** | shape propia (`tasa_referencia`) y valuación por otro riel |

**Esa diferencia de significado es la trampa documentada en `RENTA_FIJA.md` paso
15**: la primera conversión que alguien escribió estaba mal justo por eso (un
cupón de 2 daba 200) y **no se veía leyendo el código** — la cazó un chequeo
numérico. Por eso las ramas donde el mismo campo significa dos cosas distintas
NO se dan de alta solas: se simulan, se muestran, y las carga un humano.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from core import curvas_ejes, mercado_1816

logger = logging.getLogger(__name__)

# Ramas de `engines.curvas.rama_calculo` cuya conversión desde 1816 es INEQUÍVOCA.
RAMAS_AUTOMATICAS = ("tasa_fija", "soberanos", "cer")

# Tolerancia para decidir la ESCALA del cuadro. 1816 manda por VN 100 en los bonos
# por paridad y en NOMINALES en algunas ONs (medido, §4.9 de VISTA_RESEARCH): no
# se asume un divisor global, se mide la Σ de amortizaciones.
_VN100_MIN, _VN100_MAX = 95.0, 105.0


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fecha(v) -> str:
    if isinstance(v, str):
        return v.strip()[:10]
    return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else ""


def convertir_flujos(cupones: list[dict], rama: str) -> dict:
    """Cuadro de 1816 → nuestra shape. PURA (testeable sin red).

    Devuelve `{flujos, escala, suma_amort, flujo_vencimiento, n}`.

    Usa la **fecha EFECTIVA**: medido el 2026-08-15, nuestro master guarda esa y
    no la teórica (por efectiva matchean 21/23 cupones de AE38, por teórica 13/23).
    Keyear por la equivocada inventa divergencias de 2-3 días que no existen.
    """
    filas = []
    for c in cupones:
        if not isinstance(c, dict):
            continue
        f = _fecha(c.get("fechaPagoEfectiva")) or _fecha(c.get("fechaPagoTeorica"))
        if not f:
            continue
        filas.append((f, _num(c.get("flujoAmortizacion")) or 0.0,
                      _num(c.get("flujoInteres")) or 0.0))
    filas.sort()

    suma_amort = round(sum(a for _, a, _ in filas), 6)
    escala = "vn100" if _VN100_MIN <= suma_amort <= _VN100_MAX else "nominales"

    # Residual vivo antes de cada pago (por 100), para poder expresar el cupón de
    # un CER como TASA. Se recorre en orden y se descuenta lo ya amortizado.
    residual, residuales = suma_amort, []
    for _, amort, _ in filas:
        residuales.append(residual)
        residual = round(residual - amort, 6)

    flujos: list[dict] = []
    for (f, amort, interes), res_prev in zip(filas, residuales, strict=False):
        if rama == "cer":
            # ⚠ En la rama CER, `cupon_sobre_residual` NO es el monto: es la TASA
            # que el motor MULTIPLICA por el residual vivo. Guardar el monto de
            # 1816 tal cual daría un cupón inflado ~50x en un bono amortizante —
            # la trampa del paso 15, que no se ve leyendo el código.
            flujos.append({
                "fecha": f,
                "amortizacion_pct": (amort / suma_amort * 100) if suma_amort else 0.0,
                "cupon_sobre_residual": (interes / res_prev) if res_prev else 0.0,
                "residual_previo_pct": (res_prev / suma_amort * 100) if suma_amort else 0.0,
            })
        elif rama == "soberanos":
            # En esta rama `cupon_sobre_residual` ES un monto por 100 VN (se divide
            # por 100 al valuar) — o sea, exactamente lo que manda 1816.
            flujos.append({"fecha": f, "amortizacion_pct": amort,
                           "cupon_sobre_residual": interes})
        else:
            flujos.append({"fecha": f, "amortizacion": amort, "interes": interes})

    # Bullet: un solo pago al final y sin cupones intermedios → el master lo guarda
    # como `flujo_vencimiento`, no como cronograma (así lo valúa el motor).
    fv = None
    if len(filas) == 1:
        fv = round(filas[0][1] + filas[0][2], 6)

    return {"flujos": flujos, "escala": escala, "suma_amort": suma_amort,
            "flujo_vencimiento": fv, "n": len(filas)}


def _doc_simulado(ticker: str, ejes, conv: dict, vencimiento: str,
                  simbolo: str, cer_emision: float | None = None) -> dict:
    """El doc de `mercado.curvas` que TENDRÍA este bono. No se persiste."""
    from engines.curvas import rama_calculo

    doc = {
        "ticker_corto": ticker, "ticker": simbolo,
        "emisor_tipo": ejes.emisor_tipo, "moneda_eje": ejes.moneda,
        "ajuste": ejes.ajuste, "ajuste_alt": ejes.ajuste_alt, "ley": ejes.ley,
        "fecha_vencimiento": vencimiento, "valor_nominal": 100.0,
        # `moneda_flujo` decide el divisor del precio en el motor y DEBE coincidir
        # con la cartera (falla #1 del catálogo de SALUD_CURVAS).
        "moneda_flujo": "USD" if ejes.moneda == "USD" else "ARS",
    }
    if cer_emision:
        doc["cer_emision"] = cer_emision
    if conv["flujo_vencimiento"] is not None:
        doc["flujo_vencimiento"] = conv["flujo_vencimiento"]
    else:
        doc["flujos"] = conv["flujos"]
    doc["rama"] = rama_calculo(doc)
    return doc


def _motivo_no_aplicable(rama: str, ejes) -> str:
    """Por qué ESTA rama no se da de alta sola. Un motivo por rama, no un texto
    fijo: el primero decía «en CER cupon_sobre_residual es una TASA» hasta para un
    BADLAR, que no tiene nada que ver con CER. Un mensaje que no habla del caso
    que uno está mirando no explica: confunde."""
    if rama in RAMAS_AUTOMATICAS:
        return ""
    if rama == "otros":
        from core import curvas_catalogo
        fuente = curvas_catalogo.fuente_valuacion(ejes.ajuste)
        if fuente == "1816":
            return (f"la curva {ejes.ajuste.upper()} se valúa TRAYENDO la tasa de "
                    "1816, así que el motor no calcula su TEA y acá no hay nada que "
                    "simular: el cuadro de flujos alcanza para darlo de alta.")
        return (f"el ajuste «{ejes.ajuste}» no tiene rama de cálculo en el motor "
                "(cae en el `else`, que solo computa duration). Su curva existe, "
                "pero la TEA necesita que se escriba la fórmula o que se traiga "
                "de 1816.")
    if rama == "dolar_linked":
        return ("un dólar-linked se valúa contra el A3500 del día y su cuadro puede "
                "venir en nominales: la conversión no es directa. Se carga a mano "
                "con el cuadro que muestra el simulador.")
    if rama == "tamar":
        return ("un TAMAR es una nota de tasa PROMEDIO: su TEA no la calcula el "
                "motor, se trae de 1816 (`jobs/tamar_1816`). El cuadro sirve igual "
                "para darlo de alta a mano.")
    return f"la rama «{rama}» todavía no tiene conversión automática."


def _estado_simbolo(simbolo: str) -> dict:
    """¿Este símbolo EXISTE en Primary? Sin eso el bono nunca va a tener precio.

    Es la pregunta que faltaba: dar de alta un bono no alcanza para que aparezca
    con precio. La cadena completa es **símbolo en Primary → el motor lo suscribe
    (lee `mercado.curvas` AL ARRANCAR) → llega el trade → `market_snapshot` →
    el motor de curvas calcula la TEA**. Si el primer eslabón no está, el resto
    no pasa nunca y el bono queda con la celda vacía sin que nadie sepa por qué.

    `None` de `validos()` = no se pudo saber (no hay catálogo). No se afirma
    nada: "no pude mirar" nunca es "no está".
    """
    try:
        from core import instrumentos_validos
        vs = instrumentos_validos.validos()
    except Exception:
        vs = None
    if vs is None:
        return {"conocido": None,
                "nota": "no se pudo leer el catálogo de Primary — no se puede "
                        "afirmar si el símbolo existe"}
    if simbolo in vs:
        return {"conocido": True,
                "nota": "Primary lo lista: al reiniciar los motores va a recibir "
                        "precio y el motor de curvas va a calcular su TEA"}
    return {"conocido": False,
            "nota": "⚠ Primary NO lista este símbolo: el bono se puede dar de alta, "
                    "pero NO va a recibir precio y su TEA va a quedar vacía. Puede "
                    "ser que cotice con otro plazo/sufijo — revisar en "
                    "mercado.especies antes de esperar la tasa."}


# ── PRE-FLIGHT: la cadena completa, paso por paso ────────────────────────────
#
# «TIENE QUE PASAR TODO EL CHEQUEO, EL PASO A PASO, VALIDAR QUE PUEDE LLEGAR,
# COMO SI LO HARÍA YO MISMO.» Eso es literalmente lo que hace esto.
#
# El problema que resuelve: **APLICAR escribe una fila en `mercado.curvas` y eso
# NO garantiza nada**. Un bono puede quedar dado de alta y sin precio para
# siempre, y el síntoma es una celda vacía — no un error. La cadena tiene siete
# eslabones y cada uno rompe en silencio:
#
#   1. la curva de 1816 traduce a nuestros ejes
#   2. 1816 tiene el cuadro de flujos, con escala reconocible
#   3. la rama de cálculo sabe convertir ese cuadro sin ambigüedad
#   4. (CER) hay `cer_emision`
#   5. la ESPECIE existe — o sea, el papel cotiza con algún símbolo
#   6. ese símbolo está en el catálogo de Primary (si no, `core/websocket`
#      lo filtra y la suscripción nunca sale)
#   7. el motor lo suscribe → llega el trade → `market_snapshot` → TEA
#   8. hay espejo en `portafolio.assets` (sin eso no entra al AuM)
#
# Cada paso reporta `ok` / `falla` / `atencion` / `no_se_puede_saber`. **El
# cuarto estado no es decorativo**: si Postgres no responde, "no pude mirar" no
# es "no está", y afirmarlo sería exactamente la REGLA #2 rota.
OK, FALLA, ATENCION, NO_SE = "ok", "falla", "atencion", "no_se_puede_saber"


def _paso(n: int, titulo: str, estado: str, detalle: str,
          *, tabla: str = "", accion: str = "") -> dict:
    return {"n": n, "titulo": titulo, "estado": estado, "detalle": detalle,
            "tabla": tabla, "accion": accion}


def _contexto_cadena(ticker: str) -> dict:
    """Las 4 preguntas de base, en UNA sola conexión.

    Cada roundtrip a Supabase cuesta un peaje fijo (~8.5ms medido) aunque la
    query ejecute en 0.1ms: lo que importa es la CANTIDAD, no el plan. Cuatro
    `cur.execute` sobre la misma conexión es lo más barato que se puede hacer
    sin inventar un join entre tablas que no se relacionan.

    Si la base no responde devuelve `{"ok": False}` y los chequeos que dependen
    de esto salen `no_se_puede_saber` en vez de mentir.
    """
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT simbolo, especie, moneda, plazo, es_default, activa, validado "
                "FROM mercado.especies WHERE ticker = %s ORDER BY es_default DESC, simbolo",
                (ticker,))
            especies = [{"simbolo": r[0], "especie": r[1], "moneda": r[2],
                         "plazo": r[3], "es_default": bool(r[4]),
                         "activa": r[5] is not False, "validado": r[6]}
                        for r in cur.fetchall()]

            cur.execute("SELECT instrumento FROM mercado.curvas WHERE ticker = %s",
                        (ticker,))
            r = cur.fetchone()
            ya_en_curvas, simbolo_actual = (r is not None), (r[0] if r else None)

            cur.execute("SELECT unidad, instrumento, vigente FROM portafolio.assets "
                        "WHERE ticker = %s", (ticker,))
            assets = [{"unidad": a, "instrumento": b, "vigente": c is not False}
                      for a, b, c in cur.fetchall()]
        return {"ok": True, "especies": especies, "ya_en_curvas": ya_en_curvas,
                "simbolo_actual": simbolo_actual, "assets": assets}
    except Exception as e:
        logger.warning("av_agent: no pude leer el contexto de cadena de %s: %s",
                       ticker, e)
        return {"ok": False, "error": type(e).__name__}


def _simbolo_del_bono(ticker: str, especies: list[dict]) -> tuple[str, str]:
    """El símbolo que se va a escribir, y de dónde salió.

    **`mercado.especies` manda.** Construir `MERV - XMEV - {tk} - 24hs` a mano es
    una ADIVINANZA: hay papeles que solo cotizan CI, y otros cuya pata en pesos
    no se llama como el ticker. Especies es el único lugar donde vive esa
    relación, y es la misma fuente de la que `jobs/assets_autofill` deriva
    `assets.instrumento` — usar otra sería crear una segunda verdad.

    Sin fila en especies se cae al símbolo armado, pero el chequeo 5 lo dice.
    """
    activas = [e for e in especies if e["activa"]]
    pesos = [e for e in activas if (e["moneda"] or "").upper() == "ARS"]
    for grupo in (pesos, activas):
        if not grupo:
            continue
        # es_default primero (ya viene ordenado), y dentro de eso 24hs sobre CI:
        # ahí está la liquidez, y por lo tanto el precio.
        elegida = next((e for e in grupo if e["es_default"]), None) \
            or next((e for e in grupo if (e["plazo"] or "") == "24hs"), None) \
            or grupo[0]
        return elegida["simbolo"], "especies"
    return f"MERV - XMEV - {ticker} - 24hs", "armado"


def _chequeos(*, ticker: str, curva_1816: str, ejes, rama: str, conv: dict,
              cer_emision: float | None, nota_cer: str, simbolo: str,
              origen_simbolo: str, ctx: dict, estado_simbolo: dict,
              precio, tea) -> list[dict]:
    """La lista ordenada. Se devuelve ENTERA, con los pasos en verde incluidos.

    Mostrar solo lo que falla obliga al que mira a confiar en que el resto se
    chequeó — que es exactamente lo que el user no quiere. Ver los ocho pasos
    verdes ES la respuesta a «¿qué pasa si aplico?».
    """
    ps: list[dict] = []

    ps.append(_paso(1, "La curva de 1816 se traduce a nuestros ejes", OK,
                    f"«{curva_1816}» → emisor {ejes.emisor_tipo} · moneda "
                    f"{ejes.moneda} · ajuste {ejes.ajuste}"
                    + (f" (+{ejes.ajuste_alt})" if ejes.ajuste_alt else "")
                    + (f" · ley {ejes.ley}" if ejes.ley else ""),
                    tabla="mercado.curvas (ejes)"))

    escala_ok = conv["escala"] == "vn100"
    ps.append(_paso(2, "1816 mandó el cuadro de flujos", OK if escala_ok else ATENCION,
                    f"{conv['n']} cupón/es · Σ amortizaciones {conv['suma_amort']} → "
                    f"escala {conv['escala']}"
                    + ("" if escala_ok else
                       " — no suma ~100, así que el cuadro viene en NOMINALES. "
                       "El motor valúa por paridad: revisar antes de aplicar."),
                    tabla="1816 /cashflow"))

    auto = rama in RAMAS_AUTOMATICAS
    ps.append(_paso(3, "La rama de cálculo sabe convertir el cuadro sola",
                    OK if auto else FALLA,
                    f"rama «{rama}»" + (" — conversión inequívoca" if auto
                                        else f" — {_motivo_no_aplicable(rama, ejes)}"),
                    tabla="engines/curvas.py::rama_calculo",
                    accion="" if auto else "cargar a mano con el cuadro de abajo"))

    if rama == "cer":
        ps.append(_paso(4, "CER de emisión resuelto",
                        OK if cer_emision else FALLA,
                        f"{cer_emision} (inferido de la fecha de emisión de 1816, "
                        "con el mismo T−10 hábiles que usa el motor)"
                        if cer_emision else (nota_cer or "no se pudo calcular"),
                        tabla="macro.series_macro (CER)",
                        accion="" if cer_emision else "cargarlo a mano en el master"))

    if not ctx.get("ok"):
        ps.append(_paso(5, "El papel cotiza (especie + símbolo + precio)", NO_SE,
                        f"no se pudo leer la base ({ctx.get('error')}) — no se "
                        "puede afirmar nada de la cadena de precio",
                        tabla="mercado.especies · curvas · assets"))
        return ps

    # 5 — ¿existe la especie? Es lo que hace que el papel TENGA símbolo.
    especies = ctx["especies"]
    activas = [e for e in especies if e["activa"]]
    if activas:
        det = " · ".join(f"{e['simbolo']} ({e['moneda'] or '?'}"
                         + (f", {e['plazo']}" if e["plazo"] else "")
                         + (", default" if e["es_default"] else "") + ")"
                         for e in activas[:4])
        ps.append(_paso(5, "El papel tiene especie: cotiza con un símbolo", OK,
                        f"{len(activas)} pata/s en el catálogo — {det}",
                        tabla="mercado.especies"))
    else:
        ps.append(_paso(5, "El papel tiene especie: cotiza con un símbolo", FALLA,
                        f"NO hay ninguna pata de {ticker} en mercado.especies. El "
                        f"símbolo «{simbolo}» está ARMADO por convención, no "
                        "verificado: puede que el papel cotice CI, o con otro "
                        "sufijo, o que todavía no haya listado.",
                        tabla="mercado.especies",
                        accion="correr `python -m scripts.sembrar_especies --aplicar` "
                               "y volver a simular"))

    # 6 — el gate REAL de la suscripción.
    con = estado_simbolo.get("conocido")
    ps.append(_paso(
        6, "Primary lista ese símbolo (si no, el WS lo filtra)",
        OK if con is True else (FALLA if con is False else NO_SE),
        f"«{simbolo}» ({'de mercado.especies' if origen_simbolo == 'especies' else 'armado por convención'}) — "
        + estado_simbolo.get("nota", ""),
        tabla="manager.pyrofex_instruments · core/instrumentos_validos",
        accion="" if con is not False else
               "verificar la grafía real en Primary — `core/websocket."
               "agregar_suscripciones` descarta lo que no está en el catálogo"))

    # 7 — el motor arma su universo AL ARRANCAR. Este paso NUNCA es verde solo:
    #     es un paso MANUAL, y decirlo es la mitad del valor del pre-flight.
    ps.append(_paso(7, "El motor lo suscribe y el precio llega a market_snapshot",
                    ATENCION,
                    "los motores leen mercado.curvas UNA vez, al arrancar: hasta "
                    "reiniciar motor_rofex + motor_curvas este bono NO se suscribe "
                    "y no va a tener precio, aunque el alta quede escrita.",
                    tabla="mercado.market_snapshot",
                    accion="tras aplicar: reiniciar motor_rofex y motor_curvas"))

    # 8 — ¿ya hay precio HOY? Si el símbolo ya venía suscripto por otra vía, la
    #     TEA sale en la simulación y el paso 7 deja de ser bloqueante.
    if precio:
        ps.append(_paso(8, "Hay precio para simular la tasa ahora", OK,
                        f"último precio {precio} en el snapshot"
                        + (f" → TEA simulada {tea:.4%}" if isinstance(tea, int | float)
                           else " — pero el motor NO devolvió TEA con este cuadro: "
                                "revisar la escala del flujo antes de aplicar"),
                        tabla="mercado.market_snapshot"))
    else:
        ps.append(_paso(8, "Hay precio para simular la tasa ahora", ATENCION,
                        "todavía no hay precio de este símbolo en el snapshot — "
                        "esperable si nunca se suscribió. El cuadro igual queda "
                        "listo: la TEA aparece cuando llegue el primer trade.",
                        tabla="mercado.market_snapshot"))

    # 9 — la TEA la calcula el motor solo si la rama tiene fórmula.
    try:
        from core import curvas_catalogo
        fuente = curvas_catalogo.fuente_valuacion(ejes.ajuste)
    except Exception:
        fuente = None
    if rama in RAMAS_AUTOMATICAS or rama in ("dolar_linked",):
        ps.append(_paso(9, "El motor de curvas va a calcular la TEA", OK,
                        f"la rama «{rama}» tiene fórmula en engines/curvas.py",
                        tabla="engines/curvas.py::calcular_campos"))
    elif fuente == "1816":
        ps.append(_paso(9, "La tasa la trae 1816, no el motor", OK,
                        f"la curva {ejes.ajuste.upper()} está marcada fuente=1816: "
                        "su TEA y su margen los baja el job, igual que los TAMAR.",
                        tabla="mercado.tamar_1816 / job de la curva",
                        accion="verificar que el job de esa curva incluya este ticker"))
    else:
        ps.append(_paso(9, "El motor de curvas va a calcular la TEA", FALLA,
                        f"el ajuste «{ejes.ajuste}» cae en el `else` del motor: solo "
                        "computa duration. El bono va a tener precio pero la celda "
                        "de TEA queda vacía.",
                        tabla="engines/curvas.py",
                        accion="marcar la curva como fuente=1816, o escribir su fórmula"))

    # 10 — sin espejo en assets el bono existe para la vista pero no para el AuM.
    assets = ctx["assets"]
    vig = [a for a in assets if a["vigente"]]
    if vig:
        ps.append(_paso(10, "Entra al AuM: hay espejo en portafolio.assets", OK,
                        f"{len(vig)} unidad/es con ticker {ticker} — "
                        + ", ".join(a["unidad"] for a in vig[:2])
                        + ("…" if len(vig) > 2 else ""),
                        tabla="portafolio.assets"))
    else:
        ps.append(_paso(10, "Entra al AuM: hay espejo en portafolio.assets", ATENCION,
                        f"no hay ninguna unidad con ticker {ticker}. El bono va a "
                        "aparecer en la curva con su tasa, pero NO en AuM/Portfolios "
                        "hasta que exista la posición (la crea el backfill de "
                        "tenencias cuando alguien lo tenga).",
                        tabla="portafolio.assets"))

    if ctx["ya_en_curvas"]:
        ps.insert(0, _paso(0, "⚠ Este ticker YA está en el master", ATENCION,
                           f"mercado.curvas ya tiene {ticker} (símbolo actual: "
                           f"{ctx['simbolo_actual']}). Aplicar lo va a PISAR con "
                           "el cuadro de 1816.",
                           tabla="mercado.curvas"))
    return ps


def _veredicto(chequeos: list[dict]) -> dict:
    """Una línea que resume la lista, para no obligar a leerla entera."""
    fallas = [c for c in chequeos if c["estado"] == FALLA]
    dudas = [c for c in chequeos if c["estado"] == NO_SE]
    if fallas:
        return {"estado": FALLA,
                "texto": f"{len(fallas)} paso/s bloquean la cadena: "
                         + "; ".join(c["titulo"] for c in fallas[:2])}
    if dudas:
        return {"estado": NO_SE,
                "texto": "la conversión está bien, pero no se pudo verificar "
                         + dudas[0]["titulo"].lower()}
    return {"estado": OK,
            "texto": "la cadena cierra: se puede aplicar. Reiniciar los motores "
                     "después para que empiece a recibir precio."}


def _cer_de_emision(fecha_emision: str) -> tuple[float | None, str]:
    """El CER de liquidación a la fecha de emisión. **No hace falta que 1816 lo
    mande**: la fecha de emisión viene en su catálogo y la serie CER ya la
    tenemos en `macro.series_macro`.

    Usa `get_cer_liquidacion` —la MISMA función que el motor— con su T−10 hábiles.
    Si se calculara distinto, el bono nuevo arrancaría con un divisor que no es el
    que usa la valuación, y la TEA saldría corrida sin que nada falle."""
    if not fecha_emision:
        return None, "1816 no trae la fecha de emisión de este bono"
    try:
        from engines.curvas import cargar_cer, cargar_dias_habiles, get_cer_liquidacion
        cer = get_cer_liquidacion(cargar_cer(dias=4000), cargar_dias_habiles(),
                                  fecha_emision[:10])
    except Exception as e:
        return None, f"no se pudo calcular el CER de emisión: {type(e).__name__}"
    if not cer:
        return None, (f"la serie CER no llega hasta {fecha_emision[:10]} "
                      "(T−10 hábiles) — hay que cargarlo a mano")
    return float(cer), ""


def _ficha_1816(ticker: str) -> dict:
    """Fecha de emisión y denominación desde el catálogo YA persistido
    (`research.mkt_1816_instrumentos`). **Cero créditos**: lo llena
    `jobs/mercado_1816_discovery --catalogo`."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT fecha_emision, denominacion, emisor FROM "
                        "research.mkt_1816_instrumentos WHERE ticker = %s", (ticker,))
            r = cur.fetchone()
        if not r:
            return {}
        return {"fecha_emision": r[0].isoformat() if r[0] else "",
                "denominacion": r[1], "emisor": r[2]}
    except Exception:
        return {}


def simular(ticker: str, *, curva_1816: str, precio: float | None = None) -> dict:
    """Baja el cuadro de 1816 y calcula la TEA que TENDRÍA el bono. **No escribe.**

    `precio`: si no se pasa, se busca el último del snapshot. Sin precio no hay
    TEA — pero el cuadro igual se baja y se muestra, que es la mitad del valor.
    """
    tk = mercado_1816.normalizar_ticker(ticker)
    ejes = curvas_ejes.desde_1816(curva_1816)
    if ejes is None:
        return {"ok": False, "ticker": tk,
                "error": f"la curva «{curva_1816}» de 1816 no se puede traducir a "
                         "ejes: hay que sumarla a EJES_1816 antes de dar de alta"}

    try:
        data = mercado_1816.cashflow(tk)
    except Exception as e:
        return {"ok": False, "ticker": tk, "error": f"1816 no dio el cuadro: {e}"}
    cupones = data.get("cashflow") or []
    if not cupones:
        return {"ok": False, "ticker": tk,
                "error": "1816 devolvió el cuadro VACÍO — este instrumento necesita "
                         "carga manual (puede ser una pata de un dual, que no tiene "
                         "cuadro propio)"}

    from engines.curvas import rama_calculo
    rama_tent = rama_calculo({"emisor_tipo": ejes.emisor_tipo,
                              "moneda_eje": ejes.moneda, "ajuste": ejes.ajuste,
                              "ajuste_alt": ejes.ajuste_alt})
    conv = convertir_flujos(cupones, rama_tent)
    hoy = date.today().isoformat()
    futuros = [f for f in conv["flujos"] if f["fecha"] > hoy]
    vencimiento = conv["flujos"][-1]["fecha"] if conv["flujos"] else ""

    # El CER de emisión NO hace falta pedirlo: la fecha de emisión está en el
    # catálogo de 1816 (ya persistido, 0 créditos) y la serie CER es nuestra.
    ficha = _ficha_1816(tk)
    cer_emision, nota_cer = (None, "")
    if rama_tent == "cer":
        cer_emision, nota_cer = _cer_de_emision(ficha.get("fecha_emision", ""))

    # El símbolo NO se adivina: sale de `mercado.especies`, la misma fuente de la
    # que se derivan los símbolos de `portafolio.assets`. `_contexto_cadena` trae
    # además todo lo que necesita el pre-flight, en una sola conexión.
    ctx = _contexto_cadena(tk)
    simbolo, origen_simbolo = _simbolo_del_bono(tk, ctx.get("especies") or [])
    doc = _doc_simulado(tk, ejes, conv, vencimiento, simbolo, cer_emision)

    out = {
        "ok": True, "ticker": tk, "curva_1816": curva_1816,
        "ejes": {"emisor_tipo": ejes.emisor_tipo, "moneda_eje": ejes.moneda,
                 "ajuste": ejes.ajuste, "ajuste_alt": ejes.ajuste_alt,
                 "ley": ejes.ley},
        "rama": doc["rama"], "escala": conv["escala"],
        "suma_amortizaciones": conv["suma_amort"],
        "cupones": conv["n"], "cupones_futuros": len(futuros),
        "vencimiento": vencimiento,
        "flujo_vencimiento": conv["flujo_vencimiento"],
        "simbolo": simbolo,
        "simbolo_origen": origen_simbolo,
        # ¿Va a tener precio? Es la otra mitad de la pregunta: dar de alta no
        # alcanza — el símbolo tiene que existir en Primary para que el motor lo
        # suscriba y llegue al snapshot.
        "simbolo_estado": _estado_simbolo(simbolo),
        "fecha_emision": ficha.get("fecha_emision") or None,
        "cer_emision": cer_emision,
        "nota_cer": nota_cer,
        "ya_en_curvas": bool(ctx.get("ya_en_curvas")),
        "aplicable": (doc["rama"] in RAMAS_AUTOMATICAS
                      and not (rama_tent == "cer" and not cer_emision)),
        "motivo_no_aplicable": _motivo_no_aplicable(doc["rama"], ejes),
        "flujos_muestra": conv["flujos"][:3] + (["…"] if conv["n"] > 3 else []),
        # El cuadro YA convertido. `aplicar` lo reusa en vez de volver a pedirle el
        # cashflow a 1816: esa llamada cuesta un crédito POR CUPÓN, y pedir dos
        # veces lo mismo no solo gasta — abre la puerta a que se aplique un cuadro
        # distinto del que se mostró, que es justo lo que el simulador previene.
        "cuadro": conv,
    }
    out.update(_simular_tasa(doc, simbolo, precio))

    # El PRE-FLIGHT va al final porque necesita el resultado de la tasa: el paso 8
    # ("¿hay precio?") es el mismo dato que ya se buscó para simular, y volver a
    # pedirlo sería un roundtrip de más diciendo lo mismo.
    out["chequeos"] = _chequeos(
        ticker=tk, curva_1816=curva_1816, ejes=ejes, rama=doc["rama"], conv=conv,
        cer_emision=cer_emision, nota_cer=nota_cer, simbolo=simbolo,
        origen_simbolo=origen_simbolo, ctx=ctx,
        estado_simbolo=out["simbolo_estado"],
        precio=out.get("precio"), tea=out.get("tea"))
    out["veredicto"] = _veredicto(out["chequeos"])
    return out


def _simular_tasa(doc: dict, simbolo: str, precio: float | None) -> dict:
    """Corre el MOTOR sobre el doc simulado. Mismo `calcular_campos` que usa
    `engines/curvas` en producción: si acá saliera otro número, la simulación no
    valdría para nada."""
    from core import market_snapshot
    from engines.curvas import calcular_campos, cargar_cer, cargar_dias_habiles

    if precio is None:
        try:
            m = market_snapshot.cols_map([simbolo], ["last_price"])
            precio = (m.get(simbolo) or {}).get("last_price")
        except Exception:
            precio = None
    if not precio or precio <= 0:
        return {"precio": None, "tea": None,
                "nota_tasa": "sin precio en el snapshot no se puede simular la TEA "
                             "(el cuadro igual queda listo para cargar)"}

    try:
        cer = cargar_cer(dias=1200) if doc.get("ajuste") == "cer" else {}
        habiles = cargar_dias_habiles()
        mep = None
        if doc.get("moneda_flujo") == "USD":
            # `get_ultimo_mep` devuelve un DICT {mep, ccl, canje, oficial, …}, no un
            # float — `calcular_campos` espera el número. Sin MEP un bono USD en
            # pesos queda sin TEA (falla conocida, §3 de SALUD_CURVAS): se reporta,
            # no se inventa un tipo de cambio.
            from api.services.macro import get_ultimo_mep
            mep = (get_ultimo_mep() or {}).get("mep")
        r = calcular_campos({"price": float(precio), "timestamp": datetime.now(UTC)},
                            doc, cer, habiles, mep=mep) or {}
    except Exception as e:
        logger.warning("av_agent: simulación de tasa falló para %s: %s", simbolo, e)
        return {"precio": float(precio), "tea": None,
                "nota_tasa": f"el motor no pudo calcular: {type(e).__name__}"}

    return {"precio": float(precio), "tea": r.get("TEA"),
            "duration": r.get("duration"), "paridad": r.get("paridad"),
            "nota_tasa": "" if r.get("TEA") is not None else
            "el motor no persistiría TEA con este cuadro y este precio — revisar "
            "la escala del flujo o la pata antes de aplicar"}


def aplicar(ticker: str, *, curva_1816: str, actor: str = "") -> dict:
    """Simula y, si la rama lo permite y hay cuadro, **da de alta el bono**.

    Escribe por `bonos_admin.upsert_bono` —la MISMA puerta que usa la mesa desde
    Manager— así no puede existir un alta del agente con otra shape que un alta
    humana. Y deja la acción en el libro (`av_agent_acciones`).
    """
    from api.services import av_agent_acciones as acc
    from api.services import bonos_admin

    sim = simular(ticker, curva_1816=curva_1816)
    if not sim.get("ok"):
        acc.registrar(accion="alta_bono", objetivo=ticker.upper(), ok=False,
                      error=sim.get("error", "")[:300], por=actor)
        return {**sim, "aplicado": False}
    if not sim.get("aplicable"):
        return {**sim, "aplicado": False}
    # Un paso del pre-flight en FALLA es un NO. `aplicable` mira la rama; esto
    # mira la cadena entera — y la cadena es lo que decide si el bono va a
    # existir de verdad o solo estar escrito.
    bloqueos = [c for c in sim.get("chequeos", []) if c["estado"] == FALLA]
    if bloqueos:
        return {**sim, "aplicado": False,
                "error": "el pre-flight no pasa: "
                         + "; ".join(c["titulo"] for c in bloqueos)}

    # La `curva` que pide upsert_bono es la del vocabulario viejo; la RAMA que
    # calculó el motor es exactamente ese valor.
    payload = {
        "ticker_corto": sim["ticker"], "ticker": sim["simbolo"],
        "curva": sim["rama"], "valor_nominal": 100.0,
        "fecha_vencimiento": sim["vencimiento"],
        "moneda_flujo": "USD" if sim["ejes"]["moneda_eje"] == "USD" else "ARS",
        **{k: v for k, v in sim["ejes"].items() if v},
    }
    if sim.get("cer_emision"):
        payload["cer_emision"] = sim["cer_emision"]
    conv = sim["cuadro"]
    if conv["flujo_vencimiento"] is not None:
        payload["flujo_vencimiento"] = conv["flujo_vencimiento"]
    else:
        payload["flujos"] = conv["flujos"]

    try:
        r = bonos_admin.upsert_bono(payload, actor=actor)
    except Exception as e:
        acc.registrar(accion="alta_bono", objetivo=sim["ticker"], ok=False,
                      error=str(e)[:300], detalle={"rama": sim["rama"]}, por=actor)
        return {**sim, "aplicado": False, "error": str(e)}

    acc.registrar(accion="alta_bono", objetivo=sim["ticker"], por=actor,
                  detalle={"rama": sim["rama"], "cupones": sim["cupones"],
                           "escala": sim["escala"], "tea_simulada": sim.get("tea"),
                           "vencimiento": sim["vencimiento"],
                           "simbolo": sim["simbolo"],
                           "simbolo_origen": sim.get("simbolo_origen"),
                           # Lo que NO estaba en verde al momento de aplicar. Dentro
                           # de un mes, «¿por qué este bono no tiene precio?» se
                           # contesta mirando acá en vez de reconstruirlo.
                           "advertencias": [c["titulo"] for c in sim.get("chequeos", [])
                                            if c["estado"] != OK]})
    try:
        from core import curvas_sql
        curvas_sql.invalidar()
    except Exception:
        pass
    return {**sim, "aplicado": True, "upsert": r,
            "aviso": "los motores cargan mercado.curvas AL ARRANCAR: la TEA de este "
                     "bono aparece recién tras reiniciar motor_rofex + motor_curvas"}
