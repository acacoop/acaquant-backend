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

    simbolo = f"MERV - XMEV - {tk} - 24hs"
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
        # ¿Va a tener precio? Es la otra mitad de la pregunta: dar de alta no
        # alcanza — el símbolo tiene que existir en Primary para que el motor lo
        # suscriba y llegue al snapshot.
        "simbolo_estado": _estado_simbolo(simbolo),
        "fecha_emision": ficha.get("fecha_emision") or None,
        "cer_emision": cer_emision,
        "nota_cer": nota_cer,
        "aplicable": (doc["rama"] in RAMAS_AUTOMATICAS
                      and not (rama_tent == "cer" and not cer_emision)),
        "motivo_no_aplicable": _motivo_no_aplicable(doc["rama"], ejes),
        "flujos_muestra": conv["flujos"][:3] + (["…"] if conv["n"] > 3 else []),
    }
    out.update(_simular_tasa(doc, simbolo, precio))
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
    if sim.get("cer_emision"):
        # Sin `cer_emision` un CER no se puede valuar: el motor devuelve solo
        # duration. Se escribe con el mismo valor que se simuló.
        pass

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
    conv = convertir_flujos((mercado_1816.cashflow(sim["ticker"]).get("cashflow") or []),
                            sim["rama"])
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
                           "vencimiento": sim["vencimiento"]})
    try:
        from core import curvas_sql
        curvas_sql.invalidar()
    except Exception:
        pass
    return {**sim, "aplicado": True, "upsert": r,
            "aviso": "los motores cargan mercado.curvas AL ARRANCAR: la TEA de este "
                     "bono aparece recién tras reiniciar motor_rofex + motor_curvas"}
