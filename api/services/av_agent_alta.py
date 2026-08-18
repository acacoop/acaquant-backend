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
from functools import wraps
from types import SimpleNamespace

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


def convertir_flujos(cupones: list[dict], rama: str, *,
                     ratio_cer: float | None = None) -> dict:
    """Cuadro de 1816 → nuestra shape. PURA (testeable sin red).

    Devuelve `{flujos, escala, suma_amort, flujo_vencimiento, n, divisor}`.

    ⚠️ **`ratio_cer` NO es opcional de verdad en la rama `cer`** — es el divisor
    correcto, y sin él la conversión solo es válida para un bono que todavía no
    amortizó nada. El porqué está medido (DICP, 2026-08-17):

    1816 manda cada flujo **en pesos ajustados por el CER DE SU PROPIA FECHA**:
    los pasados en pesos de cuando se pagaron, los futuros en pesos de hoy.
    Sumar los 60 cupones de DICP es **sumar pesos de 2024 con pesos de 2026**, y
    ese Σ era nuestro divisor. Números:

    ```
    PARP  Σ/ratio = 100,0025 por 100 VN   → cada amortización 5,000126%  ← de manual
    DICP  Σ/ratio = 118,3040 por 100 VN   → pero las 20 valdrían 126,9969
                                             (6,8% menos = la inflación entre
                                              2024 y hoy de lo YA pagado)
    ```

    **PARP salió perfecto porque TODAS sus amortizaciones son futuras** (arrancan
    en 2029): ahí Σ ya está en pesos de hoy y dividir por Σ o por el ratio es lo
    mismo. DICP, que amortiza desde 2024, daba TEA 3,91% contra 9,25%.

    Medido contra los indicadores de 1816 al MISMO precio:

    ```
    ÷ Σ (lo viejo)            TEA  3,9132%   duration 3,4830
    ÷ residual futuro          TEA 10,8880%   duration 3,1928
    ÷ ratio de CER (esto)      TEA  9,2268%   duration 3,2591
    1816                       TEA  9,2475%   duration 3,2512
    ```

    El invariante que deja: **`monto_flujo_cer(f) × ratio` reproduce el importe en
    pesos que publica 1816**, hoy y con cualquier CER futuro. Por eso el divisor
    es el ratio y no una Σ: lo que se guarda tiene que ser el % del VN ORIGINAL,
    que no depende del CER.

    `cupon_sobre_residual` NO se toca: es `interes / residual`, un cociente entre
    dos importes de la MISMA fecha, así que el CER se cancela solo. Se ve en que
    da 0,02915 para DICP — 5,83% anual, su cupón de prospecto.

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

    # EL DIVISOR que lleva el cuadro a «% del VN original». En la rama `cer` es el
    # ratio de CER (ver docstring); en el resto sigue siendo la Σ, que ahí SÍ está
    # en una sola unidad porque esos cuadros no se ajustan por índice.
    divisor = suma_amort
    if rama == "cer" and ratio_cer and ratio_cer > 0:
        # ×100 porque el ratio lleva a VN=1 y nuestra shape es por 100.
        divisor = ratio_cer * 100.0
    base = divisor or 1.0

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
                "amortizacion_pct": amort / base * 100,
                "cupon_sobre_residual": (interes / res_prev) if res_prev else 0.0,
                "residual_previo_pct": res_prev / base * 100,
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
            #
            # ⚠️ **RIESGO GEMELO NO MEDIDO (2026-08-17).** Un dólar-linked se
            # ajusta por A3500 igual que un CER por CER, así que su cuadro tiene
            # la MISMA estructura que la que rompió a DICP: si 1816 expresa cada
            # flujo con el tipo de cambio de SU fecha, la Σ mezcla dólares de
            # distintos días y el divisor correcto sería `A3500_hoy / A3500_emisión`.
            # Acá NO se cambia porque no está medido —los DL que probamos (D10Y7,
            # D30O6) no habían amortizado nada, que es exactamente el caso en que
            # los dos divisores coinciden y el problema no se ve. Para medirlo:
            # un DL que ya amortizó, con `scripts/diag_cer_amortizado`.
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

    # `suma_pct` es la Σ de amortizaciones YA en la base de salida. En la rama
    # `cer` con ratio es la lectura que importa: para PARP da 100,00 y para DICP
    # 118,30 (nominal capitalizado) — o sea, deja de ser una constante decorativa
    # y pasa a decir algo del bono.
    return {"flujos": flujos, "escala": escala, "suma_amort": suma_amort,
            "suma_pct": round(sum(f.get("amortizacion_pct", 0.0) for f in flujos), 6),
            "divisor": round(base, 6),
            "divisor_es": "ratio_cer" if (rama == "cer" and ratio_cer) else "suma",
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
          *, tabla: str = "", accion: str = "", aviso: str = "",
          pide: dict | None = None) -> dict:
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
            # `pide` = el dato se puede TIPEAR ACÁ MISMO y volver a simular con él
            # (pedido del user, 2026-08-17): *«no podría ser acá mismo interactivo
            # y que me pida el CER de emisión para continuar, y que rehaga la
            # simulación con ese dato y si va todo bien ya lo aplique con eso»*.
            # Es la diferencia entre aplicar a ciegas y aplicar VIENDO la tasa.
            "pide": pide or None,
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
                duration=None, cota_ic: float | None = None,
                precio: float | None = None) -> dict:
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

    # ── LA PRUEBA DIRECTA: TEA al MISMO precio + duration ────────────────────
    #
    # **Esto no es un umbral más: es una demostración, y por eso va ANTES que
    # cualquier interpretación de la paridad.** La TEA a un precio dado es una
    # función del cronograma y de las fechas, nada más. Si a NUESTRO precio la
    # tasa de ellos y la nuestra dan lo mismo *y* la duration también, los flujos
    # son los mismos — no hay dos cuadros distintos que produzcan las dos cosas.
    #
    # El caso que lo obligó (DICP, 2026-08-17): TEA 0 bps, duration 0,00% y
    # paridad 86,57% contra 90,19% → la cadena decía «se contradicen, otro
    # cronograma» y BLOQUEABA. Era **aritméticamente imposible** que fuera eso.
    #
    # Lo que sí difiere es la DEFINICIÓN de paridad. 1816 divide su precio CLEAN
    # por el valor técnico; nosotros dividimos el precio que OPERA (su
    # `precioDirty`, que para DICP es exactamente nuestro 48.600) por VN × ratio.
    # Llevando el nuestro a su precio clean:
    #
    #     DICP  86,57 × (50.589,80 / 48.600) = 90,11  contra 90,19  → 0,08%
    #     PARP  63,77 × (35.419,08 / 35.800) = 63,09  contra 63,34  → 0,25%
    #
    # O sea: **su valor técnico y el nuestro son el mismo número**; lo que cambia
    # es el numerador. Se muestra la cuenta en vez de una interpretación — quien
    # audita puede rehacerla.
    tea_ok = (isinstance(tea, int | float) and isinstance(suya_tea, int | float)
              and abs(float(tea) - float(suya_tea)) * 10_000 <= _BPS_COINCIDE
              and mismo.get("tea") is not None)
    if tea_ok and dur_ok:
        clean = _num(ref.get("precio_clean"))
        recon = ""
        cierra = False
        if clean and precio and precio > 0:
            ajustada = nuestra_par * clean / float(precio)
            dif_aj = abs(ajustada - suya_par) / suya_par * 100 if suya_par else 999.0
            cierra = dif_aj <= _PARIDAD_COINCIDE
            recon = (f" Llevando la nuestra a SU precio clean ({clean:,.2f} contra "
                     f"los {float(precio):,.2f} que opera): {ajustada:.2f}% contra "
                     f"{suya_par:.2f}% → {dif_aj:.2f}%."
                     + (" **El valor técnico es el mismo**: lo que cambiaba era el "
                        "numerador." if cierra else ""))
        return _paso("cotejo_1816", "El cuadro coincide con el de 1816",
                     OK if cierra or not clean else REVISAR,
                     linea + ". **La TEA al MISMO precio y la duration coinciden "
                             "las dos** → el cronograma ES el de ellos: no hay dos "
                             "cuadros distintos que den la misma tasa al mismo "
                             "precio y encima la misma duration. La paridad mide "
                             "otra cosa — ellos la calculan sobre el precio CLEAN "
                             "y nosotros sobre el que opera." + recon,
                     tabla="1816 /indicadores",
                     aviso="" if (cierra or not clean) else
                           "la reconciliación de paridad no cierra del todo: "
                           "mirar si el interés corrido de ellos es razonable")
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
                        # Con el número tipeado, `nota_cer` dice que vino a mano.
                        # Mostrar «de la fecha de emisión de 1816» en ese caso
                        # sería atribuirle a 1816 un dato que puso el user.
                        (f"{cer_emision} — {nota_cer}" if nota_cer else
                         f"{cer_emision} (de la fecha de emisión de 1816, T−10 hábiles)")
                        if cer_emision else
                        f"{nota_cer or 'no se pudo calcular'}. **El alta se hace igual**: "
                        "queda en AVISOS y sin este número el bono no muestra tasa.",
                        tabla="macro.series_macro (CER)",
                        aviso="" if cer_emision else
                              f"Cargar el CER de emisión de {ticker} en Manager → Títulos",
                        # Sin el número no hay tasa, pero el número lo tenés vos:
                        # está en el prospecto o en el BCRA. Pedirlo ACÁ y volver a
                        # simular convierte «aplicá y después andá a cargarlo» en
                        # «escribilo, mirá la tasa, y aplicá con el dato adentro».
                        pide=None if cer_emision else {
                            "campo": "cer_emision", "label": "CER de emisión",
                            "tipo": "numero",
                            "ayuda": "el índice CER del día de emisión (prospecto o "
                                     "BCRA). Con esto vuelvo a simular y, si cierra, "
                                     "el bono se da de alta CON el dato."}))

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
                          precio=precio, duration=duration,
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
    # EL DIVISOR, en la memoria y no solo en la cadena: es el insumo que decidió
    # 530 bps en DICP, y una memoria de cálculo que no lo muestra deja el número
    # más importante fuera de la auditoría.
    if conv.get("divisor_es") == "ratio_cer":
        f.append(fila("Divisor del cuadro",
                      f"{conv['divisor']:,.4f}  →  Σ {conv.get('suma_pct'):,.4f}% "
                      "del VN original",
                      "CER_liq / cer_emision × 100 — 1816 manda los importes en "
                      "pesos ajustados por el CER de CADA fecha, así que la Σ del "
                      "cuadro mezcla unidades y no sirve de divisor"))
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
    """El CER de liquidación a la fecha de emisión: **T−10 días hábiles**.

    Usa `get_cer_liquidacion` —la MISMA función que el motor— para que un bono
    nuevo no arranque con un divisor distinto del que usa la valuación.

    ⚠️ **La causa del incidente del 2026-08-17.** `get_cer_liquidacion` resuelve el
    T−10 **indexando `mercado.dias_habiles`** y devuelve `None` si esa tabla no
    llega diez hábiles antes de la fecha pedida. El llamador leía ese `None` como
    «no hay CER» y el mensaje decía *«la serie CER no llega hasta 2025-11-28»* —
    con el CER de esa semana **presente en la base**, como mostró el user.

    Dos errores encadenados, los dos míos:

    1. **El mensaje mentía la fecha.** Interpolaba la fecha de EMISIÓN y la
       etiquetaba «(T−10 hábiles)», así que nombraba un día que la función nunca
       buscó. El dato real que hace falta para una emisión del 2025-11-28 es el
       CER de **~2025-11-14**.
    2. **Un `None` con tres causas distintas** —sin calendario, sin ese día en la
       serie, o error— colapsadas en una sola frase que culpaba siempre a la
       serie. Un mensaje así no manda a mirar el lugar equivocado por casualidad:
       lo hace siempre.

    Ahora los eslabones se resuelven **por separado** y el mensaje nombra las DOS
    fechas. Y si el calendario oficial no cubre la emisión, la fecha se calcula
    igual con `calendario.restar_habiles` (puro, `holidays.Argentina`) — porque no
    poder leer la tabla no es lo mismo que no poder saber qué día era.
    """
    if not fecha_emision:
        return None, "1816 no trae la fecha de emisión de este bono"
    emision = fecha_emision[:10]
    try:
        from core.calendario import restar_habiles
        from engines.curvas import (
            cargar_cer,
            cargar_dias_habiles,
            fecha_cer_liquidacion,
            get_cer_en_fecha,
        )
        habiles = cargar_dias_habiles()
        # 1) LA FECHA. Primero con el calendario oficial —el mismo que usa el
        #    motor, así el número no puede diferir del suyo— y solo si ese no
        #    alcanza, con el cálculo puro.
        objetivo = fecha_cer_liquidacion(habiles, emision)
        via = "calendario de la mesa"
        if not objetivo:
            objetivo = restar_habiles(date.fromisoformat(emision), 10).isoformat()
            via = "calendario feriados AR (mercado.dias_habiles no cubre esa fecha)"
        # 2) EL DATO. `get_cer_en_fecha` tolera hasta 7 días para atrás (fines de
        #    semana y feriados), igual que el motor.
        cer = get_cer_en_fecha(cargar_cer(dias=4000), date.fromisoformat(objetivo))
    except Exception as e:
        return None, f"no se pudo calcular el CER de emisión: {type(e).__name__}: {e}"
    if cer:
        return float(cer), ("" if via.startswith("calendario de la mesa") else
                            f"T−10 hábiles de {emision} = {objetivo}, resuelto por {via}")
    return None, (f"emisión {emision} → T−10 hábiles = **{objetivo}**, y "
                  f"{_rango_cer(objetivo)}")


def _rango_cer(fecha: str) -> str:
    """Por qué falta ESE día, con el rango real de la serie. Sin esto el mensaje
    dice «no llega» sin decir hasta dónde llega — que es la única información que
    convierte la queja en un diagnóstico.

    Sale de la misma tabla que lee el motor. Si la consulta falla se degrada: el
    alta no puede depender de poder explicarse."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT MIN(fecha), MAX(fecha), COUNT(*) "
                        "FROM macro.series_macro WHERE serie = 'CER'")
            desde, hasta, n = cur.fetchone() or (None, None, 0)
    except Exception:
        return "la serie CER no tiene ese día"
    if not hasta:
        return "la serie CER está VACÍA en macro.series_macro"
    if str(hasta) < fecha:
        return (f"la serie CER llega hasta {hasta}: está ATRASADA — el problema "
                "no es este bono")
    return (f"la serie CER va de {desde} a {hasta} ({n:,} días) pero le falta ESE "
            "día: es un HUECO, no un atraso")


def ratio_cer_hoy(cer_emision: float | None) -> tuple[float | None, str]:
    """`CER de liquidación de HOY / CER de emisión` — el divisor del cuadro CER.

    **Es el MISMO número por el que el motor multiplica los flujos** al valuar
    (`ratio = cer_liq / cer_emision` en la rama `cer` de `engines/curvas.py`), y
    sale de las mismas dos funciones (`cargar_cer` + `get_cer_liquidacion`, T−10
    hábiles desde el settlement). Que sea el mismo no es prolijidad: es lo que
    hace que valga el invariante **`monto_flujo_cer(f) × ratio` = el importe en
    pesos que publica 1816**. Con dos ratios distintos el cuadro guardado no
    reproduciría el cuadro de ellos y nadie se enteraría.

    Devuelve `(ratio, motivo)`; `ratio=None` con el motivo en texto.
    """
    if not cer_emision or cer_emision <= 0:
        return None, "falta el CER de emisión"
    try:
        from engines.curvas import (
            cargar_cer,
            cargar_dias_habiles,
            get_cer_liquidacion,
            siguiente_dia_habil,
        )
        habiles = cargar_dias_habiles()
        settlement = siguiente_dia_habil(habiles, date.today())
        if not settlement:
            return None, "mercado.dias_habiles no tiene el día hábil siguiente a hoy"
        cer_liq = get_cer_liquidacion(cargar_cer(dias=1200), habiles, settlement, n=10)
        if not cer_liq:
            return None, _rango_cer(settlement)
        return cer_liq / float(cer_emision), f"CER {cer_liq:,.4f} / {cer_emision:,.4f}"
    except Exception as e:
        logger.warning("av_agent: no se pudo resolver el ratio de CER: %s", e)
        return None, f"no se pudo leer el CER ({e})"


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
                        "moneda_denom, moneda_pago, fecha_vencimiento, curva FROM "
                        "research.mkt_1816_instrumentos WHERE ticker = %s", (ticker,))
            r = cur.fetchone()
        if not r:
            return {}
        # `curva` es lo que 1816 dice que ES este bono, y es la entrada de
        # `curvas_ejes.desde_1816`. Sin ella un `sin_ejes` no tenía de dónde
        # sacar la propuesta y quedaba como un comentario (E3.j).
        return {"fecha_emision": r[0].isoformat() if r[0] else "",
                "denominacion": r[1], "emisor": r[2], "isin": r[3],
                "moneda_denom": r[4], "moneda_pago": r[5],
                "fecha_vencimiento": r[6].isoformat() if r[6] else "",
                "curva_1816": r[7] or ""}
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


# ── EL PRESUPUESTO DE TIEMPO DE LAS PUERTAS INTERACTIVAS ─────────────────────
#
# **Detrás de Cloudflare hay un reloj de 100s y el agente no lo sabía**
# (incidente 2026-08-17). Al ponerle backoff a `_auth` contra el 429, una sola
# simulación pasó a poder tardar minutos — y el resultado no fue lentitud sino
# un **HTTP 524**: el proxy corta, la respuesta se pierde y el usuario ve un
# error que no dice absolutamente nada de lo que pasó.
#
# La paciencia correcta **depende de quién espera**: un cron puede aguantar
# minutos, un click no. `mercado_1816.presupuesto` acota todas las esperas
# (throttle y backoff) del bloque, así que se declara UNA vez acá —en las
# puertas del agente, que son las únicas interactivas— en vez de repetirlo en
# los 12 endpoints del router, donde alcanza con olvidarse de uno.
#
# 45s deja margen de sobra dentro de los 100s del proxy, y si se agota el error
# que vuelve **dice qué pasó y qué hacer**, que es lo que un 524 nunca hace.
_PRESUPUESTO_S = 45.0


def _interactivo(fn):
    """Acota a `_PRESUPUESTO_S` todo lo que esta puerta le pida a 1816."""
    @wraps(fn)
    def envoltorio(*a, **kw):
        with mercado_1816.presupuesto(_PRESUPUESTO_S):
            return fn(*a, **kw)
    return envoltorio


@_interactivo
def simular(ticker: str, *, curva_1816: str, precio: float | None = None,
            cer_emision: float | None = None) -> dict:
    """Baja el cuadro de 1816 y calcula la TEA que TENDRÍA el bono. **No escribe.**

    `precio`: si no se pasa, se busca el último del snapshot. Sin precio no hay
    TEA — pero el cuadro igual se baja y se muestra, que es la mitad del valor.

    `cer_emision`: **el dato que ninguna fuente publica, tipeado por el user en la
    misma pantalla** (pedido 2026-08-17). Nuestra serie CER no llega hasta la
    fecha de emisión de un bono nuevo, así que `_cer_de_emision` devuelve `None` y
    el bono nace sin TEA. Pasándolo acá, la simulación se rehace CON el número y
    el user ve la tasa antes de aplicar — en vez de aplicar a ciegas, ir a Manager
    a cargarlo y recién ahí enterarse de si cerraba.

    **Gana sobre el derivado, nunca al revés**: si el user lo escribe, es porque
    lo sacó del prospecto o del BCRA — o sea de una fuente que el sistema no
    tiene. Un valor inválido (≤ 0) se ignora en vez de romper la simulación.
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
    # ⚠️ EL ORDEN IMPORTA: el CER de emisión se resuelve ANTES de convertir. En la
    # rama `cer` el divisor del cuadro ES el ratio de CER (ver `convertir_flujos`),
    # así que convertir primero y buscar el CER después daba un cuadro en una base
    # que no era la del bono. Antes no se notaba porque el divisor era la Σ, que no
    # depende de nada.
    #
    # El CER de emisión NO hace falta pedirlo: la fecha de emisión está en el
    # catálogo de 1816 (ya persistido, 0 créditos) y la serie CER es nuestra.
    ficha = _ficha_1816(tk)
    cer_manual = cer_emision if (cer_emision or 0) > 0 else None
    cer_emision, nota_cer = (None, "")
    if rama_tent == "cer":
        cer_emision, nota_cer = _cer_de_emision(ficha.get("fecha_emision", ""))
        if cer_manual:
            cer_emision = cer_manual
            nota_cer = (f"CER de emisión {cer_manual:g} cargado a mano — la serie "
                        "no llega a la fecha de emisión, así que este número no "
                        "sale de ninguna fuente nuestra")

    ratio, ratio_nota = (None, "")
    if rama_tent == "cer":
        ratio, ratio_nota = ratio_cer_hoy(cer_emision)
    conv = convertir_flujos(cupones, rama_tent, ratio_cer=ratio)
    hoy = date.today().isoformat()
    futuros = [f for f in conv["flujos"] if f["fecha"] > hoy]
    vencimiento = conv["flujos"][-1]["fecha"] if conv["flujos"] else ""

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
        "cer_manual": bool(cer_manual),
        "nota_cer": nota_cer,
        "ratio_cer": ratio, "ratio_nota": ratio_nota,
        "suma_pct": conv.get("suma_pct"), "divisor_es": conv.get("divisor_es"),
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


def _precio_local(simbolo: str, evidencia: dict | None = None) -> tuple[float | None, str]:
    """El precio SIN salir a la red, por las tres fuentes que ya tenemos.

    **Por qué existe** (user, 2026-08-17): el simulador iba `snapshot → 1816`, así
    que fuera de rueda —o con un bono que no operó— se quedaba sin nada que
    dividir y toda la cadena moría en «no hay precio en el snapshot». Pero el
    precio está guardado en otros dos lados:

      1. `mercado.market_snapshot` — el live del motor. **Un 0 NO es un precio**:
         DHSGO tiene `last_price` 0,0 y entraba como válido.
      2. `mercado.snapshots_cierre` — el cierre persistido, con su fecha.
      3. la **evidencia CONGELADA del hallazgo**, que ya viaja hasta el front y
         se estaba tirando a la basura para volver a leer en vivo.

    Cada una viaja con su etiqueta: un precio de ayer sirve para diagnosticar,
    pero el que lee tiene que saber que es de ayer.
    """
    from core import market_snapshot

    try:
        m = (market_snapshot.cols_map([simbolo], ["last_price"]) or {}).get(simbolo) or {}
        px = _num(m.get("last_price"))
        if px and px > 0:
            return px, "snapshot (live)"
    except Exception:
        pass
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT last_price, fecha FROM mercado.snapshots_cierre "
                        "WHERE ticker = %s", (simbolo,))
            fila = cur.fetchone()
        if fila and _num(fila[0]) and _num(fila[0]) > 0:
            return _num(fila[0]), f"cierre del {fila[1]}"
    except Exception:
        pass
    px = _num((evidencia or {}).get("last_price"))
    if px and px > 0:
        return px, "el precio congelado en el hallazgo"
    return None, ""


def _simular_tasa(doc: dict, simbolo: str, precio: float | None,
                  ticker: str = "", moneda_eje: str = "",
                  ref_1816: dict | None = None, sin_red: bool = False) -> dict:
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

    # **La referencia de 1816 se puede INYECTAR.** El que corre el motor DOS veces
    # sobre el mismo bono (el diagnóstico: «cómo está hoy» contra «cómo quedaría»)
    # estaba pagando DOS veces la misma consulta — y el precio de referencia de un
    # bono no cambia entre las dos. Ver `simular_arreglo`.
    fuente, ref = "manual", dict(ref_1816 or {})
    if precio is None:
        fuente = "snapshot"
        try:
            m = market_snapshot.cols_map([simbolo], ["last_price"])
            precio = (m.get(simbolo) or {}).get("last_price")
        except Exception:
            precio = None
        if (not precio or precio <= 0) and not sin_red and ticker and not ref:
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
    if not ref and ticker and not sin_red:
        ref = _referencia_1816(ticker, moneda_eje=moneda_eje, simbolo=simbolo)
    # Y el cotejo DEFINITIVO: su tasa al MISMO precio que usamos nosotros. Sin
    # esto, una diferencia puede ser la fórmula o el insumo y no hay forma de
    # saber cuál; con esto lo que queda es solo convención o cronograma.
    if ticker and r.get("TEA") is not None and not sin_red:
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


@_interactivo
def aplicar(ticker: str, *, curva_1816: str, actor: str = "",
            cer_emision: float | None = None) -> dict:
    """Simula y, si la rama lo permite y hay cuadro, **da de alta el bono**.

    Escribe por `bonos_admin.upsert_bono` —la MISMA puerta que usa la mesa desde
    Manager— así no puede existir un alta del agente con otra shape que un alta
    humana. Y deja la acción en el libro (`av_agent_acciones`).
    """
    from api.services import av_agent_acciones as acc
    from api.services import bonos_admin

    # El CER tipeado viaja hasta acá: el bono se da de alta CON el dato, no se
    # crea pelado para después completarlo. Si viene, tampoco se genera el aviso —
    # el paso deja de estar en `revisar` porque ya no falta nada.
    sim = simular(ticker, curva_1816=curva_1816, cer_emision=cer_emision)
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


# ══════════════════════════════════════════════════════════════════════════════
# COMPLETAR EL CRONOGRAMA de un bono que YA está en el master (E3.a)
#
# El hallazgo `flujos_vacios` no tenía acción: decía «1816 lo tiene y se puede
# completar» y ahí moría — el user tenía que ir a Manager y cargar el cuadro a
# mano, que es exactamente el trabajo que este agente existe para no hacer.
#
# **Es el alta al revés**: en un alta los EJES los deriva el agente de la curva de
# 1816 (el bono no existe); acá el bono YA existe y sus ejes los cargó la mesa, así
# que son la verdad y NO se tocan. Lo único que falta —y lo único que se escribe—
# es el cronograma.
#
# Y por eso el cotejo importa MÁS que en un alta: se le va a meter un cuadro a un
# bono que la vista ya muestra. Si ese cuadro está mal, el bono pasa de «sin TEA» a
# «con una TEA equivocada», que es estrictamente peor. Se reusa el MISMO
# `_cotejo_tea` —paridad, duration y la cota del devengado— porque un cuadro
# escrito por esta puerta tiene que pasar el mismo examen que uno escrito por el
# alta.
# ══════════════════════════════════════════════════════════════════════════════

def _doc_de_curvas(ticker: str) -> dict | None:
    """El doc del master **como lo arma `core/curvas_sql`**, o `None` si no está.

    ⚠️ **Un doc de `mercado.curvas` NO es el blob `data`.** Los EJES
    (`emisor_tipo` · `moneda_eje` · `ajuste` · `ajuste_alt` · `ley`) viven en
    COLUMNAS y se mezclan encima del blob — `_COLS_FUERA_DEL_BLOB`. Leer solo
    `data` devuelve un doc con los ejes en `None`, y a partir de ahí **todo lo
    demás miente en cascada** (DICP, 2026-08-17):

    · la pantalla mostraba «DICP · · ·» y «ejes cargados por la mesa
      (None · None · None)»;
    · `sin_cer` —que decide el mensaje— evalúa `ajuste == "cer"`, y con `ajuste`
      en `None` daba False, así que en vez de «falta el CER de emisión» el
      encabezado decía «revisar la escala del flujo o la pata»: mandaba a mirar
      el cuadro, que estaba perfecto;
    · el motor, sin `cer_emision`, salía por su puerta de emergencia y devolvía
      la duration NAIVE — 7,3781 para DICP, que son exactamente sus años al
      vencimiento.

    Se reusa el MISMO `curvas_sql` en vez de repetir el `SELECT`: es la fuente
    que lee el motor, así que el simulador no puede ver un doc distinto del que
    se va a valuar.
    """
    try:
        from core import curvas_sql
        tk = (ticker or "").strip().upper()
        for d in curvas_sql.cargar_todos():
            if (d.get("ticker_corto") or "").strip().upper() == tk:
                return dict(d)
        return None
    except Exception as e:
        logger.warning("av_agent: no se pudo leer %s de curvas: %s", ticker, e)
        return None


@_interactivo
def simular_flujos(ticker: str, *, cer_emision: float | None = None) -> dict:
    """Baja el cronograma de 1816 y calcula la TEA que TENDRÍA el bono. **No escribe.**

    La pregunta que contesta es la que hizo el user: *«lo que hay que chequear es
    si con el flujo que agregaríamos y nuestro modelo nos da una TEA y esos datos
    como a 1816»*. Por eso el resultado no es «bajé el cuadro» sino el cotejo
    completo contra ellos.
    """
    tk = mercado_1816.normalizar_ticker(ticker)
    doc = _doc_de_curvas(tk)
    if not doc:
        return {"ok": False, "ticker": tk,
                "error": f"{tk} no está en mercado.curvas — esto completa el "
                         "cronograma de un bono que YA existe; para uno nuevo va "
                         "el alta"}

    from api.services.acreencias import tiene_flujo_def
    from engines.curvas import rama_calculo

    if tiene_flujo_def(doc, date.today()):
        # No es un error del agente: es que el hallazgo quedó viejo. Se dice así.
        return {"ok": False, "ticker": tk,
                "error": f"{tk} YA tiene cronograma — el hallazgo es de una corrida "
                         "anterior y quedó resuelto"}

    try:
        data = mercado_1816.cashflow(tk)
    except Exception as e:
        return {"ok": False, "ticker": tk, "error": f"1816 no dio el cuadro: {e}"}
    cupones = data.get("cashflow") or []
    if not cupones:
        return {"ok": False, "ticker": tk,
                "error": "1816 devolvió el cuadro VACÍO: este bono necesita el "
                         "prospecto, no hay nada que copiar"}

    # **La rama sale del DOC, no de una curva de 1816.** Los ejes ya los cargó la
    # mesa y son la verdad; derivarlos de nuevo sería inventar una segunda opinión
    # sobre algo que no está en duda.
    rama = rama_calculo(doc)

    # ⚠️ EL CER DE EMISIÓN SE RESUELVE ANTES DE CONVERTIR. En la rama `cer` el
    # divisor del cuadro ES el ratio de CER (ver `convertir_flujos`), así que sin
    # ese número no hay cuadro correcto — no es un dato que se pueda completar
    # después. El tipeado a mano (E2.x) gana solo si el doc no lo tiene: lo que
    # cargó la mesa es la verdad.
    cer_manual = cer_emision if (cer_emision or 0) > 0 else None
    cer_usado = doc.get("cer_emision") or cer_manual
    ratio, ratio_nota = (None, "")
    if rama == "cer":
        ratio, ratio_nota = ratio_cer_hoy(cer_usado)

    conv = convertir_flujos(cupones, rama, ratio_cer=ratio)
    hoy = date.today().isoformat()
    futuros = [f for f in conv["flujos"] if f["fecha"] > hoy]
    vencimiento = conv["flujos"][-1]["fecha"] if conv["flujos"] else ""

    doc_sim = {**doc, "flujos": conv["flujos"]}
    if conv["flujo_vencimiento"] is not None:
        doc_sim["flujo_vencimiento"] = conv["flujo_vencimiento"]
    if cer_manual and not doc.get("cer_emision"):
        doc_sim["cer_emision"] = cer_manual
    simbolo = (doc.get("ticker") or "").strip()
    moneda_eje = (doc.get("moneda_eje") or "").strip()

    out = {
        "ok": True, "ticker": tk, "modo": "completar_flujos",
        "rama": rama, "curva": doc.get("curva"),
        "escala": conv["escala"], "suma_amortizaciones": conv["suma_amort"],
        "cupones": conv["n"], "cupones_futuros": len(futuros),
        "cupones_pagados": conv["n"] - len(futuros),
        "cer_emision": cer_usado,
        "cer_manual": bool(cer_manual and not doc.get("cer_emision")),
        "ratio_cer": ratio, "ratio_nota": ratio_nota,
        "suma_pct": conv.get("suma_pct"), "divisor_es": conv.get("divisor_es"),
        "vencimiento": vencimiento, "flujo_vencimiento": conv["flujo_vencimiento"],
        "simbolo": simbolo, "cuadro": conv,
        "ejes": {"emisor_tipo": doc.get("emisor_tipo"), "moneda_eje": moneda_eje,
                 "ajuste": doc.get("ajuste"), "ley": doc.get("ley")},
    }
    out.update(_simular_tasa(doc_sim, simbolo, None, ticker=tk, moneda_eje=moneda_eje))
    out["chequeos"] = _chequeos_flujos(
        ticker=tk, doc=doc_sim, rama=rama, conv=conv, vencimiento=vencimiento,
        out=out, cupones=cupones, futuros=len(futuros))
    out["veredicto"] = _veredicto(out["chequeos"])
    # `_memoria_de_calculo` espera el objeto de ejes que devuelve `curvas_ejes`.
    # Acá los ejes vienen del DOC (los cargó la mesa), así que se arma el mismo
    # shape en vez de reescribir el cuadro: el que lo lee tiene que ver lo mismo
    # venga de un alta o de un completar.
    ejes_doc = SimpleNamespace(
        emisor_tipo=doc.get("emisor_tipo") or "", moneda=moneda_eje,
        ajuste=doc.get("ajuste") or "", ajuste_alt=doc.get("ajuste_alt") or "",
        ley=doc.get("ley") or "")
    out["calculo"] = _memoria_de_calculo(
        doc=doc_sim, ejes=ejes_doc, rama=rama, conv=conv, out=out,
        ref=out.get("referencia_1816") or {}, job_tasa=_tasa_externa_doc(doc))
    return out


def _tasa_externa_doc(doc: dict) -> str:
    return tasa_externa_de(doc.get("ajuste"))[1]


def _chequeos_flujos(*, ticker: str, doc: dict, rama: str, conv: dict,
                     vencimiento: str, out: dict, cupones: list[dict],
                     futuros: int = 0) -> list[dict]:
    """La cadena del COMPLETAR. Más corta que la del alta y a propósito: los ejes,
    la curva, el símbolo y la especie **ya están resueltos** —el bono existe y la
    vista lo muestra— así que chequearlos sería teatro.

    Lo que sí se chequea es todo lo que puede salir mal al escribir un cronograma
    en un bono vivo, que es el riesgo REAL de esta puerta."""
    ps: list[dict] = []
    ps.append(_paso("existe", "El bono ya está en el master", OK,
                    f"{ticker} · curva «{doc.get('curva')}» · ejes cargados por la "
                    f"mesa ({doc.get('emisor_tipo')} · {doc.get('moneda_eje')} · "
                    f"{doc.get('ajuste')}). **No se tocan**: acá solo se escribe el "
                    "cronograma.", tabla="mercado.curvas"))
    # **Los cupones YA PAGADOS se guardan y NO se valúan.** 1816 manda el
    # cronograma COMPLETO desde la emisión (medido en GD46), así que un bono de
    # 2004 trae 60 cupones de los que la mayoría ya se cobraron. Guardarlos es
    # correcto —el cuadro es el del bono, no el de hoy— y el motor los filtra al
    # valuar (`fecha_flujo(f) > fecha_settlement`, la MISMA regla que el resto del
    # sistema). Decirlo es lo que evita que el número asuste.
    pagados = conv["n"] - futuros
    ps.append(_paso("cuadro", "1816 mandó el cuadro de flujos",
                    OK if futuros else BLOQUEA,
                    f"{conv['n']} cupón/es · Σ amortizaciones {conv['suma_amort']:,.2f} "
                    f"→ escala {conv['escala']} · vence {vencimiento}"
                    + (f". **{pagados} ya se pagaron** y {futuros} quedan por delante: "
                       "el cuadro se guarda COMPLETO (es el del bono) y el motor "
                       "valúa solo los futuros." if pagados else "")
                    if futuros else
                    "el cuadro no tiene ningún cupón FUTURO: este bono ya venció, "
                    "no hay nada que valuar",
                    tabla="1816 /cashflow (fechaPagoEfectiva)"))
    # EL DIVISOR — el paso que no existía y que le costó a DICP 530 bps.
    #
    # 1816 manda cada flujo en pesos ajustados por el CER **de su propia fecha**:
    # los pasados en pesos de cuando se pagaron, los futuros en pesos de hoy. El
    # divisor que lleva eso a «% del VN original» es el ratio de CER, no la Σ del
    # cuadro — sumar los 60 cupones de DICP es sumar pesos de 2024 con pesos de
    # 2026. Se muestra porque es AUDITABLE: la Σ resultante tiene que ser 100 para
    # un bono común y >100 para uno que capitalizó (DICP: 118,30).
    if rama == "cer":
        suma_pct, divisor_es = conv.get("suma_pct"), conv.get("divisor_es")
        ratio_nota = out.get("ratio_nota") or ""
        por_ratio = divisor_es == "ratio_cer"
        # ⚠️ **UNA CAUSA, UN SOLO BLOQUEO.** Si falta el CER de emisión, este paso
        # y el de abajo se ponen rojos por lo MISMO: dos alarmas para un dato.
        # Eso no informa, asusta — y hace parecer que hay dos cosas que arreglar
        # cuando hay una sola casilla que llenar. Cuando la causa es el CER, acá
        # va `no_se_puede_saber` y el que frena es el paso que PIDE el dato.
        falta_cer = not doc.get("cer_emision")
        ps.append(_paso("divisor", "El cuadro está en la escala del VN original",
                        OK if por_ratio else (NO_SE if falta_cer else BLOQUEA),
                        (f"dividido por el ratio de CER ({ratio_nota}) → Σ "
                         f"amortizaciones {suma_pct:,.4f}% del VN original"
                         + (" — el bono CAPITALIZÓ interés, por eso pasa de 100"
                            if (suma_pct or 0) > 101 else "")
                         if por_ratio else
                         "todavía no se puede saber: el divisor es `CER de hoy / "
                         "CER de emisión` y falta el segundo — se carga en el paso "
                         "de abajo y esto se resuelve solo."
                         if falta_cer else
                         f"el CER de emisión está ({doc.get('cer_emision')}) pero "
                         f"no se pudo armar el ratio: {ratio_nota or 'sin motivo'}. "
                         "Sin él el cuadro solo se normalizaría por su propia Σ, "
                         "que mezcla pesos de distintas fechas: para un bono que "
                         "ya amortizó eso da una TEA equivocada sin dar ningún "
                         "error (DICP: 3,91% contra 9,25%)."),
                        tabla="1816 /cashflow ÷ (CER_liq / cer_emision)"))

    # La rama sale del doc, así que no puede ser «otros» por un error de traducción:
    # si lo es, es porque la mesa clasificó el bono en algo que no valuamos.
    convertible = rama in RAMAS_AUTOMATICAS
    ps.append(_paso("rama", "El cuadro se puede convertir sin ambigüedad",
                    OK if convertible else REVISAR,
                    f"rama «{rama}» — conversión inequívoca" if convertible else
                    f"rama «{rama}»: a este bono no le calculamos la tasa nosotros, "
                    "así que el cuadro se escribe igual pero la TEA la trae otra "
                    "fuente (o ninguna).",
                    tabla="engines/curvas.py::rama_calculo"))
    # EL CER DE EMISIÓN. Sin este número la rama `cer` del motor sale por su
    # puerta de emergencia y devuelve la duration NAIVE (años al vencimiento) — el
    # síntoma que confundió a DICP y PARP. Se pide ACÁ MISMO, igual que en el alta
    # (E2.x): escribís el número, se re-simula, y si cierra se aplica con el dato.
    if rama == "cer":
        cer_e = doc.get("cer_emision")
        ps.append(_paso("cer_emision", "CER de emisión resuelto",
                        OK if cer_e else BLOQUEA,
                        f"{cer_e}" + (" (cargado a mano)" if out.get("cer_manual")
                                      else " (ya estaba en el master)")
                        if cer_e else
                        "el bono no lo tiene cargado, y **sin ese número el cuadro "
                        "ni siquiera se puede convertir**: 1816 manda los importes "
                        "en pesos ajustados por CER y el divisor que los lleva a % "
                        "del VN es justamente `CER de hoy / CER de emisión`. No es "
                        "un dato que se complete después — escribilo acá y se "
                        "re-simula con él.",
                        tabla="mercado.curvas · macro.series_macro (CER)",
                        aviso="" if cer_e else
                              f"Cargar el CER de emisión de {ticker}",
                        pide=None if cer_e else {
                            "campo": "cer_emision", "label": "CER de emisión",
                            "tipo": "numero",
                            "ayuda": "el índice CER del día de emisión (prospecto o "
                                     "BCRA). Con esto vuelvo a simular y se ve la "
                                     "TEA antes de aplicar."}))
    tea, paridad = out.get("tea"), out.get("paridad")
    ps.append(_paso("precio", "Hay precio para simular la tasa",
                    OK if out.get("precio") else INFO,
                    (f"precio {out['precio']:,.4f} ({out.get('precio_fuente')})"
                     if out.get("precio") else
                     "sin precio: el cuadro se puede escribir igual, la TEA aparece "
                     "con el primer trade"),
                    tabla="mercado.market_snapshot · 1816"))
    ps.append(_cotejo_tea(tea, out.get("referencia_1816") or {},
                          precio=out.get("precio"),
                          job_tasa=_tasa_externa_doc(doc), paridad=paridad,
                          duration=out.get("duration"),
                          cota_ic=cota_devengado(cupones)))
    # **Lo que se va a escribir, enumerado.** Es la diferencia entre «confiá» y
    # «mirá»: son los ÚNICOS campos que toca esta puerta.
    campos = ["flujos"]
    if conv["flujo_vencimiento"] is not None:
        campos.append("flujo_vencimiento")
    if out.get("cer_manual"):
        campos.append("cer_emision (lo escribiste vos)")
    ps.append(_paso("escritura", "Qué se va a escribir", INFO,
                    "solo " + " · ".join(campos) + f" en {ticker}. Ejes, curva, "
                    "emisor, símbolo y cer_emision quedan **intactos**.",
                    tabla="mercado.curvas"))
    for i, p in enumerate(ps, 1):
        p["n"] = i
    return ps


@_interactivo
def aplicar_flujos(ticker: str, *, actor: str = "",
                   cer_emision: float | None = None) -> dict:
    """Simula y, si la cadena cierra, **escribe el cronograma** — y nada más.

    A diferencia del alta, que arma el doc entero, acá se hace un UPDATE puntual
    sobre el blob: los ejes, el emisor, el símbolo y el `cer_emision` que cargó la
    mesa no se pueden pisar por accidente porque **ni siquiera están en el
    payload**.
    """
    from api.services import av_agent_acciones as acc

    sim = simular_flujos(ticker, cer_emision=cer_emision)
    if not sim.get("ok"):
        acc.registrar(accion="completar_flujos", objetivo=ticker.upper(), ok=False,
                      error=sim.get("error", "")[:300], por=actor)
        return {**sim, "aplicado": False}
    ver = sim.get("veredicto") or {}
    if not ver.get("puede_aplicar", True):
        bloqueos = [c for c in sim["chequeos"] if c["estado"] == BLOQUEA]
        return {**sim, "aplicado": False,
                "error": "el pre-flight no pasa: "
                         + "; ".join(c["titulo"] for c in bloqueos)}

    conv = sim["cuadro"]
    parche: dict = {"flujos": conv["flujos"]}
    if conv["flujo_vencimiento"] is not None:
        parche["flujo_vencimiento"] = conv["flujo_vencimiento"]
    if sim.get("vencimiento"):
        parche["fecha_vencimiento"] = sim["vencimiento"]
    # El CER de emisión SOLO si lo tipeó el user y el bono no lo tenía. Es la
    # única excepción a «acá solo se escribe el cronograma», y es explícita: sin
    # ese número el bono queda escrito y sin tasa, que es la mitad del trabajo.
    if sim.get("cer_manual") and sim.get("cer_emision"):
        parche["cer_emision"] = sim["cer_emision"]
    try:
        import json

        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            # `data || parche` MERGEA: lo que no está en el parche no se toca.
            cur.execute("UPDATE mercado.curvas SET data = COALESCE(data, '{}'::jsonb) "
                        "|| %s::jsonb WHERE ticker = %s",
                        (json.dumps(parche), sim["ticker"]))
            filas = cur.rowcount or 0
    except Exception as e:
        acc.registrar(accion="completar_flujos", objetivo=sim["ticker"], ok=False,
                      error=str(e)[:300], por=actor)
        return {**sim, "aplicado": False, "error": f"no se pudo escribir: {e}"}
    if not filas:
        return {**sim, "aplicado": False, "error": "el UPDATE no tocó ninguna fila"}

    # ⚠️ Sin `tabla=`: el libro de acciones NO lo acepta — la tabla la resuelve él
    # solo por la ACCIÓN (`DESTINOS`), justamente para que la misma acción no se
    # anote con dos destinos según quién la llame. Pasárselo tiraba `TypeError`
    # **después de que el UPDATE ya había commiteado**: HTTP 500 con el cronograma
    # escrito y el user viendo «Error: HTTP 500» sin llegar a QUÉ HIZO (PARP,
    # 2026-08-17). Un test congela que toda `accion=` sea una clave de `DESTINOS`.
    acc.registrar(accion="completar_flujos", objetivo=sim["ticker"], ok=True,
                  por=actor,
                  detalle={"cupones": conv["n"], "escala": conv["escala"],
                           "campos": list(parche), "tea_simulada": sim.get("tea")})
    return {**sim, "aplicado": True,
            "aviso": "los motores leen mercado.curvas al arrancar: reiniciar "
                     "motor_curvas para que empiece a calcular su TEA"}


# ── E3.j — ARREGLAR una TASA SOSPECHOSA ───────────────────────────────────────
#
# El último tipo de hallazgo que era solo un comentario. Son 38 filas y tres
# síntomas: `sin_ejes` (el bono no cae en ninguna curva y desaparece de la vista
# sin dar error), `sin_tea_con_precio` (el XIRR no converge) y
# `paridad_fuera_de_rango` (156.570% en LOC6O, 0,0% en PECKO).
#
# **Los tres son el mismo problema visto de tres lados**: un INSUMO del bono está
# mal —los ejes, o la escala del cuadro— y el motor no puede avisar porque no
# tiene con qué comparar. Nosotros sí: 1816 publica la curva a la que pertenece y
# el cronograma completo. Es exactamente lo que ya usamos para DICP.
#
# **La diferencia con las otras dos puertas, y por qué necesita otra garantía.**
# El alta escribe un bono que no existe y completar-cronograma llena un campo
# vacío: en los dos casos, lo peor que puede pasar es no mejorar nada. Acá se
# PISA un dato que ya está. Por eso la regla de aplicación es más dura y se puede
# decir en una línea:
#
#   > Se aplica **solo si el propuesto coincide con 1816 y el actual NO**.
#
# Las dos mitades importan. Sin la primera se pisaría con algo no verificado;
# sin la segunda se pisaría un bono que ya estaba bien —el hallazgo pudo quedar
# viejo, o el umbral pudo ser demasiado angosto para ese instrumento— y eso es
# estrictamente peor que no hacer nada.
# ── DIAGNÓSTICO LOCAL: lo que se puede afirmar SIN preguntarle a nadie ────────
#
# **Un bono que YA tiene precio y cuadro no necesita a 1816 para saber qué le
# pasa** (user, 2026-08-17: *«no entiendo qué tiene que ver 1816 si esto ya
# existe, ya tenemos precio y flujo»*). Tenía razón, y era la falla de diseño de
# esta puerta: nació consultando a 1816 en la PRIMERA instancia, así que cuando
# 1816 no está —rate limit, caída, un bono que no cubre— no dice absolutamente
# nada, ni siquiera lo que se deduce de una división.
#
# La paridad ES `precio / residual`. Si el resultado se va de escala, uno de los
# dos lados está en la unidad equivocada, y **cuál de los dos se sabe mirando el
# residual**: un cuadro sano tiene el residual cerca de 100 (el bono cotiza por
# 100 de VN). Con los números reales de producción:
#
#     OLC3O  paridad 0,06%      → residual ~1.600x de lo normal  → EL CUADRO
#     RC1CO  paridad 167.830%   → residual 100 (sano)            → EL PRECIO
#
# Los dos son diagnósticos distintos y los dos salen de una división. 1816 queda
# donde corresponde: para CONFIRMAR y para traer el cuadro de reemplazo, no para
# poder abrir la boca.
_RESIDUAL_MIN, _RESIDUAL_MAX = 10.0, 1000.0   # un residual sano ronda 100


def _residual_vivo(doc: dict, rama: str) -> tuple[float, int]:
    """Σ de las amortizaciones FUTURAS del cuadro guardado, con los MISMOS
    accesores del motor — si usara otros, el diagnóstico hablaría de un cuadro
    distinto del que se valúa."""
    from engines.curvas import fecha_flujo

    hoy = date.today()
    campo = "amortizacion" if rama in ("on", "tasa_fija") else "amortizacion_pct"
    total, n = 0.0, 0
    for f in doc.get("flujos") or []:
        fd = fecha_flujo(f)
        if fd and fd > hoy:
            total += float(f.get(campo) or 0.0)
            n += 1
    return round(total, 6), n


# ── EL CATÁLOGO DE ARREGLOS ─────────────────────────────────────────────────
#
# **El agente tenía UN solo movimiento**: traer el cronograma de 1816 y pisar los
# flujos. Por eso las cinco reglas de tasa pasaban por la misma cadena y sus dos
# puertas de 1816 la bloqueaban entera — con rate limit, un domingo, o con un bono
# que 1816 no cubre, no decía NADA, ni siquiera lo que sale de una división.
#
# Medido sobre los 38 hallazgos reales (`scripts/diag_av_agent_arreglos`, corrida
# del 2026-08-17): **38 de 38 se resuelven con datos que ya están en la base.**
# Ninguno necesitaba la red. 1816 no era el insumo: era una costumbre.
#
# Cada causa dice qué se escribe y QUIÉN lo escribe. `agente=False` no es un
# hueco: es el agente diciendo «esto lo veo pero no lo toco», que es distinto de
# no verlo — y es lo que evita que proponga un cambio para tapar un síntoma que
# no le corresponde (el caso `paridad_del_motor`).
CAUSAS: dict[str, dict] = {
    "moneda_flujo_contradice": {
        "titulo": "`moneda_flujo` contradice a los ejes",
        "arreglo": "alinear `moneda_flujo` con lo que dicen los ejes",
        "agente": True, "campo": "moneda_flujo"},
    "escala_del_cuadro": {
        "titulo": "El cuadro está en nominales de la emisión",
        "arreglo": "reescalar las amortizaciones a base 100",
        "agente": False, "campo": "flujos"},
    "campo_de_amortizacion": {
        "titulo": "El cuadro usa el campo de la OTRA rama",
        "arreglo": "reescribir el cronograma en la forma que lee su rama",
        "agente": False, "campo": "flujos"},
    "falta_cer": {
        "titulo": "Falta el CER de emisión",
        "arreglo": "cargar el `cer_emision` (dato manual, se tipea en la cadena)",
        "agente": False, "campo": "cer_emision"},
    "sin_ejes": {
        "titulo": "El bono no cae en ninguna curva",
        "arreglo": "escribir los ejes", "agente": False, "campo": "ejes"},
    # ⚠️ La única causa cuyo arreglo NO es un dato. Existe para que el agente
    # pueda decirlo en vez de proponer que se toque el bono: el bono está bien.
    "paridad_del_motor": {
        "titulo": "La paridad la calcula mal el MOTOR, no el dato",
        "arreglo": "no se arregla con datos — es `engines/curvas.py`",
        "agente": False, "campo": ""},
    "sin_precio": {
        "titulo": "No hay precio en ninguna fuente local",
        "arreglo": "ninguno: sin precio no hay métrica que arreglar",
        "agente": False, "campo": ""},
    "sin_residual": {
        "titulo": "El cronograma no tiene amortizaciones futuras",
        "arreglo": "revisar si el bono ya amortizó todo o si el cuadro está corto",
        "agente": False, "campo": "flujos"},
    "sano": {
        "titulo": "Con los datos de hoy no se detecta nada roto",
        "arreglo": "ninguno — el hallazgo puede haber quedado viejo",
        "agente": False, "campo": ""},
}


# ── LAS LENTES: el agente mira el bono por VARIOS LADOS ─────────────────────
#
# **Pedido del user (2026-08-17)**: *«lo que yo quiero es el ANÁLISIS, que el
# agente tenga varias formas de detectar qué es lo que pasa… muchas
# funcionalidades que simularían a una persona que razona: es por el valor
# técnico, es por la paridad, es por la moneda, es porque falta esto…»*.
#
# La primera versión de este diagnóstico era un árbol de decisión: preguntaba en
# orden y **devolvía en el primer match**. Con eso el agente acertaba la causa
# pero no mostraba el razonamiento — y quien lee la pantalla necesita las dos
# cosas, porque es el que decide si escribir o no.
#
# Ahora corren **TODAS**. Cada lente es una pregunta que una persona le haría al
# bono, contesta con lo que ve, y **la que falla más aguas arriba se lleva la
# causa**. El orden sigue siendo el contrato (equivocarlo ya hizo proponer «cambiá
# la pata» sobre bonos con la pata perfecta); lo nuevo es que las otras siete
# igual hablan.
#
# Todas son LOCALES: cero red, cero créditos, andan un domingo.
LENTES: tuple[tuple[str, str], ...] = (
    ("ficha",    "La ficha: ¿qué es este bono y quién le calcula la tasa?"),
    ("moneda",   "La moneda: ¿en qué unidad entra el precio al motor?"),
    ("insumos",  "Los insumos externos: CER de emisión, MEP, A3500"),
    ("cuadro",   "El valor técnico: ¿en qué escala está el cronograma?"),
    ("precio",   "El precio: de dónde sale y en qué escala está"),
    ("paridad",  "La paridad, con la división a la vista"),
    ("tasa",     "La TEA: ¿el XIRR converge?"),
    ("espejo",   "El espejo en portafolio.assets"),
)


def _ctx(doc: dict, rama: str, est: dict) -> dict:
    """Todo lo que las lentes necesitan, calculado UNA vez. Que compartan los
    mismos números es lo que evita que dos lentes se contradigan."""
    from engines.curvas import moneda_flujo_esperada

    residual, n_fut = _residual_vivo(doc, rama)
    campo_rama = "amortizacion" if rama in ("on", "tasa_fija") else "amortizacion_pct"
    otro_campo = "amortizacion_pct" if campo_rama == "amortizacion" else "amortizacion"
    precio = _num(est.get("precio"))
    simbolo = (doc.get("ticker") or "").strip()
    sufijo = ""
    partes = simbolo.split(" - ")
    if len(partes) >= 3 and partes[2]:
        sufijo = partes[2][-1].upper()
    return {
        "doc": doc, "rama": rama, "est": est, "simbolo": simbolo, "sufijo": sufijo,
        "ejes": (doc.get("emisor_tipo"), doc.get("moneda_eje"), doc.get("ajuste")),
        "residual": residual, "n_fut": n_fut,
        "campo_rama": campo_rama, "otro_campo": otro_campo,
        "suma_otro": _suma_amortizacion(doc, otro_campo),
        "precio": precio, "paridad": _num(est.get("paridad")),
        "tea": _num(est.get("tea")),
        "mf": (doc.get("moneda_flujo") or "").strip().upper(),
        "mf_esperada": moneda_flujo_esperada(doc),
        "cer_emision": _num(doc.get("cer_emision")),
        "vn": _num(doc.get("valor_nominal")) or 100.0,
    }


def _ob(clave: str, estado: str, detalle: str, *, causa: str = "",
        parche: dict | None = None, hechos: dict | None = None) -> dict:
    """Una observación. `causa` solo la ponen las lentes que ENCONTRARON algo.

    ⚠️ **`hechos` es lo que la lente AFIRMA, en máquina.** El detector de
    contradicciones comparaba el TEXTO de las lentes, y eso es frágil: un sinónimo
    lo rompe, y lo que tiene que cazar son justamente incoherencias que ya se le
    escaparon a una lectura humana. Con los hechos declarados, la comparación es
    exacta y no depende de cómo esté redactada la frase.
    """
    return {"clave": clave, "estado": estado, "detalle": detalle,
            "causa": causa, "parche": parche or {}, "hechos": hechos or {}}


def _lente_ficha(c: dict) -> dict:
    rama = c["rama"]
    emisor, moneda, ajuste = c["ejes"]
    if not (emisor and moneda and ajuste):
        return _ob("ficha", REVISAR,
                   "**sin ejes**: este bono no cae en ninguna curva, así que "
                   "desaparece de la tabla, de los forwards y del fair value **sin "
                   "dar un solo error**. Nada de lo que sigue se puede clasificar "
                   "bien mientras falte esto.",
                   causa="sin_ejes")
    fuente, job = tasa_externa_de(ajuste)
    externa = fuente == "1816"
    return _ob("ficha", INFO if externa else OK,
               f"{emisor} · {moneda} · {ajuste} → rama de cálculo «{rama}». "
               + (f"La TEA **no la calculamos nosotros**: la trae {job or '1816'}, "
                  "así que un hallazgo de TASA acá no se arregla tocando el cuadro "
                  "(uno de PARIDAD sí puede: la paridad sale del precio y del "
                  "cronograma, no del cupón)."
                  if externa else
                  "La TEA la calcula nuestro motor con el cronograma cargado."))


def _lente_moneda(c: dict) -> dict:
    """La lente que más veces tuvo razón: 30 de 140 bonos de la rama ON."""
    from engines.curvas import MONEDAS_FLUJO

    if c["rama"] != "on":
        return _ob("moneda", INFO,
                   f"la rama «{c['rama']}» no despacha por `moneda_flujo` — su "
                   "conversión la decide la fórmula de la rama.")
    mf, esperada = c["mf"], c["mf_esperada"]
    if not esperada:
        return _ob("moneda", NO_SE, "sin ejes no se puede decir qué debería decir "
                                    "`moneda_flujo`.")
    # El razonamiento, dicho como lo diría una persona mirando el bono.
    puerta = {"USD": "convierte el precio a dólares (÷MEP si el símbolo no termina "
                     "en D/C)",
              "DL": "divide el precio por el A3500 si viene en escala peso",
              "ARS": "usa el precio tal cual, peso nativo"}
    if mf == esperada:
        return _ob("moneda", OK,
                   f"`moneda_flujo`={mf} coincide con los ejes → el motor "
                   f"{puerta.get(mf, '?')}. El símbolo cargado termina en "
                   f"«{c['sufijo']}».")
    desconocida = bool(mf) and mf not in MONEDAS_FLUJO
    return _ob("moneda", REVISAR,
               f"`moneda_flujo`=**{mf or '(vacío)'}** pero los ejes piden "
               f"**{esperada}**"
               + (f", y «{mf}» ni siquiera es una palabra que el motor conozca: cae "
                  "en el `else` y el precio entra **como peso nativo, sin dar "
                  "error**. Es el vocabulario de la CARTERA, no el del motor."
                  if desconocida else
                  f" → el motor {puerta.get(mf or 'ARS', 'usa el precio tal cual')} "
                  f"cuando debería {puerta.get(esperada, '?')}.")
               + f" El símbolo termina en «{c['sufijo']}», así que la PATA no es el "
                 "problema: con `moneda_flujo` bien, el motor la resuelve solo.",
               causa="moneda_flujo_contradice", parche={"moneda_flujo": esperada})


def _lente_insumos(c: dict) -> dict:
    doc = c["doc"]
    if (doc.get("ajuste") or "") == "cer" and not c["cer_emision"]:
        return _ob("insumos", REVISAR,
                   "falta el **CER de emisión**. Sin él la rama CER sale en "
                   "`curvas.py:439` **sin escribir TEA ni paridad**, así que lo que "
                   "haya guardado en el snapshot es de otra época — el hallazgo lo "
                   "disparó un número viejo, no el bono de hoy.",
                   causa="falta_cer")
    if (doc.get("ajuste") or "") == "cer":
        return _ob("insumos", OK, f"CER de emisión cargado: {c['cer_emision']}.")
    return _ob("insumos", OK, "esta rama no necesita CER de emisión.")


def _lente_cuadro(c: dict) -> dict:
    """El VALOR TÉCNICO: lo que el bono todavía debe. Es el denominador de la
    paridad, así que si está en otra escala **todo lo demás miente**."""
    residual, n_fut = c["residual"], c["n_fut"]
    if not residual and c["suma_otro"]:
        return _ob("cuadro", REVISAR,
                   f"la rama «{c['rama']}» lee `{c['campo_rama']}` y ahí hay **0**, "
                   f"pero el cronograma tiene Σ `{c['otro_campo']}` = "
                   f"**{c['suma_otro']:,.2f}**: está cargado con el campo de la OTRA "
                   "rama. El motor no ve ninguna amortización futura.",
                   causa="campo_de_amortizacion")
    if not residual:
        return _ob("cuadro", REVISAR,
                   f"no queda ninguna amortización futura en el cronograma "
                   f"({n_fut} cupón/es por delante). O el bono ya amortizó todo, o "
                   "el cuadro está incompleto.",
                   causa="sin_residual")
    if not (_RESIDUAL_MIN <= residual <= _RESIDUAL_MAX):
        grande = residual > _RESIDUAL_MAX
        return _ob("cuadro", REVISAR,
                   f"Σ de las amortizaciones futuras = **{residual:,.2f}** en "
                   f"{n_fut} cupón/es. Un cuadro sano ronda **100**, porque el bono "
                   "cotiza por 100 de VN"
                   + (f" → este está **{residual / 100:,.0f}× más grande**: son los "
                      "NOMINALES DE LA EMISIÓN, no base 100."
                      if grande else
                      " → este está muy por debajo: el cuadro quedó en una escala "
                      "más chica que el precio."),
                   causa="escala_del_cuadro")
    # Amortizado pero sano: no es una falla del dato, pero cambia cómo se lee todo.
    if c["rama"] == "cer" and residual < 99 and c["vn"] > residual * 1.05:
        return _ob("cuadro", REVISAR,
                   f"el bono **ya amortizó**: le queda un residual vivo de "
                   f"**{residual:,.2f}** sobre un `valor_nominal` de {c['vn']:,.2f}. "
                   f"El motor arma el valor técnico con el segundo, así que la "
                   f"paridad le sale **×{c['vn'] / residual:,.1f} más chica** de lo "
                   "real. **El dato del bono está bien**: lo que está mal es la "
                   "cuenta del motor.",
                   causa="paridad_del_motor")
    return _ob("cuadro", OK,
               f"Σ de las amortizaciones futuras = **{residual:,.2f}** en {n_fut} "
               "cupón/es → base 100, como corresponde.")


def _lente_precio(c: dict) -> dict:
    px, fuente = c["precio"], (c["est"].get("precio_fuente") or "")
    if not px:
        return _ob("precio", REVISAR,
                   "ninguna fuente local tiene un precio **mayor que 0** (ni el "
                   "snapshot live, ni el cierre persistido, ni el que quedó "
                   "congelado en el hallazgo). Sin precio no hay paridad ni TEA que "
                   "arreglar: no es un dato mal cargado, es un papel que no operó.",
                   causa="sin_precio", hechos={"precio": None})
    # La escala se lee del número, igual que la lee el motor (`precio >= 1000`).
    escala = "PESOS" if px >= 1000 else "dólares o porcentual (base 100)"
    return _ob("precio", OK,
               f"**{px:,.4f}** ({fuente or 'snapshot'}) → por su magnitud está en "
               f"escala **{escala}**. Es el mismo criterio que usa el motor para "
               "decidir si un dólar-linked hay que dividirlo por el A3500.",
               hechos={"precio": px})


def _lente_paridad(c: dict) -> dict:
    """La división, hecha a la vista. Y el cotejo contra lo guardado, que es lo que
    distingue un problema del bono de un número viejo pegado en el snapshot."""
    from api.services.av_agent import PARIDAD_MAX, PARIDAD_MIN

    par, px, residual = c["paridad"], c["precio"], c["residual"]
    if par is None:
        return _ob("paridad", NO_SE,
                   "el motor no devolvió paridad con estos datos"
                   + (" (es lo esperable: salió antes por una de sus puertas de "
                      "emergencia)." if not px or not residual else "."))
    en_rango = PARIDAD_MIN <= par <= PARIDAD_MAX
    cuenta = (f"paridad = precio / residual = {px:,.4f} / {residual:,.4f} = "
              f"**{par:,.4f}%**" if px and residual else f"paridad = **{par:,.4f}%**")
    return _ob("paridad", OK if en_rango else REVISAR,
               hechos={"precio": px, "paridad": par},
               detalle=cuenta + (". Dentro de [40, 160]: por acá no es." if en_rango else
                         f". **Fuera de [{PARIDAD_MIN:.0f}, {PARIDAD_MAX:.0f}]** → "
                         "uno de los dos lados de esa división está en la unidad "
                         "equivocada, y cuál se sabe mirando el residual."))


def _lente_tasa(c: dict) -> dict:
    tea, doc = c["tea"], c["doc"]
    if tea is not None:
        return _ob("tasa", OK, f"el motor calculó TEA **{tea:.2%}** con este cuadro "
                               "y este precio.")
    if tasa_externa_de(doc.get("ajuste"))[0] == "1816":
        return _ob("tasa", INFO, "sin TEA, y **así tiene que ser**: a este ajuste no "
                                 "le calculamos la tasa nosotros.")
    if not c["precio"]:
        return _ob("tasa", INFO, "sin TEA porque no hay precio — no es el cuadro.",
                   hechos={"precio": None})
    return _ob("tasa", REVISAR,
               "**el XIRR no converge**: hay precio y hay cronograma, pero el motor "
               "no encuentra una tasa. Eso pasa cuando el precio y los flujos están "
               "en escalas distintas — o sea que la causa vive en una de las lentes "
               "de arriba, no acá.")


def _lente_espejo(c: dict) -> dict:
    return _ob("espejo", INFO,
               "el espejo en `portafolio.assets` decide si el bono entra al AuM y a "
               "Portfolios; lo controla la regla `sin_espejo_en_assets` del "
               "detector, que mira la tenencia.")


_FN_LENTE = {"ficha": _lente_ficha, "moneda": _lente_moneda, "insumos": _lente_insumos,
             "cuadro": _lente_cuadro, "precio": _lente_precio,
             "paridad": _lente_paridad, "tasa": _lente_tasa, "espejo": _lente_espejo}


def analizar(doc: dict, rama: str, est: dict) -> dict:
    """**EL ANÁLISIS COMPLETO**: las ocho lentes, cada una con lo que ve.

    Devuelve `{observaciones, causa, detalle, parche}`. La causa la fija la lente
    que falla **más aguas arriba** —el orden de `LENTES` es el contrato— pero las
    demás igual hablan: son el razonamiento que sostiene la conclusión, y son lo
    que permite discutirla en vez de creerle.
    """
    c = _ctx(doc, rama, est)
    obs = []
    for clave, titulo in LENTES:
        o = _FN_LENTE[clave](c)
        o["titulo"] = titulo
        obs.append(o)
    culpable = next((o for o in obs if o.get("causa")), None)
    if culpable is None:
        return {"observaciones": obs, "causa": "sano", "parche": {},
                "detalle": ("las ocho lentes dan bien: ejes, moneda, cuadro en base "
                            "100, precio y paridad en rango.")}
    return {"observaciones": obs, "causa": culpable["causa"],
            "detalle": culpable["detalle"], "parche": culpable["parche"]}


def diagnosticar_local(doc: dict, rama: str, est: dict) -> dict:
    """**LA CAUSA**, derivada del análisis. Cero red, cero créditos.

    Es una vista angosta de `analizar()` y no una segunda implementación: dos
    lugares decidiendo la misma causa terminan contradiciéndose, que es el bug que
    este agente ya se comió tres veces.
    """
    return analizar(doc, rama, est)


def _suma_amortizacion(doc: dict, campo: str) -> float:
    """Σ del campo pedido sobre los cupones FUTUROS. Existe para que un 0 no sea
    ambiguo: `_residual_vivo` elige el campo según la rama, así que sin mirar el
    otro no se distingue «ya no amortiza» de «está cargado en el campo de al
    lado» — y las dos cosas piden arreglos opuestos."""
    from engines.curvas import fecha_flujo

    hoy = date.today()
    return round(sum(float(f.get(campo) or 0.0) for f in (doc.get("flujos") or [])
                     if fecha_flujo(f) and fecha_flujo(f) > hoy), 6)


def _diagnostico_local(doc: dict, rama: str, est: dict) -> list[dict]:
    """Los pasos que NO dependen de 1816. Corren siempre y van primeros."""
    from api.services.av_agent import PARIDAD_MAX, PARIDAD_MIN

    ps: list[dict] = []
    # ⚠️ **QUIÉN CALCULA LA TASA LO DICE EL AJUSTE, NO LA RAMA** (fix 2026-08-17).
    # Acá decía `rama not in (*RAMAS_AUTOMATICAS, "on")`, pero un corporativo TAMAR
    # devuelve rama **`on`** (`rama_calculo` pregunta `corporativo` primero), así
    # que el aviso «esto no lo calculamos nosotros» no se disparaba justo en el caso
    # que lo motivó (DHSGO, el bono de la captura del user).
    #
    # Y ahora es un paso INFORMATIVO en vez de un `return`: que la tasa venga de
    # afuera no dice nada sobre la PARIDAD, que sale del precio y del cuadro. Cortar
    # acá dejaba al bono sin diagnóstico por un motivo que no aplicaba a su hallazgo.
    fuente_tasa, job_tasa = tasa_externa_de(doc.get("ajuste"))
    if fuente_tasa == "1816" or rama not in (*RAMAS_AUTOMATICAS, "on"):
        ps.append(_paso("rama_local", "¿A este bono le calculamos la tasa?", INFO,
                        f"ajuste «{doc.get('ajuste')}» (rama «{rama}»): **la TEA no "
                        "la calculamos nosotros**"
                        + (f" — la trae {job_tasa}." if job_tasa else
                           ". Cae en el `else` del motor, que solo devuelve "
                           "duration.")
                        + " Un hallazgo de TASA sobre este bono no se arregla "
                          "tocando el cuadro; uno de PARIDAD sí puede.",
                        tabla="engines/curvas.py::rama_calculo"))
        if rama not in (*RAMAS_AUTOMATICAS, "on"):
            return ps

    residual, n_fut = _residual_vivo(doc, rama)
    paridad = est.get("paridad")
    precio = est.get("precio")
    escala_mal = bool(residual) and not (_RESIDUAL_MIN <= residual <= _RESIDUAL_MAX)

    ps.append(_paso("escala_local", "La escala del cuadro que YA está cargado",
                    REVISAR if escala_mal else OK,
                    (f"Σ de las amortizaciones futuras = **{residual:,.2f}** en "
                     f"{n_fut} cupón/es. Un cuadro sano ronda **100** (el bono "
                     f"cotiza por 100 de VN), así que este está **{residual / 100:,.0f}× "
                     "más grande**: está en NOMINALES DE LA EMISIÓN y no en base "
                     "100. Es la misma falla que midió D30O6 (Σ=148.869)."
                     if escala_mal and residual > _RESIDUAL_MAX else
                     f"Σ de las amortizaciones futuras = **{residual:,.4f}** en "
                     f"{n_fut} cupón/es, muy por debajo de 100: el cuadro está en "
                     "una escala más chica que el precio."
                     if escala_mal else
                     f"Σ de las amortizaciones futuras = **{residual:,.2f}** en "
                     f"{n_fut} cupón/es → está en base 100, como corresponde."),
                    tabla="mercado.curvas (el cuadro guardado, sin consultar a 1816)"))

    # LA DIVISIÓN. Es toda la aritmética de la paridad, hecha a la vista.
    if isinstance(paridad, int | float) and isinstance(precio, int | float):
        culpa = ("**EL CUADRO**: el residual está fuera de escala, el precio no."
                 if escala_mal else
                 "**EL PRECIO**: el cuadro está en base 100, así que el que no "
                 "está en la unidad del cuadro es el precio — típicamente un bono "
                 "en dólares cuyo precio viene en pesos, o al revés.")
        ok_par = PARIDAD_MIN <= float(paridad) <= PARIDAD_MAX
        ps.append(_paso("division_local", "Dónde está el problema, por división",
                        OK if ok_par else REVISAR,
                        f"paridad = precio / residual = {float(precio):,.4f} / "
                        f"{residual:,.4f} = **{float(paridad):,.4f}%**"
                        + (". En rango — por acá no es." if ok_par else
                           f". Fuera de [{PARIDAD_MIN:.0f}, {PARIDAD_MAX:.0f}] → " + culpa),
                        tabla="engines/curvas.py (la misma cuenta del motor)"))

    # ── EL ANÁLISIS: las OCHO lentes, cada una con lo que ve.
    #
    # No solo la que encontró el problema. El user lo pidió así y tiene razón: el
    # que lee la pantalla es el que decide si escribir o no, y para eso necesita el
    # razonamiento, no el veredicto. Una lente en verde también informa — es la que
    # descarta un camino.
    dx = analizar(doc, rama, est)
    for o in dx["observaciones"]:
        ps.append(_paso(f"lente_{o['clave']}", o["titulo"], o["estado"], o["detalle"],
                        tabla="mercado.curvas + mercado.market_snapshot "
                              "(sin una sola llamada a 1816)"))

    # ── EL AGENTE SE AUDITA A SÍ MISMO. Dos lentes que afirman cosas
    # incompatibles sobre el MISMO hecho es un bug DEL AGENTE, no del bono — y es
    # peor que un dato malo, porque destruye la confianza en todo lo demás que
    # dice, incluido lo que está bien. Pasó con OLC3O (la lente 7 decía «no hay
    # precio» y tres pasos abajo la misma pantalla mostraba 137.280) y por eso
    # ahora se busca solo, en cada diagnóstico, para siempre.
    from api.services import av_agent_memoria as mem

    incoherencias = mem.contradicciones(dx["observaciones"], est)
    if incoherencias:
        ps.append(_paso("incoherencia", "⚠ EL AGENTE SE CONTRADICE", REVISAR,
                        "\n\n".join(f"**{i['id']}** — {i['detalle']}"
                                     for i in incoherencias)
                        + "\n\nEsto **no habla del bono**: habla del agente. "
                          "Mientras esté, el diagnóstico de abajo no es confiable "
                          "y no debería aplicarse sin mirarlo.",
                        tabla="av_agent_memoria.contradicciones"))

    # ── LO QUE YA APRENDIMOS sobre esta causa. Va ANTES de la conclusión a
    # propósito: una lección sirve cuando alguien está por decidir, no cuando se le
    # ocurra ir a buscarla a un doc.
    for lec in mem.lecciones_de(dx["causa"], "bono"):
        ps.append(_paso(f"leccion_{lec['slug']}", f"📚 YA APRENDIMOS: {lec['titulo']}",
                        INFO,
                        f"**Se veía así:** {lec.get('sintoma') or '—'}\n\n"
                        f"**Lo que pasaba:** {lec.get('causa_raiz') or '—'}\n\n"
                        f"**Qué se cambió:** {lec['cambio']}",
                        tabla=(f"commit {lec['commit'][:9]}" if lec.get("commit")
                               else "mercado.av_agent_lecciones")
                              + f" · lo detectó: {lec.get('detectado_por')}"))

    # ── CASOS PARECIDOS: exemplar learning sin modelos ni vectores. Contesta la
    # pregunta que una persona haría primero — «¿esto ya lo vimos?» — y un caso
    # anterior que salió bien es la mejor evidencia de que la propuesta sirve.
    tk_doc = (doc.get("ticker_corto") or "").strip().upper()
    parecidos = mem.casos_parecidos(dx["causa"], excluir=tk_doc)
    if parecidos:
        ps.append(_paso("parecidos", "🔁 CASOS PARECIDOS", INFO,
                        " · ".join(
                            f"**{c['caso']}**" + (f" ({c['aciertos']}/{c['votos']} ✔)"
                                                 if c["votos"] else "")
                            for c in parecidos)
                        + f"\n\nEl agente ya dijo «{dx['causa']}» en "
                          f"{len(parecidos)} caso/s más. Los votos son del eval "
                          "set: dicen si esa conclusión resultó correcta.",
                        tabla="mercado.av_agent_trazas + av_agent_evals"))

    meta = CAUSAS.get(dx["causa"]) or {}
    ps.append(_paso("causa_local", "⇒ LA CONCLUSIÓN", OK if dx["causa"] == "sano"
                    else REVISAR,
                    f"**{meta.get('titulo') or dx['causa']}** — {dx['detalle']}"
                    + f"\n\nArreglo: {meta.get('arreglo', '—')}."
                    + (" **Lo hace el agente**, y se verifica antes de escribir."
                       if meta.get("agente") and dx.get("parche") else
                       " El agente lo VE pero no lo toca."),
                    tabla="la lente que falla más aguas arriba"))

    # LA TRAZA: lo que el agente dijo, guardado entero. Best-effort — si la
    # escritura falla el diagnóstico sigue: un registro que puede tumbar la
    # funcionalidad que registra se termina apagando, y ahí se pierde todo.
    mem.registrar_traza(caso=tk_doc or "?", dominio="bono", causa=dx["causa"],
                        veredicto=dx["causa"], observaciones=dx["observaciones"],
                        contexto={"precio": est.get("precio"),
                                  "paridad": est.get("paridad"),
                                  "tea": est.get("tea"), "rama": rama},
                        incoherencias=incoherencias)
    for i, p in enumerate(ps, 1):
        p["n"] = i
    return ps


def _aplicar_parche_local(sim: dict, *, actor: str = "") -> dict:
    """Escribe el parche del arreglo local. **En la columna Y en el blob.**

    `moneda_flujo` existe como COLUMNA en `mercado.curvas` y además vive adentro
    del jsonb `data`, que es de donde lo lee `core/curvas_sql` (no está en
    `_COLS_FUERA_DEL_BLOB`). Escribir uno solo dejaría al otro contradiciéndolo —
    que es exactamente la enfermedad que este arreglo viene a curar. Mismo
    criterio que `jobs/ficha_1816` con el emisor.
    """
    import json

    from api.services import av_agent_acciones as acc
    from core.postgres import get_pool

    tk, parche = sim["ticker"], sim["parche"]
    antes = {k: sim.get("_antes_campos", {}).get(k) for k in parche}
    try:
        sets, vals = ["data = COALESCE(data, '{}'::jsonb) || %s::jsonb"], [
            json.dumps(parche)]
        for col in ("moneda_flujo",):        # las que además son columna
            if col in parche:
                sets.append(f"{col} = %s")
                vals.append(parche[col])
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE mercado.curvas SET {', '.join(sets)} "
                        "WHERE ticker = %s", (*vals, tk))
            filas = cur.rowcount or 0
    except Exception as e:
        acc.registrar(accion="arreglar_bono", objetivo=tk, ok=False,
                      error=str(e)[:300], por=actor, antes=antes)
        return {**sim, "aplicado": False, "error": f"no se pudo escribir: {e}"}
    if not filas:
        return {**sim, "aplicado": False, "error": "el UPDATE no tocó ninguna fila"}

    acc.registrar(accion="arreglar_bono", objetivo=tk, por=actor, antes=antes,
                  detalle={"causa": sim.get("causa"), "campos": list(parche),
                           "parche": parche,
                           "paridad_antes": (sim.get("antes") or {}).get("paridad"),
                           "paridad_despues": sim.get("paridad")})
    return {**sim, "aplicado": True,
            "aviso": "los motores leen mercado.curvas al arrancar: reiniciar "
                     "motor_curvas para que la métrica nueva llegue a la vista"}


def _arreglo_local(*, doc: dict, tk: str, simbolo: str, rama: str, dx: dict,
                   hoy_est: dict, px_local: float | None, px_fuente: str,
                   curva_1816: str) -> dict:
    """El arreglo que NO necesita la red: se propone, **se verifica** y recién ahí
    queda habilitado.

    ⚠️ **LA VERIFICACIÓN ES LO QUE HACE QUE ESTO SEA SEGURO.** Sin cotejo contra
    1816 hacía falta otra prueba de que el cambio mejora algo, y no puede ser «yo
    creo»: se corre el MOTOR con el bono parchado y se exige que **la métrica que
    disparó el hallazgo vuelva al rango** — y que antes estuviera afuera. Es un
    control tan duro como el de 1816, cuesta cero créditos y se puede correr un
    domingo. Si la paridad no vuelve, el diagnóstico estaba mal y no se escribe.
    """
    from api.services.av_agent import PARIDAD_MAX, PARIDAD_MIN

    meta = CAUSAS.get(dx["causa"]) or {}
    doc_prop = {**doc, **dx["parche"]}
    prop_est = _simular_tasa(doc_prop, simbolo, px_local, ticker=tk,
                             moneda_eje=(doc_prop.get("moneda_eje") or "").strip(),
                             sin_red=True)

    par_antes, par_desp = hoy_est.get("paridad"), prop_est.get("paridad")
    en_rango = (isinstance(par_desp, int | float)
                and PARIDAD_MIN <= float(par_desp) <= PARIDAD_MAX)
    estaba_mal = (not isinstance(par_antes, int | float)
                  or not (PARIDAD_MIN <= float(par_antes) <= PARIDAD_MAX))

    out = {
        "ok": True, "ticker": tk, "modo": "arreglo", "rama": rama,
        "curva": doc.get("curva"), "curva_1816": curva_1816, "simbolo": simbolo,
        "sin_red": True, "causa": dx["causa"], "parche": dx["parche"],
        # El ANTES de los campos que se van a pisar, congelado para el LIBRO DE
        # ACCIONES. Sin esto, «revertir» es una promesa y no un dato.
        "_antes_campos": {k: doc.get(k) for k in dx["parche"]},
        "cupones": 0, "escala": None, "cuadro": None, "error_cuadro": "",
        "ejes_hoy": {"emisor_tipo": doc.get("emisor_tipo"),
                     "moneda_eje": doc.get("moneda_eje"),
                     "ajuste": doc.get("ajuste"), "ley": doc.get("ley")},
        "ejes_propuestos": None, "nota_ejes": "",
        "antes": {"tea": hoy_est.get("tea"), "paridad": par_antes,
                  "duration": hoy_est.get("duration")},
    }
    out.update(prop_est)
    out["precio_fuente"] = px_fuente or out.get("precio_fuente")

    ps: list[dict] = []
    ps.append(_paso("hoy", "Cómo está el bono AHORA", INFO,
                    f"TEA {_pct_o(hoy_est.get('tea'), pct=True)} · paridad "
                    f"{_pct_o(par_antes)} · duration "
                    f"{_pct_o(hoy_est.get('duration'), dec=4)} · precio "
                    f"{_pct_o(px_local, dec=4)}"
                    + (f" ({px_fuente})" if px_fuente else " — sin precio"),
                    tabla="mercado.curvas + mercado.market_snapshot"))
    ps.extend(_diagnostico_local(doc, rama, hoy_est))

    campos = " · ".join(f"`{k}` = {v}" for k, v in dx["parche"].items())
    ps.append(_paso("escritura", "Qué se va a PISAR", INFO,
                    f"solo {campos} en {tk}. El cuadro, el emisor, la curva y el "
                    "símbolo quedan intactos.",
                    tabla="mercado.curvas"))

    # EL JUEZ, y es LOCAL.
    ps.append(_paso("verificacion", "La métrica vuelve al rango", OK if
                    (en_rango and estaba_mal) else BLOQUEA,
                    f"paridad **{_pct_o(par_antes)} → {_pct_o(par_desp)}** "
                    f"(rango sano [{PARIDAD_MIN:.0f}, {PARIDAD_MAX:.0f}])"
                    + (". El arreglo la devuelve adentro: el diagnóstico se sostiene."
                       if en_rango and estaba_mal else
                       ". **Lo de hoy YA estaba en rango**: no hay nada que arreglar "
                       "y pisarlo sería empeorarlo." if not estaba_mal else
                       ". **NO vuelve al rango** → el diagnóstico no se sostiene y "
                       "no se escribe nada. Hay otra causa además de esta."),
                    tabla="engines/curvas.py (el MISMO motor que valúa en producción)"))

    ps.append(_paso("sin_1816", "¿Hace falta preguntarle a 1816?", INFO,
                    f"**No.** La causa es «{meta.get('titulo', dx['causa'])}» y se "
                    "resuelve con datos que ya están en la base, así que esta "
                    "pantalla no hizo una sola llamada: anda con la API caída, un "
                    "domingo y en pleno rate limit.",
                    tabla="—"))
    for i, p in enumerate(ps, 1):
        p["n"] = i
    out["chequeos"] = ps
    out["veredicto"] = _veredicto(ps)
    return out


@_interactivo
def simular_arreglo(ticker: str, *, cer_emision: float | None = None) -> dict:
    """Qué insumo está mal en un bono con TASA SOSPECHOSA, y qué pasaría al
    arreglarlo. **No escribe.**

    Corre el motor DOS veces —con el bono como está HOY y con la propuesta— y
    coteja las dos contra 1816. Ver el ANTES es lo que convierte esto en un
    diagnóstico: sin esa columna sería otra simulación más, sin forma de saber si
    el arreglo mejora algo.
    """
    tk = mercado_1816.normalizar_ticker(ticker)
    doc = _doc_de_curvas(tk)
    if not doc:
        return {"ok": False, "ticker": tk,
                "error": f"{tk} no está en mercado.curvas"}

    from engines.curvas import rama_calculo

    simbolo = (doc.get("ticker") or "").strip()
    ficha = _ficha_1816(tk)

    # ── (1) LOS EJES. Solo se proponen si FALTAN: los que cargó la mesa son la
    # verdad y no se discuten (misma regla que en completar-cronograma).
    ejes_actuales = curvas_ejes.ejes_de_doc(doc)
    curva_1816 = (ficha.get("curva_1816") or "").strip()
    ejes_prop, nota_ejes = None, ""
    if ejes_actuales is None:
        ejes_prop = curvas_ejes.desde_1816(curva_1816) if curva_1816 else None
        nota_ejes = (f"1816 lo clasifica en «{curva_1816}»" if ejes_prop
                     else (f"1816 dice «{curva_1816}» y esa curva no se puede "
                           "traducir a ejes todavía" if curva_1816 else
                           "1816 no tiene a este bono en su catálogo"))

    doc_prop = dict(doc)
    if ejes_prop is not None:
        doc_prop.update({"emisor_tipo": ejes_prop.emisor_tipo,
                         "moneda_eje": ejes_prop.moneda, "ajuste": ejes_prop.ajuste,
                         "ajuste_alt": ejes_prop.ajuste_alt, "ley": ejes_prop.ley})

    # ── ¿HACE FALTA LA RED? SE PREGUNTA ANTES DE USARLA (2026-08-17) ────────
    #
    # **El cambio de diseño** (user): *«hay casos donde este agente podría
    # debuguear internamente, ya que hay precio y ya está el flujo cargado»*.
    # Medido sobre los 38 hallazgos reales: **38 de 38 se resuelven con datos que
    # ya están en la base**. La cadena salía igual a pedirle a 1816 el cronograma
    # y el precio, y esas dos puertas la bloqueaban ENTERA cuando 1816 no
    # contestaba — o sea que el agente se quedaba mudo justo cuando más falta
    # hacía, sin poder decir ni lo que sale de una división.
    #
    # Ahora el diagnóstico local corre PRIMERO y, si la causa tiene un arreglo que
    # no necesita la red, **no se hace una sola llamada**: la puerta anda un
    # domingo, con la API caída y en pleno rate limit. Y de paso deja de aportar
    # al 429 que ella misma provocaba.
    rama_hoy = rama_calculo(doc)
    px_local, px_fuente = _precio_local(simbolo)
    hoy_local = _simular_tasa(dict(doc), simbolo, px_local, ticker=tk,
                              moneda_eje=(doc.get("moneda_eje") or "").strip(),
                              sin_red=True)
    if px_fuente:
        hoy_local["precio_fuente"] = px_fuente
    dx = diagnosticar_local(doc, rama_hoy, hoy_local)
    if dx.get("parche") and (CAUSAS.get(dx["causa"]) or {}).get("agente"):
        return _arreglo_local(doc=doc, tk=tk, simbolo=simbolo, rama=rama_hoy,
                              dx=dx, hoy_est=hoy_local, px_local=px_local,
                              px_fuente=px_fuente, curva_1816=curva_1816)

    # ⚠️ **UNA SOLA CONSULTA A 1816 PARA LOS DOS ESTADOS** (2026-08-17).
    #
    # Este diagnóstico corre el motor DOS veces —el bono de HOY y la propuesta— y
    # cada corrida salía a pedirle a 1816 **su propia** referencia de precio. Es la
    # MISMA pregunta sobre el MISMO bono: el precio de referencia no cambia entre
    # «cómo está» y «cómo quedaría». Con el retroceso de ruedas (hasta 5 intentos)
    # más el cotejo al mismo precio, un solo click llegaba a ~13 requests, **la
    # mitad duplicados** — y probar seis bonos seguidos alcanzó para que 1816 nos
    # aplicara el rate limit **a todo, endpoint de login incluido**: de ahí el
    # «auth HTTP 429» que rompió la pantalla y los cuatro jobs a la vez.
    #
    # Se pide UNA vez, con los ejes de la PROPUESTA (los correctos), y se comparte.
    # Además de costar la mitad, **es lo que corresponde**: los dos estados quedan
    # juzgados con la MISMA vara, que es el principio que ya rige el cotejo.
    #
    # Por eso este bloque va DESPUÉS de resolver los ejes: la moneda con la que se
    # le pregunta a 1816 sale de ellos, y con los ejes vacíos —que es justo el caso
    # `sin_ejes`— la pregunta saldría mal formulada.
    ref_unica = _referencia_1816(
        tk, simbolo=simbolo,
        moneda_eje=(doc_prop.get("moneda_eje") or doc.get("moneda_eje") or "").strip())

    # EL ANTES: el bono tal como lo ve el motor hoy, sin tocar nada.
    hoy_est = _simular_tasa(dict(doc), simbolo, None, ticker=tk,
                            moneda_eje=(doc.get("moneda_eje") or "").strip(),
                            ref_1816=ref_unica)

    # ── (2) EL CUADRO de 1816, con la rama YA corregida por los ejes propuestos.
    rama = rama_calculo(doc_prop)
    cer_manual = cer_emision if (cer_emision or 0) > 0 else None
    cer_usado = doc_prop.get("cer_emision") or cer_manual
    ratio, ratio_nota = (None, "")
    if rama == "cer":
        ratio, ratio_nota = ratio_cer_hoy(cer_usado)

    conv, err_cuadro, cupones = None, "", []
    try:
        cupones = (mercado_1816.cashflow(tk) or {}).get("cashflow") or []
        if cupones:
            conv = convertir_flujos(cupones, rama, ratio_cer=ratio)
        else:
            err_cuadro = "1816 devolvió el cuadro VACÍO"
    except Exception as e:
        err_cuadro = f"1816 no dio el cuadro: {e}"

    if conv:
        doc_prop["flujos"] = conv["flujos"]
        if conv["flujo_vencimiento"] is not None:
            doc_prop["flujo_vencimiento"] = conv["flujo_vencimiento"]
    if cer_manual and not doc_prop.get("cer_emision"):
        doc_prop["cer_emision"] = cer_manual

    prop_est = _simular_tasa(doc_prop, simbolo, None, ticker=tk,
                             moneda_eje=(doc_prop.get("moneda_eje") or "").strip(),
                             ref_1816=ref_unica)

    out = {
        "ok": True, "ticker": tk, "modo": "arreglo", "rama": rama,
        "curva": doc.get("curva"), "curva_1816": curva_1816, "simbolo": simbolo,
        "cupones": conv["n"] if conv else 0,
        "escala": conv["escala"] if conv else None,
        "suma_pct": conv.get("suma_pct") if conv else None,
        "divisor_es": conv.get("divisor_es") if conv else None,
        "vencimiento": conv["flujos"][-1]["fecha"] if conv and conv["flujos"] else "",
        "flujo_vencimiento": conv["flujo_vencimiento"] if conv else None,
        "cer_emision": cer_usado, "cer_manual": bool(cer_manual
                                                     and not doc.get("cer_emision")),
        "ratio_cer": ratio, "ratio_nota": ratio_nota,
        "cuadro": conv, "error_cuadro": err_cuadro,
        "ejes_hoy": None if ejes_actuales is None else {
            "emisor_tipo": doc.get("emisor_tipo"), "moneda_eje": doc.get("moneda_eje"),
            "ajuste": doc.get("ajuste"), "ley": doc.get("ley")},
        "ejes_propuestos": None if ejes_prop is None else {
            "emisor_tipo": ejes_prop.emisor_tipo, "moneda_eje": ejes_prop.moneda,
            "ajuste": ejes_prop.ajuste, "ajuste_alt": ejes_prop.ajuste_alt,
            "ley": ejes_prop.ley},
        "nota_ejes": nota_ejes,
        # El ANTES, explícito y con su propio nombre: es la mitad del diagnóstico.
        "antes": {"tea": hoy_est.get("tea"), "paridad": hoy_est.get("paridad"),
                  "duration": hoy_est.get("duration")},
    }
    out.update(prop_est)          # tea/paridad/duration/precio del PROPUESTO
    out["chequeos"] = _chequeos_arreglo(ticker=tk, doc=doc, out=out, rama=rama,
                                        conv=conv, cupones_1816=cupones)
    out["veredicto"] = _veredicto(out["chequeos"])
    return out


def _cotejo_de(tea, paridad, duration, ref: dict, precio, cota_ic) -> dict:
    """El cotejo contra 1816 de UN estado (el de hoy o el propuesto). Es el MISMO
    `_cotejo_tea` — así el ANTES y el DESPUÉS no se pueden juzgar con dos varas."""
    return _cotejo_tea(tea, ref, paridad=paridad, duration=duration,
                       cota_ic=cota_ic, precio=precio)


def _chequeos_arreglo(*, ticker: str, doc: dict, out: dict, rama: str,
                      conv: dict | None, cupones_1816) -> list[dict]:
    """La cadena del ARREGLO. Su forma es distinta de las otras dos a propósito:
    acá lo que se muestra es una COMPARACIÓN (hoy contra propuesta contra 1816),
    porque la pregunta no es «¿esto está bien?» sino «¿esto está MEJOR?»."""
    ps: list[dict] = []
    antes = out.get("antes") or {}
    ref = out.get("referencia_1816") or {}

    ps.append(_paso("hoy", "Cómo está el bono AHORA", INFO,
                    f"TEA {_pct_o(antes.get('tea'), pct=True)} · paridad "
                    f"{_pct_o(antes.get('paridad'))} · duration "
                    f"{_pct_o(antes.get('duration'), dec=4)} · ejes "
                    + (" · ".join(str(v or "—") for v in
                                  (out.get("ejes_hoy") or {}).values())
                       if out.get("ejes_hoy") else "**NINGUNO** — este bono no cae "
                       "en ninguna curva y desaparece de la vista sin dar error"),
                    tabla="mercado.curvas + mercado.market_snapshot"))

    # ── EL DIAGNÓSTICO LOCAL, ANTES QUE NADA. No depende de 1816, así que
    # aparece aunque estén rechazándonos por rate limit — que es exactamente
    # cuando más falta hace. En muchos casos ya contesta la pregunta entera.
    # ⚠️ **LAS LENTES TIENEN QUE VER EL MISMO PRECIO QUE LA CADENA** (fix
    # 2026-08-17). Acá se pasaba `antes`, que solo lleva tea/paridad/duration — sin
    # `precio`. Resultado: en OLC3O la lente 7 decía «ninguna fuente local tiene un
    # precio mayor que 0» y tres pasos más abajo la misma pantalla mostraba
    # «precio 137.280 (snapshot)». **Las dos afirmaciones eran del mismo request.**
    #
    # No era un bono mal cargado: era el agente contradiciéndose, que es peor —
    # destruye la confianza en TODO lo demás que dice, incluido lo que está bien.
    # Por eso además de pasar el precio ahora hay un chequeo que busca estas
    # contradicciones solo (ver `av_agent_memoria.contradicciones`).
    est_lentes = {**antes, "precio": out.get("precio"),
                  "precio_fuente": out.get("precio_fuente")}
    ps.extend(_diagnostico_local(doc, rama, est_lentes))

    # EJES. Solo aparece si faltan: los que cargó la mesa no se discuten.
    if out.get("ejes_hoy") is None:
        prop = out.get("ejes_propuestos")
        ps.append(_paso("ejes", "Los ejes que faltan salen de 1816",
                        OK if prop else BLOQUEA,
                        (f"{out.get('nota_ejes')} → {prop['emisor_tipo']} · "
                         f"{prop['moneda_eje']} · {prop['ajuste']}"
                         + (f" · {prop['ley']}" if prop.get("ley") else "")
                         if prop else
                         f"{out.get('nota_ejes')}. Sin ejes no hay rama de "
                         "cálculo, así que no hay nada que simular: esto lo "
                         "resuelve la mesa o una entrada nueva en EJES_1816."),
                        tabla="research.mkt_1816_instrumentos → core/curvas_ejes"))

    ps.append(_paso("cuadro", "El cronograma de 1816",
                    OK if conv else BLOQUEA,
                    (f"{conv['n']} cupón/es · vence {out.get('vencimiento')} · "
                     f"escala {conv['escala']}" if conv else
                     out.get("error_cuadro") or "sin cuadro"),
                    tabla="1816 /cashflow (fechaPagoEfectiva)"))

    if rama == "cer":
        cer_e = out.get("cer_emision")
        ps.append(_paso("cer_emision", "CER de emisión resuelto",
                        OK if cer_e else BLOQUEA,
                        f"{cer_e}" + (" (cargado a mano)" if out.get("cer_manual")
                                      else " (del master)") if cer_e else
                        "sin el CER de emisión el cuadro no se puede llevar a la "
                        "escala del VN — escribilo acá y se re-simula con él.",
                        tabla="mercado.curvas · macro.series_macro (CER)",
                        aviso="" if cer_e else f"Cargar el CER de emisión de {ticker}",
                        pide=None if cer_e else {
                            "campo": "cer_emision", "label": "CER de emisión",
                            "tipo": "numero",
                            "ayuda": "el índice CER del día de emisión (prospecto "
                                     "o BCRA)."}))

    ps.append(_paso("precio", "Hay precio para comparar", OK if out.get("precio")
                    else BLOQUEA,
                    f"precio {out['precio']:,.4f} ({out.get('precio_fuente')})"
                    if out.get("precio") else
                    "sin precio no se puede cotejar contra 1816 — y sin cotejo "
                    "no se pisa nada.",
                    tabla="mercado.market_snapshot · 1816"))

    # ── EL JUEZ. Dos cotejos con la MISMA vara: el propuesto tiene que coincidir
    # y el actual NO. Las dos mitades hacen falta — ver el comentario del bloque.
    cota = cota_devengado(cupones_1816 or [])
    cot_prop = _cotejo_de(out.get("tea"), out.get("paridad"), out.get("duration"),
                          ref, out.get("precio"), cota)
    cot_prop["clave"], cot_prop["titulo"] = "cotejo_propuesto", \
        "La PROPUESTA coincide con 1816"
    ps.append(cot_prop)

    cot_hoy = _cotejo_de(antes.get("tea"), antes.get("paridad"),
                         antes.get("duration"), ref, out.get("precio"), cota)
    ya_estaba_bien = cot_hoy["estado"] == OK
    # ⚠️ **UN ERROR DE RED NO ES UNA CONCLUSIÓN SOBRE EL BONO** (2026-08-17).
    # Acá había `BLOQUEA if ya_estaba_bien else OK`, o sea que **cualquier cosa
    # que no fuera OK se leía como «no coincide»** — y con 1816 devolviendo 429 la
    # cadena afirmaba, en verde, *«confirmado que lo de hoy NO coincide: hay algo
    # real que arreglar»* sin haber podido preguntar nada.
    #
    # Es exactamente el pecado que ya corregimos en la paridad (E3.g) repetido en
    # otro paso: **tratar la ausencia de respuesta como una respuesta.** Son TRES
    # estados y no dos — coincide / no coincide / no se pudo saber — y el tercero
    # tiene que frenar, porque sin cotejo no se pisa nada.
    no_se_pudo = cot_hoy["estado"] == NO_SE
    ps.append(_paso("cotejo_hoy", "El bono de HOY, contra 1816",
                    NO_SE if no_se_pudo else (BLOQUEA if ya_estaba_bien else OK),
                    cot_hoy["detalle"]
                    + ("  ⚠️ **No se pudo consultar a 1816**, así que de este bono "
                       "no se sabe nada todavía — ni que está mal ni que está "
                       "bien. Reintentá en unos minutos: esto NO dice nada del "
                       "instrumento." if no_se_pudo else
                       "  ⚠️ **El bono de hoy YA coincide con 1816**: no hay nada "
                       "que arreglar y pisarlo sería empeorarlo. El hallazgo "
                       "quedó viejo o el umbral es angosto para este instrumento "
                       "— revisalo o ignoralo." if ya_estaba_bien else
                       "  → confirmado que lo de hoy NO coincide: hay algo real "
                       "que arreglar."),
                    tabla="1816 /indicadores"))

    campos = []
    if out.get("ejes_propuestos"):
        campos.append("los ejes (emisor_tipo · moneda_eje · ajuste · ley)")
    if conv:
        campos.append("flujos" + (" · flujo_vencimiento"
                                  if conv["flujo_vencimiento"] is not None else ""))
    if out.get("cer_manual"):
        campos.append("cer_emision (lo escribiste vos)")
    ps.append(_paso("escritura", "Qué se va a PISAR", INFO,
                    ("solo " + " · ".join(campos) + f" en {ticker}. El emisor, la "
                     "curva y el símbolo quedan intactos."
                     if campos else "nada: no hay propuesta que aplicar"),
                    tabla="mercado.curvas"))
    for i, p in enumerate(ps, 1):
        p["n"] = i
    return ps


def _pct_o(v, *, pct: bool = False, dec: int = 2) -> str:
    """Un número o un guion. Existe porque en esta cadena **el vacío es un dato**:
    «sin TEA» es justamente el síntoma que se está diagnosticando, y mostrar 0,00
    en su lugar lo escondería."""
    if not isinstance(v, int | float):
        return "—"
    return f"{float(v):.{dec}%}" if pct else f"{float(v):,.{dec}f}"


@_interactivo
def aplicar_arreglo(ticker: str, *, actor: str = "",
                    cer_emision: float | None = None) -> dict:
    """Simula y, si la cadena cierra, **pisa el insumo que estaba mal**.

    Es la única puerta del agente que SOBRESCRIBE un dato existente, así que el
    veredicto ya trae las dos condiciones que la habilitan (la propuesta coincide
    con 1816 **y** lo de hoy no). Acá no se re-decide nada: si `puede_aplicar` es
    falso, no se escribe — que el criterio viva en un solo lado es lo que evitó
    tres veces el bug del segundo gate.

    ⚠️ **Los EJES viven en COLUMNAS, no en el blob** (`_COLS_FUERA_DEL_BLOB` de
    `core/curvas_sql`). Escribirlos dentro del jsonb los dejaría invisibles para
    el motor y para la vista: el merge de lectura pone la columna ENCIMA del
    blob, así que un eje escrito solo en `data` lo pisa un `NULL` de la columna.
    """
    from api.services import av_agent_acciones as acc

    sim = simular_arreglo(ticker, cer_emision=cer_emision)
    if not sim.get("ok"):
        acc.registrar(accion="arreglar_bono", objetivo=ticker.upper(), ok=False,
                      error=sim.get("error", "")[:300], por=actor)
        return {**sim, "aplicado": False}
    ver = sim.get("veredicto") or {}
    if not ver.get("puede_aplicar", True):
        bloqueos = [c for c in sim["chequeos"] if c["estado"] == BLOQUEA]
        return {**sim, "aplicado": False,
                "error": "el pre-flight no pasa: "
                         + "; ".join(c["titulo"] for c in bloqueos)}

    # ── EL ARREGLO LOCAL: un parche chico, verificado, sin cuadro de por medio.
    # Va por su propia rama y no por la de abajo a propósito: acá NO se toca el
    # cronograma, así que reusar el camino del cuadro obligaría a razonar todo el
    # rato sobre un `conv` que no existe.
    if sim.get("sin_red") and sim.get("parche"):
        return _aplicar_parche_local(sim, actor=actor)

    conv, ejes = sim.get("cuadro"), sim.get("ejes_propuestos")
    parche: dict = {}
    if conv:
        parche["flujos"] = conv["flujos"]
        if conv["flujo_vencimiento"] is not None:
            parche["flujo_vencimiento"] = conv["flujo_vencimiento"]
        if sim.get("vencimiento"):
            parche["fecha_vencimiento"] = sim["vencimiento"]
    if sim.get("cer_manual") and sim.get("cer_emision"):
        parche["cer_emision"] = sim["cer_emision"]
    if not parche and not ejes:
        return {**sim, "aplicado": False, "error": "no hay nada que aplicar"}

    # El ANTES, congelado para el libro. Sin esto «revertir» es una promesa.
    antes = {"ejes": sim.get("ejes_hoy"), **(sim.get("antes") or {})}
    try:
        import json

        from core.postgres import get_pool
        sets, vals = [], []
        if ejes:
            for col in ("emisor_tipo", "moneda_eje", "ajuste", "ajuste_alt", "ley"):
                sets.append(f"{col} = %s")
                vals.append(ejes.get(col) or None)
        if parche:
            sets.append("data = COALESCE(data, '{}'::jsonb) || %s::jsonb")
            vals.append(json.dumps(parche))
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE mercado.curvas SET {', '.join(sets)} "
                        "WHERE ticker = %s", (*vals, sim["ticker"]))
            filas = cur.rowcount or 0
    except Exception as e:
        acc.registrar(accion="arreglar_bono", objetivo=sim["ticker"], ok=False,
                      error=str(e)[:300], por=actor, antes=antes)
        return {**sim, "aplicado": False, "error": f"no se pudo escribir: {e}"}
    if not filas:
        return {**sim, "aplicado": False, "error": "el UPDATE no tocó ninguna fila"}

    acc.registrar(accion="arreglar_bono", objetivo=sim["ticker"], por=actor,
                  antes=antes,
                  detalle={"ejes": ejes, "campos": list(parche),
                           "tea_antes": (sim.get("antes") or {}).get("tea"),
                           "tea_despues": sim.get("tea")})
    return {**sim, "aplicado": True,
            "aviso": "los motores leen mercado.curvas al arrancar: reiniciar "
                     "motor_curvas para que la tasa nueva llegue a la vista"}
