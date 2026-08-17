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
# ⚠️ `dolar_linked` SE SUMÓ el 2026-08-17. No estaba por una hipótesis mía que el
# código desmiente: `engines/curvas.py:597` dice, textual, que sus flujos usan «el
# shape porcentual sobre VN **igual que soberanos**» y llama a la MISMA
# `monto_flujo_soberano`. O sea que la conversión es tan directa como la de
# soberanos — y el pre-flight se contradecía solo: el paso 3 decía «no se puede
# convertir sin ambigüedad» y el 10, tres renglones abajo, «la rama dolar_linked
# tiene fórmula en engines/curvas.py».
RAMAS_AUTOMATICAS = ("tasa_fija", "soberanos", "cer", "dolar_linked")

# Tolerancia para decidir la ESCALA del cuadro. 1816 manda por VN 100 en los bonos
# por paridad y en NOMINALES en algunas ONs (medido, §4.9 de VISTA_RESEARCH): no
# se asume un divisor global, se mide la Σ de amortizaciones.
_VN100_MIN, _VN100_MAX = 95.0, 105.0


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(v) -> str:
    """Paridad para MOSTRAR, siempre en la misma unidad (porcentaje)."""
    return f"{float(v):.2f}%" if isinstance(v, int | float) else "—"


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
            #
            # `residual_previo_pct` NO lo usa el flujo (`monto_flujo_soberano` lo
            # ignora a propósito): lo usa la PARIDAD, y por no escribirlo el motor
            # lo defaulteaba a 100 y devolvía `paridad = precio_usd` — 68,86% en
            # GD46 contra 72,78% (E2.u). **Medido**: 1816 manda el cronograma
            # COMPLETO desde la emisión (GD46 arranca en 2021-07 y Σ=100.000012),
            # así que el descuento de arriba da el residual VIVO de verdad: 90,909
            # para GD46, contra los 91,3153 de valor técnico que publica 1816 — la
            # diferencia son 0,41 de interés corrido, que nuestra paridad no
            # incluye por definición.
            flujos.append({"fecha": f, "amortizacion_pct": amort,
                           "cupon_sobre_residual": interes,
                           "residual_previo_pct": res_prev})
        elif rama == "dolar_linked":
            # MISMO shape que soberanos (lo dice el motor: `monto_flujo_soberano`),
            # pero **normalizado por la Σ**, que es lo que hace la rama CER.
            #
            # Por qué la diferencia: un soberano de 1816 viene ya en base 100
            # (GD46 midió Σ=100.000012), pero los dólar-linked vienen en NOMINALES
            # DE LA EMISIÓN — D10Y7 y D30O6 miden Σ=148.869,84. Pasar eso crudo
            # como «pct» daría un valor técnico ~1.489 veces más grande y una TEA
            # absurda **sin ningún error**. Dividir por la Σ lo lleva a base 100, y
            # cuando la Σ ya es ~100 la operación es la identidad — así que es
            # correcta en los dos casos.
            flujos.append({
                "fecha": f,
                "amortizacion_pct": (amort / suma_amort * 100) if suma_amort else 0.0,
                "cupon_sobre_residual": (interes / suma_amort * 100) if suma_amort else 0.0,
            })
        else:
            flujos.append({"fecha": f, "amortizacion": amort, "interes": interes})

    # Bullet: un solo pago al final → el master lo guarda como `flujo_vencimiento`.
    #
    # ⚠️ **SOLO en la rama `tasa_fija`.** `flujo_vencimiento` es la shape de ESA
    # rama y de ninguna otra: `calcular_campos` lo lee en el `if curva ==
    # "tasa_fija"`, mientras las ramas `cer` y `soberanos` arman su cronograma
    # desde `flujos[]` y **ni miran** ese campo.
    #
    # El bug que esto arregla (2026-08-17, TZXM8): un CER CERO CUPÓN tiene un solo
    # pago, así que caía en el atajo del bullet y el doc salía con
    # `flujo_vencimiento` y SIN `flujos`. Resultado: `flujos_futuros = []` y el
    # motor devolvía solo duration. **Y no daba error** — el bono se veía bien
    # cargado, sin tasa, sin explicación. Un cero cupón CER no es una LECAP: su
    # pago se ajusta por CER y por eso necesita el cronograma, no un monto fijo.
    fv = None
    if len(filas) == 1 and rama == "tasa_fija":
        fv = round(filas[0][1] + filas[0][2], 6)

    return {"flujos": flujos, "escala": escala, "suma_amort": suma_amort,
            "flujo_vencimiento": fv, "n": len(filas), "rama": rama}


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


def _tasa_externa(ejes) -> tuple[str, str]:
    """`(fuente, job)` de la tasa de este ajuste. `('1816', 'jobs/tamar_1816')`
    para un TAMAR; `('motor', '')` o `('', '')` para el resto.

    **Es la pregunta que el agente no sabía hacer.** Veía la rama `otros`, no
    encontraba fórmula y concluía «la TEA va a quedar vacía» — cuando el TAMAR es
    el caso MÁS resuelto que hay: no lo calculamos a propósito, lo trae un job.
    """
    return tasa_externa_de(ejes.ajuste)


def tasa_externa_de(ajuste: str | None) -> tuple[str, str]:
    """La misma pregunta pero con el ajuste pelado — `aplicar` tiene los ejes
    como dict, no como objeto."""
    try:
        from core import curvas_catalogo
        return (curvas_catalogo.fuente_valuacion(ajuste) or "",
                curvas_catalogo.job_de_la_tasa(ajuste))
    except Exception:
        return "", ""


def _alta_automatica(rama: str, ejes) -> bool:
    """¿Se puede dar de alta sin que lo cargue un humano?

    Dos caminos, no uno: (a) la rama convierte el cuadro sin ambigüedad, o (b) la
    tasa **no la calculamos nosotros** y la trae 1816 — ahí el cuadro solo tiene
    que quedar bien guardado, que es el caso de conversión más simple que existe
    (montos absolutos, tal cual los manda 1816).
    """
    return rama in RAMAS_AUTOMATICAS or _tasa_externa(ejes)[0] == "1816"


def _motivo_no_aplicable(rama: str, ejes) -> str:
    """Por qué ESTA rama no se da de alta sola. Un motivo por rama, no un texto
    fijo: el primero decía «en CER cupon_sobre_residual es una TASA» hasta para un
    BADLAR, que no tiene nada que ver con CER. Un mensaje que no habla del caso
    que uno está mirando no explica: confunde."""
    if _alta_automatica(rama, ejes):
        return ""
    if rama == "otros":
        return (f"el ajuste «{ejes.ajuste}» no tiene rama de cálculo en el motor "
                "(cae en el `else`, que solo computa duration) y su curva tampoco "
                "está marcada como valuada por 1816. La TEA necesita que se escriba "
                "la fórmula, o que se marque la curva con fuente=1816.")
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
# ── LOS CINCO ESTADOS DE UN PASO ────────────────────────────────────────────
#
# **Rediseñado el 2026-08-17 tras el incidente de GD46.** El modelo viejo tenía
# cuatro estados, pero `atencion` significaba DOS cosas incompatibles:
#
#   - "esto está mal" (la paridad de GD46: 0,05% contra 75,56% de 1816)
#   - "esto es lo que va a pasar" (el alta va a sembrar la especie)
#
# Con las dos en el mismo ámbar, **una contradicción probada del cronograma se
# leía igual que un aviso de rutina**, y el veredicto igual decía «se puede
# aplicar» con el botón APLICAR habilitado. Palabras del user: *«que me diga que
# está OK cuando algo tan clave como esto está así… ahí no puede haber un
# warning»*. Y tenía otra mitad: *«hay algunos que no son ni buenos ni malos»*.
#
# Ahora **cada estado significa UNA sola cosa**, y de ahí sale la decisión —
# manual y, sobre todo, la del día que esto corra solo:
#
#   ✔ ok       verificado y correcto                      → no frena nada
#   ○ info     ni bueno ni malo: lo que va a pasar,        → NO CUENTA para nada
#              o contexto. No es un aviso.
#   ▲ revisar  se midió y NO cierra, sin ser concluyente   → frena el AUTOMÁTICO
#   ✖ bloquea  probado mal                                 → frena TODO
#   ? no_se    no se pudo verificar                        → frena el AUTOMÁTICO
#
# Los dos del medio existen porque colapsarlos en un extremo sería mentir: una
# paridad que difiere 1% no está probada mal, y "no pude leer la base" no es "está
# bien". Pero **ninguno de los dos es informativo**: los dos paran al robot.
OK, INFO, REVISAR, BLOQUEA, NO_SE = "ok", "info", "revisar", "bloquea", "no_se_puede_saber"

# Lo que NUNCA puede aplicarse solo, y lo que además no puede aplicarse a mano.
_FRENAN_AUTOMATICO = (BLOQUEA, REVISAR, NO_SE)
_FRENAN_TODO = (BLOQUEA,)


# ── Bandas del control cruzado ──────────────────────────────────────────────
#
# **La señal primaria es la PARIDAD, no la TEA.** Fue la lección de la corrida del
# 2026-08-17 y cambia el diseño:
#
#   paridad = precio / valor técnico
#
# Depende SOLO del precio y del cronograma de flujos — que es EXACTAMENTE lo que
# el cotejo quiere auditar ("¿está bien convertido el cuadro?"). La TEA, en
# cambio, agrega dos capas de ruido que no tienen nada que ver con esa pregunta:
#
#   1. **La convención de días.** Medido: 1816 usa `180-360` para GD46 y TZXM8;
#      nuestro motor usa `xirr` con fechas reales (act/365). Son dos formas
#      legítimas de anualizar el MISMO flujo y dan números distintos.
#   2. **El tipo de cambio**, en los bonos en dólares. Medido en GD46: la misma
#      tasa da 9,71% pidiendo `ars` (ellos dividen por CCL), 9,79% con `ccl` y
#      9,08% con `mep`. Contra nuestro 7,69%: 202 / 210 / **139 bps**.
#
# O sea que pedir `mep` bajó de 202 a 139 bps — **mejoró, no cerró**, y lo que
# queda es convención. Perseguir esos bps sería perseguir un empate imposible
# entre dos métodos de anualización distintos.
#
# Comparando PARIDAD las dos capas desaparecen y queda la pregunta sola. Si la
# paridad coincide, el cuadro está bien y la diferencia de TEA es método.
#
# ⚠️ Las bandas siguen siendo un PRIMER CORTE. La única evidencia dura sobre
# tasas es el TAMAR contra la planilla de la mesa (7 bps cuando todo está bien).
# Por eso nada de esto BLOQUEA: avisa.
_PARIDAD_COINCIDE, _PARIDAD_MIRAR = 0.5, 3.0     # % de diferencia RELATIVA
_BPS_COINCIDE, _BPS_MIRAR = 50.0, 150.0          # TEA, señal secundaria
# La DURATION es el testigo del CRONOGRAMA: no depende del precio, ni del tipo de
# cambio, ni del interés corrido. Medido el 2026-08-17: GD46 6,6039 contra 6,6118
# (0,12%) y BPOA8 2,1285 contra 2,1266 (0,09%) — o sea que cuando el cuadro está
# bien la coincidencia es de tercer decimal. Un cupón de más o de menos la mueve
# mucho más que eso.
_DURATION_COINCIDE, _DURATION_MIRAR = 1.0, 5.0   # % de diferencia RELATIVA


def _paso(clave: str, titulo: str, estado: str, detalle: str,
          *, tabla: str = "", accion: str = "", aviso: str = "") -> dict:
    """Un eslabón. **`clave` es la identidad, `n` es presentación** — el `n` se
    numera al final según los pasos que hayan aplicado (el de CER no siempre
    está, el control cruzado tampoco). Si el orden fuera la identidad, insertar
    un paso en el medio renumeraría todo y rompería a quien lo referencie.

    `frena_auto` / `frena` se derivan del estado y viajan RESUELTOS: el front no
    tiene que saber qué estado bloquea qué, y el día que exista la lane
    automática lee el mismo booleano que ve el humano. Dos lugares decidiendo lo
    mismo es exactamente cómo nació el bug que este rediseño arregla."""
    return {"clave": clave, "titulo": titulo, "estado": estado, "detalle": detalle,
            "tabla": tabla, "accion": accion,
            # `aviso` = TRABAJO MANUAL que queda pendiente DESPUÉS de aplicar.
            # No es un error: es la parte que ninguna fuente puede completar y que
            # el agente igual te dejó lista para tipear un número. Alimenta la
            # sección AVISOS.
            "aviso": aviso,
            "frena": estado in _FRENAN_TODO,
            "frena_auto": estado in _FRENAN_AUTOMATICO}


def _tea_de_1816_a_nuestro_precio(ticker: str, precio: float, moneda: str) -> dict:
    """La TEA que 1816 calcularía **a NUESTRO precio**. El cotejo definitivo.

    **Por qué hace falta.** Comparar nuestra tasa contra la de ellos tiene un
    agujero: cada uno la calcula sobre SU precio, así que una diferencia puede ser
    la fórmula o puede ser el insumo — y no hay forma de distinguirlo. Con el
    endpoint de input manual se le pasa el MISMO número a los dos, y lo que quede
    es exclusivamente convención o cronograma.

    Es el diagnóstico que faltaba para cerrar los 202 bps de GD46 sin teorizar.
    Costo: 3 créditos (el endpoint cobra por campo, no por ticker × campo).
    """
    try:
        r = mercado_1816.indicadores_de(
            ticker, ["tea", "paridad", "convencionTna"],
            moneda=moneda, precioDirty=float(precio))
    except Exception as e:
        return {"error": f"{e}"[:200]}
    ind = (r or {}).get("indicadores") or {}
    return {"tea": _num(ind.get("tea")), "paridad": _num(ind.get("paridad")),
            "convencion_tna": ind.get("convencionTna"), "precio": float(precio)}


def cota_devengado(cupones: list[dict], desde: str = "") -> float | None:
    """Cuánto pueden diferir NUESTRA paridad y la de 1816 **sin que nada esté mal**.

    Las dos paridades no miden lo mismo, y es por definición, no por error:

        nuestra  = precio / residual                 (`engines/curvas.py`)
        1816     = precio / (residual + devengado)   (valor técnico)

    Entonces la nuestra da SIEMPRE un poco más alta, y «un poco» tiene un techo
    exacto: el devengado nunca supera **un cupón entero** sobre el residual vivo.
    Medido el 2026-08-17 en los dos casos que teníamos: GD46 difería 0,24% con un
    cupón de 2,5%, y BPOA8 0,90% — los dos MUY por debajo de su cota, y los dos
    con la TEA y la duration clavadas contra 1816.

    **Cota y no predicción, a propósito.** Calcular el devengado exacto obliga a
    elegir una convención de días (30/360 vs reales, fecha teórica vs efectiva) y
    a acertarle a la que usa 1816 — o sea, a inventar una hipótesis nueva para
    tapar un problema que ya se resuelve sin ella. La cota se deriva del cuadro y
    de nada más.

    Y no pierde poder de detección: un error de ESCALA —lo único que este cotejo
    existe para cazar— mueve la paridad 100 o 1.000 veces, dos órdenes de magnitud
    arriba de cualquier cupón.

    Se calcula sobre el cuadro CRUDO de 1816 (`flujoInteres` / `flujoAmortizacion`)
    porque ahí las unidades son las mismas para todas las ramas — en el nuestro,
    `cupon_sobre_residual` es una tasa en CER y un monto en soberanos.
    """
    hoy = desde or date.today().isoformat()
    futuros = [c for c in cupones if isinstance(c, dict)
               and (_fecha(c.get("fechaPagoEfectiva"))
                    or _fecha(c.get("fechaPagoTeorica"))) > hoy]
    if not futuros:
        return None
    futuros.sort(key=lambda c: _fecha(c.get("fechaPagoEfectiva"))
                 or _fecha(c.get("fechaPagoTeorica")))
    residual = sum(_num(c.get("flujoAmortizacion")) or 0.0 for c in futuros)
    interes = _num(futuros[0].get("flujoInteres")) or 0.0
    if residual <= 0 or interes <= 0:
        return None
    return interes / residual * 100


def _cotejo_tea(tea, ref: dict, *, job_tasa: str = "", paridad=None,
                duration=None, cota_ic: float | None = None) -> dict:
    """La segunda opinión sobre el MISMO bono. Un cuadro mal convertido no tira
    error: da un número plausible y equivocado.

    **Compara PARIDAD, no TEA.** La paridad es `precio / valor técnico`: depende
    solo del precio y del cronograma, que es exactamente lo que hay que auditar.
    La TEA agrega convención de días y —en dólares— tipo de cambio, dos capas que
    no dicen nada sobre si el cuadro está bien. La TEA se sigue mostrando, pero
    como dato secundario.

    **Salvo cuando no hay dos cálculos.** Si la tasa la trae un job, la de 1816 ES
    la nuestra: compararlas sería compararse consigo mismo y salir siempre bien.
    """
    if job_tasa:
        suya = ref.get("tea")
        return _paso("cotejo_1816", "Control cruzado de la tasa", OK,
                     "no aplica: la tasa de este bono ES la de 1816 "
                     f"({job_tasa}), no una cuenta nuestra. No hay dos números "
                     "que comparar" + (f" — 1816 publica {float(suya):.4%}."
                                       if isinstance(suya, int | float) else "."),
                     tabla="mercado.tamar_1816")

    mismo = ref.get("a_nuestro_precio") or {}
    # Su paridad al MISMO precio si la tenemos; si no, la de su propio precio.
    suya_par = mismo.get("paridad") if mismo.get("paridad") is not None \
        else ref.get("paridad")
    base = ("al MISMO precio" if mismo.get("paridad") is not None
            else "cada uno sobre SU precio")

    # Escalas: nuestro motor devuelve la paridad en PORCENTAJE (72.78); 1816 la
    # publica como FRACCIÓN (0.7278). Compararlas crudas daría siempre "se
    # contradicen" — es la clase de bug que no da error y solo da alarmas.
    nuestra_par = float(paridad) if isinstance(paridad, int | float) else None
    suya_par = float(suya_par) * 100 if isinstance(suya_par, int | float) else None

    # La TEA, como línea de apoyo.
    suya_tea = mismo.get("tea") if mismo.get("tea") is not None else ref.get("tea")
    apoyo = ""
    if isinstance(tea, int | float) and isinstance(suya_tea, int | float):
        bps = abs(float(tea) - float(suya_tea)) * 10_000
        conv = (mismo.get("convencion_tna") or ref.get("convencion_tna") or "")
        apoyo = (f" · TEA: nuestra {float(tea):.4%} vs 1816 {float(suya_tea):.4%} "
                 f"({bps:,.0f} bps)"
                 + (f", ellos anualizan {conv} y nosotros con días reales — "
                    "una diferencia acá NO significa que el cuadro esté mal"
                    if conv and bps > _BPS_COINCIDE else ""))

    if nuestra_par is None or suya_par is None:
        return _paso("cotejo_1816", "El cuadro coincide con el de 1816", NO_SE,
                     "falta la paridad de alguno de los dos" + apoyo
                     + (f" ({ref['error']})" if ref.get("error") else ""),
                     tabla="1816 /indicadores")

    dif_rel = abs(nuestra_par - suya_par) / suya_par * 100 if suya_par else 999.0
    linea = (f"paridad nuestra {nuestra_par:.2f}% vs 1816 {suya_par:.2f}% "
             f"→ {dif_rel:.2f}% de diferencia ({base}){apoyo}")

    # SEGUNDO TESTIGO: la duration. Mide otra cosa que la paridad y las dos hacen
    # falta — la paridad es invariante a las FECHAS y la duration es invariante a
    # la ESCALA, así que cada una es ciega justo donde la otra ve. La leyenda del
    # cuadro ya decía «depende solo del cuadro y las fechas» y no la usábamos para
    # decidir nada: era evidencia a la vista, sin voto.
    dur_dif = None
    if isinstance(duration, int | float) and isinstance(ref.get("duration"), int | float):
        suya_dur = float(ref["duration"])
        if suya_dur:
            dur_dif = abs(float(duration) - suya_dur) / suya_dur * 100
            linea += (f" · duration: nuestra {float(duration):.4f} vs 1816 "
                      f"{suya_dur:.4f} ({dur_dif:.2f}%)")
    dur_ok = dur_dif is not None and dur_dif <= _DURATION_COINCIDE
    if dur_dif is not None and dur_dif > _DURATION_MIRAR:
        return _paso("cotejo_1816", "El cuadro coincide con el de 1816", BLOQUEA,
                     linea + ". **La duration no coincide** → las fechas o los "
                             "cupones que bajamos no son los de ellos. La "
                             "duration no depende del precio ni del tipo de "
                             "cambio: si difiere, difiere el cronograma.",
                     tabla="1816 /indicadores",
                     accion="comparar fecha por fecha el cuadro de abajo contra 1816")

    if dif_rel <= _PARIDAD_COINCIDE:
        return _paso("cotejo_1816", "El cuadro coincide con el de 1816", OK,
                     linea + ". Coincide → el cronograma que vamos a escribir "
                             "es el mismo que el de ellos.",
                     tabla="1816 /indicadores")
    # LA DIFERENCIA ESPERADA. Nuestra paridad es sobre el residual y la de 1816
    # sobre el valor técnico (residual + devengado), así que la nuestra da SIEMPRE
    # un poco más alta — y el techo de «un poco» es un cupón entero. Si la
    # diferencia va en ese sentido, está por debajo de la cota, y encima la
    # duration coincide, no hay nada que revisar: es la definición, no un error.
    # Sin esto, BPOA8 quedaba en «revisar» con la TEA clavada (7,08% contra
    # 7,0814%) y sin ningún dato que cargar — un aviso que no pedía nada.
    if (cota_ic and dif_rel <= cota_ic and nuestra_par >= suya_par and dur_ok):
        return _paso("cotejo_1816", "El cuadro coincide con el de 1816", OK,
                     linea + f". La diferencia es el INTERÉS CORRIDO: nuestra "
                             f"paridad es sobre el residual y la de ellos sobre "
                             f"el valor técnico (residual + devengado), así que "
                             f"la nuestra da más alta hasta un cupón entero "
                             f"({cota_ic:.2f}%). Con la duration clavada, el "
                             f"cronograma es el mismo.",
                     tabla="1816 /indicadores")
    if dif_rel <= _PARIDAD_MIRAR:
        return _paso("cotejo_1816", "El cuadro coincide con el de 1816", REVISAR,
                     linea + ". Casi: puede faltar o sobrar un cupón, o correrse "
                             "una fecha. No alcanza para afirmar que está mal.",
                     tabla="1816 /indicadores",
                     accion="comparar el cuadro de abajo contra la pantalla de 1816")
    # **BLOQUEA, y esto es el corazón del rediseño.** Hasta el 2026-08-17 este
    # caso era `atencion` con el texto «no se bloquea el alta: el umbral es un
    # primer corte, no una medición». Ese razonamiento estaba mal: el umbral es
    # un primer corte para decidir CUÁNDO alarmarse, pero una vez cruzado por 20
    # veces —GD46 dio 99,94%— lo que hay no es una alarma difusa, es una
    # DEMOSTRACIÓN de que el cronograma es otro. Escribir eso en el master es
    # meter un bono mal valuado y que nadie lo note: la TEA sale plausible.
    return _paso("cotejo_1816", "El cuadro coincide con el de 1816", BLOQUEA,
                 linea + ". **Se contradicen** → otro valor técnico, o sea otro "
                         "cronograma. Aplicar así escribe un bono con una TEA "
                         "plausible y equivocada.",
                 tabla="1816 /indicadores",
                 accion="revisar «cómo se calculó»: escala del cuadro y Σ de "
                        "amortizaciones son los dos sospechosos")


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
              precio, tea, fuente_precio: str = "", ref: dict | None = None,
              paridad=None, ficha_curvas: dict | None = None,
              duration=None, cupones: list[dict] | None = None) -> list[dict]:
    """La lista ordenada. Se devuelve ENTERA, con los pasos en verde incluidos.

    Mostrar solo lo que falla obliga al que mira a confiar en que el resto se
    chequeó — que es exactamente lo que el user no quiere. Ver los ocho pasos
    verdes ES la respuesta a «¿qué pasa si aplico?».
    """
    ps: list[dict] = []
    ref = ref or {}

    ps.append(_paso("ejes", "La curva de 1816 se traduce a nuestros ejes", OK,
                    f"«{curva_1816}» → emisor {ejes.emisor_tipo} · moneda "
                    f"{ejes.moneda} · ajuste {ejes.ajuste}"
                    + (f" (+{ejes.ajuste_alt})" if ejes.ajuste_alt else "")
                    + (f" · ley {ejes.ley}" if ejes.ley else ""),
                    tabla="mercado.curvas (ejes)"))

    # La escala **solo importa donde los montos se guardan ABSOLUTOS**
    # (`tasa_fija`, `soberanos`). En la rama `cer` la conversión divide todo por
    # `suma_amort` para expresar porcentajes, así que es invariante a la escala:
    # avisar ahí era un falso positivo — TZXM8 salía en ámbar con Σ=112,65
    # cuando ese número no afecta a NADA de lo que se escribe.
    escala_importa = rama in ("tasa_fija", "soberanos")
    escala_ok = conv["escala"] == "vn100" or not escala_importa
    ps.append(_paso("cuadro", "1816 mandó el cuadro de flujos", OK if escala_ok else INFO,
                    f"{conv['n']} cupón/es · Σ amortizaciones {conv['suma_amort']} → "
                    f"escala {conv['escala']}"
                    + ("" if escala_ok else
                       " — no suma ~100: viene en nominales y esta rama guarda "
                       "montos absolutos.")
                    + (" — la rama «cer» lo pasa a porcentajes (divide por la Σ), "
                       "así que la escala no la afecta."
                       if rama == "cer" and conv["escala"] != "vn100" else ""),
                    tabla="1816 /cashflow"))

    _fuente_tasa, job_tasa = _tasa_externa(ejes)
    auto = _alta_automatica(rama, ejes)
    if rama in RAMAS_AUTOMATICAS:
        detalle_rama = f"rama «{rama}» — conversión inequívoca"
    elif auto:
        # El caso TAMAR: no es que "no tiene fórmula", es que la tasa NO la
        # calculamos NOSOTROS a propósito. El cuadro se guarda igual (montos
        # absolutos, tal cual los manda 1816) — es la conversión más simple que
        # hay — y la TEA la trae el job.
        detalle_rama = (f"rama «{rama}» — la tasa la trae {job_tasa or '1816'}, "
                        "no la calculamos. El cuadro se guarda con montos "
                        "absolutos, tal cual los manda 1816.")
    else:
        detalle_rama = f"rama «{rama}» — {_motivo_no_aplicable(rama, ejes)}"
    ps.append(_paso("rama", "El cuadro se puede convertir sin ambigüedad",
                    OK if auto else BLOQUEA, detalle_rama,
                    tabla="engines/curvas.py::rama_calculo",
                    accion="" if auto else "cargar a mano con el cuadro de abajo"))

    # ⚠️ **¿El DESTINO va a aceptar esto?** El pre-flight validaba 13 cosas sobre
    # los DATOS y ninguna sobre si la escritura iba a entrar. TMG27 pasó los 13
    # pasos en verde, mostró APLICAR, y `upsert_bono` lo rechazó con «curva
    # inválida: 'otros'». El user: *«¿por qué lo permitió aplicar?»* — porque
    # nadie estaba chequeando eso.
    #
    # Es el eslabón que faltaba y el más barato de todos: se valida el mismo campo
    # contra la misma constante que usa el writer, así que no puede desincronizarse.
    cur_dest = curva_destino(rama, ejes)
    ps.append(_paso("curva_destino", "La escritura va a ser aceptada",
                    OK if cur_dest else BLOQUEA,
                    f"curva «{cur_dest}»" if cur_dest else
                    f"el ajuste «{ejes.ajuste}» no tiene curva en el catálogo de "
                    "mercado.curvas, así que el alta sería rechazada al escribir",
                    tabla="mercado.curvas (curva)",
                    accion="" if cur_dest else
                           f"crear la curva «{ejes.ajuste}» desde el agente (pestaña ME "
                           "PREGUNTA), y el alta queda habilitada sola"))

    if rama == "cer":
        # **NO bloquea (decisión del user, 2026-08-17).** Es el único dato que el
        # agente no puede sacar de ningún lado, y el alta igual hace todo el resto:
        # flujos, ejes, ficha, especies. *«A los CER les perdonamos: igual me saca
        # el laburo de cargarlo en la base, me lo deja sencillo, solo poner el CER
        # de emisión y nada más.»* Queda como AVISO — trabajo manual anotado, no
        # una puerta cerrada. Sí frena la lane automática: un robot dejaría el bono
        # sin tasa y sin nadie enterado.
        ps.append(_paso("cer_emision", "CER de emisión resuelto",
                        OK if cer_emision else REVISAR,
                        f"{cer_emision} (de la fecha de emisión de 1816, T−10 hábiles)"
                        if cer_emision else
                        f"{nota_cer or 'no se pudo calcular'}. **El alta se hace igual**: "
                        "queda en AVISOS y sin este número el bono no muestra tasa.",
                        tabla="macro.series_macro (CER)",
                        aviso="" if cer_emision else
                              f"Cargar el CER de emisión de {ticker} en Manager → Títulos"))

    if not ctx.get("ok"):
        ps.append(_paso("especie", "El papel cotiza (especie + símbolo + precio)", NO_SE,
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
        ps.append(_paso("especie", "El papel tiene especie: cotiza con un símbolo", OK,
                        f"{len(activas)} pata/s en el catálogo — {det}",
                        tabla="mercado.especies"))
    else:
        # NO bloquea: **sembrarla es parte del alta**. Que el papel todavía no
        # tenga especie no dice "esto no va a funcionar", dice "esto no está
        # listo" — y dejarlo listo es justamente el trabajo. Se siembra al
        # aplicar, con las patas ARS y USD que Primary liste (paso `sembrar`).
        ps.append(_paso("especie", "El papel tiene especie: cotiza con un símbolo",
                        INFO,
                        f"«{simbolo}» está armado por convención. El alta la "
                        "siembra sola — no hay que hacer nada.",
                        tabla="mercado.especies"))

    # 6 — el gate REAL de la suscripción.
    con = estado_simbolo.get("conocido")
    ps.append(_paso(
        "primary", "Primary lista ese símbolo (si no, el WS lo filtra)",
        OK if con is True else (BLOQUEA if con is False else NO_SE),
        f"«{simbolo}» ({'de especies' if origen_simbolo == 'especies' else 'armado'}) — "
        + estado_simbolo.get("nota", ""),
        tabla="manager.pyrofex_instruments · core/instrumentos_validos",
        accion="" if con is not False else
               "verificar la grafía real en Primary — `core/websocket."
               "agregar_suscripciones` descarta lo que no está en el catálogo"))

    # 7 — el motor arma su universo AL ARRANCAR. Este paso NUNCA es verde solo:
    #     es un paso MANUAL, y decirlo es la mitad del valor del pre-flight.
    ps.append(_paso("suscripcion", "El motor lo suscribe y el precio llega a market_snapshot",
                    INFO,
                    "los motores leen mercado.curvas al arrancar: hasta "
                    "reiniciarlos, el bono queda escrito pero sin precio.",
                    tabla="mercado.market_snapshot",
                    accion="tras aplicar: reiniciar motor_rofex y motor_curvas"))

    # 8 — ¿hay con qué calcular la tasa AHORA? Un bono nuevo nunca tiene snapshot
    #     (nunca se suscribió), así que se cae al precio de referencia de 1816.
    #
    # ⚠️ **HAY PRECIO Y EL MOTOR NO DEVOLVIÓ TEA = BLOQUEA.** Este paso salía en
    # VERDE ✔ diciendo, en el mismo renglón, «pero el motor NO devolvió TEA:
    # revisar la escala» — un tilde verde sobre un texto que describe una falla.
    # Y es una falla DURA, no una sospecha: se le dio al motor el cuadro real y
    # el precio real, y no calculó. Aplicar así escribe un bono que va a quedar
    # con la celda de TEA vacía para siempre y nadie va a saber por qué.
    #
    # La excepción es real y hay que respetarla: a un TAMAR/BADLAR **no le
    # calculamos la tasa a propósito** (la trae 1816). Ahí que el motor no
    # devuelva TEA es lo ESPERADO, no un defecto — tratarlo como falla habría
    # bloqueado a los 18 bonos que justamente están bien.
    sin_tea = not isinstance(tea, int | float)
    tea_es_nuestra = _fuente_tasa != "1816"
    # ⚠️ **CAUSALIDAD.** Si ya sabemos POR QUÉ no calculó, este paso no es un
    # hallazgo propio: es la consecuencia de otro. En TZXA7 (2026-08-17) faltaba el
    # CER de emisión —`engines/curvas.py:438` sale con solo duration si no está— y
    # la pantalla mostraba TRES pasos en rojo por UNA causa, encima mandando a
    # «revisar la escala del cuadro», que no tenía nada que ver. El user: *«no lo
    # entiendo, no es claro. Si hay flujo y hay precio, ¿por qué no podrías
    # simular?»*. Un diagnóstico que apunta al lugar equivocado es peor que no
    # tenerlo: hace perder el tiempo buscando donde no está.
    causa = ("falta el CER de emisión (paso de arriba): sin ese número la rama CER "
             "no puede calcular el valor técnico" if rama == "cer" and not cer_emision
             else "")
    est_precio = (OK if (causa or not precio or not sin_tea or not tea_es_nuestra)
                  else BLOQUEA)
    if precio:
        origen = (f"precio de 1816 {precio} ({ref.get('fecha') or '—'}, no se guarda)"
                  if fuente_precio == "1816" else f"precio {precio} del snapshot")
        if not sin_tea:
            cola = f" → TEA {tea:.4%}"
        elif causa:
            cola = f" → sin TEA: {causa}"
        elif not tea_es_nuestra:
            cola = " → sin TEA, y está bien: la tasa la trae 1816"
        else:
            cola = (" → **el motor no calculó la TEA** con este cuadro y este "
                    "precio. El bono nacería sin tasa")
        ps.append(_paso("precio", "Hay precio para simular la tasa", est_precio,
                        origen + cola,
                        tabla=("1816 /indicadores" if fuente_precio == "1816"
                               else "mercado.market_snapshot"),
                        accion="" if est_precio == OK else
                               "revisar la escala del cuadro y la pata"))
    else:
        ps.append(_paso("precio", "Hay precio para simular la tasa", INFO,
                        "sin precio"
                        + (f": {ref['error']}" if ref.get("error") else "")
                        + ". La TEA aparece con el primer trade.",
                        tabla="mercado.market_snapshot · 1816"))

    # 8.b — EL CONTROL CRUZADO. Es el paso que más vale de toda la lista.
    #
    # Un cuadro de flujos mal convertido NO da error: da una TEA plausible pero
    # equivocada, y ahí se acaban las formas de darse cuenta leyendo. Correr
    # NUESTRO motor sobre el precio de 1816 y comparar contra LA TEA DE ELLOS es
    # una segunda opinión independiente sobre el mismo bono — y hasta ahora era
    # imposible de tener justo cuando más falta hace: en un bono nuevo.
    ps.append(_cotejo_tea(tea, ref, job_tasa=job_tasa, paridad=paridad,
                          duration=duration,
                          cota_ic=cota_devengado(cupones or [])))

    # 9 — ¿QUIÉN calcula la tasa? Dos respuestas válidas, no una.
    if _fuente_tasa == "1816":
        # El job selecciona por `ajuste ∈ ajustes_de_1816()`, así que este ticker
        # entra SOLO en cuanto el alta escriba su fila: no hay nada que registrar
        # a mano. Decirlo es la mitad del valor — antes esto se leía como un error.
        ps.append(_paso("tea_motor", "La tasa la TRAE 1816, no la calculamos", OK,
                        f"a un {ejes.ajuste.upper()} no le calculamos la tasa a "
                        "propósito (es de tasa promedio: la parte futura habría que "
                        f"proyectarla). La baja **{job_tasa or 'el job'}** con su TEA "
                        "y su margen, y el ticker entra solo por su ajuste.",
                        tabla="mercado.tamar_1816"))
    elif rama in RAMAS_AUTOMATICAS or rama == "dolar_linked":
        ps.append(_paso("tea_motor", "El motor de curvas va a calcular la TEA", OK,
                        f"la rama «{rama}» tiene fórmula en engines/curvas.py",
                        tabla="engines/curvas.py::calcular_campos"))
    else:
        ps.append(_paso("tea_motor", "El motor de curvas va a calcular la TEA", BLOQUEA,
                        f"el ajuste «{ejes.ajuste}» cae en el `else` del motor: solo "
                        "computa duration. El bono va a tener precio pero la celda "
                        "de TEA queda vacía.",
                        tabla="engines/curvas.py",
                        accion="marcar la curva como fuente=1816, o escribir su fórmula"))

    # 10 — sin espejo en assets el bono existe para la vista pero no para el AuM.
    assets = ctx["assets"]
    vig = [a for a in assets if a["vigente"]]
    if vig:
        ps.append(_paso("assets", "Entra al AuM: hay espejo en portafolio.assets", OK,
                        f"{len(vig)} unidad/es con ticker {ticker} — "
                        + ", ".join(a["unidad"] for a in vig[:2])
                        + ("…" if len(vig) > 2 else ""),
                        tabla="portafolio.assets"))
    else:
        ps.append(_paso("assets", "Entra al AuM: hay espejo en portafolio.assets", INFO,
                        f"no hay unidad con ticker {ticker}: entra a la curva, "
                        "no al AuM. Aparece sola cuando alguien lo tenga.",
                        tabla="portafolio.assets"))

    # La FICHA. Responde una pregunta que nadie podía contestar mirando la
    # pantalla: **¿el bono entra completo, o entra pelado?** `upsert_bono` acepta
    # 19 campos; medido el 2026-08-17, el agente mandaba 14.
    fc = ficha_curvas or {}
    escritos = ["ejes (emisor_tipo · moneda_eje · ajuste · ley)", "cuadro de flujos",
                "vencimiento", "valor_nominal", "moneda_flujo", "símbolo de mercado"]
    escritos += [f"{k} = {v}" for k, v in sorted(fc.items())]
    if cer_emision:
        escritos.append(f"cer_emision = {cer_emision}")
    faltan = [c for c in ("emisor", "fecha_emision", "cupon_anual")
              if c not in fc]
    ps.append(_paso("ficha", "El bono entra COMPLETO, no pelado",
                    OK if not faltan else INFO,
                    f"se escriben {len(escritos)} campos — " + " · ".join(escritos[:9])
                    + ("…" if len(escritos) > 9 else "")
                    + (f". Quedan vacíos: {', '.join(faltan)}." if faltan else "")
                    + (" `cupon_anual` solo se deriva si es cero cupón: con cupones "
                       "habría que asumir la frecuencia." if "cupon_anual" in faltan
                       else ""),
                    tabla="mercado.curvas",
                    accion="completar a mano en Manager → TÍTULOS" if faltan else ""))

    # TASA EXTERNA: el bono nace CON su tasa y su margen, o no nace entero.
    if _fuente_tasa == "1816":
        ps.append(_paso("tasa_1816", "Al aplicar: cargar la TASA y el MARGEN de 1816",
                        INFO,
                        f"el **margen** es lo que mira la mesa en un "
                        f"{ejes.ajuste.upper()}. El alta lo trae de 1816 ahora, sin "
                        f"esperar a {job_tasa or 'el job'}.",
                        tabla="mercado.tamar_1816",
                        accion="lo hace solo — no hay que correr nada"))

    # ÚLTIMO PASO — y último a propósito: es lo que el alta VA A HACER, no un
    # requisito previo. «Se agrega como instancia final, porque hay que agregar
    # algo que sabés que va a quedar productivo» (el user, 2026-08-17).
    if not activas:
        ps.append(_paso("sembrar", "Al aplicar: sembrar las patas del papel",
                        INFO,
                        f"el alta busca en Primary las patas de {ticker} (pesos "
                        "y dólares) y las escribe.",
                        tabla="mercado.especies",
                        accion="lo hace solo — no hay que correr nada"))

    if ctx["ya_en_curvas"]:
        ps.insert(0, _paso("ya_existe", "⚠ Este ticker YA está en el master", REVISAR,
                           f"ya existe (símbolo {ctx['simbolo_actual']}). Aplicar lo "
                           "PISA con el cuadro de 1816.",
                           tabla="mercado.curvas"))
    # El `n` es PRESENTACIÓN: se numera acá, sobre los pasos que efectivamente
    # aplicaron. El de CER solo está en la rama CER y el control cruzado solo si
    # 1816 contestó, así que numerar en el origen dejaría huecos.
    for i, paso in enumerate(ps, start=1):
        paso["n"] = i
    return ps


def _memoria_de_calculo(*, doc: dict, ejes, rama: str, conv: dict, out: dict,
                        ref: dict, job_tasa: str) -> list[dict]:
    """QUÉ cuenta se hizo, con qué números y de dónde salió cada uno.

    **Por qué existe** (pedido del user, 2026-08-17): la pantalla mostraba «TEA
    7,69%» y «202 bps de diferencia» sin decir qué precio se usó, en qué moneda,
    con qué MEP ni qué fórmula. Una tasa sin su memoria de cálculo no se puede
    auditar: solo se puede creer o no creer.

    Es una lista de `{campo, valor, fuente}` — el "de dónde" pesa tanto como el
    número, porque cuando dos cuentas no coinciden lo que hay que mirar es
    justamente el insumo que difiere.
    """
    def fila(campo, valor, fuente):
        return {"campo": campo, "valor": valor, "fuente": fuente}

    f: list[dict] = [
        fila("Bono", f"{doc.get('ticker_corto')} · {ejes.emisor_tipo} · "
                     f"{ejes.moneda} · {ejes.ajuste}"
                     + (f" · ley {ejes.ley}" if ejes.ley else ""),
             "ejes de mercado.curvas"),
        fila("Fórmula", f"rama «{rama}»"
             + (f" — no se calcula acá, la trae {job_tasa}" if job_tasa
                else " de engines/curvas.py::calcular_campos"),
             "engines/curvas.py::rama_calculo"),
        fila("Cuadro", f"{conv['n']} cupón/es · Σ amortizaciones "
                       f"{conv['suma_amort']} → escala {conv['escala']}",
             "1816 /cashflow (fechaPagoEfectiva)"),
    ]
    if conv["flujo_vencimiento"] is not None:
        f.append(fila("Pago único", conv["flujo_vencimiento"],
                      "amortización + interés del único cupón (bullet)"))
    if doc.get("cer_emision"):
        f.append(fila("CER de emisión", doc["cer_emision"],
                      "macro.series_macro, T−10 hábiles desde la fecha de emisión "
                      "de 1816 (la MISMA función que usa el motor)"))

    px, fuente_px = out.get("precio"), out.get("precio_fuente")
    if px:
        pedido = ref.get("pedido") or {}
        origen = ("mercado.market_snapshot (Primary, live)" if fuente_px == "snapshot"
                  else f"1816 precioClean · moneda={pedido.get('moneda', '?')} · "
                       f"plazo={pedido.get('plazo', '?')} · "
                       f"fuente={pedido.get('fuente', '?')} · {ref.get('fecha', '?')}"
                  if fuente_px == "1816" else "pasado a mano")
        f.append(fila("Precio usado", px, origen))
    # El divisor que NO se ve y explica la mitad de las divergencias. Cuál de los
    # dos TC se usó lo decide la RAMA, no la moneda: un dólar-linked cotiza en
    # pesos y se divide por el A3500 (feed MAE), no por el MEP.
    if out.get("_a3500"):
        f.append(fila("A3500 aplicado", out["_a3500"],
                      "el dólar-linked paga pesos al TC oficial: el motor divide "
                      "el precio por el A3500 (feed MAE mayorista). Sin ese feed "
                      "el bono queda sin TEA y sin paridad."))
    elif out.get("_mep"):
        f.append(fila("MEP aplicado", out["_mep"],
                      "el bono paga en USD y el precio viene en pesos: el motor "
                      "divide por el MEP. Si 1816 usó otro TC, las dos tasas "
                      "difieren sin que ninguna esté mal."))
    if ref.get("convencion_tna"):
        f.append(fila("Convención TNA (1816)", ref["convencion_tna"],
                      "el conteo de días que usan ellos. **Si el precio es el "
                      "mismo y las tasas difieren, la respuesta está acá.**"))
    if ref.get("fuente_precio") or ref.get("ultima_operacion"):
        f.append(fila("Su precio", f"fuente {ref.get('fuente_precio') or '?'}"
                                   f" · última op {ref.get('ultima_operacion') or '?'}"
                                   + (f" · volumen {ref['volumen']:,.0f}"
                                      if ref.get("volumen") else " · SIN volumen"),
                      "un precio sin volumen del día es teórico, no un trade: "
                      "comparar tasas contra eso es comparar contra una opinión"))
    mismo = ref.get("a_nuestro_precio") or {}
    if mismo.get("tea") is not None:
        f.append(fila("1816 A NUESTRO PRECIO", f"{mismo['tea']:.4%}",
                      "su fórmula sobre el MISMO número que usamos nosotros "
                      "(endpoint de input manual). Elimina el precio como "
                      "variable: lo que quede es convención o cronograma."))
    elif mismo.get("error"):
        f.append(fila("1816 A NUESTRO PRECIO", "—", f"no se pudo: {mismo['error']}"))
    if out.get("paridad") is not None or ref.get("paridad") is not None:
        # LAS DOS EN LA MISMA UNIDAD. Nuestro motor devuelve PORCENTAJE (72.78) y
        # 1816 FRACCIÓN (0.7278): el paso del cotejo ya normalizaba, este cuadro
        # no — y mostraba «nuestra 68.8575 · 1816 0.7278», que se lee como un
        # error de escala de 100× cuando la diferencia real era del 5%.
        # La de 1816 que se muestra es **la misma que usa el paso del cotejo**: la
        # que calculó a NUESTRO precio si la hay, y recién si no la de su propio
        # precio. Mostrar una y decidir con otra es cómo un cuadro de auditoría
        # deja de servir para auditar.
        _par_1816 = (mismo.get("paridad") if mismo.get("paridad") is not None
                     else ref.get("paridad"))
        f.append(fila("Paridad",
                      f"nuestra {_pct(out.get('paridad'))} · "
                      f"1816 {_pct(_par_1816 * 100 if isinstance(_par_1816, int | float) else None)}",
                      "**si la paridad coincide y la TEA no, es convención de "
                      "días; si NO coincide, es el precio o su escala**"))
    if out.get("duration") is not None or ref.get("duration") is not None:
        f.append(fila("Duration", f"nuestra {out.get('duration')} · "
                                  f"1816 {ref.get('duration')}",
                      "depende solo del cuadro y las fechas: si difiere, el "
                      "cronograma que bajamos no es el mismo que el de ellos"))
    return f


def _veredicto(chequeos: list[dict]) -> dict:
    """**LA DECISIÓN**, derivada de los pasos y de nada más.

    Devuelve dos booleanos y no uno, porque son dos preguntas distintas:

    - **`puede_aplicar`** — ¿puede apretar APLICAR un humano? Solo lo impide un
      paso `bloquea`, o sea algo PROBADO mal.
    - **`puede_auto`** — ¿puede aplicarse SOLO, sin que nadie mire? Acá además
      frenan `revisar` (se midió y no cierra) y `no_se` (no se pudo verificar).
      Un humano puede decidir con evidencia parcial; un robot, no.

    **Los dos salen de acá y viajan resueltos.** El botón APLICAR se escondía o
    aparecía según `aplicable`, que miraba OTRA cosa (la rama), mientras el
    veredicto decía «se puede aplicar»: TMG27 mostraba la cadena en verde y sin
    botón, y GD46 mostraba el botón con el cronograma probadamente equivocado.
    Dos lugares contestando la misma pregunta siempre terminan contradiciéndose.
    """
    bloqueos = [c for c in chequeos if c["estado"] == BLOQUEA]
    revisar = [c for c in chequeos if c["estado"] == REVISAR]
    dudas = [c for c in chequeos if c["estado"] == NO_SE]
    conteo = {"ok": sum(1 for c in chequeos if c["estado"] == OK),
              "info": sum(1 for c in chequeos if c["estado"] == INFO),
              "revisar": len(revisar), "bloquea": len(bloqueos), "no_se": len(dudas)}
    base = {"conteo": conteo, "puede_aplicar": not bloqueos,
            "puede_auto": not (bloqueos or revisar or dudas)}
    if bloqueos:
        return {**base, "estado": BLOQUEA,
                "texto": f"NO se puede aplicar — {len(bloqueos)} paso/s lo bloquean: "
                         + "; ".join(c["titulo"] for c in bloqueos[:2])}
    if revisar:
        # **«A mano» solo si hay algo que hacer a mano.** Un `revisar` con `aviso`
        # pide CARGAR un dato (el CER de emisión); uno sin aviso es un juicio —
        # «esto quedó cerca, miralo». El texto los mezclaba y BPOA8 decía «se puede
        # aplicar A MANO» cuando no había ningún campo que completar: el user
        # preguntó, con razón, qué era lo que tenía que aplicar a mano.
        con_dato = [c for c in revisar if c.get("aviso")]
        if con_dato:
            texto = (f"se puede aplicar, y queda {len(con_dato)} dato/s para "
                     "cargar a mano (van a AVISOS): "
                     + "; ".join(c["aviso"] for c in con_dato[:2]))
            if len(revisar) > len(con_dato):
                texto += f" · y {len(revisar) - len(con_dato)} paso/s para mirar"
        else:
            texto = (f"se puede aplicar — no falta ningún dato, pero {len(revisar)} "
                     "paso/s no cierran del todo y conviene mirarlos: "
                     + "; ".join(c["titulo"] for c in revisar[:2]))
        return {**base, "estado": REVISAR, "texto": texto + ". Automático NO."}
    if dudas:
        return {**base, "estado": NO_SE,
                "texto": "la conversión está bien, pero no se pudo verificar "
                         + dudas[0]["titulo"].lower()
                         + " — se puede aplicar a mano, automático no."}
    return {**base, "estado": OK,
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


# Lo que se le pide a 1816 para poder simular un bono que TODAVÍA no tiene precio
# propio. **Los nombres salen del enum del OpenAPI**, no de adivinar (relevado
# 2026-08-17). Como el simulador NO PERSISTE NADA, traer de más no tiene el costo
# habitual —no ensucia el modelo ni crea una segunda verdad— y cada campo de más
# es una hipótesis menos: `convencionTna` explica una diferencia de días,
# `ultimaOperacion` + `volumenMontoDiario` dicen si el precio es de un trade real
# o quedó viejo, y `fuente` dice contra qué mercado se está comparando.
# Costo: 1 ticker × 11 campos = 11 créditos de 100.000 diarios.
_CAMPOS_REF = ("precioDirty", "precioClean", "tea", "tem", "paridad", "duration",
               "durationMod", "currentYield", "convencionTna", "fuente",
               "ultimaOperacion", "volumenMontoDiario", "fechaLiquidacion")

# **Lo que decide que la rueda sirve.** Se piden 13 campos para enriquecer el
# diagnóstico, pero al simulador solo lo salva el PRECIO: es el insumo del motor,
# y una rueda con la TEA modelada de 1816 y sin operaciones lo deja igual de
# plantado que una vacía. Sin esta distinción el retroceso frenaba en la primera
# rueda con cualquier número y devolvía «no publicó precio» — cuando el precio
# estaba, dos ruedas más atrás.
_CAMPOS_PRECIO = ("precioDirty", "precioClean")


def _fallo_ref(e, moneda: str, pedido: dict) -> dict:
    """El error de 1816, COMPLETO. `type(e).__name__` daba «Error1816» a secas —
    el nombre de la clase, sin el HTTP ni el motivo. Un error que no dice qué pasó
    no se puede arreglar; el mensaje del cliente trae el status y el body."""
    return {"error": f"1816 no respondió (moneda={moneda}): {e}"[:300],
            "pedido": pedido}


def curva_destino(rama: str, ejes) -> str:
    """La `curva` que hay que escribir en `mercado.curvas`. `""` si no hay una.

    ⚠️ **RAMA y CURVA son DOS VOCABULARIOS DISTINTOS, y el alta los confundía.**
    Medido en TMG27 (2026-08-17): el alta mandaba `curva = rama` con un comentario
    que decía «la RAMA que calculó el motor es exactamente ese valor» — y es cierto
    para cuatro de ellos, que es justo por qué nadie lo notó:

        rama  (qué FÓRMULA usa el motor)  tasa_fija · cer · soberanos ·
                                          dolar_linked · **otros**
        curva (qué TIPO de instrumento)   tasa_fija · cer · soberanos ·
                                          dolar_linked · **tamar** · **dual**

    Un TAMAR cae en la rama `otros` —no le calculamos la tasa a propósito— pero su
    curva **existe y se llama `tamar`**. Al mandar `otros`, `upsert_bono` rechazaba
    el alta con «curva inválida».

    **Sin equivalente NO se inventa uno.** `badlar`, `tpm` y `caucion` son ajustes
    válidos que no tienen curva en el catálogo: devolver `""` hace que el
    pre-flight lo BLOQUEE con el motivo, en vez de escribirlos bajo una curva
    parecida y que la vista los agrupe mal para siempre.
    """
    from api.services.bonos_admin import curvas_validas
    validas = curvas_validas()
    if rama in validas:
        return rama
    return ejes.ajuste if ejes.ajuste in validas else ""


def moneda_pedido_1816(simbolo: str, moneda_eje: str) -> str:
    """En qué moneda pedirle el precio a 1816 = **la que el MOTOR espera**.

    ⚠️ **El bug de GD46 (2026-08-17), y lo introduje yo en E2.g.** Ahí cambié el
    pedido a `mep` para cerrar los 202 bps del cotejo de TEA, sin ver que el motor
    hace su PROPIA conversión: `precio_soberano_a_usd` divide el precio por el MEP
    **salvo que el símbolo termine en D o C**. Como el símbolo se arma
    `MERV - XMEV - GD46 - 24hs` —sin sufijo—, el motor asumía pesos y dividía un
    precio que 1816 **ya había devuelto en dólares**:

        69 (USD) / 1.517,63 = 0,0455  →  paridad 0,05% contra 75,56% de 1816

    Y no daba error: devolvía la duration ingenua y la TEA vacía. Sin la doble
    división la paridad da **75,57% contra 75,56%** — o sea que el cuadro de
    flujos estaba PERFECTO todo el tiempo; el problema era la unidad del insumo.

    **La regla que evita que vuelva**: la moneda no se elige por criterio propio,
    se DERIVA del mismo predicado que usa el motor (el sufijo del símbolo). El
    simulador tiene que darle exactamente lo que va a recibir en producción — el
    `last_price` de Primary — y para un símbolo sin sufijo eso son PESOS.
    """
    if (moneda_eje or "").strip().upper() != "USD":
        return "ars"
    partes = (simbolo or "").split(" - ")
    tk = partes[2] if len(partes) >= 3 else (simbolo or "")
    # Sufijo D/C = el precio YA viene en dólares y el motor NO lo convierte, así
    # que ahí sí conviene pedirlo en MEP (mismo TC que usaríamos nosotros).
    return "mep" if (tk[-1:].upper() in ("D", "C")) else "ars"


def moneda_cotejo_1816(rama: str) -> str:
    """En qué moneda COMPARAR el resultado = **la moneda en la que el motor lo
    calcula**, que no tiene por qué ser la del precio que le damos de comer.

    Suena a lo mismo que `moneda_pedido_1816` y es la pregunta opuesta:

        pedido  → ¿en qué moneda quiere el motor su INSUMO?   (soberano: PESOS)
        cotejo  → ¿en qué moneda devuelve el RESULTADO?       (soberano: USD)

    Un soberano cotiza en pesos y el motor los divide por el MEP: la paridad que
    devuelve es en dólares. Compararla contra la paridad que 1816 publica en `ars`
    es comparar dos cosas distintas — y **medido el 2026-08-17 no es un detalle**:
    para GD46 1816 da 0,7278 en `ars` y 0,7556 en `mep`, o sea que ni siquiera son
    la misma magnitud reexpresada (su valor técnico en pesos sale a un TC de
    ~1.572 y el precio a ~1.514). Contra la de `ars` nuestra paridad quedaba 4,07%
    afuera; contra la de `mep`, 0,24%.

    Las demás ramas quedan en `ars` a propósito: en `cer` y `tasa_fija` el motor
    trabaja en pesos, y en `dolar_linked` la paridad es un cociente entre dos
    números pesificados al MISMO TC —o sea que no depende de la moneda— y encima
    1816 no publica `mep` para ellos (D30O6 devolvió todo `None`).
    """
    return "mep" if rama == "soberanos" else "ars"


def _sin_rueda(intentos: list[str]) -> str:
    """Mensaje de «ninguna rueda trajo datos», diciendo CUÁLES se probaron.

    Que no haya precio tiene dos causas muy distintas —el día pedido no fue rueda,
    o el papel no opera— y el mensaje viejo («no publicó precio al <hoy>») las
    confundía en una sola, encima nombrando un domingo. Con el rango probado, la
    respuesta se lee sola."""
    if not intentos:
        return "1816 no devolvió datos y no se llegó a retroceder ninguna rueda"
    # Dice PRECIO y no «datos» a propósito: 1816 puede haber publicado una TEA
    # modelada esas mismas ruedas. Lo que falta —y lo único que necesita el motor
    # para simular— es un precio operado.
    return (f"1816 no publicó precio de este ticker en ninguna de las "
            f"{len(intentos)} ruedas probadas ({intentos[0]} → {intentos[-1]}): "
            f"es un papel sin operaciones recientes, no un problema del pedido")


def _referencia_1816(ticker: str, *, moneda_eje: str = "",
                     simbolo: str = "") -> dict:
    """Precio y TASA de referencia de 1816 para un bono que no tiene snapshot.

    **Por qué existe** (idea del user, 2026-08-17): un bono que se acaba de dar de
    alta no tiene precio en `mercado.market_snapshot` —nunca se suscribió— así que
    el simulador no podía calcular NADA y había que aplicar a ciegas y esperar a
    ver si salía bien. Pero 1816 **publica el precio**, y no hay que persistirlo:
    alcanza con usarlo para la simulación.

    **Y trae de yapa lo que vale más que el precio: SU PROPIA TEA.** Eso convierte
    la simulación en un CONTROL CRUZADO — se corre nuestro motor sobre el precio de
    ellos y se compara contra su tasa. Si los dos números coinciden, el cuadro de
    flujos que estamos por escribir está bien convertido; si no, hay algo mal en la
    escala o en la pata, que es EXACTAMENTE lo que el simulador existe para cazar y
    lo único que no podía chequear en un bono nuevo.

    **NO se persiste nada.** `market_snapshot` es del motor (Primary, live, 5s);
    esto es 1816/BYMA con delay. Mezclarlos escondería cuál es cuál — el mismo
    criterio por el que `jobs/tamar_1816` escribe en su propia tabla.

    Costo: 1 ticker × 4 campos = **4 créditos**, y solo cuando alguien aprieta
    SIMULAR (de 100.000 diarios).
    """
    moneda = moneda_pedido_1816(simbolo, moneda_eje)
    pedido = {"moneda": moneda, "campos": list(_CAMPOS_REF)}
    # Las ruedas que se descartaron por venir vacías. Sin esto el mensaje de
    # fracaso decía «no publicó precio al <fecha>» con la fecha de HOY —un domingo,
    # por ejemplo— y sonaba a que 1816 estaba roto cuando lo que pasaba es que ese
    # día no hubo mercado. Diciendo QUÉ ruedas se probaron, el que lee decide.
    intentos: list[str] = []
    def _anotar(d):
        intentos.append(d.isoformat())
    try:
        resp = mercado_1816.indicadores_vigentes([ticker], list(_CAMPOS_REF),
                                                 moneda=moneda, al_retroceder=_anotar,
                                                 campos_dato=list(_CAMPOS_PRECIO))
    except Exception as e:
        # **Degradación elegida**: si la API no acepta esa moneda, se reintenta en
        # `ars` — que es el default y lo que venía andando. Un precio en la moneda
        # equivocada se puede explicar mirando el detalle del cálculo; NINGÚN
        # precio deja al simulador sin poder calcular nada, que es peor.
        if moneda != "ars":
            logger.info("av_agent: 1816 rechazó moneda=%s para %s (%s) — "
                        "reintento en ars", moneda, ticker, e)
            try:
                resp = mercado_1816.indicadores_vigentes([ticker], list(_CAMPOS_REF),
                                                         moneda="ars",
                                                         al_retroceder=_anotar,
                                                         campos_dato=list(_CAMPOS_PRECIO))
                moneda, pedido = "ars", {**pedido, "moneda": "ars",
                                         "nota": f"pedido en {moneda.upper()} "
                                                 "rechazado, se usó ARS"}
            except Exception as e2:
                e = e2
                resp = None
        else:
            resp = None
        if resp is None:
            return _fallo_ref(e, moneda, pedido)
        # `type(e).__name__` daba «Error1816» a secas — el nombre de la clase, sin
        # el HTTP ni el motivo. Un error que no dice qué pasó no se puede arreglar.
    if not resp:
        return {"error": _sin_rueda(intentos), "pedido": {**pedido, "ruedas": intentos}}
    v = (resp.get("instrumentos") or {}).get(ticker) or {}
    # **`precioDirty`, no `precioClean`.** Los bonos argentinos cotizan SUCIOS
    # (con intereses corridos), así que el `last_price` que nos da Primary —el
    # precio que el motor espera— es el dirty. Y está verificado por consistencia
    # interna: para GD46, `precioDirty / paridad` da un valor técnico con un TC
    # implícito de ~1.436 (plausible), mientras que con `precioClean` da ~1.570.
    # El que cierra con la paridad que 1816 publica es el dirty.
    px = _num(v.get("precioDirty")) or _num(v.get("precioClean"))
    # `pedido` viaja hasta la pantalla: sin saber en qué moneda y a qué plazo se
    # pidió, un precio raro no se puede diagnosticar — que fue exactamente lo que
    # pasó con GD46.
    pedido = {**pedido, "plazo": resp.get("plazo"), "fuente": resp.get("fuente"),
              "ruedas": intentos}
    if not px or px <= 0:
        # Llegar acá ya NO es «no hubo rueda» — el retroceso paró porque ESTA rueda
        # trajo algún dato de valor (una TEA, una paridad), pero sin precio. El
        # único que puede explicarlo es `ultimaOperacion`: dice cuándo operó el
        # papel por última vez, que casi siempre es la respuesta real («no opera
        # desde hace meses»), y sin eso el mensaje culpaba a la fecha del pedido.
        ult = v.get("ultimaOperacion")
        return {"error": f"1816 conoce {ticker} y respondió por la rueda del "
                         f"{resp.get('fechaOperacion')}, pero sin precio"
                         + (f" — su última operación es del {ult}" if ult else
                            " y tampoco informa última operación: es un papel "
                            "sin mercado, no un problema del pedido"),
                "pedido": pedido}
    return {"precio": px, "precio_campo": ("precioDirty" if _num(v.get("precioDirty"))
                                           else "precioClean"),
            "precio_clean": _num(v.get("precioClean")),
            "tea": _num(v.get("tea")), "tem": _num(v.get("tem")),
            "paridad": _num(v.get("paridad")),
            "duration": _num(v.get("duration")),
            "duration_mod": _num(v.get("durationMod")),
            "current_yield": _num(v.get("currentYield")),
            "convencion_tna": v.get("convencionTna"),
            "fuente_precio": v.get("fuente"),
            "ultima_operacion": v.get("ultimaOperacion"),
            "volumen": _num(v.get("volumenMontoDiario")),
            "fecha_liquidacion": v.get("fechaLiquidacion"),
            "fecha": resp.get("fechaOperacion"), "pedido": pedido}


def _ficha_1816(ticker: str) -> dict:
    """La FICHA completa desde el catálogo YA persistido
    (`research.mkt_1816_instrumentos`). **Cero créditos**: lo llena
    `jobs/mercado_1816_discovery --catalogo`.

    Se traen todos los campos y no dos, porque cada uno tapa un hueco del alta:
    el bono nacía **sin emisor** cuando 1816 es la FUENTE DE VERDAD del emisor
    (`jobs/ficha_1816`: 74 strings para 67 emisores reales antes de estandarizar),
    y sin `fecha_emision` aunque ya la estábamos leyendo para inferir el CER.
    """
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT fecha_emision, denominacion, emisor, isin, "
                        "moneda_denom, moneda_pago, fecha_vencimiento FROM "
                        "research.mkt_1816_instrumentos WHERE ticker = %s", (ticker,))
            r = cur.fetchone()
        if not r:
            return {}
        return {"fecha_emision": r[0].isoformat() if r[0] else "",
                "denominacion": r[1], "emisor": r[2], "isin": r[3],
                "moneda_denom": r[4], "moneda_pago": r[5],
                "fecha_vencimiento": r[6].isoformat() if r[6] else ""}
    except Exception:
        return {}


# `tipo` de `mercado.curvas` derivado de los ejes. Es un LABEL de la ficha (no
# decide ningún cálculo — eso lo hace `rama_calculo`), así que derivarlo es
# seguro y deja un campo menos vacío.
_TIPO_POR_EJES = {("soberano", "fija"): "Lecap", ("bcra", "fija"): "Bono",
                  ("corporativo", None): "ON", ("provincial", None): "Bono"}


def _tipo_de(ejes) -> str:
    if ejes.emisor_tipo == "corporativo":
        return "ON"
    if ejes.emisor_tipo == "soberano" and ejes.moneda == "USD":
        return "Global" if ejes.ley == "ny" else "Bonar"
    return _TIPO_POR_EJES.get((ejes.emisor_tipo, ejes.ajuste)) or "Bono"


def _ficha_para_curvas(sim: dict, ficha: dict, ejes, conv: dict) -> dict:
    """Los campos de `mercado.curvas` que se pueden completar SIN que nadie tipee.

    **Medido**: `upsert_bono` acepta 19 campos y el agente mandaba 14 — quedaban
    vacíos `emisor`, `fecha_emision`, `tipo`, `tasa_referencia` y `cupon_anual`.
    Los cuatro primeros salen de datos que ya tenemos; el quinto solo cuando es
    inequívoco.
    """
    extra: dict = {"tipo": _tipo_de(ejes)}
    if ficha.get("emisor"):
        # 1816 es la fuente de verdad del emisor — el mismo criterio de
        # `jobs/ficha_1816`, que PISA el nuestro porque estandarizar no es opinar.
        extra["emisor"] = ficha["emisor"]
    if ficha.get("fecha_emision"):
        extra["fecha_emision"] = ficha["fecha_emision"]
    if ejes.ajuste in ("tamar", "badlar", "tpm"):
        # Contra qué índice ajusta. Es el campo que la shape de esta familia usa.
        extra["tasa_referencia"] = ejes.ajuste.upper()
    # `cupon_anual` SOLO cuando es inequívoco: cero cupón. Con cupones de por
    # medio habría que asumir la frecuencia, y asumir es justo lo que no se hace.
    if conv["flujos"] and not any(
            (f.get("interes") or f.get("cupon_sobre_residual") or 0) for f in conv["flujos"]):
        extra["cupon_anual"] = 0.0
    return extra


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
        # Los campos de la FICHA que el alta va a completar sola. Se calculan acá
        # para que el pre-flight pueda MOSTRARLOS antes de escribir: «¿el bono
        # entra completo?» es una pregunta legítima y no se contestaba.
        "ficha_curvas": _ficha_para_curvas({}, ficha, ejes, conv),
        # ⚠️⚠️ **`aplicable` NO GATEA NADA — es solo la etiqueta de la rama.**
        #
        # Este campo generó el MISMO bug TRES veces: la cadena en verde, el
        # veredicto diciendo «se puede aplicar» y el botón escondido, porque acá
        # se contestaba la misma pregunta con otro criterio. Primero era
        # `rama in RAMAS_AUTOMATICAS` (TMG27, E2.k). Después quedó
        # `and not (rama_tent == "cer" and not cer_emision)` — y cuando en E2.l el
        # CER dejó de bloquear la cadena, ESTE renglón lo siguió bloqueando
        # (TZXA7: «se puede aplicar A MANO» sin botón).
        #
        # La causa no es el valor, es la ESTRUCTURA: dos gates para una decisión
        # siempre terminan contradiciéndose. Su único contenido legítimo —¿la rama
        # convierte sin ambigüedad?— YA es un paso de la cadena (`rama`, que
        # BLOQUEA cuando no), así que `veredicto.puede_aplicar` lo cubre entero.
        # Queda solo para PINTAR el motivo en la pantalla. **Quien decide si se
        # puede aplicar es el veredicto, y nadie más.**
        "aplicable": _alta_automatica(doc["rama"], ejes),
        # La `curva` que se va a escribir. Viaja RESUELTA desde la simulación para
        # que `aplicar` no la vuelva a derivar: eso sería otra vez dos lugares
        # calculando lo mismo, que es el bug que se repitió tres veces.
        "curva_destino": curva_destino(doc["rama"], ejes),
        "motivo_no_aplicable": _motivo_no_aplicable(doc["rama"], ejes),
        "flujos_muestra": conv["flujos"][:3] + (["…"] if conv["n"] > 3 else []),
        # El cuadro YA convertido. `aplicar` lo reusa en vez de volver a pedirle el
        # cashflow a 1816: esa llamada cuesta un crédito POR CUPÓN, y pedir dos
        # veces lo mismo no solo gasta — abre la puerta a que se aplique un cuadro
        # distinto del que se mostró, que es justo lo que el simulador previene.
        "cuadro": conv,
    }
    out.update(_simular_tasa(doc, simbolo, precio, ticker=tk,
                             moneda_eje=ejes.moneda))

    # El PRE-FLIGHT va al final porque necesita el resultado de la tasa: el paso 8
    # ("¿hay precio?") es el mismo dato que ya se buscó para simular, y volver a
    # pedirlo sería un roundtrip de más diciendo lo mismo.
    out["chequeos"] = _chequeos(
        ticker=tk, curva_1816=curva_1816, ejes=ejes, rama=doc["rama"], conv=conv,
        cer_emision=cer_emision, nota_cer=nota_cer, simbolo=simbolo,
        origen_simbolo=origen_simbolo, ctx=ctx,
        estado_simbolo=out["simbolo_estado"],
        precio=out.get("precio"), tea=out.get("tea"),
        fuente_precio=out.get("precio_fuente") or "",
        ref=out.get("referencia_1816") or {}, paridad=out.get("paridad"),
        ficha_curvas=out.get("ficha_curvas") or {},
        duration=out.get("duration"), cupones=cupones)
    out["veredicto"] = _veredicto(out["chequeos"])
    # QUÉ cuenta se hizo y con qué números. Una tasa sin su memoria de cálculo no
    # se puede auditar: solo se puede creer o no creer.
    out["calculo"] = _memoria_de_calculo(
        doc=doc, ejes=ejes, rama=doc["rama"], conv=conv, out=out,
        ref=out.get("referencia_1816") or {}, job_tasa=_tasa_externa(ejes)[1])
    return out


def _simular_tasa(doc: dict, simbolo: str, precio: float | None,
                  ticker: str = "", moneda_eje: str = "") -> dict:
    """Corre el MOTOR sobre el doc simulado. Mismo `calcular_campos` que usa
    `engines/curvas` en producción: si acá saliera otro número, la simulación no
    valdría para nada.

    **Orden de precios**: primero el del snapshot (Primary, live, es EL precio);
    si no hay —el caso de todo bono nuevo— se cae al `precioClean` de 1816. Nunca
    al revés: el de referencia sirve para poder calcular algo, no para reemplazar
    al real.
    """
    from core import market_snapshot
    from engines.curvas import (
        calcular_campos,
        cargar_a3500_actual,
        cargar_cer,
        cargar_dias_habiles,
        curva_depende_de,
        rama_calculo,
    )

    fuente, ref = "manual", {}
    if precio is None:
        fuente = "snapshot"
        try:
            m = market_snapshot.cols_map([simbolo], ["last_price"])
            precio = (m.get(simbolo) or {}).get("last_price")
        except Exception:
            precio = None
        if (not precio or precio <= 0) and ticker:
            # Sin snapshot no había NADA que simular y había que aplicar a ciegas.
            # 1816 publica el precio: se usa de referencia y no se persiste.
            ref = _referencia_1816(ticker, moneda_eje=moneda_eje, simbolo=simbolo)
            if ref.get("precio"):
                precio, fuente = ref["precio"], "1816"
    if not precio or precio <= 0:
        return {"precio": None, "tea": None, "precio_fuente": None,
                "referencia_1816": ref or None,
                "nota_tasa": "no hay precio en el snapshot"
                             + (f" y {ref['error']}" if ref.get("error") else "")
                             + " — el cuadro igual queda listo para cargar"}

    try:
        cer = cargar_cer(dias=1200) if doc.get("ajuste") == "cer" else {}
        habiles = cargar_dias_habiles()
        # QUÉ TIPO DE CAMBIO NECESITA ESTA RAMA no se decide acá: lo dice
        # `curva_depende_de`, el MISMO predicado que usa el motor para invalidar su
        # cache. Antes esto preguntaba `moneda_flujo == "USD"` —un criterio propio—
        # y por eso el A3500 no se cargaba NUNCA: un dólar-linked salía por la
        # puerta de emergencia de la rama (línea 579 de engines/curvas.py) con la
        # duration ingenua y sin TEA, sin que nada diera error (D30O6, 2026-08-17).
        rama_doc = rama_calculo(doc)
        mep = a3500 = None
        if curva_depende_de(rama_doc, "mep"):
            # `get_ultimo_mep` devuelve un DICT {mep, ccl, canje, oficial, …}, no un
            # float — `calcular_campos` espera el número. Sin MEP un bono USD en
            # pesos queda sin TEA (falla conocida, §3 de SALUD_CURVAS): se reporta,
            # no se inventa un tipo de cambio.
            from api.services.macro import get_ultimo_mep
            mep = (get_ultimo_mep() or {}).get("mep")
        if curva_depende_de(rama_doc, "a3500"):
            # Feed MAE mayorista (el mismo que usa motor_curvas). Si la PC de la
            # oficina está apagada devuelve None y el bono queda sin TEA — se
            # reporta, no se sustituye por el A3500 del BCRA, que está 1 día viejo.
            a3500 = cargar_a3500_actual()
        r = calcular_campos({"price": float(precio), "timestamp": datetime.now(UTC)},
                            doc, cer, habiles, mep=mep, tc_a3500=a3500) or {}
    except Exception as e:
        logger.warning("av_agent: simulación de tasa falló para %s: %s", simbolo, e)
        return {"precio": float(precio), "tea": None, "precio_fuente": fuente,
                "referencia_1816": ref or None,
                "nota_tasa": f"el motor no pudo calcular: {type(e).__name__}"}

    # Si NO hubo que caer a 1816 (había snapshot), igual conviene tener su tasa
    # para el control cruzado: es el único chequeo que dice si el cuadro que
    # estamos por escribir está bien convertido.
    if not ref and ticker:
        ref = _referencia_1816(ticker, moneda_eje=moneda_eje, simbolo=simbolo)
    # Y el cotejo DEFINITIVO: su tasa al MISMO precio que usamos nosotros. Sin
    # esto, una diferencia puede ser la fórmula o el insumo y no hay forma de
    # saber cuál; con esto lo que queda es solo convención o cronograma.
    if ticker and r.get("TEA") is not None:
        # **La moneda del COTEJO, no la del pedido.** Le pasamos EL MISMO NÚMERO
        # que consumió el motor, expresado como el motor lo expresa: para un
        # soberano eso es el precio ya pasado a dólares por `precio_soberano_a_usd`
        # —la función del motor, no una copia— y la pregunta va en `mep`.
        moneda_cot = moneda_cotejo_1816(rama_doc)
        px_cot = float(precio)
        if moneda_cot == "mep":
            from engines.curvas import precio_soberano_a_usd
            px_cot = precio_soberano_a_usd(float(precio), simbolo, mep) or px_cot
        ref = {**ref, "a_nuestro_precio":
               _tea_de_1816_a_nuestro_precio(ticker, px_cot, moneda_cot)}

    # La nota del encabezado. **No puede decir "revisar la escala" cuando la tasa
    # es EXTERNA**: a un TAMAR el motor no le devuelve TEA a propósito, y esa
    # línea aparecía igual en TMG27 —arriba de todo, en rojo— contradiciendo a los
    # dos pasos de la cadena que explican, en verde, que así tiene que ser.
    externa = tasa_externa_de(doc.get("ajuste"))[0] == "1816"
    # Y si la causa YA se conoce, tampoco se culpa a la escala. En TZXA7 esta
    # línea seguía apareciendo arriba de todo —«revisar la escala del flujo o la
    # pata»— mientras la cadena, dos renglones más abajo, decía que lo que falta
    # es el CER de emisión. El encabezado no puede contradecir al detalle.
    sin_cer = doc.get("ajuste") == "cer" and not doc.get("cer_emision")
    # Misma regla para el TC: si la rama necesita un tipo de cambio y el feed no lo
    # dio, el motor sale por la puerta de emergencia ANTES de mirar el cuadro. Decir
    # «revisar la escala del flujo» ahí manda a buscar un problema que no existe.
    sin_tc = ((curva_depende_de(rama_doc, "a3500") and not a3500)
              or (curva_depende_de(rama_doc, "mep") and not mep))
    return {"precio": float(precio), "tea": r.get("TEA"), "precio_fuente": fuente,
            "duration": r.get("duration"), "paridad": r.get("paridad"),
            "referencia_1816": ref or None, "_mep": mep, "_a3500": a3500,
            "nota_tasa": "" if r.get("TEA") is not None or externa else
            "sin TEA hasta cargar el CER de emisión" if sin_cer else
            ("sin TEA: falta el tipo de cambio "
             + ("A3500 (feed MAE)" if curva_depende_de(rama_doc, "a3500") else "MEP")
             + " — no es el cuadro") if sin_tc else
            "el motor no calculó la TEA con este cuadro y este precio — revisar "
            "la escala del flujo o la pata"}


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
    # Acá había un `if not sim["aplicable"]: return` — el SEGUNDO gate. Se borró:
    # una rama que no convierte sin ambigüedad ya deja el paso `rama` en BLOQUEA,
    # así que el chequeo de abajo la rechaza igual, y con el motivo puesto.
    # **El backend es el que decide, no el botón.** Se lee el MISMO
    # `puede_aplicar` que el front usa para mostrar APLICAR, así que esconder el
    # botón y rechazar la escritura no pueden desincronizarse; y si alguien pega
    # al endpoint a mano, el bloqueo sigue en pie.
    ver = sim.get("veredicto") or {}
    if not ver.get("puede_aplicar", True):
        bloqueos = [c for c in sim.get("chequeos", []) if c["estado"] == BLOQUEA]
        return {**sim, "aplicado": False,
                "error": "el pre-flight no pasa: "
                         + "; ".join(c["titulo"] for c in bloqueos)}

    payload = {
        "ticker_corto": sim["ticker"], "ticker": sim["simbolo"],
        "curva": sim["curva_destino"], "valor_nominal": 100.0,
        "fecha_vencimiento": sim["vencimiento"],
        "moneda_flujo": "USD" if sim["ejes"]["moneda_eje"] == "USD" else "ARS",
        **{k: v for k, v in sim["ejes"].items() if v},
    }
    if sim.get("cer_emision"):
        payload["cer_emision"] = sim["cer_emision"]
    # La FICHA: emisor (1816 es la fuente de verdad), fecha de emisión, tipo y
    # tasa de referencia. Sin esto el bono nacía con 5 campos vacíos que ya
    # teníamos a un SELECT de distancia.
    payload.update(sim.get("ficha_curvas") or {})
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

    # ── TASA EXTERNA: el bono NACE con su tasa y su MARGEN ──────────────────
    # De un TAMAR **el margen es el número que mira la mesa**. Sin esto el bono
    # quedaba escrito y con la celda vacía hasta que corriera el cron —30 minutos
    # en rueda, hasta mañana fuera de ella— y un alta que deja vacío el dato
    # principal del instrumento está a medio hacer.
    tasa_sembrada = None
    fuente_tasa, _job = tasa_externa_de(sim["ejes"]["ajuste"])
    if fuente_tasa == "1816":
        from core import tamar_1816_sql
        tasa_sembrada = tamar_1816_sql.sembrar_desde_1816(
            sim["ticker"], sim["ejes"]["ajuste"])
        acc.registrar(accion="sembrar_tasa_1816", objetivo=sim["ticker"], por=actor,
                      ok=bool(tasa_sembrada.get("ok")),
                      error=(tasa_sembrada.get("error") or "")[:300],
                      detalle={k: tasa_sembrada.get(k)
                               for k in ("tea", "spread", "fecha_operacion", "pata")})

    # ── PASO FINAL DEL ALTA: sembrar las patas ──────────────────────────────
    # Va DESPUÉS del upsert y no antes: solo tiene sentido sembrar la especie de
    # un bono que existe. Y **no puede tumbar el alta** — `sembrar_ticker` no
    # levanta nunca; si Primary no lista nada, el bono queda escrito igual y el
    # resultado lo dice. La fila de `mercado.curvas` es lo que mueve la vista;
    # la especie es lo que le da el símbolo REAL en vez del armado.
    from core import especies as _especies
    siembra = _especies.sembrar_ticker(sim["ticker"],
                                       moneda_bono=sim["ejes"]["moneda_eje"])
    acc.registrar(accion="sembrar_especies", objetivo=sim["ticker"], por=actor,
                  ok=bool(siembra.get("ok")), error=(siembra.get("error") or "")[:300],
                  detalle={"simbolos": siembra.get("simbolos", []),
                           "monedas": siembra.get("monedas", [])})

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

    # Los AVISOS: lo que este alta dejó para hacer a mano. Se anotan DESPUÉS de
    # escribir —solo tienen sentido si el bono entró— y los cierra una persona
    # desde la tab AVISOS.
    from api.services import av_agent_vista as _vista
    n_avisos = _vista.crear_avisos(sim["ticker"], sim.get("chequeos", []), por=actor)
    try:
        from core import curvas_sql
        curvas_sql.invalidar()
    except Exception:
        pass
    return {**sim, "aplicado": True, "upsert": r, "siembra": siembra,
            "tasa_sembrada": tasa_sembrada, "avisos_creados": n_avisos,
            "aviso": "los motores cargan mercado.curvas AL ARRANCAR: la TEA de este "
                     "bono aparece recién tras reiniciar motor_rofex + motor_curvas"
                     + (f" · especies sembradas: {', '.join(siembra['simbolos'])}"
                        if siembra.get("ok") else
                        f" · ⚠ la especie NO se pudo sembrar: {siembra.get('error')}")
                     + (f" · tasa 1816 cargada: TEA {tasa_sembrada['tea']:.4%}"
                        + (f", MARGEN {tasa_sembrada['spread']:.4%}"
                           if tasa_sembrada.get("spread") is not None else "")
                        if (tasa_sembrada or {}).get("ok") else
                        f" · ⚠ tasa de 1816 NO cargada: {tasa_sembrada.get('error')}"
                        if tasa_sembrada else "")}
