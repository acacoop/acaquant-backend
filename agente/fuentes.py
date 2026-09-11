"""`agente/fuentes.py` — de dónde saca los datos el agente.

**Existe porque cada reloj del agente viejo leía lo suyo por su cuenta.** Dos
detectores que necesitaban el master lo pedían dos veces, en momentos distintos,
y podían estar mirando fotos distintas del mismo instante — y después la
pantalla mostraba sus dos veredictos uno al lado del otro como si hablaran de lo
mismo.

Acá una PASADA del motor lee una vez y todos los detectores de esa pasada ven
**la misma foto**. La caché muere con la pasada: `motor.tick()` llama a
`refrescar()` al empezar.

⚠️ **`None` NO es un conjunto vacío.** Cuando una fuente no se pudo leer, estas
funciones devuelven `None` y el detector que la necesita levanta `SinDatos`.
Tratar «no pude mirar» como «no hay nada» es exactamente cómo se fabrica un
falso positivo que nadie puede explicar.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)

_cache: dict = {}


def refrescar() -> None:
    """Arranca una pasada nueva. La foto anterior se descarta entera."""
    _cache.clear()


def _una_vez(clave: str, fn):
    if clave not in _cache:
        try:
            _cache[clave] = fn()
        except Exception as e:
            logger.warning("agente/fuentes: no pude leer «%s» (%s)", clave, e)
            _cache[clave] = None
    return _cache[clave]


# ── EL MASTER DE BONOS ─────────────────────────────────────────────────────
#
# ⚠️ Los nombres están CRUZADOS y no es un error de tipeo: en el blob de
# `mercado.curvas`, `ticker_corto` es la PK (`AL30`) y `ticker` es el SÍMBOLO DE
# MERCADO (`MERV - XMEV - AL30 - 24hs`). El renombre de columnas de 2026-08-15
# arregló la BASE y dejó el blob como estaba, porque ~500 lugares lo leen así.
def master() -> list[dict] | None:
    def _leer():
        from core import curvas_sql
        return curvas_sql.cargar_todos() or []
    return _una_vez("master", _leer)


def simbolos_del_master() -> list[str]:
    return [(b.get("ticker") or "").strip()
            for b in (master() or []) if b.get("ticker")]


# ── LOS PRECIOS ────────────────────────────────────────────────────────────
def snapshot(cols=("last_price", "updated_at", "tea", "paridad", "duration")
             ) -> dict[str, dict] | None:
    def _leer():
        from core import market_snapshot
        return market_snapshot.cols_map(simbolos_del_master(), list(cols)) or {}
    return _una_vez(f"snap:{','.join(cols)}", _leer)


def mep() -> float | None:
    def _leer():
        from api.services.macro import get_ultimo_mep
        return float((get_ultimo_mep() or {}).get("mep") or 0) or None
    return _una_vez("mep", _leer)


# ── LAS PATAS ──────────────────────────────────────────────────────────────
#
# `mercado.especies` es la fuente ÚNICA de la relación ticker → sus patas. Se
# traen `especie` y `plazo` porque sin ellos no se puede decir qué pata le
# corresponde a una curva en dólares, y el detector caía en `es_default` — que
# es una COPIA del master, o sea una comparación vacía.
def especies() -> dict | None:
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT simbolo, ticker, es_default, especie, plazo "
                        "FROM mercado.especies")
            filas = cur.fetchall()
        patas: dict[str, list[dict]] = {}
        for sim, tk, _d, esp, plazo in filas:
            if sim and tk:
                patas.setdefault(tk.strip().upper(), []).append(
                    {"simbolo": sim, "especie": esp, "plazo": plazo})
        return {
            "simbolos": {r[0] for r in filas if r[0]},
            "default": {(r[1] or "").strip().upper(): r[0]
                        for r in filas if r[2] and r[0] and r[1]},
            "patas": patas}
    return _una_vez("especies", _leer)


# ── EL CATÁLOGO REAL DE PRIMARY ────────────────────────────────────────────
#
# Es la habilidad `deteccion_primary`: la pregunta «¿esto cotiza?» la hacen
# cuatro detectores y tres arreglos, y en el agente viejo cada uno la resolvía a
# su manera. Una sola respuesta para todos.
def primary() -> set[str] | None:
    """Los símbolos que Primary publica hoy. `None` = no se pudo saber.

    ⚠️ **DELEGA en `core.instrumentos_validos`, no lee la tabla por su cuenta.**
    La primera versión escribía su propio `SELECT symbol FROM
    manager.pyrofex_instruments` — y esa columna **no existe**: el catálogo
    guarda los símbolos adentro de un jsonb (`instruments -> [] -> ticker`),
    agrupados por CFI. El detector se degradó honestamente («no pude leer
    primary») y siguió, así que no rompió nada — pero corrió CIEGO.

    Y el nombre de columna era el síntoma, no el bug. El bug era escribir un
    SEGUNDO lector de la misma pregunta: `core/instrumentos_validos` ya es el
    criterio único, el mismo que aplica `core/websocket.py` a TODAS las
    suscripciones de todos los motores. Dos lectores de «¿esto cotiza?»
    terminan siempre igual — uno se queda viejo y nadie sabe cuál manda.

    La degradación (`None` cuando el catálogo no se puede leer o tiene menos de
    100 símbolos) también vive allá, y es la correcta: filtrar de más deja
    papeles sin precio; ante la duda, no se filtra.
    """
    def _leer():
        from core import instrumentos_validos
        return instrumentos_validos.validos()
    return _una_vez("primary", _leer)


def fichas_primary() -> list[dict] | None:
    """La FICHA de cada símbolo de la foto (`cficode`, moneda, subyacente…), por
    el mismo lector único que `primary()`. `None` = no se pudo saber.

    Es lo que permite decir «esto ES un CEDEAR» por la ficha y no por el nombre
    (REGLA #9): el detector `cedear_faltante` calibra qué `cficode` tienen los
    CEDEARs que YA tenemos y busca los que faltan con esa misma ficha (§0.dl)."""
    def _leer():
        from core import instrumentos_validos
        return instrumentos_validos.fichas()
    return _una_vez("fichas_primary", _leer)


# ── LOS FONDOS QUE LA MESA YA LINKEÓ ───────────────────────────────────────
def fci_por_unidad() -> dict[str, dict] | None:
    """`{unidad: {nombre, tipo_renta, moneda, categoria}}` de `mercado.fci`, solo
    los fondos LINKEADOS a un asset. `None` = no pude leer.

    ⚠️ **Es un link por ID, no por texto**, y esa es toda la diferencia.
    `mercado.fci.unidad` la confirma la mesa en Manager y es la MISMA clave con
    la que `CompletarFicha` escribe; el `tipo_renta` que trae es el `underlying`
    de Primary que ya bajó `jobs/fci_universo`. Antes de esto, la regla de FCI
    emparejaba por NOMBRE contra la ficha de Primary y fallaba en 73 de 84
    (2026-09-11, §0.fg) — teniendo el link al lado. `core/fci_match.py` lo dice
    desde el principio: *«el link es por símbolo, no por texto»*.
    """
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            # `simbolo_primary` viaja porque es lo que distingue las DOS razones
            # por las que una fila puede no tener `tipo_renta`, y se atienden
            # distinto: sin símbolo es una fila propia que `fci_universo` creó
            # desde el asset (`origen = 'asset'`) porque no pudo emparejarlo con
            # Primary —eso se arregla cargando `instrumento` en Manager—; con
            # símbolo, el emparejamiento está y es la ficha la que no trae el
            # tipo de renta.
            cur.execute("SELECT unidad, nombre, tipo_renta, moneda, categoria, "
                        "       simbolo_primary, origen "
                        "  FROM mercado.fci WHERE unidad IS NOT NULL")
            return {u: {"nombre": n, "tipo_renta": tr, "moneda": m,
                        "categoria": cat, "simbolo_primary": sym, "origen": org}
                    for u, n, tr, m, cat, sym, org in cur.fetchall()}
    return _una_vez("fci_por_unidad", _leer)


# ── EL MASTER DE CEDEARs ───────────────────────────────────────────────────
def cedears_master() -> list[dict] | None:
    """`mercado.cedears` entero (activos e inactivos), por `core.cedears_sql`.
    `None` = no pude leer. Vacío es una afirmación: no hay ninguno cargado."""
    def _leer():
        from core import cedears_sql
        return cedears_sql.cargar_master()
    return _una_vez("cedears_master", _leer)


def primary_fecha():
    """Cuándo se sacó la foto de Primary (`manager.pyrofex_discovery.generated_at`),
    o `None` si nunca. **La foto tiene fecha y la fecha se muestra**: un «no
    cotiza» sin decir de cuándo es la foto es lo que hizo que S29E7 saliera como
    inexistente 17 días después de licitarse (§0.cy)."""
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT generated_at FROM manager.pyrofex_discovery "
                        "WHERE id = 'current'")
            r = cur.fetchone()
        return r[0] if r else None
    return _una_vez("primary_fecha", _leer)


def catalogo_1816_fecha():
    """Cuándo se refrescó por última vez `research.mkt_1816_instrumentos`
    (`max(actualizado_en)`), o `None` si está vacía. Misma idea que
    `primary_fecha`: una foto tiene fecha y la fecha se vigila (§0.df)."""
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(actualizado_en) FROM research.mkt_1816_instrumentos")
            r = cur.fetchone()
        return r[0] if r else None
    return _una_vez("catalogo_1816_fecha", _leer)


def tickers_en_primary() -> set[str] | None:
    """Los tickers que Primary lista, sacados del símbolo tal cual.

    ⚠️ **No se le saca ningún sufijo.** La primera versión hacía
    `.rstrip("DC")` para "conseguir el ticker base", y eso convierte `TXAD` en
    `TXA` y `PBAC` en `PBA`: la REGLA #9 del repo dice exactamente esto — la
    identidad no es el nombre, y adivinar por sufijo es cómo se emparejan mal
    dos cosas distintas sin que falle nada.

    No hace falta: Primary lista `AL30`, `AL30D` y `AL30C` como símbolos
    SEPARADOS, así que el ticker base ya está en el conjunto por derecho propio.
    """
    p = primary()
    if p is None:
        return None
    out = set()
    for s in p:
        partes = s.split(" - ")
        base = (partes[2] if len(partes) >= 3 else s).strip().upper()
        if base:
            out.add(base)
    return out


# ── LOS LATIDOS ────────────────────────────────────────────────────────────
def latidos() -> dict[str, dict] | None:
    """proceso → su último latido (`operaciones.latidos`, core/latido.py).
    `None` = no pude leer. Vacío = nadie late todavía (código sin desplegar
    en los motores), que es distinto y el detector lo dice distinto."""
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT proceso, pid, host, arrancado_at, latido_at, data "
                        "FROM operaciones.latidos")
            return {r[0]: {"proceso": r[0], "pid": r[1], "host": r[2],
                           "arrancado_at": r[3], "latido_at": r[4],
                           "data": dict(r[5] or {})} for r in cur.fetchall()}
    return _una_vez("latidos", _leer)


# ── EL PULSO DEL CLIENTE ───────────────────────────────────────────────────
def pulsos(minutos: int = 10) -> list[dict] | None:
    """Los pulsos de los últimos `minutos` (`agente.pulso_cliente`, §0.dg):
    pantallas que no pudieron refrescar. `None` = no pude leer.

    ⚠️ **Solo `tipo = 'ciega'`.** La misma tabla guarda los TILDES (el navegador
    trabado, `tipo = 'tilde'`), que son otro problema y se arreglan en otro
    lado: mezclarlos haría que una pestaña clavada se reporte como «la vista
    está ciega» y mande a revisar el backend, que es justo donde no está.
    """
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT at, email, vista, endpoint, motivo, desde_at "
                        "FROM agente.pulso_cliente "
                        "WHERE at >= now() - make_interval(mins => %s) "
                        "  AND tipo = 'ciega' ORDER BY at",
                        (int(minutos),))
            return [{"at": r[0], "email": r[1], "vista": r[2], "endpoint": r[3],
                     "motivo": r[4], "desde_at": r[5]} for r in cur.fetchall()]
    return _una_vez(f"pulsos_{int(minutos)}", lambda: _leer())


def tildes(minutos: int = 30) -> list[dict] | None:
    """Los TILDES de los últimos `minutos`: pantallas donde el NAVEGADOR se
    clavó (`tipo = 'tilde'`, 2026-09-04). `None` = no pude leer.

    Es lo único que le cuenta al servidor algo que nunca pasa por el servidor:
    un hilo principal bloqueado no falla, no tira excepción y no deja request.
    `ms` es cuánto duró; `datos` trae la peor tarea larga y la memoria, que es
    lo que separa «un render caro» de «la pestaña se está quedando sin memoria».
    """
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT at, email, vista, motivo, ms, datos "
                        "FROM agente.pulso_cliente "
                        "WHERE at >= now() - make_interval(mins => %s) "
                        "  AND tipo = 'tilde' ORDER BY at",
                        (int(minutos),))
            return [{"at": r[0], "email": r[1], "vista": r[2], "motivo": r[3],
                     "ms": r[4], "datos": dict(r[5] or {})} for r in cur.fetchall()]
    return _una_vez(f"tildes_{int(minutos)}", lambda: _leer())


# ── LO QUE LA CASA TIENE ───────────────────────────────────────────────────
def en_cartera() -> set[str] | None:
    """Tickers con tenencia viva. Convierte «¿te interesa?» en una obviedad: un
    bono que la casa TIENE y no valúa no es una opinión, es un problema."""
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT upper(btrim(a.ticker)) "
                "  FROM portafolio.tenencia t "
                "  JOIN portafolio.assets a ON a.unidad = t.unidad "
                " WHERE t.aum = 'si' AND a.ticker IS NOT NULL AND a.ticker <> ''")
            return {r[0] for r in cur.fetchall()}
    return _una_vez("en_cartera", _leer)


def ons_no_interesan() -> set[str] | None:
    """Tickers de ON que la mesa YA descartó (`mercado.ons_ignoradas`, §0.eh).
    La escribe SOLO el agente, desde «no me interesan» (`vista.no_interesan_ons`):
    el panel de ONs de Manager, que era la otra mitad —y donde se RESTAURABAN—,
    se borró con el tab BONOS. `None` = no pude leer → no se filtra nada: ofrecer
    de más es mejor que callar una ON nueva."""
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT upper(btrim(ticker)) FROM mercado.ons_ignoradas")
            return {r[0] for r in cur.fetchall() if r[0]}
    return _una_vez("ons_no_interesan", _leer)


# ── EL UNIVERSO DE 1816 ────────────────────────────────────────────────────
#
# ⚠️⚠️ **DOS FUENTES, Y LA DIFERENCIA ES QUIÉN ESPERA** (§0.fh).
#
# El CENSO (`universo_1816`) recorre `/curvas` y pide `/instrumentos` por cada
# una: ~29 requests con un throttle obligatorio de 2,5 s entre llamadas
# (`core/mercado_1816._MIN_INTERVALO_S`), o sea **72 s de piso** antes de contar
# un milisegundo de red. Eso está bien para un job y es imposible para una
# pantalla: el proxy de Next corta a los 30 s (`maxDuration`) y Cloudflare a los
# ~100. El preview de `cartera` lo pedía y **no podía contestar nunca** — la fila
# quedaba en «trabajando…» para siempre.
#
# El CATÁLOGO (`universo_1816_local`) es la misma información ya persistida por
# `jobs/mercado_1816_discovery`: UNA consulta, sin créditos y sin red. Es lo que
# tiene que leer todo lo que corre a pedido de una persona.
def _catalogo_1816_local() -> dict | None:
    """`{ticker: {"_curva": …}}` desde `research.mkt_1816_instrumentos`, o `None`
    si está vacía. La forma es la del censo para que los dos sean
    intercambiables — quien las consume mira `_curva` y nada más.

    ⚠️ **Las columnas son las TIPADAS de la tabla.** Hasta hoy esta lectura
    pedía `SELECT ticker, curva, data` y **`data` no existe en esa tabla**: la
    única salida posible era `column "data" does not exist`. No fallaba nada
    porque `_una_vez` captura y cachea `None` — así que la «degradación honesta
    al catálogo» que este módulo prometía nunca ocurrió una sola vez.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, curva, denominacion, emisor, "
                    "       moneda_denom, moneda_pago, fecha_vencimiento "
                    "  FROM research.mkt_1816_instrumentos "
                    " WHERE coalesce(activo, true)")
        filas = cur.fetchall()
    if not filas:
        return None
    inst = {tk: {"_curva": curva, "denominacion": den, "emisorNombre": em,
                 "monedaDenom": md, "monedaPago": mp, "fechaVencimiento": venc}
            for tk, curva, den, em, md, mp, venc in filas}
    return {"instrumentos": inst, "fuente": "catalogo_local"}


def universo_1816() -> dict | None:
    """El CENSO VIVO. **Cuesta créditos y tarda** (~29 llamadas): sólo para lo
    que corre en el daemon, nunca para una pantalla.

    Si la API no contesta se cae al catálogo ya persistido: degradar es distinto
    de fallar, y lo que se usó se declara en `fuente`.
    """
    def _leer():
        from core import mercado_1816
        try:
            u = (mercado_1816.censar() or {}).get("instrumentos") or {}
            if u:
                return {"instrumentos": u, "fuente": "1816"}
        except Exception as e:
            logger.warning("agente: 1816 no contestó (%s) — voy al catálogo", e)
        return _catalogo_1816_local()
    return _una_vez("univ1816", _leer)


def universo_1816_local() -> dict | None:
    """El CATÁLOGO persistido, sin red y sin créditos. **Es lo que usa todo lo
    que corre a pedido de una persona** (§0.fh).

    La foto es de la última corrida de `jobs/mercado_1816_discovery`, y para
    PROPONER un valor que alguien confirma eso alcanza: lo otro es no poder
    abrir la pantalla.
    """
    return _una_vez("univ1816_local", _catalogo_1816_local)


# ── LAS CORRIDAS DE UN JOB (§0.dk) ─────────────────────────────────────────
def corridas(job: str, n: int = 15) -> list[dict] | None:
    """Las últimas N corridas de ese job, la más nueva primero:
    `[{started_at, finished_at, status, stats}, …]`. Va por el índice
    `(tipo, started_at)`. `started_at` está porque «¿esta corrida es POSTERIOR
    al último dato?» se contesta con el arranque: la que escribió el dato
    termina unos segundos DESPUÉS del sello que dejó (§0.fb)."""
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT started_at, finished_at, status, data->'stats' "
                        "  FROM manager.job_runs "
                        " WHERE tipo = %s AND finished_at IS NOT NULL "
                        " ORDER BY finished_at DESC LIMIT %s", (job, int(n)))
            return [{"started_at": r[0], "finished_at": r[1], "status": r[2],
                     "stats": dict(r[3] or {})} for r in cur.fetchall()]
    return _una_vez(f"corridas:{job}:{n}", _leer)



# ── A QUÉ DÓLAR ESTÁ GUARDADO CADA DATO DE 1816 (§0.ez) ────────────────────
def monedas_1816() -> list[dict] | None:
    """Todo lo que 1816 nos dejó escrito, con la moneda con la que se pidió.

    Una fila por `(tabla, ticker, moneda)`: `[{tabla, ticker, moneda_pago,
    moneda, filas, desde}]`. Junta las TRES tablas donde termina un número de
    1816, porque la pregunta «¿esto está al CCL?» es la misma en las tres y
    responderla por separado es cómo se escapó la primera vez:

      · `research.mkt_1816_series`  → los gráficos de RESEARCH (spread/comparar)
      · `agente.tasa_1816`          → la TEA que ve la mesa en RENTA FIJA
      · `mercado.tamar_1816`        → la TEA y el margen de cada pata

    `moneda_pago` viene del catálogo (`mkt_1816_instrumentos`) y es lo que
    decide qué moneda CORRESPONDE — el que paga en dólares tiene que estar en
    `mep`. Un ticker que no está en el catálogo sale con `moneda_pago = None`:
    de ese no se puede afirmar nada y el detector lo dice así, no lo asume bien.

    `None` = no pude leer.
    """
    def _leer():
        # LEFT JOIN contra el catálogo en las tres: sin `moneda_pago` no se
        # puede juzgar, y una fila que falta es distinto de una fila que dice ARS.
        sql = """
            SELECT 'research.mkt_1816_series' AS tabla, s.ticker,
                   i.moneda_pago, s.moneda, count(*) AS filas, min(s.fecha)::text AS desde
              FROM research.mkt_1816_series s
              LEFT JOIN research.mkt_1816_instrumentos i ON i.ticker = s.ticker
             GROUP BY s.ticker, i.moneda_pago, s.moneda
            UNION ALL
            SELECT 'agente.tasa_1816', t.ticker, i.moneda_pago, t.moneda,
                   count(*), min(t.fecha_1816)::text
              FROM agente.tasa_1816 t
              LEFT JOIN research.mkt_1816_instrumentos i ON i.ticker = t.ticker
             GROUP BY t.ticker, i.moneda_pago, t.moneda
            UNION ALL
            SELECT 'mercado.tamar_1816', m.ticker_1816, i.moneda_pago, m.moneda,
                   count(*), min(m.fecha_operacion)::text
              FROM mercado.tamar_1816 m
              LEFT JOIN research.mkt_1816_instrumentos i ON i.ticker = m.ticker_1816
             GROUP BY m.ticker_1816, i.moneda_pago, m.moneda
        """
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return [{"tabla": r[0], "ticker": r[1], "moneda_pago": r[2],
                     "moneda": r[3], "filas": r[4], "desde": r[5]}
                    for r in cur.fetchall()]
    return _una_vez("monedas_1816", _leer)
