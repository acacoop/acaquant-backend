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
    if rama == "dolar_linked":
        return ("un dólar-linked se valúa contra el A3500 del día y su cuadro puede "
                "venir en nominales: la conversión no es directa. Se carga a mano "
                "con el cuadro que muestra el simulador.")
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


def _paso(clave: str, titulo: str, estado: str, detalle: str,
          *, tabla: str = "", accion: str = "") -> dict:
    """Un eslabón. **`clave` es la identidad, `n` es presentación** — el `n` se
    numera al final según los pasos que hayan aplicado (el de CER no siempre
    está, el control cruzado tampoco). Si el orden fuera la identidad, insertar
    un paso en el medio renumeraría todo y rompería a quien lo referencie."""
    return {"clave": clave, "titulo": titulo, "estado": estado, "detalle": detalle,
            "tabla": tabla, "accion": accion}


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


def _paso(clave: str, titulo: str, estado: str, detalle: str,
          *, tabla: str = "", accion: str = "") -> dict:
    """Un eslabón. **`clave` es la identidad, `n` es presentación** — el `n` se
    numera al final según los pasos que hayan aplicado (el de CER no siempre
    está, el control cruzado tampoco). Si el orden fuera la identidad, insertar
    un paso en el medio renumeraría todo y rompería a quien lo referencie."""
    return {"clave": clave, "titulo": titulo, "estado": estado, "detalle": detalle,
            "tabla": tabla, "accion": accion}


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


def _cotejo_tea(tea, ref: dict, *, job_tasa: str = "", paridad=None) -> dict:
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
                     "falta la paridad de alguno de los dos, así que no hay "
                     "comparación posible" + apoyo
                     + (f" ({ref['error']})" if ref.get("error") else ""),
                     tabla="1816 /indicadores")

    dif_rel = abs(nuestra_par - suya_par) / suya_par * 100 if suya_par else 999.0
    linea = (f"paridad nuestra {nuestra_par:.2f}% vs 1816 {suya_par:.2f}% "
             f"→ {dif_rel:.2f}% de diferencia ({base}){apoyo}")

    if dif_rel <= _PARIDAD_COINCIDE:
        return _paso("cotejo_1816", "El cuadro coincide con el de 1816", OK,
                     linea + ". **La paridad es el juez**: si coincide, el "
                             "cronograma que estamos por escribir es el mismo que "
                             "el de ellos. El cuadro está bien convertido.",
                     tabla="1816 /indicadores")
    if dif_rel <= _PARIDAD_MIRAR:
        return _paso("cotejo_1816", "El cuadro coincide con el de 1816", ATENCION,
                     linea + ". Se parecen pero no son iguales: puede faltar o "
                             "sobrar un cupón, o diferir una fecha de pago.",
                     tabla="1816 /indicadores",
                     accion="comparar el cuadro de abajo contra la pantalla de 1816")
    return _paso("cotejo_1816", "El cuadro coincide con el de 1816", ATENCION,
                 linea + ". **Se contradicen.** Con el mismo precio, una paridad "
                         "distinta significa OTRO valor técnico — o sea, otro "
                         "cronograma. Casi siempre es la escala del cuadro o la "
                         "pata equivocada. No se bloquea el alta (el umbral es un "
                         "primer corte, no una medición), pero acá hay algo que "
                         "entender antes de confiar en este bono.",
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
              paridad=None, ficha_curvas: dict | None = None) -> list[dict]:
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
    ps.append(_paso("cuadro", "1816 mandó el cuadro de flujos", OK if escala_ok else ATENCION,
                    f"{conv['n']} cupón/es · Σ amortizaciones {conv['suma_amort']} → "
                    f"escala {conv['escala']}"
                    + ("" if escala_ok else
                       " — no suma ~100, así que el cuadro viene en NOMINALES y "
                       "esta rama guarda montos ABSOLUTOS. El motor valúa por "
                       "paridad: revisar antes de aplicar.")
                    + (" — la rama «cer» expresa el cuadro en PORCENTAJES "
                       "(se divide por la Σ), así que la escala no la afecta."
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
        detalle_rama = (f"rama «{rama}» — a este bono NO le calculamos la tasa "
                        f"nosotros: la trae {job_tasa or '1816'}. El cuadro se "
                        "guarda igual, con montos absolutos tal cual los manda "
                        "1816, que es la conversión más simple que hay.")
    else:
        detalle_rama = f"rama «{rama}» — {_motivo_no_aplicable(rama, ejes)}"
    ps.append(_paso("rama", "El cuadro se puede convertir sin ambigüedad",
                    OK if auto else FALLA, detalle_rama,
                    tabla="engines/curvas.py::rama_calculo",
                    accion="" if auto else "cargar a mano con el cuadro de abajo"))

    if rama == "cer":
        ps.append(_paso("cer_emision", "CER de emisión resuelto",
                        OK if cer_emision else FALLA,
                        f"{cer_emision} (inferido de la fecha de emisión de 1816, "
                        "con el mismo T−10 hábiles que usa el motor)"
                        if cer_emision else (nota_cer or "no se pudo calcular"),
                        tabla="macro.series_macro (CER)",
                        accion="" if cer_emision else "cargarlo a mano en el master"))

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
                        ATENCION,
                        f"todavía no hay ninguna pata de {ticker} en "
                        f"mercado.especies, así que «{simbolo}» está ARMADO por "
                        "convención. **No hace falta hacer nada**: si el resto de "
                        "la cadena da OK, el alta la siembra sola.",
                        tabla="mercado.especies"))

    # 6 — el gate REAL de la suscripción.
    con = estado_simbolo.get("conocido")
    ps.append(_paso(
        "primary", "Primary lista ese símbolo (si no, el WS lo filtra)",
        OK if con is True else (FALLA if con is False else NO_SE),
        f"«{simbolo}» ({'de mercado.especies' if origen_simbolo == 'especies' else 'armado por convención'}) — "
        + estado_simbolo.get("nota", ""),
        tabla="manager.pyrofex_instruments · core/instrumentos_validos",
        accion="" if con is not False else
               "verificar la grafía real en Primary — `core/websocket."
               "agregar_suscripciones` descarta lo que no está en el catálogo"))

    # 7 — el motor arma su universo AL ARRANCAR. Este paso NUNCA es verde solo:
    #     es un paso MANUAL, y decirlo es la mitad del valor del pre-flight.
    ps.append(_paso("suscripcion", "El motor lo suscribe y el precio llega a market_snapshot",
                    ATENCION,
                    "los motores leen mercado.curvas UNA vez, al arrancar: hasta "
                    "reiniciar motor_rofex + motor_curvas este bono NO se suscribe "
                    "y no va a tener precio, aunque el alta quede escrita.",
                    tabla="mercado.market_snapshot",
                    accion="tras aplicar: reiniciar motor_rofex y motor_curvas"))

    # 8 — ¿hay con qué calcular la tasa AHORA? Un bono nuevo nunca tiene snapshot
    #     (nunca se suscribió), así que se cae al precio de referencia de 1816.
    if precio and fuente_precio == "1816":
        ps.append(_paso("precio", "Hay precio para simular la tasa ahora", OK,
                        f"sin snapshot todavía, así que se usó el **precio de "
                        f"referencia de 1816**: {precio} al {ref.get('fecha') or '—'}"
                        + (f" → TEA simulada {tea:.4%}" if isinstance(tea, int | float)
                           else " — pero el motor NO devolvió TEA: revisar la escala")
                        + ". Ese precio NO se guarda: es solo para poder calcular "
                          "antes de aplicar.",
                        tabla="1816 /indicadores (no se persiste)"))
    elif precio:
        ps.append(_paso("precio", "Hay precio para simular la tasa ahora", OK,
                        f"último precio {precio} en el snapshot"
                        + (f" → TEA simulada {tea:.4%}" if isinstance(tea, int | float)
                           else " — pero el motor NO devolvió TEA con este cuadro: "
                                "revisar la escala del flujo antes de aplicar"),
                        tabla="mercado.market_snapshot"))
    else:
        ps.append(_paso("precio", "Hay precio para simular la tasa ahora", ATENCION,
                        "no hay precio en el snapshot"
                        + (f", y {ref['error']}" if ref.get("error") else "")
                        + ". El cuadro igual queda listo: la TEA aparece cuando "
                          "llegue el primer trade.",
                        tabla="mercado.market_snapshot · 1816"))

    # 8.b — EL CONTROL CRUZADO. Es el paso que más vale de toda la lista.
    #
    # Un cuadro de flujos mal convertido NO da error: da una TEA plausible pero
    # equivocada, y ahí se acaban las formas de darse cuenta leyendo. Correr
    # NUESTRO motor sobre el precio de 1816 y comparar contra LA TEA DE ELLOS es
    # una segunda opinión independiente sobre el mismo bono — y hasta ahora era
    # imposible de tener justo cuando más falta hace: en un bono nuevo.
    ps.append(_cotejo_tea(tea, ref, job_tasa=job_tasa, paridad=paridad))

    # 9 — ¿QUIÉN calcula la tasa? Dos respuestas válidas, no una.
    if _fuente_tasa == "1816":
        # El job selecciona por `ajuste ∈ ajustes_de_1816()`, así que este ticker
        # entra SOLO en cuanto el alta escriba su fila: no hay nada que registrar
        # a mano. Decirlo es la mitad del valor — antes esto se leía como un error.
        ps.append(_paso("tea_motor", "La tasa la TRAE 1816, no la calculamos", OK,
                        f"a un {ejes.ajuste.upper()} no le calculamos la tasa a "
                        "propósito (es una nota de tasa promedio: la parte ya "
                        "observada está congelada y la futura hay que proyectarla). "
                        f"La baja **{job_tasa or 'el job de esa curva'}** con su TEA "
                        "y su MARGEN, y este ticker entra solo a su universo en "
                        "cuanto el alta escriba la fila — el job selecciona por "
                        "ajuste, no por una lista.",
                        tabla="mercado.tamar_1816"))
    elif rama in RAMAS_AUTOMATICAS or rama == "dolar_linked":
        ps.append(_paso("tea_motor", "El motor de curvas va a calcular la TEA", OK,
                        f"la rama «{rama}» tiene fórmula en engines/curvas.py",
                        tabla="engines/curvas.py::calcular_campos"))
    else:
        ps.append(_paso("tea_motor", "El motor de curvas va a calcular la TEA", FALLA,
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
        ps.append(_paso("assets", "Entra al AuM: hay espejo en portafolio.assets", ATENCION,
                        f"no hay ninguna unidad con ticker {ticker}. El bono va a "
                        "aparecer en la curva con su tasa, pero NO en AuM/Portfolios "
                        "hasta que exista la posición (la crea el backfill de "
                        "tenencias cuando alguien lo tenga).",
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
                    OK if not faltan else ATENCION,
                    f"se escriben {len(escritos)} campos — " + " · ".join(escritos[:9])
                    + ("…" if len(escritos) > 9 else "")
                    + (f". Quedan vacíos: {', '.join(faltan)}." if faltan else "")
                    + (" `cupon_anual` solo se deriva cuando el bono es cero cupón: "
                       "con cupones habría que asumir la frecuencia, y asumir es "
                       "justo lo que no se hace." if "cupon_anual" in faltan else ""),
                    tabla="mercado.curvas",
                    accion="completar a mano en Manager → TÍTULOS" if faltan else ""))

    # TASA EXTERNA: el bono nace CON su tasa y su margen, o no nace entero.
    if _fuente_tasa == "1816":
        ps.append(_paso("tasa_1816", "Al aplicar: cargar la TASA y el MARGEN de 1816",
                        ATENCION,
                        f"de un {ejes.ajuste.upper()} el **margen** es el número que "
                        "mira la mesa. El alta va a pedirle a 1816 su última tasa "
                        "(retrocediendo día hábil por día hábil) y dejarla escrita, "
                        "así el bono NACE con el dato en vez de esperar hasta 30 "
                        f"minutos a que corra {job_tasa or 'el job'}.",
                        tabla="mercado.tamar_1816",
                        accion="lo hace solo — no hay que correr nada"))

    # ÚLTIMO PASO — y último a propósito: es lo que el alta VA A HACER, no un
    # requisito previo. «Se agrega como instancia final, porque hay que agregar
    # algo que sabés que va a quedar productivo» (el user, 2026-08-17).
    if not activas:
        ps.append(_paso("sembrar", "Al aplicar: sembrar las patas del papel",
                        ATENCION,
                        f"el alta va a buscar en Primary todas las especies de "
                        f"{ticker} y escribirlas en mercado.especies — la pata en "
                        "PESOS y la pata en DÓLARES si las dos existen, con la "
                        "misma lógica que `scripts.sembrar_especies`. De ahí sale "
                        "el símbolo real, y de ahí los deriva `assets_autofill`.",
                        tabla="mercado.especies",
                        accion="lo hace solo — no hay que correr nada"))

    if ctx["ya_en_curvas"]:
        ps.insert(0, _paso("ya_existe", "⚠ Este ticker YA está en el master", ATENCION,
                           f"mercado.curvas ya tiene {ticker} (símbolo actual: "
                           f"{ctx['simbolo_actual']}). Aplicar lo va a PISAR con "
                           "el cuadro de 1816.",
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
    if doc.get("moneda_flujo") == "USD":
        # El divisor que NO se ve y explica la mitad de las divergencias.
        f.append(fila("MEP aplicado", out.get("_mep") or "—",
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
        f.append(fila("Paridad", f"nuestra {out.get('paridad')} · "
                                 f"1816 {ref.get('paridad')}",
                      "**si la paridad coincide y la TEA no, es convención de "
                      "días; si NO coincide, es el precio o su escala**"))
    if out.get("duration") is not None or ref.get("duration") is not None:
        f.append(fila("Duration", f"nuestra {out.get('duration')} · "
                                  f"1816 {ref.get('duration')}",
                      "depende solo del cuadro y las fechas: si difiere, el "
                      "cronograma que bajamos no es el mismo que el de ellos"))
    return f


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


def _referencia_1816(ticker: str, *, moneda_eje: str = "") -> dict:
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
    # ⚠️ **La MONEDA del pedido no es un detalle.** El default del cliente es
    # `moneda="ars"`, y para un bono en dólares eso devuelve el precio EN PESOS:
    # GD46 vino `precioClean = 114.247` (un global cotiza ~60-90 por 100 VN). Con
    # ese número nuestro motor tuvo que dividir por NUESTRO MEP para volver a
    # dólares, mientras 1816 calculó su TEA con SU tipo de cambio — y las dos
    # tasas salieron a 202 bps sin que ninguna estuviera mal.
    #
    # Pidiendo el precio en la moneda DEL BONO no hay conversión de por medio, y
    # el cotejo compara dos cuentas sobre el mismo número.
    # ⚠️ **`usd` NO EXISTE**: el enum es `ars | ccl | mep` (OpenAPI). Y elegir mal
    # acá no da error, da OTRA TASA: con el default `ars`, para un bono pagadero
    # en dólares 1816 **divide las cotizaciones por CCL**, mientras NUESTRO motor
    # divide por MEP (`precio_soberano_a_usd`). Esa —y no la fórmula— es la
    # explicación de los 202 bps de GD46. Pidiendo `mep` los dos usan el mismo
    # tipo de cambio y el cotejo compara lo que dice comparar.
    moneda = "mep" if (moneda_eje or "").strip().upper() == "USD" else "ars"
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
        ficha_curvas=out.get("ficha_curvas") or {})
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
    from engines.curvas import calcular_campos, cargar_cer, cargar_dias_habiles

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
            ref = _referencia_1816(ticker, moneda_eje=moneda_eje)
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
        return {"precio": float(precio), "tea": None, "precio_fuente": fuente,
                "referencia_1816": ref or None,
                "nota_tasa": f"el motor no pudo calcular: {type(e).__name__}"}

    # Si NO hubo que caer a 1816 (había snapshot), igual conviene tener su tasa
    # para el control cruzado: es el único chequeo que dice si el cuadro que
    # estamos por escribir está bien convertido.
    if not ref and ticker:
        ref = _referencia_1816(ticker, moneda_eje=moneda_eje)
    # Y el cotejo DEFINITIVO: su tasa al MISMO precio que usamos nosotros. Sin
    # esto, una diferencia puede ser la fórmula o el insumo y no hay forma de
    # saber cuál; con esto lo que queda es solo convención o cronograma.
    if ticker and r.get("TEA") is not None:
        moneda_pedido = (ref.get("pedido") or {}).get("moneda") or "ars"
        ref = {**ref, "a_nuestro_precio":
               _tea_de_1816_a_nuestro_precio(ticker, float(precio), moneda_pedido)}

    return {"precio": float(precio), "tea": r.get("TEA"), "precio_fuente": fuente,
            "duration": r.get("duration"), "paridad": r.get("paridad"),
            "referencia_1816": ref or None, "_mep": mep,
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
    try:
        from core import curvas_sql
        curvas_sql.invalidar()
    except Exception:
        pass
    return {**sim, "aplicado": True, "upsert": r, "siembra": siembra,
            "tasa_sembrada": tasa_sembrada,
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
