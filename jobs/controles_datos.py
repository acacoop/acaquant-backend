"""controles_datos.py — Auto-control de CALIDAD DE DATOS de ACAQuant.

Controles de CONTENIDO: datos que existen pero están mal/incompletos. Cada
control devuelve una lista de anomalías con una clave estable; el runner las
diffea contra el estado persistido en SQL `manager.controles_datos`,
destacando lo NUEVO de hoy y lo RESUELTO (no repite lo ya conocido como si
fuera novedad). El detalle se consume vía GET /api/manager/controles (tab de
Manager). Con DEEPSEEK_API_KEY seteada, core/ai_resumen (vía el gateway
core/ai.py) agrega una lectura ejecutiva en criollo al resumen.

Controles v1:
  * forwards_faltantes    — bonos del master (mercado.curvas) que NO están en la
                            matriz de forwards publicada de su curva, con causa
                            probable (ej. TZXM9 sin TEA en market_snapshot), +
                            matrices que dejaron de publicarse.
  * rf_sin_tasa           — bonos cotizando (last_price>0) sin TEA en la rueda.
  * assets_sin_cartera    — portafolio.assets con cartera vacía/'NO APLICA'
                            (rompen el divisor de valuación: quedan SIN CLASIFICAR).
  * fci_incompletos       — assets FCI sin ticker/emisor (salen SIN NOMBRE y se
                            fusionan en el detalle de /aum → FCI).
  * rf_valuada_x1         — renta fija (HD/DL/ARS) valuada SIN ÷100 en el último
                            snapshot de tenencia: firma de un tipoTitulo nuevo de
                            Aunesa fuera de TIPOS_DIVISOR_100 (caso LEDE 2026-07-24).
  * comitentes_sin_nivel1 — comitentes Activas sin nivel_1 (quedan fuera de la
                            segmentación / filtros madre). Privado: solo conteo
                            en el resumen, detalle vía GET /api/manager/controles.
  * contrapartes_pendientes — corre el conciliador de contrapartes (Aunesa live,
                            el botón "Solicitar cuentas" de Manager) y marca si
                            hay cuentas candidatas sin dar de alta. Privado.
  * ops_sin_tc            — boletos ARS sin `mep` en un día que SÍ tiene cotización:
                            no se pueden dolarizar en la vista OPERACIONES
                            (caso FCI Bilateral 2026-08).

Regla del resumen: NUNCA datos de clientes → los controles con publico=False
muestran solo conteos; el detalle queda en la tabla y en el endpoint de Manager.

Cron: L-V 16:30 UTC (media rueda: motores vivos → forwards/tasas medibles).
Uso: python -m jobs.controles_datos [--dry]
"""
from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from psycopg.rows import dict_row

from core.postgres import get_pool

logger = logging.getLogger("controles_datos")

_DDL = """
CREATE SCHEMA IF NOT EXISTS manager;
CREATE TABLE IF NOT EXISTS manager.controles_datos (
    control_id  text NOT NULL,
    item_key    text NOT NULL,
    detalle     text,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    resuelto_at timestamptz,
    PRIMARY KEY (control_id, item_key)
);
CREATE INDEX IF NOT EXISTS ix_controles_datos_activos
    ON manager.controles_datos(control_id) WHERE resuelto_at IS NULL;
"""


def _q(sql: str, params=None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def _ensure() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()


# ── Controles (cada uno devuelve [{key, detalle}]) ───────────────────────────

# La matriz de forwards que importa a la mesa es SOLO la de la tabla de renta
# fija: TASA FIJA y CER. Las curvas de ONs (on_*) tienen forwards publicados
# pero sus faltantes no son accionables (bonos ilíquidos) → ruido.
CURVAS_FORWARDS = ("tasa_fija", "cer")


def _chk_forwards_faltantes() -> list[dict]:
    """Bonos del master ausentes de la matriz de forwards de su curva + matrices
    stale. Solo evalúa CURVAS_FORWARDS (tasa_fija/cer) que el motor efectivamente
    publica (hay doc en mercado_hist)."""
    from core.curvas_sql import agrupado_por_curva
    from core.market_snapshot import cols_map
    grupos = agrupado_por_curva()
    docs = {r["k"]: r for r in _q(
        "SELECT DISTINCT ON (k) k, fecha, data FROM mercado.mercado_hist "
        "WHERE coleccion = 'ForwardsHistorico' AND k = ANY(%s) ORDER BY k, fecha DESC",
        (list(CURVAS_FORWARDS),))}
    items: list[dict] = []
    for curva, doc in docs.items():
        insts = grupos.get(curva) or []
        if not insts:
            continue
        # Matriz que dejó de publicarse (>4 días cubre finde largo).
        if doc["fecha"] < date.today() - timedelta(days=4):
            items.append({"key": f"{curva}::__stale__",
                          "detalle": f"[{curva}] matriz sin publicar desde {doc['fecha']}"})
        publicados = set((doc.get("data") or {}).get("tickers") or [])
        master = {(i.get("ticker_corto") or "").strip(): i["ticker"]
                  for i in insts if (i.get("ticker_corto") or "").strip() and i.get("ticker")}
        faltantes = sorted(set(master) - publicados)
        if not faltantes:
            continue
        snap = cols_map([master[tc] for tc in faltantes], ["tea", "duration", "last_price"])
        for tc in faltantes:
            m = snap.get(master[tc]) or {}
            # last_price None O 0 = no operó (el dry-run 2026-07-09 mostró filas
            # con precio 0: "cotiza sin TEA" era engañoso — no había trade).
            if not m.get("last_price"):
                causa = "no operó (sin precio vivo en market_snapshot)"
            elif m.get("tea") is None:
                causa = "cotiza pero SIN TEA (motor_curvas no le calculó tasa — revisar flujos/CER del bono)"
            elif m.get("duration") is None:
                causa = "cotiza con TEA pero sin duration"
            else:
                causa = "tiene TEA+duration y aún no entró a la matriz (revisar motor_forwards)"
            items.append({"key": f"{curva}::{tc}", "detalle": f"[{curva}] {tc}: {causa}"})
    return items


def _chk_rf_sin_tasa() -> list[dict]:
    """Bonos del master cotizando (precio vivo) pero sin TEA/TNA — figuran en la
    tabla de renta fija sin tasa. Mismo criterio que el informe de salud."""
    rows = _q(
        "SELECT c.ticker AS ticker_corto FROM mercado.curvas c "
        "JOIN mercado.market_snapshot ms ON ms.ticker = c.instrumento "
        "WHERE COALESCE(ms.last_price, 0) > 0 AND COALESCE(ms.tea, 0) = 0 "
        "ORDER BY c.ticker")
    return [{"key": r["ticker_corto"], "detalle": f"{r['ticker_corto']}: cotiza sin TEA/TNA"}
            for r in rows if r.get("ticker_corto")]


def _chk_assets_sin_cartera() -> list[dict]:
    """Assets sin CARTERA: su valuación queda SIN CLASIFICAR (el divisor del AuM
    lo decide la cartera) — Manager → Títulos → Assets."""
    rows = _q(
        "SELECT unidad FROM portafolio.assets "
        "WHERE cartera IS NULL OR cartera = '' OR cartera = 'NO APLICA' "
        "ORDER BY unidad")
    return [{"key": r["unidad"], "detalle": f"{r['unidad']}: sin cartera"}
            for r in rows if r.get("unidad")]


def _chk_unidades_gemelas() -> list[dict]:
    """Assets cuya `unidad` colisiona con otra al sacarle los espacios de los
    bordes: son LA MISMA cosa dos veces (REGLA #9). Es el caso OTC del
    2026-08-22 — una escritura con la identidad stripeada + el upsert de
    `set_campos` fabricó una fila FANTASMA trimmeada, y tres pantallas quedaron
    coherentes entre sí contando mentiras (verificado ✔, control cantando,
    propuesta infinita). El código ya no puede repetirlo (la identidad no se
    stripea y el agente escribe con `crear=False`); este control existe para
    que si entra por CUALQUIER otra vía (un import, un tipeo, Aunesa), dure
    horas y no días. Se repara con `scripts/diag_unidades_fantasma`."""
    rows = _q(
        "SELECT btrim(unidad) AS base, count(*) AS n "
        "FROM portafolio.assets GROUP BY btrim(unidad) HAVING count(*) > 1 "
        "ORDER BY base")
    return [{"key": r["base"],
             "detalle": (f"{r['base']}: {r['n']} filas que son la misma unidad "
                         "con distintos espacios — reparar con "
                         "scripts/diag_unidades_fantasma")}
            for r in rows if r.get("base")]


def _chk_fci_incompletos() -> list[dict]:
    """Assets con cartera FCI sin `ticker` y/o sin `emisor`: salen SIN NOMBRE en el
    detalle de /aum → FCI y, si comparten "vacío", se fusionan en un renglón mudo.
    El `ticker` se autocompleta desde la unidad (writer diario + scripts.
    backfill_fci_ticker); si acá aparece uno con ticker vacío es que la unidad no
    matchea el formato CAFCI. El `emisor` se carga a mano (Manager → Assets)."""
    rows = _q(
        "SELECT unidad, "
        "       (ticker IS NULL OR trim(ticker) = '') AS sin_ticker, "
        "       (emisor IS NULL OR trim(emisor) = '') AS sin_emisor "
        "FROM portafolio.assets "
        "WHERE cartera IN ('FCI', 'CARTERA FCI') "
        "  AND ((ticker IS NULL OR trim(ticker) = '') "
        "       OR (emisor IS NULL OR trim(emisor) = '')) "
        "ORDER BY unidad")
    out: list[dict] = []
    for r in rows:
        faltan = [c for c, v in (("ticker", r["sin_ticker"]), ("emisor", r["sin_emisor"])) if v]
        out.append({"key": r["unidad"], "detalle": f"{r['unidad']}: FCI sin {' y '.join(faltan)}"})
    return out


def _chk_titulos_sin_flujo() -> list[dict]:
    """Bonos que la casa TIENE HOY y que no tienen cronograma de flujos cargado.

    Esto existía como un botón en Manager → VALIDACIONES («Títulos sin flujo»),
    o sea que solo se enteraba el que se acordaba de apretarlo. Pasa a control
    (2026-08-19, pedido del user: *«que ni haga falta decirle que hay algo
    mal»*): así se re-verifica solo todos los días, entra al agente con su
    historial, y lo que se resuelve desaparece de la lista sin que nadie lo
    marque.

    **Solo los que están EN CARTERA.** El conciliador completo trae también los
    que no tenemos, y eso convierte la lista en un catálogo — 300 filas que
    nadie mira. Un bono sin flujo que no tenemos no cuesta nada hoy; uno que
    tenemos **no valúa**, y eso sí es plata mal contada.

    Import de api/services permitido: misma excepción documentada que los jobs
    de precompute (jobs/CLAUDE.md).
    """
    from api.services.acreencias import titulos_sin_flujo
    return [{
        "key": str(t.get("unidad") or t.get("ticker")),
        "detalle": (f"{t.get('ticker') or t.get('unidad')}: sin flujo en "
                    f"{t.get('fuente') or 'ninguna fuente'} — {t.get('motivo') or ''}"
                    f" (cartera {t.get('cartera') or '?'})").strip(),
    } for t in titulos_sin_flujo() if t.get("en_cartera")]


def _chk_rf_valuada_x1() -> list[dict]:
    """Renta fija (cartera HD/DL/ARS) cuya valuación en el ÚLTIMO snapshot de
    tenencia quedó SIN dividir por 100 (cociente valuacion/(precio×cantidad)≈1).
    Es la firma del bug LEDE (2026-07-24: Aunesa inventó el tipoTitulo 'LEDE',
    no estaba en TIPOS_DIVISOR_100 → la S13N6 quedó ×100 en el AuM). Si Aunesa
    inventa OTRO tipo nuevo, aparece acá al día siguiente. Acción: sumar el
    tipo nuevo a las listas de divisor y corregir la historia de tenencia."""
    rows = _q(
        "SELECT unidad, count(*) AS n, sum(valuacion) AS val "
        "FROM portafolio.tenencia "
        "WHERE fecha = (SELECT max(fecha) FROM portafolio.tenencia) "
        "  AND cartera IN ('HD','DL','ARS') "
        "  AND precio <> 0 AND cantidad <> 0 "
        "  AND abs(valuacion / (precio * cantidad) - 1) < 0.05 "
        "GROUP BY unidad ORDER BY sum(valuacion) DESC")
    return [{
        "key": r["unidad"],
        "detalle": (f"{r['unidad']}: RF valuada SIN ÷100 en {r['n']} cuenta(s) "
                    f"(${float(r['val'] or 0):,.0f}) — ¿tipoTitulo nuevo de Aunesa?"),
    } for r in rows if r.get("unidad")]


def _chk_comitentes_sin_nivel1() -> list[dict]:
    """Comitentes Activas sin nivel_1 → fuera de la segmentación (Clientes →
    Segmentación) y de los filtros madre. PRIVADO (keys = id_cuenta)."""
    rows = _q(
        "SELECT id_cuenta FROM clientes.comitentes "
        "WHERE estado = 'Activa' AND (nivel_1 IS NULL OR nivel_1 = '') "
        "ORDER BY id_cuenta")
    return [{"key": str(r["id_cuenta"]), "detalle": f"cuenta {r['id_cuenta']}: sin nivel_1"}
            for r in rows if r.get("id_cuenta")]


def _chk_simbolos_cuarentena() -> list[dict]:
    """Símbolos en cuarentena: ROFEX los rechazó ("Product don't exist") y el WS
    los excluye de las suscripciones (core/simbolos_cuarentena). La causa de
    fondo suele ser un ticker mal cargado o un bono vencido en el master —
    corregirlo ahí es el fix definitivo. Se auto-resuelven si ROFEX los vuelve
    a aceptar en la ventana de reintento (7d)."""
    rows = _q(
        "SELECT ticker, motivo, rechazos, last_seen::date AS ultimo "
        "FROM mercado.simbolos_cuarentena "
        "WHERE last_seen >= now() - interval '7 days' ORDER BY ticker")
    return [{
        "key": r["ticker"],
        "detalle": (f"{r['ticker']}: {r['motivo']} — {r['rechazos']} rechazo(s), "
                    f"último {r['ultimo']}"),
    } for r in rows if r.get("ticker")]


def _chk_contrapartes_pendientes() -> list[dict]:
    """Conciliador de contrapartes (= botón "Solicitar cuentas" de Manager):
    cuentas de Aunesa que matchean una contraparte conocida y NO están dadas de
    alta en clientes.contrapartes. PRIVADO (keys = cuenta; detalle queda en DB).
    Import de api/services permitido: misma excepción documentada que los jobs
    de precompute (jobs/CLAUDE.md)."""
    from api.services.contrapartes_seg import reconciliar
    res = reconciliar(limit=500)
    return [{
        "key": str(c.get("cuenta")),
        "detalle": (f"cuenta {c.get('cuenta')} '{c.get('denominacion', '')}' → "
                    f"sugerida: {c.get('contraparte_sugerida')} ({c.get('segmento_sugerido')})"),
    } for c in res.get("candidatos", []) if c.get("cuenta")]


def _chk_ops_sin_tc() -> list[dict]:
    """Boletos ARS sin `mep` en un día que SÍ tiene cotización en el feed: la vista
    OPERACIONES en modo DOLARIZAR no los puede convertir con su snapshot.

    Es la firma del incidente 2026-08-10 (jobs/fci_bilateral insertaba sin `mep`
    → el mercado FCI Bilateral tenía volumen ARS real y ~0 al dolarizar). El
    service ya cae al TC del día, así que hoy no MIENTE, pero un boleto sin
    snapshot sigue siendo una fuente de ingesta rota que hay que arreglar.

    Agrupa por (mercado, mes) → pocas keys y se auto-resuelven al rellenarse.
    Lo anterior al inicio del feed queda afuera: ahí no hay TC que estampar."""
    rows = _q(
        "SELECT COALESCE(NULLIF(mercado, ''), '(sin mercado)') AS mercado, "
        "       to_char(concertacion, 'YYYY-MM') AS mes, count(*) AS n "
        "FROM operaciones.operaciones "
        "WHERE anulado_en IS NULL AND moneda = 'ARS' AND (mep IS NULL OR mep = 0) "
        "  AND concertacion >= (SELECT min(timestamp)::date FROM valuaciones.dolar "
        "                       WHERE mep IS NOT NULL AND mep > 0) "
        "GROUP BY 1, 2 ORDER BY 2 DESC, 3 DESC")
    return [{
        "key": f"{r['mercado']}::{r['mes']}",
        "detalle": (f"{r['mercado']} {r['mes']}: {r['n']} boleto(s) ARS sin TC "
                    f"— la ingesta no estampó `mep`"),
    } for r in rows]


def _chk_patas_sin_precio() -> list[dict]:
    """Bonos del master cuyo SÍMBOLO no tiene precio: nadie se lo está pidiendo.

    ⚠️ **La pregunta que ninguna de nuestras tablas podía contestar** (incidente
    2026-08-19). `mercado.timesales` y `market_snapshot` los escribe el motor
    **solo para lo que suscribe**, así que «no tiene precio» nunca significó «no
    cotiza»: significaba «no lo estamos escuchando». Se midió con esas tablas y
    dieron 0 por construcción — el AO29D resultó cotizar a USD 90,76 en cuanto se
    lo pidió.

    Por eso este control NO concluye nada sobre liquidez: solo señala que hay un
    símbolo del master que nadie está pidiendo. La respuesta se consigue
    pidiéndolo, que es lo que hace la acción `mercado.pedir_pata`.
    """
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.ticker, c.instrumento, c.curva
            FROM mercado.curvas c
            LEFT JOIN mercado.market_snapshot s ON s.ticker = c.instrumento
            LEFT JOIN mercado.adhoc_subscriptions a
                   ON a.ticker = c.instrumento AND a.expires_at > now()
            WHERE c.instrumento IS NOT NULL
              AND (s.last_price IS NULL OR s.last_price = 0)
              AND a.ticker IS NULL          -- ya pedido = no hace falta proponerlo
              -- Y que Primary lo conozca: pedir algo que no existe no informa nada.
              AND EXISTS (SELECT 1 FROM mercado.especies e
                           WHERE e.simbolo = c.instrumento)
            ORDER BY c.ticker
        """)
        return [{"key": r[0], "ticker": r[0], "simbolo": r[1], "curva": r[2],
                 "detalle": f"«{r[1]}» no tiene precio y nadie lo está pidiendo"}
                for r in cur.fetchall()]


def _chk_patas_dolar_sin_pedir() -> list[dict]:
    """Bonos de curva USD que cotizan por su pata en PESOS teniendo una pata en
    dólares sembrada **que nadie está pidiendo**.

    Es el hermano nocturno del hallazgo `cotiza_en_pesos`, y existe por lo que el
    user marcó el 2026-08-19: *«no ofrece una solución o algo, nada»*. El hallazgo
    describía la situación y se terminaba ahí.

    ⚠️ **No propone cambiar el master.** Apuntar `curvas.instrumento` a la otra
    pata exige reiniciar `motor_rofex`, y eso no se ve hasta la noche (§0.v). Lo
    que este control habilita es el paso ANTERIOR y el que sí se ve en el acto:
    **escuchar** esa pata, para saber si opera. Sin ese dato, el reinicio sería a
    ciegas — y apuntar el master a una pata que tampoco cotiza es cambiar un
    problema por otro.

    Solo mira las patas **ya sembradas**: las que están únicamente en el catálogo
    de Primary hay que sembrarlas primero, y eso lo hace la puerta del agente
    (`agente.pata`) con el bono a la vista, no un cron.

    ⚠️⚠️ **EL SQL SOLO PRESELECCIONA. QUIÉN DECIDE ES `av_agent_pata.explicar`**
    (2026-08-22, §0.cf). Este control decía **133** y el botón arregló **16**: los
    otros 117 contestaron *«ya la escuchamos»*. No era el botón — era que la
    misma pregunta («¿alguien pide la pata en dólares de este bono?») estaba
    contestada en DOS lugares con criterios distintos:

      · **Qué pata es.** Acá se elegía por `ORDER BY e.simbolo`, o sea
        ALFABÉTICO, que devuelve el **cable** (`BPA7C` < `BPA7D`). La acción usa
        `core.especies.mejor` (MEP sobre cable, 24hs sobre CI). Miraban dos
        símbolos distintos del mismo bono. Ese bug ya se había arreglado en la
        puerta y en el diag —está escrito en `_elegir`— y **este control era el
        tercer lugar que nadie tocó**.
      · **Qué es «que nadie la pida».** Acá era `adhoc_subscriptions IS NULL`.
        Pero el motor la puede estar suscribiendo desde el master o desde el
        universo de portfolio **sin ningún adhoc**, así que estar en
        `market_snapshot` también cuenta como escucharla. Además el `LEFT JOIN`
        no distinguía «no está en el snapshot» de «está con precio 0» — que son
        los dos estados que el AO29 enseñó a separar (§0.v).

    Un control que cuenta 133 y arregla 16 es peor que no tener el control: el
    número no dice cuánto trabajo hay. Ahora el SQL trae los CANDIDATOS y el
    veredicto lo da `explicar()`, la misma función que corre al apretar el
    botón — así el control no puede volver a contar algo que la acción no toca.

    El costo está MEDIDO, no estimado: la corrida del 2026-08-22 llamó a
    `explicar()` 133 veces en `aplicar` y terminó sin problema. Es un cron
    nocturno.
    """
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.ticker, e.simbolo, c.curva
            FROM mercado.curvas c
            JOIN mercado.especies e
                 ON upper(e.ticker) = upper(c.ticker) AND upper(e.moneda) = 'USD'
            -- el símbolo que el master usa hoy TIENE precio (si no, el caso es
            -- `patas_sin_precio`, que es otro control y otra acción)
            JOIN mercado.market_snapshot s
                 ON s.ticker = c.instrumento AND s.last_price > 0
            LEFT JOIN mercado.market_snapshot sd ON sd.ticker = e.simbolo
            LEFT JOIN mercado.adhoc_subscriptions a
                   ON a.ticker = e.simbolo AND a.expires_at > now()
            WHERE upper(coalesce(c.moneda_eje, '')) = 'USD'
              AND e.simbolo <> c.instrumento
              AND (sd.last_price IS NULL OR sd.last_price = 0)
              AND a.ticker IS NULL          -- ya pedida = no hace falta proponerla
            ORDER BY c.ticker, e.simbolo
        """)
        candidatos = cur.fetchall()

    # ── EL VEREDICTO LO DA LA ACCIÓN, NO ESTE SQL ────────────────────────────
    from agente import pata as av_agent_pata

    vistos: set[str] = set()
    out = []
    # ⚠️⚠️ **POR QUÉ SE CUENTA EL DESGLOSE Y NO SOLO EL RESULTADO.** Este control
    # pasó de 133 a **0** en una corrida, y *«0 porque no hay nada que hacer»* y
    # *«0 porque me quedé ciego»* se ven EXACTAMENTE IGUAL en la pantalla: los
    # dos son un tilde verde. Es la misma regla que ya rige para la prueba de
    # permisos (§0.s): **si el chequeo no pudo mirar, lo tiene que DECIR — el
    # silencio se lee como un verde.**
    #
    # Con el desglose, el verde queda auditable sin volver a consultar nada:
    # «0 de 133 · 117 ya tienen precio · 16 ya escuchadas». Si mañana dijera
    # «0 de 0», eso es otra cosa completamente distinta y se ve de una.
    conteo: dict[str, int] = {}
    for tk, simbolo, curva in candidatos:
        if tk in vistos:      # una propuesta por bono: la mesa mira el bono
            continue
        vistos.add(tk)
        # `hay_que_pedirla` es el ÚNICO veredicto con trabajo. Los otros son
        # estados legítimos que no tienen botón: ya tiene precio, ya la
        # escuchamos y el mercado no da punta, o el mercado está cerrado y no
        # se puede afirmar nada todavía.
        try:
            d = av_agent_pata.explicar(tk)
        except Exception:
            logger.warning("patas_dolar: no pude evaluar %s", tk, exc_info=True)
            conteo["no_pude"] = conteo.get("no_pude", 0) + 1
            continue
        v = (d.get("veredicto") or "?")
        conteo[v] = conteo.get(v, 0) + 1
        if v != "hay_que_pedirla":
            continue
        # El SÍMBOLO también sale de la acción: si el detector siguiera
        # nombrando el suyo, la fila diría un símbolo y el botón pediría otro.
        simbolo = d.get("pedible") or simbolo
        out.append({"key": tk, "ticker": tk, "simbolo": simbolo, "curva": curva,
                    "detalle": (f"«{tk}» cotiza en pesos y su pata en dólares "
                                f"«{simbolo}» no se la pide nadie")})
    logger.info("patas_dolar: %d con trabajo, de %d bonos evaluados · %s",
                len(out), len(vistos),
                " · ".join(f"{k}={n}" for k, n in sorted(conteo.items())) or "sin datos")
    # **Si no hubo NI UN candidato, el verde no vale.** Un `WHERE` que dejó de
    # matchear —una columna renombrada, un join que se rompió— produce cero
    # candidatos y por lo tanto cero hallazgos, con el mismo tilde verde que
    # «está todo bien». Se avisa fuerte porque es el único caso en que este
    # control no está diciendo nada.
    if not vistos:
        logger.warning("patas_dolar: CERO candidatos — el verde de este control "
                       "no significa nada. ¿Cambió el esquema de curvas/especies?")
    return out


def _chk_dia_sin_dato() -> list[dict]:
    """**EL DÍA QUE EL JOB TENÍA QUE ESCRIBIR Y NO ESTÁ.**

    Nace del 2026-08-20: Aunesa contestó HTTP 500 a las 11:00, `jobs/aum` murió
    y `portafolio.tenencia` se quedó sin el día. El AuM, la Tenencia Valorizada
    y Títulos en Alquiler mostraron el día anterior **sin ningún cartel**.

    ⚠️ **Mira el RESULTADO, no el proceso.** SALUD ya avisa cuando un job falla,
    y eso no alcanza por los dos lados: un job puede reventar al final habiendo
    escrito todo (nada que rehacer) y puede salir en verde sin escribir una fila
    (todo por rehacer). La única pregunta que importa es si la fecha está en la
    tabla, y por eso este control consulta la tabla.

    La lista de jobs y el día que le toca a cada uno viven en
    `agente.rehacer.REHACIBLES` — el mismo lugar del que sale el arreglo, así
    el control y la acción no pueden discrepar sobre qué día falta.
    """
    from agente import rehacer as reh
    return reh.faltantes()


def _chk_patas_equivocadas() -> list[dict]:
    """**El master suscribe una pata y la correcta es OTRA.** El caso BOPREAL.

    Distinto de `patas_dolar_sin_pedir`, que es su hermano y mira otra cosa: aquél
    busca una pata en dólares que nadie escucha; **éste busca el CAMPO MAL
    CARGADO**. `mercado.curvas.instrumento` se llena a mano; la pata que le
    corresponde la decide **la moneda del EJE de la curva**.

    ⚠️⚠️ **EL ÁRBITRO ES `core.especies.pata_para_el_eje` — el MISMO que usa el
    detector — y NO `es_default`** (§0.cq). `es_default` es una COPIA del
    master (`sembrar_especies`: `es_default = simbolo == curvas.instrumento`),
    así que compararla contra el master es comparar el dato consigo mismo. El
    costo se midió el 2026-08-22: **11 bonos** de curva USD suscribiendo su
    pata en PESOS eran INVISIBLES para este control (Primary tenía la default
    en ARS → el JOIN no devolvía fila) mientras el detector del monitor los
    cantaba — dos árbitros para la misma pregunta, y la acción
    `mercado.apuntar_pata` decía «ya no aparece en el control: se resolvió
    solo» sobre bonos que seguían rotos. REGLA #9 de manual.

    `pata_para_el_eje` ya trae el criterio fino adentro: MEP antes que cable
    (el cable NO es el MEP) y 24hs antes que CI; y `None` = «no hay pata para
    ese eje», que no es un problema — una ON hard dollar con una sola especie
    no está cruzada.

    Y el arreglo ya no exige reiniciar el motor (que era lo que frenaba
    automatizarlo): `mercado.apuntar_pata` corrige el campo **y pide la pata** en
    el mismo paso, así el precio entra por el `adhoc_watcher` en 5 s.
    """
    from core import especies as _esp
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.ticker, c.instrumento, c.curva, c.moneda_eje
            FROM mercado.curvas c
            WHERE coalesce(c.moneda_eje, '') <> ''
              AND coalesce(c.instrumento, '') <> ''
            ORDER BY c.ticker
        """)
        bonos = cur.fetchall()
        cur.execute("SELECT upper(ticker), simbolo, especie, plazo "
                    "FROM mercado.especies WHERE coalesce(activa, true)")
        patas: dict[str, list[dict]] = {}
        for tk, sim, esp, plazo in cur.fetchall():
            patas.setdefault(tk or "", []).append(
                {"simbolo": sim, "especie": esp, "plazo": plazo})
    vistos: set[str] = set()
    out = []
    for tk, actual, curva, eje in bonos:
        tku = (tk or "").strip().upper()
        if not tku or tku in vistos:      # una propuesta por bono
            continue
        mejor = _esp.pata_para_el_eje(patas.get(tku) or [], eje)
        sugerido = (mejor or {}).get("simbolo") or ""
        if not sugerido or sugerido == actual:
            continue
        vistos.add(tku)
        out.append({"key": tku, "ticker": tku, "simbolo": actual,
                    "sugerido": sugerido, "curva": curva,
                    "detalle": (f"el master de «{tku}» apunta a «{actual}» y a "
                                f"su eje {str(eje).upper()} le corresponde "
                                f"«{sugerido}»")})
    return out


def _chk_assets_ticker_partido() -> list[dict]:
    """**La ficha del título y su curva se llaman distinto.** El caso PLC5O.

    ⚠️ Medido el 2026-08-22: 2 de 2 eran **un carácter tipeado a mano** —
    `PLC5O`→`PLC50` (la O es un cero) y `S13N6`→`S13B6`. `portafolio.assets` es
    catálogo de la mesa, se carga a mano, así que esto va a volver a pasar.

    Lo que rompe **no es el AuM** (ese join va por `unidad` y sigue andando, que
    es lo que hacía tan confuso el hallazgo viejo): rompe
    `curvas.ticker → assets.ticker → unidad`, el que atribuye la tenencia a su
    CURVA. El bono suma plata y desaparece de flujos, acreencias y renta fija.

    ⚠️ **NO reimplementa el predicado.** Sale de `core.duplicados`, donde el par
    ya está declarado con su árbitro (`ticker_curva_vs_assets`). Escribirlo dos
    veces sería exactamente el problema que ese módulo existe para evitar — y no
    es hipotético: es lo que pasó con `preferencia`, escrita tres veces y
    eligiendo distinto en cada una.
    """
    from core import duplicados
    r = duplicados.una("ticker_curva_vs_assets")
    if not r.get("ok"):
        # «No pude mirar» NO es «no hay». Se canta como una anomalía propia para
        # que el silencio no se lea como un verde.
        return [{"key": "?", "detalle": f"no pude correr el chequeo: "
                                        f"{r.get('error') or 'sin motivo'}"}]
    return [{"key": unidad, "unidad": unidad, "esperado": cod, "actual": tk,
             "detalle": (f"la ficha dice TICKER «{tk or '(vacío)'}» y su unidad "
                         f"dice «{cod}»")}
            for unidad, cod, tk in r.get("filas") or []]


@dataclass(frozen=True)
class Control:
    id: str
    titulo: str
    publico: bool          # True → los keys pueden ir al resumen (tickers/unidades)
    fn: Callable[[], list[dict]]


CONTROLES: list[Control] = [
    Control("patas_sin_precio", "Símbolos del master que nadie está pidiendo", True,
            _chk_patas_sin_precio),
    Control("patas_dolar_sin_pedir",
            "Patas en dólares que nadie pide", True,
            _chk_patas_dolar_sin_pedir),
    Control("dia_sin_dato",
            "El día que un job tenía que escribir y no está", True,
            _chk_dia_sin_dato),
    Control("patas_equivocadas",
            "El master apunta a una pata que no es la default", True,
            _chk_patas_equivocadas),
    Control("assets_ticker_partido",
            "La ficha del título y su curva se llaman distinto", True,
            _chk_assets_ticker_partido),
    Control("forwards_faltantes", "Bonos ausentes de forwards", True, _chk_forwards_faltantes),
    Control("rf_sin_tasa", "Renta fija cotizando sin TEA/TNA", True, _chk_rf_sin_tasa),
    Control("assets_sin_cartera", "Assets sin cartera", True, _chk_assets_sin_cartera),
    Control("unidades_gemelas", "Assets duplicados por espacios en la unidad", True,
            _chk_unidades_gemelas),
    Control("fci_incompletos", "Assets FCI sin ticker/emisor", True, _chk_fci_incompletos),
    Control("titulos_sin_flujo", "Bonos en cartera sin cronograma de flujos", True,
            _chk_titulos_sin_flujo),
    Control("rf_valuada_x1", "Renta fija valuada sin ÷100 (¿tipo nuevo de Aunesa?)", True,
            _chk_rf_valuada_x1),
    Control("simbolos_cuarentena", "Símbolos rechazados por ROFEX (cuarentena)", True,
            _chk_simbolos_cuarentena),
    Control("ops_sin_tc", "Boletos ARS sin tipo de cambio (no se dolarizan)", True,
            _chk_ops_sin_tc),
    Control("comitentes_sin_nivel1", "Comitentes activos sin nivel 1", False,
            _chk_comitentes_sin_nivel1),
    Control("contrapartes_pendientes", "Cuentas de contrapartes sin dar de alta", False,
            _chk_contrapartes_pendientes),
]


# ── Diff + persistencia ──────────────────────────────────────────────────────

def _diff_y_persistir(control_id: str, items: list[dict]) -> dict:
    """Compara las anomalías actuales contra el estado en manager.controles_datos.
    Nuevos → insert/reactivación (first_seen=now); vigentes → last_seen=now;
    ausentes → resuelto_at=now. Devuelve el resumen del control."""
    now = datetime.now(UTC)
    activos_previos = {r["item_key"] for r in _q(
        "SELECT item_key FROM manager.controles_datos "
        "WHERE control_id = %s AND resuelto_at IS NULL", (control_id,))}
    por_key = {it["key"]: it["detalle"] for it in items}
    nuevos = sorted(set(por_key) - activos_previos)
    resueltos = sorted(activos_previos - set(por_key))

    with get_pool().connection() as conn, conn.cursor() as cur:
        if por_key:
            cur.executemany(
                "INSERT INTO manager.controles_datos "
                "(control_id, item_key, detalle, first_seen, last_seen) "
                "VALUES (%(cid)s, %(k)s, %(d)s, %(now)s, %(now)s) "
                "ON CONFLICT (control_id, item_key) DO UPDATE SET "
                "  detalle = EXCLUDED.detalle, last_seen = EXCLUDED.last_seen, "
                # Reaparición de un resuelto = anomalía NUEVA → first_seen se resetea.
                "  first_seen = CASE WHEN manager.controles_datos.resuelto_at IS NOT NULL "
                "               THEN EXCLUDED.first_seen ELSE manager.controles_datos.first_seen END, "
                "  resuelto_at = NULL",
                [{"cid": control_id, "k": k, "d": d, "now": now} for k, d in por_key.items()])
        if resueltos:
            cur.execute(
                "UPDATE manager.controles_datos SET resuelto_at = %s "
                "WHERE control_id = %s AND item_key = ANY(%s) AND resuelto_at IS NULL",
                (now, control_id, resueltos))
        conn.commit()

    # ── Y COMO OBJETOS, con el ciclo común (§0.bg) ──────────────────────────
    #
    # Acá el cierre por ausencia es el caso LIMPIO, y conviene decir por qué:
    # a esta función **solo se llega si `c.fn()` no levantó** (en `main()` está
    # adentro del `try`, y si el control explota se anota en `errores` y no se
    # persiste nada). O sea que una lista vacía significa de verdad «no hay
    # anomalías», y no «no pude mirar» — que es la distinción que costó los dos
    # bugs anteriores (§0.be, §0.bf).
    #
    # Un `origen` por control: cada uno concilia SU universo y no toca el de al
    # lado. Y `_diff_y_persistir` es el único camino de escritura, así que el
    # cron y el botón ↻ CHEQUEAR AHORA dejan exactamente el mismo estado.
    try:
        _espejar_items(control_id, por_key, resueltos)
    except Exception as e:
        logging.getLogger(__name__).warning(
            "controles_datos: no pude espejar %s en items (%s)", control_id, e)

    # Antigüedad del activo más viejo (para que el mensaje diga "roto hace N días").
    viejo = _q("SELECT min(first_seen) AS f FROM manager.controles_datos "
               "WHERE control_id = %s AND resuelto_at IS NULL", (control_id,))
    mas_viejo = viejo[0]["f"] if viejo and viejo[0]["f"] else None
    return {
        "activos": len(por_key), "nuevos": nuevos, "resueltos": resueltos,
        "detalles_nuevos": [por_key[k] for k in nuevos[:20]],
        "dias_mas_viejo": (now - mas_viejo).days if mas_viejo else 0,
    }


def _espejar_items(control_id: str, por_key: dict, resueltos: list[str]) -> None:
    """Las anomalías de un control, como objetos con ciclo de vida.

    Los controles NO escriben en el agente. Lo hacía el agente viejo y era una
    de sus SEIS puertas escribiendo estado.
    """
    # ⚠️ **LOS CONTROLES YA NO ESPEJAN HALLAZGOS** (AGENT 2.0). Antes cada
    # control escribía en la memoria del agente por su cuenta: era una de las
    # seis puertas que escribían estado. Ahora el agente los LEE a través de la
    # habilidad `salud`, que es la única que los convierte en hallazgos.
    return {"ok": True, "espejado": 0}



# ── Render resumen (stdout / log del job) ────────────────────────────────────

def _md(s) -> str:
    import re
    return re.sub(r"([_*\[`])", r"\\\1", str(s))


def render_resumen(resultados: dict[str, dict], errores: dict[str, str],
                   lectura_ai: str | None) -> str:
    total_nuevos = sum(len(r["nuevos"]) for r in resultados.values())
    icono = "🟢" if total_nuevos == 0 and not errores else ("🔴" if errores else "🟠")
    lines = [f"{icono} *Controles de datos ACAQuant* — {datetime.now(UTC):%Y-%m-%d %H:%M} UTC"]
    for c in CONTROLES:
        r = resultados.get(c.id)
        if r is None:
            lines.append(f"  ⚪ {_md(c.titulo)}: sin datos ({_md(errores.get(c.id, '?'))})")
            continue
        marca = "✅" if r["activos"] == 0 else "⚠️"
        extra = []
        if r["nuevos"]:
            extra.append(f"▲{len(r['nuevos'])} nuevos")
        if r["resueltos"]:
            extra.append(f"▼{len(r['resueltos'])} resueltos")
        if r["activos"] and r["dias_mas_viejo"] >= 2:
            extra.append(f"el más viejo hace {r['dias_mas_viejo']}d")
        sufijo = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"  {marca} {_md(c.titulo)}: {r['activos']}{sufijo}")
        # Detalle SOLO de lo nuevo, y SOLO si el control es público (regla del canal).
        if c.publico and r["nuevos"]:
            for d in r["detalles_nuevos"][:8]:
                lines.append(f"      • {_md(d)}")
            if len(r["nuevos"]) > 8:
                lines.append(f"      … +{len(r['nuevos']) - 8} más")
        elif not c.publico and r["nuevos"]:
            lines.append("      (detalle en Manager → controles)")
    if lectura_ai:
        lines.append("")
        lines.append(f"🧠 *Lectura:* {_md(lectura_ai)}")
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    dry = "--dry" in sys.argv

    from core.job_runs import JobRunLogger
    with JobRunLogger("controles_datos") as jr:
        _ensure()
        resultados: dict[str, dict] = {}
        errores: dict[str, str] = {}
        for c in CONTROLES:
            try:
                items = c.fn()
                if dry:
                    resultados[c.id] = {"activos": len(items),
                                        "nuevos": [i["key"] for i in items],
                                        "detalles_nuevos": [i["detalle"] for i in items[:20]],
                                        "resueltos": [], "dias_mas_viejo": 0}
                else:
                    resultados[c.id] = _diff_y_persistir(c.id, items)
                jr.set_stat(c.id, resultados[c.id]["activos"])
            except Exception as e:
                errores[c.id] = f"{type(e).__name__}: {e}"
                jr.error(f"{c.id}: {errores[c.id]}")

        # Contexto extra para la lectura AI: fallas de jobs de las últimas 24h.
        try:
            fallas_jobs = _q(
                "SELECT tipo, status, started_at::text AS started_at "
                "FROM manager.job_runs WHERE started_at >= now() - interval '24 hours' "
                "AND status <> 'ok' ORDER BY started_at DESC LIMIT 20")
        except Exception:
            fallas_jobs = []

        # ⚠️ **LA LECTURA CON IA SE DIO DE BAJA** (2026-08-20). `core/ai_resumen`
        # se borró el 2026-08-19 con el copiloto (§0.k) y esta línea quedó
        # importándolo: el job venía MURIENDO todos los días con
        # `ModuleNotFoundError` DESPUÉS de correr los 20 controles — así que
        # calculaba todo y no persistía ni avisaba nada.
        #
        # No se reemplaza por otra IA: rige la regla de §0.k — *una tarea de IA
        # existe solo si alguien lee su salida*, y este resumen no lo leía nadie.
        # `fallas_jobs` se deja porque va en el texto, que sí se imprime.
        lectura = (f"{len(fallas_jobs)} job(s) fallaron en las últimas 24 h"
                   if fallas_jobs else None)
        jr.set_stat("jobs_fallados_24h", len(fallas_jobs))

        msg = render_resumen(resultados, errores, lectura)
        print(msg)
        if dry:
            print("\n[--dry: no se persiste]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
