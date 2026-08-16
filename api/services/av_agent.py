"""api/services/av_agent.py — los detectores del AV AGENT (etapa E1).

Doc madre: **`docs/AV_AGENT.md`** (leerlo antes de tocar esto).

**Qué es esta etapa.** El espejo: contesta las tres preguntas del agente SIN
escribir en `mercado.curvas` y **sin una sola llamada a un LLM**. Es deliberado —
un agente que diagnostica sin poder medir si acertó no es un agente, es un
generador de opiniones. Primero se construye el piso medible (E1), se calibra
contra la realidad, y recién ahí se le enchufa el modelo (E4).

Las tres preguntas:

  1. **¿Qué hay en 1816 que no tengo?**  → `detectar_faltantes`
  2. **¿Qué bono mío está sin flujo?**   → `detectar_sin_flujo`
  3. **¿Qué tasa está dando mal?**       → `detectar_tasas_sospechosas`

**Módulo PURO** (regla de capas): las tres `detectar_*` reciben los datos ya
leídos y no tocan la base ni la red, así se testean sin Postgres y sin créditos.
`relevar()` es el único que lee, y es el que llama el job.

⚠️ **El enemigo de esta etapa es el FALSO POSITIVO, no el falso negativo.** Si la
lista trae ruido, a las tres semanas no la mira nadie y el agente muere aunque
funcione. Por eso cada regla excluye explícitamente los casos que YA sabemos que
no son problemas (ver `_MOTIVOS_SIN_TASA_LEGITIMOS` y el reuso de
`es_tasa_ruido`), y por eso E3 va a castigar el falso positivo igual que el
falso negativo.
"""
from __future__ import annotations

from typing import Any

from core import curvas_ejes, mercado_1816

# ── Rangos de sanidad (docs/SALUD_CURVAS.md §7.1) ────────────────────────────
#
# ⚠️ ESCALAS, que no son obvias y equivocarlas invierte el resultado:
#   · `tea` de `mercado.market_snapshot` viene en **FRACCIONES** (0.0973 = 9,73%),
#     igual que el `spread` de 1816 — verificado en jobs/tamar_1816.
#   · `paridad` viene en **PORCENTAJE** (100 = a la par) — el motor la calcula
#     como `precio_calc / Σ amortizaciones futuras × 100`.
#   · `duration` en años.
PARIDAD_MIN, PARIDAD_MAX = 40.0, 160.0
TEA_MIN, TEA_MAX = -0.30, 0.60

# Ajustes cuya TEA el motor NO calcula por diseño: caen en el `else` de
# `engines/curvas.py`, que solo computa duration. Reportarlos como "sin tasa"
# sería denunciar todas las noches una decisión de arquitectura — 18 bonos TAMAR
# de ruido fijo, que es exactamente cómo se entrena a la gente a ignorar la
# lista. Su tasa llega por otro riel (`mercado.tamar_1816`, desde 1816).
_MOTIVOS_SIN_TASA_LEGITIMOS = {"tamar", "badlar", "tpm", "caucion"}

# Qué `emisor_tipo` entran según el alcance elegido (decisión D1 del doc, ABIERTA:
# por eso es un parámetro y no una constante). `soberanos` incluye **bcra** porque
# los BOPREALes viven en la curva "BCRA USD" de 1816 y en nuestra base están del
# lado soberano — es el mismo criterio que ya usa `jobs/mercado_1816_discovery`.
ALCANCES: dict[str, frozenset[str] | None] = {
    "soberanos": frozenset({"soberano", "bcra"}),
    "no_corporativos": frozenset({"soberano", "bcra", "provincial"}),
    "todo": None,          # None = sin filtro
}

# Monedas que la mesa SIGUE. 1816 publica 6 Globales en EUROS (GE29/GE30/GE35/
# GE38/GE41/GE46) que no operamos: no son un faltante, son un mercado en el que
# no estamos. Calibrado con la primera corrida (2026-08-16) — eran 6 de los 33.
#
# Se excluye por MONEDA y no anotando los seis tickers a mano a propósito: una
# regla estructural sigue valiendo cuando el Tesoro emita el séptimo, una lista
# de excepciones no. Para lo que sí es caso por caso está `IGNORADOS`.
MONEDAS_SEGUIDAS = frozenset({"ARS", "USD"})

_norm = mercado_1816.normalizar_ticker


def _es_pata(ticker: str) -> bool:
    """¿Es una VISTA DE VALUACIÓN por componente y no un instrumento?

    1816 publica las patas de un dual y otras variantes como tickers aparte con
    un sufijo `@`: `TXMD9 @TAMAR`, `BPOA8 @AFIP`, `TY30P @PUT`, `TTS26 @TASA
    FIJA`. **No son instrumentos**: `/cashflow` les da 404 (medido) porque el
    cuadro lo tiene el ticker BASE, que ya está —o ya se reporta— por su cuenta.

    Era el riesgo #3 del doc y se materializó en la primera corrida: 3 de los 33
    faltantes eran patas, y `BPOA8` salía DOS VECES (base y `@AFIP`)."""
    return "@" in (ticker or "")


def _hallazgo(tipo: str, ticker: str, regla: str, severidad: str,
              motivo: str, evidencia: dict[str, Any]) -> dict:
    """Un hallazgo es siempre la MISMA forma, venga del detector que venga: así la
    tabla, la bandeja (E5) y el diagnóstico (E4) leen una sola estructura.

    `evidencia` es lo que sostiene la afirmación — se congela junto al hallazgo
    porque para cuando alguien lo mire, el motivo puede haber dejado de existir
    (mismo criterio que `manager.salud_eventos`)."""
    return {"tipo": tipo, "ticker": ticker, "regla": regla, "severidad": severidad,
            "motivo": motivo, "evidencia": evidencia}


# ── 1) ¿Qué hay en 1816 que no tengo? ────────────────────────────────────────


def detectar_faltantes(universo_1816: dict[str, dict], docs: list[dict], *,
                       alcance: str = "soberanos",
                       ignorados: set[str] | None = None) -> list[dict]:
    """Tickers vigentes en 1816 que NO están en `mercado.curvas`.

    El cruce se hace sobre el ticker NORMALIZADO (sin la especie D/C final): 1816
    publica `AL30` y nuestro master puede tener `AL30D`. La normalización vive en
    el cliente porque es una convención DEL PROVEEDOR — tenerla duplicada hacía
    que dos cruces dieran universos distintos sin que nadie se entere.

    El **alcance** filtra por los ejes que `core.curvas_ejes` deriva del NOMBRE de
    la curva de 1816 (tabla explícita de 28 nombres, no un parser que adivina).
    Una curva que la tabla no conoce se reporta con `curva_desconocida` en vez de
    clasificarse mal en silencio: un nombre nuevo del proveedor tiene que ser
    visible, no invisible.
    """
    permitidos = ALCANCES.get(alcance, ALCANCES["soberanos"])
    ignorados = {_norm(t) for t in (ignorados or set())}
    mios = {_norm(d.get("ticker_corto")) for d in docs if d.get("ticker_corto")}
    mios.discard("")

    out: list[dict] = []
    for ticker, inst in sorted(universo_1816.items()):
        if _es_pata(ticker):          # vista de valuación, no instrumento
            continue
        tk = _norm(ticker)
        if not tk or tk in mios or tk in ignorados:
            continue
        curva_1816 = inst.get("_curva") or ""
        ejes = curvas_ejes.desde_1816(curva_1816)
        if permitidos is not None and (ejes is None or ejes.emisor_tipo not in permitidos):
            continue
        # Moneda que no seguimos → no es un faltante (los Globales en EUR).
        if ejes is not None and ejes.moneda not in MONEDAS_SEGUIDAS:
            continue
        out.append(_hallazgo(
            "falta_en_base", tk, "no_esta_en_curvas", "media",
            f"1816 lo publica en «{curva_1816}» y no está en mercado.curvas.",
            {"curva_1816": curva_1816, "curva_id": inst.get("_curva_id"),
             "ticker_1816": ticker,
             "emisor_1816": inst.get("emisor") or inst.get("emisorNombre"),
             "vencimiento_1816": inst.get("fechaVencimiento") or inst.get("vencimiento"),
             "ejes_sugeridos": (
                 {"emisor_tipo": ejes.emisor_tipo, "moneda_eje": ejes.moneda,
                  "ajuste": ejes.ajuste, "ajuste_alt": ejes.ajuste_alt, "ley": ejes.ley}
                 if ejes else None),
             "curva_desconocida": ejes is None}))
    return out


# ── 2) ¿Qué bono mío está sin flujo? ─────────────────────────────────────────


def detectar_sin_flujo(docs: list[dict],
                       universo_1816: dict[str, dict] | None = None) -> list[dict]:
    """Bonos de `mercado.curvas` sin cronograma de pagos.

    "Sin flujo" es ESTRUCTURAL (no hay definición de flujo), no de valuación — un
    CER futuro tiene flujo aunque todavía no se pueda valuar. Sin flujo no hay
    XIRR: el bono no tiene TEA, no entra al gráfico y no aporta al fair value.

    ⚠️ **El predicado es `acreencias.tiene_flujo_def`, no `bool(doc['flujos'])`.**
    Una LECAP/BONCAP es zero-coupon: no tiene array y NO le falta nada — el motor
    la valúa con `flujo_vencimiento` (`engines/curvas.py` rama `tasa_fija`). La
    primera corrida (2026-08-16) marcó 11 letras que rinden perfecto porque esta
    función miraba solo el array; el criterio correcto ya existía en el
    conciliador de Manager y había que USARLO, no reescribirlo peor.

    Marca `resoluble` cuando 1816 tiene ese ticker, que es la diferencia entre
    *"esto lo completa el agente"* y *"esto necesita el prospecto"*. **Un agente
    que no puede decir "no sé" empieza a rellenar**, así que la distinción viaja
    en el hallazgo y no se resuelve a dedo.
    """
    from datetime import date

    from api.services.acreencias import tiene_flujo_def

    hoy = date.today()
    univ = {_norm(t) for t in (universo_1816 or {})}
    out: list[dict] = []
    for d in docs:
        tc = (d.get("ticker_corto") or "").strip().upper()
        if not tc:
            continue
        if tiene_flujo_def(d, hoy):
            continue
        resoluble = _norm(tc) in univ
        out.append(_hallazgo(
            "sin_flujo", tc, "flujos_vacios", "alta" if resoluble else "media",
            ("Sin cronograma de pagos; 1816 lo tiene y se puede completar."
             if resoluble else
             "Sin cronograma de pagos, y 1816 tampoco lo publica: necesita carga manual."),
            {"resoluble_con_1816": resoluble, "curva": d.get("curva"),
             "emisor": d.get("emisor"), "moneda_flujo": d.get("moneda_flujo"),
             "vencimiento": d.get("fecha_vencimiento")}))
    return out


# ── 3) ¿Qué tasa está dando mal? ─────────────────────────────────────────────


def detectar_tasas_sospechosas(docs: list[dict], metricas: dict[str, dict],
                               tickers_en_assets: set[str] | None = None,
                               en_cartera: set[str] | None = None) -> list[dict]:
    """Las reglas de sanidad de `docs/SALUD_CURVAS.md` §6-§7 sobre el cierre.

    `metricas` = `{simbolo_de_mercado: {tea, paridad, duration, last_price}}` tal
    como lo devuelve `core.market_snapshot.cols_map`. El join va por el SÍMBOLO
    (`doc['ticker']`, que tras el renombre de columnas es el de Primary), no por
    el ticker corto.

    **Cada regla dice qué falla del catálogo sospecha**, porque ese mapeo es el
    que E4 va a usar como few-shot: el modelo no arranca de cero, arranca de las
    7 fallas que ya conocemos.

    Tres exclusiones deliberadas, que son lo que separa una lista útil de una
    lista que nadie mira:
      · los ajustes que el motor NO calcula por diseño (TAMAR y compañía);
      · las tasas que ya están marcadas como RUIDO por duration — mismo predicado
        que usa la vista (`es_tasa_ruido`), no una copia que pueda divergir;
      · los bonos sin flujo, que ya los reporta el detector 2 (un bono sin flujo
        no tiene tasa por definición: contarlo dos veces infla la lista y hace
        parecer que hay dos problemas donde hay uno).

    `en_cartera` acota `sin_espejo_en_assets` a lo que la casa TIENE: un bono que
    no está en la tenencia no necesita fila en `portafolio.assets` — no le falta
    nada al AuM porque no aporta al AuM. Es el mismo recorte que hace el
    conciliador de Manager (`titulos_sin_flujo` parte del último AuM). `None`
    apaga la regla en vez de marcar todo, igual que `tickers_en_assets`.
    """
    from datetime import date

    from api.services.acreencias import tiene_flujo_def
    from api.services.curvas_vista import es_tasa_ruido

    hoy = date.today()
    out: list[dict] = []
    for d in docs:
        tc = (d.get("ticker_corto") or "").strip().upper()
        simbolo = (d.get("ticker") or "").strip()
        if not tc:
            continue

        # sin flujo → es el hallazgo del detector 2, no una tasa rota. MISMO
        # predicado que allá: si acá se mirara solo el array, una LECAP entraría
        # a las reglas de tasa por una puerta y saldría por la otra.
        if not tiene_flujo_def(d, hoy):
            continue

        ejes = curvas_ejes.ejes_de_doc(d)
        emisor_tipo = ejes.emisor_tipo if ejes else d.get("emisor_tipo")
        ajuste = (d.get("ajuste") or "").strip().lower()
        m = metricas.get(simbolo) or {}
        tea, paridad = m.get("tea"), m.get("paridad")
        precio, duration = m.get("last_price"), m.get("duration")
        base = {"simbolo": simbolo, "tea": tea, "paridad": paridad,
                "last_price": precio, "duration": duration,
                "moneda_flujo": d.get("moneda_flujo"), "emisor": d.get("emisor"),
                "ajuste": ajuste or None, "n_flujos": len(d.get("flujos") or [])}

        # Un bono sin ejes desaparece de todo lo que llame a `por_curva` — y no
        # da error, que es lo que lo hace peligroso (paso 14 de RENTA_FIJA).
        if ejes is None:
            out.append(_hallazgo(
                "tasa_sospechosa", tc, "sin_ejes", "alta",
                "Sin ejes: no cae en ninguna curva y desaparece de la vista, "
                "los forwards y el fair value, sin dar error.",
                base))
            continue

        if (tickers_en_assets is not None and tc not in tickers_en_assets
                and en_cartera is not None and tc in en_cartera):
            out.append(_hallazgo(
                "tasa_sospechosa", tc, "sin_espejo_en_assets", "alta",
                "La casa TIENE este bono y no está en portafolio.assets: no entra "
                "al AuM ni a Portfolios (falla del join de valuación).",
                base))

        ruidosa = es_tasa_ruido(m, emisor_tipo)

        if tea is None and precio and ajuste not in _MOTIVOS_SIN_TASA_LEGITIMOS:
            out.append(_hallazgo(
                "tasa_sospechosa", tc, "sin_tea_con_precio", "alta",
                "Tiene precio y flujo pero el motor no persiste TEA: el XIRR no "
                "converge. Sospecha: pata equivocada o escala del flujo distinta "
                "de la del precio (fallas #2 y #4 del catálogo).",
                base))

        if paridad is not None and not (PARIDAD_MIN <= paridad <= PARIDAD_MAX):
            out.append(_hallazgo(
                "tasa_sospechosa", tc, "paridad_fuera_de_rango",
                "alta" if paridad > 300 or paridad < 10 else "media",
                f"Paridad {paridad:,.1f}% fuera de [{PARIDAD_MIN:.0f}, "
                f"{PARIDAD_MAX:.0f}]. Sospecha: escala del flujo o pata "
                "equivocada (fallas #2, #3 y #4).",
                base))

        if tea is not None and not (TEA_MIN <= tea <= TEA_MAX) and not ruidosa:
            out.append(_hallazgo(
                "tasa_sospechosa", tc, "tea_fuera_de_rango", "media",
                f"TEA {tea:.2%} fuera de [{TEA_MIN:.0%}, {TEA_MAX:.0%}] y la "
                "duration no la explica. Sospecha: precio stale/ilíquido o dato "
                "del bono mal cargado (fallas #4 y #5).",
                base))

    return out


# ── Orquestación (el único que lee de la base / la red) ──────────────────────


def relevar(*, alcance: str = "soberanos",
            universo_1816: dict[str, dict] | None = None) -> dict:
    """Corre los tres detectores y devuelve `{alcance, universo, hallazgos, resumen}`.

    **READ-ONLY**: no escribe una sola fila en `mercado.curvas`. Persistir el
    resultado es responsabilidad del job (`jobs/av_agent.py`), y solo en la tabla
    propia del agente.

    `universo_1816` se puede inyectar (un censo ya pagado) para no repetir los ~29
    créditos — así el job, los tests y un diag comparten la misma foto.
    """
    from core import curvas_sql, market_snapshot
    from core.postgres import get_pool

    if universo_1816 is None:
        universo_1816 = (mercado_1816.censar() or {}).get("instrumentos") or {}

    docs = curvas_sql.cargar_todos()
    simbolos = [s for s in ((d.get("ticker") or "").strip() for d in docs) if s]
    metricas = market_snapshot.cols_map(
        simbolos, ["tea", "paridad", "duration", "last_price"])

    # Las tres lecturas de abajo comparten un contrato: si la query falla, el dato
    # queda en `None` y la regla que lo usa **no corre**. Marcar 222 bonos como
    # huérfanos porque se cayó una query sería el peor falso positivo posible —
    # "no pude mirar" jamás puede convertirse en "no está".
    en_assets: set[str] | None = None
    en_cartera: set[str] | None = None
    ignorados: set[str] = set()
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT upper(btrim(ticker)) FROM portafolio.assets "
                        "WHERE ticker IS NOT NULL AND ticker <> ''")
            en_assets = {r[0] for r in cur.fetchall()}
    except Exception:
        en_assets = None

    try:
        from api.services.acreencias import codigo_de_unidad
        with get_pool().connection() as conn, conn.cursor() as cur:
            # El código sale de la UNIDAD ('[57187] OLC3O' → 'OLC3O') y no de un
            # join con assets: el bono que nos interesa es justamente el que NO
            # tiene asset, así que joinear por ahí lo escondería.
            cur.execute("SELECT DISTINCT unidad FROM portafolio.tenencia "
                        "WHERE aum = 'si' AND fecha = ("
                        "  SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si')")
            en_cartera = {codigo_de_unidad(r[0]) for r in cur.fetchall() if r[0]}
            en_cartera.discard("")
    except Exception:
        en_cartera = None

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT upper(btrim(ticker)) FROM mercado.av_agent_ignorados")
            ignorados = {r[0] for r in cur.fetchall() if r[0]}
    except Exception:
        # Acá el default seguro es el CONTRARIO: sin la lista se reporta de más,
        # que es ruido; asumir que todo está ignorado escondería hallazgos reales.
        ignorados = set()

    hallazgos = [
        *detectar_faltantes(universo_1816, docs, alcance=alcance, ignorados=ignorados),
        *detectar_sin_flujo(docs, universo_1816),
        *detectar_tasas_sospechosas(docs, metricas, en_assets, en_cartera),
    ]

    resumen: dict[str, int] = {}
    for h in hallazgos:
        resumen[h["tipo"]] = resumen.get(h["tipo"], 0) + 1
        resumen[f"regla:{h['regla']}"] = resumen.get(f"regla:{h['regla']}", 0) + 1

    return {
        "alcance": alcance,
        "universo": {"1816": len(universo_1816), "mio": len(docs),
                     "con_metricas": len(metricas),
                     "assets_leidos": en_assets is not None,
                     "cartera_leida": en_cartera is not None,
                     "en_cartera": len(en_cartera or ()),
                     "ignorados": len(ignorados)},
        "hallazgos": hallazgos,
        "resumen": resumen,
    }
