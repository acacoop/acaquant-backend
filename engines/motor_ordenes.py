"""Motor de órdenes — escucha execution reports y persiste el ciclo de vida.

Es proceso aparte (systemd unit propio). El API tiene su propia sesión
pyRofex liviana para REST `send_order`/`cancel_order`. Acá vivimos el
WS con `order_report_subscription` y persistimos cada ER que llega del
broker, así el frontend lee el estado actualizado de la base (no espera
respuesta sincrónica del broker).

Flujo:
  1. Inicializar sesión pyRofex con WS + handler de order_report (cuenta
     master, nuestras propias órdenes).
  2. Recovery on start: leer `Operaciones.OrdenesLive` con estado != FINAL,
     consultar `pyRofex.get_all_orders_status()` y reconciliar (cierra
     órdenes que ya completaron mientras el motor estaba caído, marca
     huérfanas las que el broker no conoce).
  3. Órdenes del día (2026-09-17): en thread aparte, backfill REST +
     suscripción de TODA la ALyC (`clientes.cuentas`) sobre esta MISMA
     sesión WS — ver sección "ÓRDENES DEL DÍA" más abajo.
  4. Loop principal: el handler corre en thread del WS, este loop solo
     hace heartbeat y maneja cierre limpio.

⚠️ Solo puede haber UNA sesión WS por usuario ROFEX — por eso "Órdenes del
día" NO abre un proceso/login nuevo, suma suscripciones sobre esta sesión
(pyRofex reparte cada mensaje a TODOS los handlers, sin filtrar por cuenta;
ver `_es_nuestra` para el freno que evita que eso contamine `ordenes_live`).

Colecciones:
  Operaciones.OrdenesLive: SOLO nuestras órdenes (las que este sistema mandó).
    {
      _id, cl_ord_id, ws_cl_ord_id, account, ticker, side, order_type,
      tif, size, price, status,
      cum_qty, leaves_qty, avg_px, last_px, last_qty,
      reject_reason, actor_email,
      created_at, updated_at, last_er_ts
    }
    upsert por cl_ord_id (last write wins).

  Operaciones.OrdenesAudit: SOLO nuestras órdenes (audit trail).
    {
      _id, ts, kind, cl_ord_id, ws_cl_ord_id, account, actor_email,
      payload  # ER crudo o dict del request
    }
    append-only. `kind` ∈ SEND_REQUEST | SEND_ERROR | CANCEL_REQUEST |
    CANCEL_ERROR | EXECUTION_REPORT | RECOVERY.

  operaciones.ordenes_dia: TODA la ALyC, orderReport crudo del broker, sin
    normalizar (para /api/operar/ordenes-dia). Ver sql/schema.sql.

Ejecutar:
    python -m engines.motor_ordenes
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from datetime import UTC, datetime
from typing import Any

import pyRofex
from dotenv import load_dotenv

# Cargar .env antes que core/rofex_orders_session lea ROFEX_ORDERS_ENV y demás.
load_dotenv()

from core.logs import configurar  # noqa: E402
from core.rofex_orders_session import (  # noqa: E402
    cerrar_ws,
    inicializar_para_motor,
)

# El formato (con NIVEL) vive en core/logs — ver `AGENT.md` §0.ac.
configurar()
logger = logging.getLogger("MotorOrdenes")

# Estados que consideramos terminales — no hace falta re-fetchearlos.
ESTADOS_FINALES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}

# Heartbeat del loop principal (no afecta latencia de ER).
HEARTBEAT_S = 5
# Escritura de heartbeat para que /manager → DIAG vea que el motor está vivo.
# Sin esto, motor_ordenes no figura porque solo escribe cuando llega un ER
# (puede pasar horas sin actividad).
HEARTBEAT_DB_S = 30

_running = True
_lock = threading.Lock()


def _handle_signal(sig, frame):
    global _running
    logger.info("Señal de cierre recibida, apagando motor de órdenes.")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────────────────────────────────────


def _audit(kind: str, *, cl_ord_id: str | None = None,
           ws_cl_ord_id: str | None = None, account: str | None = None,
           actor_email: str | None = None, payload: dict | None = None) -> None:
    ts = datetime.now(UTC)
    # SQL-native (decomiso 2026-06-29): append a operaciones.ordenes_audit, sin Mongo.
    from core import pg_mirror
    pg_mirror.append_native("operaciones.ordenes_audit", [{
        "ts": ts, "kind": kind, "cl_ord_id": cl_ord_id, "account": account,
        "actor_email": actor_email,
        "data": pg_mirror.doc_iso({"ws_cl_ord_id": ws_cl_ord_id, "payload": payload or {}}),
    }])


def _es_nuestra(cl_ord_id: str) -> bool:
    """True si YA existe una fila para este cl_ord_id en ordenes_live.

    Hace falta desde que el motor se suscribe a las cuentas comitentes para
    "Órdenes del día" (`_sincronizar_ordenes_dia`): pyRofex reparte CADA
    mensaje a TODOS los handlers registrados sobre la conexión (confirmado en
    `pyRofex/clients/websocket_rfx.py::on_message`, no filtra por cuenta), así
    que este mismo handler pasa a recibir también órdenes de terminales
    ajenas (ISV_MATRIZ4, otros operadores). Sin este freno, `ordenes_live`
    —que hoy es "lo que ESTE sistema mandó"— se llenaría de filas de
    terceros, y `_maybe_dispatch_bracket_exit` quedaría mirando ese ruido.

    `api/services/ordenes.py::send_order` inserta la fila ANTES de tocar al
    broker, así que todo cl_ord_id nuestro ya tiene fila cuando llega el
    primer ER. Uno ajeno nunca la tiene → se ignora acá (sigue yendo a
    `operaciones.ordenes_dia`, que es la tabla pensada para "todos")."""
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM operaciones.ordenes_live WHERE cl_ord_id = %s", (cl_ord_id,))
        return cur.fetchone() is not None


def _upsert_live_from_er(rep: dict[str, Any]) -> bool:
    """Mapea un orderReport del broker a la forma normalizada y hace upsert.

    Solo para órdenes YA trackeadas acá (ver `_es_nuestra`) — no crea filas
    nuevas a partir de ER de cuentas/terminales ajenas. Devuelve True si
    procesó (para que el handler decida si también audita/loguea como propia)."""
    cl_ord_id = rep.get("clOrdId")
    if not cl_ord_id:
        # Sin clOrdId no podemos identificar la orden — al audit y listo.
        return False
    if not _es_nuestra(cl_ord_id):
        return False

    instrument = rep.get("instrumentId") or {}
    ticker = instrument.get("symbol") or rep.get("symbol") or ""

    now = datetime.now(UTC)
    doc_set = {
        "ws_cl_ord_id": rep.get("wsClOrdId"),
        "account": (rep.get("accountId") or {}).get("id") if isinstance(rep.get("accountId"), dict) else rep.get("accountId"),
        "ticker": ticker,
        "side": rep.get("side"),
        "order_type": rep.get("ordType"),
        "tif": rep.get("timeInForce"),
        "size": rep.get("orderQty"),
        "price": rep.get("price"),
        "status": rep.get("status"),
        "cum_qty": rep.get("cumQty"),
        "leaves_qty": rep.get("leavesQty"),
        "avg_px": rep.get("avgPx"),
        "last_px": rep.get("lastPx"),
        "last_qty": rep.get("lastQty"),
        "reject_reason": rep.get("text") if rep.get("status") in ("REJECTED", "CANCELLED") else None,
        "updated_at": now,
        "last_er_ts": rep.get("transactTime") or now,
    }
    # `created_at` solo en insert (no se pisa).
    # SQL-native (decomiso 2026-06-29): read-modify-write a operaciones.ordenes_live. El
    # estado de la orden es INCREMENTAL (cada ER pisa solo sus campos no-null) → leemos el
    # doc actual, mergeamos y reescribimos, preservando `created_at` (como el $setOnInsert
    # de Mongo). Corre bajo `_lock` (single-thread) → no hay race read→write. `created_at`
    # vive en el jsonb (el read-side filtra /dia por data->>'created_at').
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    from core import pg_mirror
    from core.postgres import get_pool
    nuevos = {k: v for k, v in doc_set.items() if v is not None}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM operaciones.ordenes_live WHERE cl_ord_id = %s", (cl_ord_id,))
        row = cur.fetchone()
        prev = (row["data"] if row else None) or {}
        merged = {**prev, **nuevos, "cl_ord_id": cl_ord_id}
        merged.setdefault("created_at", now.isoformat())  # solo en el 1er insert
        cur.execute(
            "INSERT INTO operaciones.ordenes_live (cl_ord_id, account, ticker, estado, updated_at, data) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (cl_ord_id) DO UPDATE SET "
            "account = EXCLUDED.account, ticker = EXCLUDED.ticker, estado = EXCLUDED.estado, "
            "updated_at = EXCLUDED.updated_at, data = EXCLUDED.data",
            (cl_ord_id, merged.get("account"), merged.get("ticker"), merged.get("status"),
             now, Jsonb(pg_mirror.doc_iso(merged))))
        conn.commit()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# ÓRDENES DEL DÍA (OPERAR) — TODA la ALyC, cuenta por cuenta, misma sesión WS
# ─────────────────────────────────────────────────────────────────────────────
# Nace del discovery 2026-09-17 (`scripts/diag_rofex_crudo.py`): el broker solo
# expone las órdenes de una cuenta a la vez (`rest/order/all?accountId=X`), la
# cuenta master NO ve las de las comitentes, y las ejecuciones no son un
# endpoint aparte — son los mismos orderReport con `lastQty > 0`. Acá se
# persiste el orderReport CRUDO (sin normalizar) de cualquier cuenta suscripta,
# para que /operar → "Órdenes del día" lo lea tal cual vino del broker.


def _parse_transact_time(s: Any) -> datetime | None:
    """`transactTime` del broker viene FIX-style: 'YYYYMMDD-HH:MM:SS.mmm±HHMM'
    (ej. '20260917-12:20:38.082-0300'). None si no vino o no matchea el formato
    (no tiramos la orden entera por esto — se guarda igual con transact_time NULL,
    el jsonb `data` crudo siempre queda completo)."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y%m%d-%H:%M:%S.%f%z")
    except ValueError:
        return None


def _upsert_ordenes_dia(rep: dict[str, Any]) -> None:
    """Upsert del orderReport CRUDO en `operaciones.ordenes_dia`, para
    CUALQUIER cuenta (nuestra o comitente) — a diferencia de `ordenes_live`,
    acá no hay filtro de pertenencia. PK broker (account, order_id): a
    diferencia de `cl_ord_id` (que solo generamos nosotros), `orderId` lo pone
    el broker y existe también para órdenes de terceros."""
    order_id = rep.get("orderId")
    account_raw = rep.get("accountId")
    account = account_raw.get("id") if isinstance(account_raw, dict) else account_raw
    if not order_id or not account:
        return  # sin identificar cuenta+orden no hay PK posible

    transact_time = _parse_transact_time(rep.get("transactTime"))
    fecha = (transact_time or datetime.now(UTC)).date()
    instrument = rep.get("instrumentId") or {}

    from psycopg.types.json import Jsonb

    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operaciones.ordenes_dia "
            "(account, order_id, fecha, cl_ord_id, proprietary, symbol, price, "
            " order_qty, ord_type, side, transact_time, avg_px, last_px, last_qty, "
            " cum_qty, status, originating_username, updated_at, data) "
            "VALUES (%(account)s, %(order_id)s, %(fecha)s, %(cl_ord_id)s, %(proprietary)s, "
            " %(symbol)s, %(price)s, %(order_qty)s, %(ord_type)s, %(side)s, "
            " %(transact_time)s, %(avg_px)s, %(last_px)s, %(last_qty)s, %(cum_qty)s, "
            " %(status)s, %(originating_username)s, now(), %(data)s) "
            "ON CONFLICT (account, order_id) DO UPDATE SET "
            "fecha = EXCLUDED.fecha, cl_ord_id = EXCLUDED.cl_ord_id, "
            "proprietary = EXCLUDED.proprietary, symbol = EXCLUDED.symbol, "
            "price = EXCLUDED.price, order_qty = EXCLUDED.order_qty, "
            "ord_type = EXCLUDED.ord_type, side = EXCLUDED.side, "
            "transact_time = EXCLUDED.transact_time, avg_px = EXCLUDED.avg_px, "
            "last_px = EXCLUDED.last_px, last_qty = EXCLUDED.last_qty, "
            "cum_qty = EXCLUDED.cum_qty, status = EXCLUDED.status, "
            "originating_username = EXCLUDED.originating_username, "
            "updated_at = now(), data = EXCLUDED.data",
            {
                "account": str(account), "order_id": str(order_id), "fecha": fecha,
                "cl_ord_id": rep.get("clOrdId"), "proprietary": rep.get("proprietary"),
                "symbol": instrument.get("symbol") or rep.get("symbol"),
                "price": rep.get("price"), "order_qty": rep.get("orderQty"),
                "ord_type": rep.get("ordType"), "side": rep.get("side"),
                "transact_time": transact_time, "avg_px": rep.get("avgPx"),
                "last_px": rep.get("lastPx"), "last_qty": rep.get("lastQty"),
                "cum_qty": rep.get("cumQty"), "status": rep.get("status"),
                "originating_username": rep.get("originatingUsername"),
                "data": Jsonb(rep),
            },
        )
        conn.commit()


# Ventana para decidir "esta cuenta opera": el negocio real de los últimos N
# días corridos. Es la misma señal que usa el tablero comercial para decir si
# un cliente está activo — no una heurística nueva.
DIAS_ACTIVIDAD = 30


def _cuentas_prioritarias() -> set[str]:
    """Cuentas con actividad reciente: las que hay que cubrir PRIMERO.

    MEDIDO 2026-09-18: el barrido plano de las 1743 cuentas confirmadas
    tardaba ~43 minutos (≈1.4 s por cuenta: `get_all_orders_status` REST +
    throttle), recorridas en orden de `id_cuenta`. Consecuencias reales:
    (a) el 2026-09-17 el sync arrancó 19:42 UTC y el cron paró el motor a
    las 20:05 **sin haber terminado**; (b) una cuenta que opera a las 10:35
    podía no estar suscripta hasta 40 minutos después, según dónde cayera
    alfabéticamente su número. Es exactamente lo que había que evitar: las
    que operan son pocas y estaban esperando detrás de ~1.600 que no operan.

    Dos fuentes que se suman:
      - `operaciones.negocio_movimientos` (negocio real desde Aunesa,
        últimos `DIAS_ACTIVIDAD` días) — quién efectivamente operó;
      - `operaciones.ordenes_dia` de la ventana retenida — auto-aprendizaje:
        si mandó una orden ayer, hoy arranca priorizada aunque el negocio
        todavía no haya liquidado.

    Si la consulta falla, devuelve un set vacío: el sync degrada al orden
    plano de antes (más lento, pero funciona) en vez de no correr."""
    from core.postgres import get_pool
    out: set[str] = set()
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT id_cuenta FROM operaciones.negocio_movimientos "
                "WHERE fecha >= current_date - %s::int AND id_cuenta IS NOT NULL "
                "AND anulado_en IS NULL",
                (DIAS_ACTIVIDAD,),
            )
            out |= {str(r[0]) for r in cur.fetchall() if r[0]}
            cur.execute("SELECT DISTINCT account FROM operaciones.ordenes_dia")
            out |= {str(r[0]) for r in cur.fetchall() if r[0]}
    except Exception as e:
        logger.warning("Órdenes del día: no pude calcular cuentas prioritarias "
                       "(sigo con orden plano): %s", e)
    return out


def _cuentas_a_sincronizar(cuenta_master: str) -> list[tuple[str, str, bool]]:
    """Universo de cuentas ROFEX ya CONFIRMADAS offline, sin la master (ya
    suscripta desde `inicializar_para_motor`). Devuelve tripletas
    `(id_cuenta, rofex_account, es_prioritaria)`, **con las prioritarias
    primero** (ver `_cuentas_prioritarias`).

    INCIDENTE 2026-09-17 (dos rounds): la v1 recorría
    `clientes.cuentas` completa (~800 filas, espejo crudo de Aunesa con
    basura histórica); la v2 filtraba por `clientes.comitentes`
    (tipo/estado) pero seguía RESOLVIENDO cada cuenta en caliente contra el
    broker (`get_account_report`, uno por uno) para decidir si suscribir.
    Los dos rounds tiraron abajo el WS de la master (1839) — resultó que el
    problema no es SOLO `order_report_subscription` con una cuenta inválida
    (que ya era grave), sino cualquier interacción con el broker sobre una
    cuenta que no reconoce, incluido el simple PROBING por REST: ROFEX
    cierra la conexión WS ENTERA de ese usuario, no rechaza el pedido
    puntual.

    Fix definitivo: la resolución contra el broker se hace UNA SOLA VEZ,
    OFFLINE, con el motor de órdenes PARADO (`jobs/resolver_cuentas_rofex.py`,
    corrido fuera de rueda por cron) y el resultado queda cacheado en
    `clientes.comitentes.rofex_account/rofex_valida`. Este motor, con la
    sesión en vivo arriba, NUNCA vuelve a llamar a ROFEX para resolver nada:
    solo LEE lo ya confirmado. Si una cuenta nueva no fue resuelta todavía,
    simplemente no aparece acá hasta la próxima corrida del resolver."""
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id_cuenta, rofex_account FROM clientes.comitentes "
            "WHERE tipo IN ('Comitente', 'Propia') AND estado = 'Activa' "
            "AND rofex_valida IS TRUE AND rofex_account IS NOT NULL "
            "ORDER BY id_cuenta"
        )
        rows = cur.fetchall()
    prioritarias = _cuentas_prioritarias()
    out: list[tuple[str, str, bool]] = []
    for id_cuenta, rofex_account in rows:
        if not id_cuenta or str(id_cuenta) == str(cuenta_master):
            continue
        ic, ra = str(id_cuenta), str(rofex_account)
        out.append((ic, ra, ic in prioritarias or ra in prioritarias))
    # Estable: prioritarias primero, cada grupo en su orden original.
    out.sort(key=lambda t: not t[2])
    return out


# Pausa entre cuentas al sumar suscripciones/backfill — nada de ráfaguear al
# broker con centenares de llamadas REST/WS seguidas (mismo espíritu que el
# throttle de `jobs/control_saldos.py`).
#
# Dos ritmos, porque los dos tramos NO cuestan lo mismo: la cuenta
# prioritaria paga un `get_all_orders_status` REST (lo caro, ~1 s) y la fría
# solo manda una suscripción por el WS que YA está abierto (barato). Poner el
# mismo pace a las dos era pagar el precio del tramo caro 1.743 veces.
SYNC_PACE_S = 0.35
SYNC_PACE_FRIA_S = 0.15


# ── EXPERIMENTO MEDIBLE (2026-09-18): ¿el WS ya trae el historial del día? ──
#
# `order_report_subscription(snapshot=...)` viaja al broker como
# `{"type":"os","account":{"id":X},"snapshotOnlyActive":<snapshot>}`. Según la
# doc de Primary y el docstring de pyRofex, el nombre real invierte el sentido
# del nuestro:
#     snapshot=True  → snapshot con SOLO las órdenes ACTIVAS
#     snapshot=False → snapshot con TODAS las del día (ejecutadas, canceladas,
#                      rechazadas)
# Si eso es cierto, el backfill REST (`get_all_orders_status`, ~1 s por cuenta
# y la razón de que el barrido tardara 43 min) es REDUNDANTE: pide por REST lo
# mismo que el WS ya regala por una conexión abierta.
#
# NO está verificado contra NUESTRA cuenta — solo leído en la doc, y con este
# broker ya nos costó caro asumir (incidente 2026-09-17). Se mide primero, en
# el único lugar donde equivocarse no cuesta nada: las cuentas FRÍAS, que hoy
# no reciben ningún backfill. Peor caso = siguen sin recibir nada (idéntico a
# hoy). Mejor caso = queda probado con datos de prod y se borra el REST para
# todas, y el barrido entero baja a ~4 minutos sin dos fases ni priorización.
#
# El contador de abajo es el instrumento de medida: cuenta los reports que
# llegan por WS atribuibles a cuentas frías, que por definición NO pueden
# venir de un backfill REST (no se les hace) → si llegan, vinieron del
# snapshot del WS. Se reporta en el log y se borra cuando el experimento
# concluya.
SNAPSHOT_FRIAS = False  # False = pedile al broker TODAS las del día

# Las frías solas dan evidencia DÉBIL: por definición no operan hace 30 días,
# así que lo más probable es que hoy tampoco y el resultado sea "0 reports",
# que no distingue "el WS no trae historial" de "no había nada que traer".
#
# Sonda concluyente, sobre cuentas que SÍ operan: a las primeras
# `SONDA_PRIORITARIAS` prioritarias se les invierte el orden — primero se
# suscribe con snapshot=False, se escucha `SONDA_ESPERA_S`, y RECIÉN DESPUÉS
# se hace el backfill REST de siempre. Todo lo que llegue en esa ventana vino
# por WS y solo por WS. No se pierde nada: el backfill se hace igual, apenas
# unos segundos más tarde, y el upsert es idempotente.
SONDA_PRIORITARIAS = 15
SONDA_ESPERA_S = 2.0

_medicion_lock = threading.Lock()
_frias_suscriptas: set[str] = set()
_sonda_escuchando: set[str] = set()
_frias_reports: dict[str, int] = {}
_sonda_reports: dict[str, int] = {}


def _contar_si_fria(rep: dict[str, Any]) -> None:
    """Instrumento del experimento `SNAPSHOT_FRIAS` (ver arriba). Barato y en
    memoria: no toca SQL ni puede frenar el camino de plata real."""
    account_raw = rep.get("accountId")
    account = account_raw.get("id") if isinstance(account_raw, dict) else account_raw
    if not account:
        return
    acc = str(account)
    with _medicion_lock:
        if acc in _sonda_escuchando:
            _sonda_reports[acc] = _sonda_reports.get(acc, 0) + 1
        elif acc in _frias_suscriptas:
            _frias_reports[acc] = _frias_reports.get(acc, 0) + 1


def _reportar_experimento_snapshot() -> None:
    """Resultado del experimento, al log. Se llama un rato DESPUÉS de terminar
    el barrido: el broker manda los snapshots de forma asincrónica, medir en
    el instante de la última suscripción daría un falso negativo."""
    with _medicion_lock:
        s_cuentas, s_total = len(_sonda_reports), sum(_sonda_reports.values())
        f_cuentas, f_total = len(_frias_reports), sum(_frias_reports.values())
        suscriptas = len(_frias_suscriptas)
    if s_total or f_total:
        logger.info(
            "Órdenes del día [EXPERIMENTO snapshot=%s]: VEREDICTO = el WS SÍ trae "
            "historial. SONDA (prioritarias, antes de su backfill REST): %d report(s) "
            "de %d cuenta(s). FRÍAS (sin backfill nunca): %d report(s) de %d cuenta(s), "
            "sobre %d suscriptas. → el backfill REST es redundante: se puede borrar y "
            "el barrido baja a ~4 min.",
            SNAPSHOT_FRIAS, s_total, s_cuentas, f_total, f_cuentas, suscriptas)
    else:
        logger.info(
            "Órdenes del día [EXPERIMENTO snapshot=%s]: VEREDICTO = SIN evidencia — "
            "0 reports tanto en la sonda (%d prioritarias escuchadas %.0fs antes de su "
            "backfill) como en las %d frías. Si las prioritarias sondeadas tenían "
            "órdenes hoy, esto indica que el WS NO reproduce historial y el backfill "
            "REST hay que dejarlo. Contrastar con las filas que trajo el backfill.",
            SNAPSHOT_FRIAS, SONDA_PRIORITARIAS, SONDA_ESPERA_S, len(_frias_suscriptas))


def _sincronizar_ordenes_dia(cuenta_master: str) -> None:
    """Al arrancar (una vez, en thread propio): suscribe las cuentas ya
    CONFIRMADAS offline (ver `_cuentas_a_sincronizar`) a la MISMA sesión WS
    abierta por `inicializar_para_motor`, **en dos tramos**:

      1. PRIORITARIAS (operaron en los últimos `DIAS_ACTIVIDAD` días o ya
         aparecen en la ventana de `ordenes_dia`): backfill REST de lo que
         pasó hoy ANTES de que arrancara el proceso (`get_all_orders_status`)
         + suscripción. Son las que importan y se cubren en los primeros
         minutos.
      2. FRÍAS (el resto): SOLO suscripción, sin backfill REST. Una cuenta
         que no operó en 30 días casi seguro tampoco operó hoy antes de que
         arrancara el motor, así que ese REST devolvía vacío — pagarlo 1.600
         veces era lo que estiraba el barrido a ~43 minutos (medido
         2026-09-18) y dejaba a las que sí operan esperando detrás.

    Si una fría opera durante el día, igual queda cubierta: está suscripta y
    el push del WS trae sus reports en vivo.

    Cero llamadas de RESOLUCIÓN contra el broker acá — eso es justo lo que
    causó el incidente 2026-09-17 (ver docstring de `_cuentas_a_sincronizar`).
    Las llamadas son sobre un `rofex_account` YA CONFIRMADO por
    `jobs/resolver_cuentas_rofex.py` fuera de rueda."""
    try:
        cuentas = _cuentas_a_sincronizar(cuenta_master)
    except Exception as e:
        logger.error("Órdenes del día: no pude leer clientes.comitentes: %s", e)
        return
    n_prior = sum(1 for _, _, p in cuentas if p)
    logger.info("Órdenes del día: sincronizando %d cuenta(s) confirmada(s) — "
                "%d prioritaria(s) con backfill, %d fría(s) solo suscripción (+ master %s)",
                len(cuentas), n_prior, len(cuentas) - n_prior, cuenta_master)

    def _backfill(rofex_acc: str) -> None:
        resp = pyRofex.get_all_orders_status(account=rofex_acc)
        if not isinstance(resp, dict) or resp.get("status") != "OK":
            return
        # El broker envuelve cada orden en {"orderReport": {...}}; a veces
        # (según versión) manda el report pelado — se acepta cualquiera.
        for o in resp.get("orders") or []:
            rep = o.get("orderReport", o) if isinstance(o, dict) else o
            if isinstance(rep, dict):
                _upsert_ordenes_dia(rep)

    # Master: ya está suscripta (inicializar_para_motor) — solo falta el
    # backfill de lo que pasó hoy ANTES de que este proceso arrancara.
    t0 = time.monotonic()
    try:
        _backfill(cuenta_master)
        logger.info("Órdenes del día: backfill de la master (%s) OK", cuenta_master)
    except Exception as e:
        logger.warning("Órdenes del día: backfill de la master (%s) falló: %s", cuenta_master, e)

    ok = err = 0
    n_sonda = 0
    fase_fria_anunciada = False
    for i, (id_cuenta, rofex_acc, prioritaria) in enumerate(cuentas, 1):
        if not _running:
            logger.info("Órdenes del día: corte pedido — %d/%d procesadas", i - 1, len(cuentas))
            return
        if not prioritaria and not fase_fria_anunciada:
            fase_fria_anunciada = True
            logger.info("Órdenes del día: prioritarias LISTAS en %.1fs — "
                        "sigo con las frías (solo suscripción)", time.monotonic() - t0)
        try:
            if prioritaria:
                if n_sonda < SONDA_PRIORITARIAS:
                    # Sonda: escuchar por WS ANTES del backfill REST, para que
                    # lo que llegue sea atribuible al WS y nada más. El backfill
                    # se hace igual unas líneas abajo — no se pierde nada.
                    n_sonda += 1
                    with _medicion_lock:
                        _sonda_escuchando.add(rofex_acc)
                    pyRofex.order_report_subscription(account=rofex_acc, snapshot=False)
                    time.sleep(SONDA_ESPERA_S)
                    with _medicion_lock:
                        _sonda_escuchando.discard(rofex_acc)  # congela el conteo
                    _backfill(rofex_acc)
                else:
                    _backfill(rofex_acc)
                    pyRofex.order_report_subscription(account=rofex_acc, snapshot=True)
            else:
                # Fría: sin backfill REST. Se registra ANTES de suscribir para
                # no perder el snapshot, que puede llegar de inmediato.
                with _medicion_lock:
                    _frias_suscriptas.add(rofex_acc)
                pyRofex.order_report_subscription(account=rofex_acc, snapshot=SNAPSHOT_FRIAS)
            ok += 1
        except Exception as e:
            err += 1
            logger.warning("Órdenes del día: cuenta %s (rofex=%s) falló (sigo con el resto): %s",
                           id_cuenta, rofex_acc, e)
        if i % 250 == 0:
            logger.info("Órdenes del día: progreso %d/%d (%.1fs)", i, len(cuentas),
                        time.monotonic() - t0)
        time.sleep(SYNC_PACE_S if prioritaria else SYNC_PACE_FRIA_S)
    logger.info("Órdenes del día: sincronización inicial terminada "
                "(%d ok, %d error, %.1fs)", ok, err, time.monotonic() - t0)

    # Colchón para que lleguen los snapshots que el broker manda asincrónicamente
    # (ver `_reportar_experimento_snapshot`). Estamos en thread propio: dormir
    # acá no frena ni el heartbeat ni el procesamiento de reports.
    for _ in range(120):
        if not _running:
            return
        time.sleep(1)
    _reportar_experimento_snapshot()


def _purgar_ordenes_dia() -> None:
    """Ventana rodante hoy + día hábil anterior: borra filas de fecha < corte.
    `restar_habiles` es pura (holidays.Argentina, no toca la tabla
    `mercado.dias_habiles`) — corre una vez al arrancar el motor, no hace
    falta un job aparte."""
    try:
        from core.calendario import restar_habiles
        corte = restar_habiles(datetime.now(UTC).date(), 1)
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM operaciones.ordenes_dia WHERE fecha < %s", (corte,))
            borradas = cur.rowcount
            conn.commit()
        if borradas:
            logger.info("Órdenes del día: purgadas %d fila(s) anteriores a %s", borradas, corte)
    except Exception as e:
        logger.warning("Órdenes del día: purga falló (no crítico): %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Handler de execution reports (corre en thread del WS)
# ─────────────────────────────────────────────────────────────────────────────


def _make_er_handler():
    def _handler(message: dict) -> None:
        try:
            rep = message.get("orderReport") or {}
            if not rep:
                return
            cl_ord_id = rep.get("clOrdId")
            with _lock:
                nuestra = _upsert_live_from_er(rep)
                if nuestra:
                    _audit(
                        "EXECUTION_REPORT",
                        cl_ord_id=cl_ord_id,
                        ws_cl_ord_id=rep.get("wsClOrdId"),
                        payload=message,
                    )
            # "Órdenes del día": TODA cuenta suscripta, nuestra o ajena — ver
            # `_sincronizar_ordenes_dia`. Separado del `with _lock` de arriba
            # (SQL propio, PK distinta) y en su propio try para que un fallo acá
            # nunca bloquee el camino de plata real (ordenes_live/brackets).
            try:
                _upsert_ordenes_dia(rep)
                _contar_si_fria(rep)
            except Exception as e:
                logger.warning("ordenes_dia upsert falló (cl_ord_id=%s): %s", cl_ord_id, e)
            logger.log(
                logging.INFO if nuestra else logging.DEBUG,
                "ER %s %s %s status=%s lastPx=%s lastQty=%s nuestra=%s",
                cl_ord_id,
                rep.get("side"),
                (rep.get("instrumentId") or {}).get("symbol"),
                rep.get("status"),
                rep.get("lastPx"),
                rep.get("lastQty"),
                nuestra,
            )

            # Hook de brackets: si esta orden es la entrada de un bracket
            # PENDING_ENTRY y llegó a FILLED, disparamos la salida.
            _maybe_dispatch_bracket_exit(rep)
        except Exception as e:
            logger.error("Error procesando ER: %s", e, exc_info=True)
    return _handler


def _maybe_dispatch_bracket_exit(rep: dict[str, Any]) -> None:
    """Si el ER recibido corresponde a la entrada de un bracket en PENDING_ENTRY,
    actúa según el status:

      - FILLED              → manda la salida LIMIT (side opuesto al de la entrada)
                              y marca el bracket EXIT_SENT.
      - REJECTED/CANCELLED/EXPIRED → marca ENTRY_CANCELLED, no hay salida.
      - otros               → no-op, esperamos el próximo ER.

    También maneja el lado de la salida: si llega FILLED para el exit_cl_ord_id
    del bracket, marcamos COMPLETED.
    """
    from core import brackets

    cl_ord_id = rep.get("clOrdId")
    status = rep.get("status")
    if not cl_ord_id or not status:
        return

    # Caso 1: esto es la SALIDA de un bracket → solo actualizamos estado final.
    try:
        bracket_exit = brackets.find_by_exit(cl_ord_id)
    except Exception as e:
        logger.warning("brackets.find_by_exit falló: %s", e)
        bracket_exit = None
    if bracket_exit:
        if status == "FILLED":
            brackets.mark_completed(cl_ord_id)
            logger.info(
                "Bracket %s COMPLETADO (salida %s FILLED)",
                bracket_exit.get("entry_cl_ord_id"), cl_ord_id,
            )
        elif status in brackets.ENTRY_DEAD:
            brackets.mark_exit_rejected(
                bracket_exit.get("entry_cl_ord_id", ""),
                rep.get("text") or status,
            )
            logger.warning(
                "Bracket %s SALIDA %s terminó %s — intervención manual",
                bracket_exit.get("entry_cl_ord_id"), cl_ord_id, status,
            )
        return

    # Caso 2: esto es la ENTRADA de un bracket pendiente.
    try:
        bracket = brackets.find_pending_by_entry(cl_ord_id)
    except Exception as e:
        logger.warning("brackets.find_pending_by_entry falló: %s", e)
        return
    if not bracket:
        return

    if status in brackets.ENTRY_DEAD:
        brackets.mark_entry_dead(cl_ord_id, status)
        logger.info(
            "Bracket %s ENTRY %s terminó %s — no se manda salida",
            cl_ord_id, bracket.get("ticker"), status,
        )
        return

    if status not in brackets.ENTRY_FILLED:
        return  # PARTIALLY_FILLED, NEW, PENDING_NEW, etc. — esperamos más.

    # ── FILLED: disparar la salida ──
    side_exit = "SELL" if bracket["side_entry"] == "BUY" else "BUY"
    logger.info(
        "Bracket %s entrada FILLED — disparando salida %s LIMIT %s x %s @ %s",
        cl_ord_id, side_exit, bracket["ticker"], bracket["size"], bracket["price_exit"],
    )

    try:
        exit_resp = pyRofex.send_order(
            ticker=bracket["ticker"],
            side=pyRofex.Side.SELL if side_exit == "SELL" else pyRofex.Side.BUY,
            size=int(bracket["size"]),
            price=float(bracket["price_exit"]),
            order_type=pyRofex.OrderType.LIMIT,
            time_in_force=getattr(
                pyRofex.TimeInForce, bracket.get("tif", "DAY"), pyRofex.TimeInForce.DAY,
            ),
            account=bracket["account"],
            cancel_previous=False,
        )
    except Exception as e:
        logger.error("Bracket %s falló al enviar salida: %s", cl_ord_id, e, exc_info=True)
        brackets.mark_exit_rejected(cl_ord_id, str(e))
        return

    if not exit_resp or exit_resp.get("status") != "OK":
        reason = (exit_resp or {}).get("description", "broker rechazó la salida")
        logger.error("Bracket %s salida rechazada: %s", cl_ord_id, reason)
        brackets.mark_exit_rejected(cl_ord_id, reason)
        return

    order_blk = exit_resp.get("order") or {}
    exit_cl_ord_id = order_blk.get("clientId") or order_blk.get("clOrdId")
    exit_proprietary = order_blk.get("proprietary")
    if not exit_cl_ord_id:
        brackets.mark_exit_rejected(cl_ord_id, "broker OK pero sin clientId")
        return

    brackets.mark_exit_sent(
        cl_ord_id,
        exit_cl_ord_id=exit_cl_ord_id,
        exit_proprietary=exit_proprietary,
    )
    _audit(
        "BRACKET_EXIT_SENT",
        cl_ord_id=exit_cl_ord_id,
        account=bracket["account"],
        actor_email=bracket.get("actor_email"),
        payload={
            "entry_cl_ord_id": cl_ord_id,
            "exit_cl_ord_id":  exit_cl_ord_id,
            "side_exit":       side_exit,
            "price_exit":      bracket["price_exit"],
            "size":            bracket["size"],
        },
    )
    logger.info(
        "Bracket %s salida ENVIADA (exit_cl_ord_id=%s)",
        cl_ord_id, exit_cl_ord_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Recovery on start
# ─────────────────────────────────────────────────────────────────────────────


def _recovery(_account_master: str) -> None:
    """Reconcilia OrdenesLive contra el broker, agrupando por cuenta REAL
    de la orden (no la del master).

    BUG histórico: antes filtraba `{"account": account_master}` y pedía
    `get_all_orders_status(account=master)`. Pero el master nunca opera
    — opera con cuentas 100/255/805/etc. autorizadas. Resultado: las
    órdenes en esas cuentas quedaban PENDING_NEW para siempre porque
    el recovery no las miraba.

    Ahora:
      1. Lee TODAS las órdenes locales en estado no-final (sin filtrar
         por cuenta).
      2. Agrupa por `account`.
      3. Para cada cuenta, pega `get_all_orders_status(account=X)` y
         reconcilia las que matchean por clOrdId.
      4. Las locales que el broker no conoce → UNKNOWN_LOCAL.
    """
    # SQL-native (decomiso 2026-06-29): órdenes locales no-finales desde operaciones.ordenes_live.
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT cl_ord_id, account FROM operaciones.ordenes_live "
                    "WHERE estado IS NOT NULL AND NOT (estado = ANY(%s))",
                    (list(ESTADOS_FINALES),))
        pendientes = [{"cl_ord_id": r[0], "account": r[1]} for r in cur.fetchall()]
    if not pendientes:
        logger.info("Recovery: sin órdenes pendientes locales — nada que reconciliar.")
        return

    # Agrupar por cuenta. Las que no tienen `account` (caso raro) van a un
    # bucket especial que igual intentamos contra el master por compat.
    por_cuenta: dict[str, set[str]] = {}
    for p in pendientes:
        cid = p.get("cl_ord_id")
        if not cid:
            continue
        acc = str(p.get("account") or _account_master)
        por_cuenta.setdefault(acc, set()).add(cid)

    total = sum(len(v) for v in por_cuenta.values())
    logger.info(
        "Recovery: %d orden(es) pendientes en %d cuenta(s): %s",
        total, len(por_cuenta), list(por_cuenta.keys()),
    )

    vistos: set[str] = set()
    for acc, cl_ord_locales in por_cuenta.items():
        try:
            resp = pyRofex.get_all_orders_status(account=acc)
        except Exception as e:
            logger.error("Recovery acc=%s: get_all_orders_status falló: %s", acc, e)
            continue

        if not resp or resp.get("status") != "OK":
            logger.warning("Recovery acc=%s: respuesta no-OK del broker: %s", acc, resp)
            continue

        for o in resp.get("orders", []):
            rep = o.get("orderReport", o)
            cid = rep.get("clOrdId", "")
            if cid in cl_ord_locales:
                with _lock:
                    _upsert_live_from_er(rep)
                    _audit("RECOVERY", cl_ord_id=cid, account=acc, payload=rep)
                vistos.add(cid)

    todas_locales = set().union(*por_cuenta.values()) if por_cuenta else set()
    huerfanas = todas_locales - vistos
    if huerfanas:
        logger.warning(
            "Recovery: %d orden(es) local(es) que el broker no conoce — marcando UNKNOWN_LOCAL: %s",
            len(huerfanas), list(huerfanas)[:5],
        )
        now = datetime.now(UTC)
        # SQL-native: marca UNKNOWN_LOCAL en operaciones.ordenes_live (merge jsonb, sin Mongo).
        from psycopg.types.json import Jsonb

        from core.postgres import get_pool
        patch = Jsonb({"status": "UNKNOWN_LOCAL", "updated_at": now.isoformat()})
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE operaciones.ordenes_live SET estado = 'UNKNOWN_LOCAL', updated_at = %s, "
                "data = data || %s WHERE cl_ord_id = ANY(%s)",
                (now, patch, list(huerfanas)))
            conn.commit()
        for cid in huerfanas:
            _audit("RECOVERY", cl_ord_id=cid,
                   payload={"reason": "no encontrada en broker"})

    logger.info("Recovery: reconciliadas=%d, huérfanas=%d", len(vistos), len(huerfanas))


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────


def _heartbeat_loop(account: str) -> None:
    """Thread daemon: cada HEARTBEAT_DB_S segundos escribe Operaciones.
    MotorOrdenesHeartbeat para que /manager → DIAG sepa que el motor
    está vivo. A diferencia de los otros motores, este no escribe
    snapshots periódicos (solo ERs reactivos), por eso necesita el
    heartbeat explícito."""
    while _running:
        try:
            ts = datetime.now(UTC)
            # SQL-native (decomiso 2026-06-29): heartbeat a operaciones.motor_heartbeat, sin Mongo.
            from core import pg_mirror
            pg_mirror.write_native("operaciones.motor_heartbeat", ["id"], [{
                "id": "current", "updated_at": ts,
                "data": pg_mirror.doc_iso({"account": account})}])
        except Exception as e:
            logger.warning("heartbeat write falló: %s", e)
        time.sleep(HEARTBEAT_DB_S)


def main() -> None:
    # SQL-native (decomiso Mongo 2026-06-29): el motor NO toca Mongo. Persiste todo en
    # operaciones.{ordenes_live,ordenes_audit,motor_heartbeat} (SQL). Sin handle Mongo.
    handler = _make_er_handler()
    account, env = inicializar_para_motor(handler)
    logger.info("Motor de órdenes ARRIBA (cuenta=%s, env=%s)", account, env.name)

    # Recovery de NUESTRAS órdenes pendientes (audit general / ordenes_live).
    # Las operativas de mesa (operativa_mep) ya no dependen del WS — confirman
    # los fills vía REST `pyRofex.get_order_status` directo al broker.
    _recovery(account)

    # Órdenes del día (OPERAR, 2026-09-17): purga la ventana vieja y arranca en
    # thread aparte la sincronización de la ALyC (backfill REST + suma de
    # suscripciones sobre esta MISMA sesión WS, no una nueva). En thread propio
    # porque puede tardar (una llamada REST por cuenta, paceada) y no debe
    # demorar el arranque del heartbeat ni el procesamiento de ER de la master.
    #
    # Incidente 2026-09-17 (dos rounds, WS de la master caído en rueda):
    # resuelto de raíz sacando TODA resolución/probing contra el broker de
    # este proceso. `_cuentas_a_sincronizar` ya no llama a
    # `resolver_cuenta_rofex`/`get_account_report` en caliente — solo lee
    # `clientes.comitentes.rofex_account/rofex_valida`, poblado OFFLINE por
    # `jobs/resolver_cuentas_rofex.py` (corrido por cron con el motor
    # PARADO, fuera de rueda). Con eso, prender el sync durante rueda es
    # seguro: no hay ninguna llamada nueva al broker con cuentas no
    # confirmadas, solo backfill REST + suscripción WS de cuentas YA
    # validadas. Igual queda apagado por defecto (ORDENES_DIA_SYNC=1 para
    # prender) hasta correr el resolver por primera vez y confirmar en un
    # deploy fuera de rueda que el arranque queda limpio.
    if os.getenv("ORDENES_DIA_SYNC", "").strip() == "1":
        _purgar_ordenes_dia()
        threading.Thread(
            target=_sincronizar_ordenes_dia,
            args=(account,),
            daemon=True,
        ).start()
    else:
        logger.info("Órdenes del día: sync de la ALyC DESACTIVADO "
                     "(ORDENES_DIA_SYNC≠1) — ver nota en main().")

    # Heartbeat para monitoreo desde /manager → DIAG.
    threading.Thread(
        target=_heartbeat_loop,
        args=(account,),
        daemon=True,
    ).start()

    while _running:
        time.sleep(HEARTBEAT_S)

    logger.info("Apagando motor de órdenes…")
    cerrar_ws()
    logger.info("Motor de órdenes detenido.")


if __name__ == "__main__":
    # Redirigir el log a archivo si se ejecuta vía systemd (stdout va al journal).
    log_path = os.getenv("MOTOR_ORDENES_LOG")
    if log_path:
        fh = logging.FileHandler(log_path)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(fh)
    main()
