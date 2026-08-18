"""jobs/control_saldos.py — SALDO LIQUIDADO de hoy por cuenta y moneda.

QUÉ ES Y POR QUÉ EXISTE
=======================
El control de descubiertos se hacía a mano porque la única posición que teníamos
es **proyectada**: `posicionValuada` mete adentro lo que todavía no liquidó, así
que una cuenta que hoy tiene 0 pesos aparece NEGATIVA porque la caución que vence
mañana ya está contada. Ese negativo no es un descubierto — es el futuro
metiéndose en la foto del presente, y separarlos era el trabajo manual.

El endpoint `cuentas/{id}/posiciones` (Resumen de cuenta → tipo "Posiciones")
devuelve las dos cosas separadas: `cantidadLiquidada` (lo que ESTÁ) y
`cantidadPendienteLiquidar` (lo que va a estar). Este daemon persiste la PRIMERA,
que es la que contesta «¿esta cuenta está en descubierto HOY?».

Medido contra la cuenta 805 el 2026-08-13 (`scripts/diag_posiciones_resumen`):
  · formato de `fecha` = **DD/MM/YYYY**. Con `YYYY-MM-DD` devuelve HTTP 400
    («Error en formato de fechas») — no es un detalle de estilo, es el contrato.
  · el endpoint responde en ~260-420ms.
  · las monedas vienen con `tipoTitulo = 'Moneda'` y `especie` = el código
    (`ARS`, `USD`, `USDC`), sin corchetes — a diferencia de los títulos, que
    traen `[9422] AO29`.

LO QUE ESTE JOB NO HACE (a propósito)
=====================================
* NO guarda histórico: la tabla tiene SIEMPRE el día de hoy y nada más. El primer
  barrido del día borra lo anterior. Es un TABLERO, no una serie.
* NO escribe en `portafolio.tenencia`, `tenencia_live`, `assets`, AuM ni PnL.
  Solo su tabla.
* NO persiste títulos: hoy solo las monedas de `MONEDAS`. Sumar una especie es
  cambiar esa constante.

DOS CONVENCIONES QUE HAY QUE SABER SÍ O SÍ
==========================================

**1. EL SIGNO VIENE AL REVÉS.** Aunesa manda las tenencias con el signo dado
vuelta: la 805 tiene pesos a favor y el endpoint dice `-56.095,90`. No es una
particularidad de este endpoint — `posicionValuada` hace lo mismo y el parser del
job diario lo corrige con el mismo `× -1` (`portafolio_backfill._parse`). Acá vive
en `SIGNO`, en un solo lugar y con nombre, porque un `-1` suelto en el medio del
código es exactamente el tipo de cosa que después nadie se anima a tocar.

**2. EL SALDO ES LA SUMA DE LAS FILAS DE ESA MONEDA.** El endpoint devuelve más
de una fila para la misma moneda (la 805 trae dos de ARS: una con liquidada 0 y
otra con -56.095,90) y **no sabemos qué las separa** — `estado`, `lugar`,
`subCuenta`, `informacion` y `monedaCotizacion` son idénticos en las dos. Como no
se puede elegir "la fila buena" sin inventar un criterio, se suman: un saldo es
aditivo por definición, así que la suma es correcta sea cual sea el corte que las
separa. `filas_origen` guarda cuántas se sumaron, para que una cuenta rara se vea
en la tabla en vez de esconderse. Correrlo con `--dry` imprime las filas crudas.

CÓMO HACE PARA NO SER CARO (el mismo diseño que `jobs/tenencia_live.py`)
========================================================================
Refrescar ~1.800 cuentas cada pocos minutos sería martillar al custodio. Tres ritmos:

  1. BARRIDO DE APERTURA — una vez, todas las cuentas. Es lo que garantiza que la
     tabla está completa.
  2. DETECTOR — cada 3 minutos, **UNA sola llamada** a `consolidadosGenerales`,
     que devuelve los movimientos del día de TODA la casa. Los comprobantes nuevos
     dicen quién se movió. Se REUSA el de `tenencia_live` (misma función, no una
     copia): ve compraventas, acreencias, depósitos, transferencias, extracciones
     y cauciones — todo lo que puede mover un saldo.
  3. REFRESCO SELECTIVO — solo esas cuentas, con debounce.
  4. REVISIÓN ACTIVA (2026-08-18) — el detector NO ALCANZA: pregunta por lo
     CONCERTADO hoy y el saldo se mueve por lo que LIQUIDA hoy. Una caución de la
     semana pasada que vence hoy mueve la plata sin aparecer en esa ventana, y
     eso dejó a la cuenta 1243 con 4.480.299,08 ARS fantasma durante 10 horas.
     Un detector es una OPTIMIZACIÓN, no una garantía: la garantía es que cada
     cuenta pase por el control se haya movido o no. Ver `cuentas_a_revisar` —
     top/bottom por moneda cada 5 min, más una rotación por antigüedad que le
     pone TECHO a cuánto puede mentir una fila (~2,6 h con los valores de hoy).

Uso:
    python -m jobs.control_saldos                    # daemon: corre hasta el cierre
    python -m jobs.control_saldos --una-pasada       # barrido de apertura y termina
    python -m jobs.control_saldos --ver              # qué quedó en la tabla (solo SELECTs)
    python -m jobs.control_saldos --dry --cuentas 805        # NO escribe: muestra el crudo
    python -m jobs.control_saldos --dry --muestra 40         # 40 cuentas: inventario + timing
    python -m jobs.control_saldos --sin-esperar-backfill
"""
from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests

from core import aunesa
from core.calendario import es_habil
from core.postgres import get_job_pool
from jobs._aum_filters import load_contrapartes_id_cuentas
from jobs.aum import autenticar, obtener_cuentas
from jobs.tenencia_live import (
    _ahora_art,
    _hoy_art,
    backfill_diario_termino,
    detectar_movimientos,
)

logger = logging.getLogger("jobs.control_saldos")

PATH = "cuentas/{}/posiciones"

# Monedas que se persisten.
#
# **USDC (dólar cable) entró el 2026-08-14**, por pedido del back office. Estaba
# afuera desde el arranque porque su presencia se había visto en UNA cuenta (la
# 805) y no alcanzaba para decidir; el job la contaba en `monedas_descartadas`
# justamente para tener el dato. La decisión la toma el negocio, no el job.
#
# Sumar una moneda es UNA línea acá: el resto del sistema es agnóstico —
# `api/services/titulos_negativos.py` no filtra por código y las pills de la
# pantalla se derivan de los datos, así que la moneda nueva aparece sola.
MONEDAS: tuple[str, ...] = ("ARS", "USD", "USDL", "USDC")

# Aunesa manda las tenencias con el signo invertido (ver el docstring). Es la
# MISMA corrección que aplica el job diario de tenencias.
SIGNO = -1

# RUIDO: saldos por debajo de esto NO se persisten (y por lo tanto no se ven).
# Se mide en VALOR ABSOLUTO, así que corta las dos puntas: ni +12.000 ni -10.000
# entran. Un descubierto de diez mil pesos no es un problema que haya que
# perseguir, y cien filas así tapan las tres que sí importan — una pantalla de
# control que hay que filtrar con el ojo se deja de mirar.
#
# El corte se aplica al ESCRIBIR y no al leer: es la decisión del negocio sobre
# qué es un saldo relevante, y la tabla no tiene histórico que se pueda
# desvirtuar (siempre es el día de hoy), así que guardarlas para nada solo
# agranda lo que la vista tiene que traer en cada poll.
#
# ⚠️ USD, USDL y USDC quedan SIN umbral hasta que el negocio defina el suyo: poner
# un número ahí sería inventarlo. Con 0 no se filtra nada (el `abs(...) < 0` nunca
# se cumple), así que las monedas dólar siguen mostrándose enteras.
UMBRALES: dict[str, float] = {"ARS": 15_000.0}

# El endpoint rechaza ISO con HTTP 400. Verificado 2026-08-13.
FMT_FECHA = "%d/%m/%Y"

# ── knobs (mismos criterios que tenencia_live) ────────────────────────────────
HORA_CIERRE_ART = 18
DETECTOR_S = 180
DEBOUNCE_S = 60
WORKERS = 6
ESPERA_BACKFILL_S = 60
MAX_ESPERA_BACKFILL_MIN = 90
ERRORES_PARA_CORTE = 10
PAUSA_BREAKER_S = 300

# ── revisión activa (ver `cuentas_a_revisar`) ─────────────────────────────────
# Cuántas cuentas por MONEDA y por lado entran al grupo prioritario: las 10 de
# mayor saldo y las 10 de menor (o sea, las más negativas).
PRIORIDAD_TOP_N = 10
# Una prioritaria no se vuelve a consultar antes de esto (5 min).
PRIORIDAD_CADA_S = 300
# A partir de acá una cuenta cuenta como "envejecida" y entra a la rotación.
ENVEJECIDA_S = 2_700          # 45 min
# Techo de cuentas por ciclo. Con DETECTOR_S=180 son 20 ciclos por hora: 30 × 20
# = 600 cuentas/hora, así que las ~1.550 del universo quedan revisadas cada ~2.6
# horas COMO MÁXIMO, se muevan o no. Ese número es el techo de cuánto puede
# mentir una fila, y subir el tope lo baja en proporción directa: es la perilla.
TOPE_CICLO = 30


# ── schema ────────────────────────────────────────────────────────────────────
def _ensure_schema() -> None:
    ddl = [
        "CREATE SCHEMA IF NOT EXISTS portafolio",
        """CREATE TABLE IF NOT EXISTS portafolio.control_saldos (
            fecha              date NOT NULL,
            id_cuenta          text NOT NULL,
            ticker             text NOT NULL,
            cuenta             text,
            cantidad           numeric,
            cantidad_pendiente numeric,
            filas_origen       integer,
            origen             text,
            actualizado_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (fecha, id_cuenta, ticker))""",
        "CREATE INDEX IF NOT EXISTS ix_csaldos_negativos "
        "ON portafolio.control_saldos (fecha, cantidad)",
        "CREATE INDEX IF NOT EXISTS ix_csaldos_cuenta "
        "ON portafolio.control_saldos (id_cuenta, fecha)",
    ]
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        for stmt in ddl:
            cur.execute(stmt)
        conn.commit()


def latido(fase: str, cuentas: int = 0) -> None:
    """Marca que el daemon está VIVO, en `operaciones.motor_heartbeat`.

    Hace falta porque `max(actualizado_at)` de la tabla NO sirve para eso: el
    daemon solo reescribe las cuentas que se movieron, así que media hora sin
    operaciones deja el dato "viejo" con todo funcionando perfecto. Sin un latido
    aparte no hay forma de distinguir «no pasó nada» de «el daemon está muerto»,
    que son justo las dos cosas que un tablero de control tiene que separar.

    Es la misma tabla que usa `motor_ordenes` (singleton por `id`), así que no
    inventa un mecanismo nuevo. Nunca rompe la corrida: si falla, se loguea.
    """
    try:
        with get_job_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO operaciones.motor_heartbeat (id, updated_at, data) "
                "VALUES ('control_saldos', now(), %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET updated_at = now(), data = EXCLUDED.data",
                (json.dumps({"fase": fase, "cuentas": cuentas}),))
            conn.commit()
    except Exception as e:
        logger.warning("no pude escribir el latido (%s): %s", type(e).__name__, e)


def cuentas_a_revisar(hoy: date) -> list[str]:
    """Qué cuentas (ids) hay que volver a consultar, en UNA query. Prioritarias primero.

    POR QUÉ EXISTE (incidente 2026-08-18, cuenta 1243)
    ---------------------------------------------------
    El detector pregunta a `consolidadosGenerales` por lo CONCERTADO hoy, pero el
    saldo se mueve por lo que LIQUIDA hoy — y las dos fechas casi nunca coinciden.
    Una caución de la semana pasada que vence hoy, o una compra de ayer en 24hs,
    mueven la plata sin aparecer en esa ventana. La 1243 tenía 4.480.299,08 ARS
    escritos en el barrido de apertura (11:01:46), esa posición se fue durante el
    día, el detector nunca la marcó y la fila quedó congelada las 10 horas.
    Peor: la caución que vence hoy es exactamente el evento que este tablero vino
    a controlar.

    La conclusión no es "arreglar el detector": es que **un detector es una
    optimización, no una garantía**. Una cuenta tiene que pasar por el control
    igual, se haya movido o no. Esta función es esa garantía, en dos capas:

      · PRIORITARIAS — top N y bottom N por MONEDA. Son las que más duelen si se
        desactualizan, y se revisan cada `PRIORIDAD_CADA_S`. El ranking se
        recalcula en CADA pasada contra la tabla ya actualizada, así que si una
        se achica y sale del top, la que sigue ocupa su lugar sola — sin listas
        congeladas ni estado en memoria.
      · ENVEJECIDAS — las que hace más tiempo que nadie tocó, en rotación. Esto
        es lo que pone un TECHO a cuánto puede mentir una fila: con `TOPE_CICLO`
        por ciclo de `DETECTOR_S`, ninguna cuenta queda sin revisar más de
        ~(total ÷ TOPE_CICLO) × DETECTOR_S. Es lo que habría salvado a la 1243.

    El ranking va POR MONEDA y no global porque comparar 4M de pesos con 194
    dólares no significa nada — es el mismo motivo por el que la pantalla muestra
    una moneda por vez.

    Costo: UNA query, un scan de la tabla del día (~1.300 filas). Un `Seq Scan`
    sobre eso es óptimo y no se indexa (ver CLAUDE.md). Lo caro son los viajes,
    no el plan.
    """
    try:
        with get_job_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "WITH hoy AS ("
                "  SELECT id_cuenta, ticker, cantidad, actualizado_at "
                "    FROM portafolio.control_saldos WHERE fecha = %(f)s), "
                "rk AS ("
                "  SELECT id_cuenta,"
                "         row_number() OVER (PARTITION BY ticker ORDER BY cantidad DESC) AS alto,"
                "         row_number() OVER (PARTITION BY ticker ORDER BY cantidad ASC)  AS bajo"
                "    FROM hoy), "
                "prio AS (SELECT DISTINCT id_cuenta FROM rk "
                "          WHERE alto <= %(n)s OR bajo <= %(n)s), "
                "cta AS (SELECT id_cuenta, min(actualizado_at) AS visto"
                "          FROM hoy GROUP BY id_cuenta) "
                "SELECT c.id_cuenta "
                "  FROM cta c LEFT JOIN prio p ON p.id_cuenta = c.id_cuenta "
                # Una prioritaria no se re-consulta antes de PRIORIDAD_CADA_S, y una
                # común solo entra si ya envejeció. Sin este corte, el ciclo gastaría
                # llamadas en cuentas que se refrescaron hace 30 segundos.
                " WHERE (p.id_cuenta IS NOT NULL"
                "        AND c.visto < now() - make_interval(secs => %(prio_s)s))"
                "    OR c.visto < now() - make_interval(secs => %(edad_s)s) "
                # Las prioritarias primero: si el tope corta, que corte por las de
                # abajo. Después, las más viejas — eso hace la rotación.
                " ORDER BY (p.id_cuenta IS NOT NULL) DESC, c.visto ASC "
                " LIMIT %(tope)s",
                {"f": hoy, "n": PRIORIDAD_TOP_N, "prio_s": PRIORIDAD_CADA_S,
                 "edad_s": ENVEJECIDA_S, "tope": TOPE_CICLO})
            filas = cur.fetchall()
    except Exception as e:
        logger.warning("no pude calcular las cuentas a revisar (%s): %s",
                       type(e).__name__, e)
        return []
    # SOLO ids: la denominación la pone el llamador desde el universo. Devolver
    # `cuenta` desde acá fue justo lo que causó el "[21] [21] [21]" de prod.
    return [str(r[0]) for r in filas]


def _purgar(hoy: date) -> int:
    """La tabla tiene SIEMPRE el día de hoy y nada más (no es acumulativa)."""
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM portafolio.control_saldos WHERE fecha < %s", (hoy,))
        n = cur.rowcount or 0
        conn.commit()
    return n


# ── universo de cuentas ───────────────────────────────────────────────────────
def universo_cuentas(headers: dict) -> tuple[dict[str, str], int]:
    """{id_cuenta: denominación} SIN las contrapartes. Devuelve (universo, excluidas).

    Las contrapartes (fondos, sociedades gerentes: SCHRODER, LOMBARD, ADCAP…) son
    cuentas que operamos pero que no son clientes nuestros — un saldo suyo no es un
    descubierto que tengamos que perseguir. La lista vive en
    `clientes.contrapartes` y la edita el equipo desde el panel, así que dar de
    alta una contraparte la saca de este control sin tocar código.

    Se excluye por `id_cuenta` y NO por nombre: el formato de la denominación
    difiere entre tablas y el id es la única clave estable.
    """
    df = obtener_cuentas(headers)
    todas = {str(r["id"]): str(r["denominacion"]) for _, r in df.iterrows()}
    contrapartes = load_contrapartes_id_cuentas()
    universo = {k: v for k, v in todas.items() if k not in contrapartes}
    return universo, len(todas) - len(universo)


# ── traer + parsear ───────────────────────────────────────────────────────────
def _traer(idc: str, fecha: date) -> tuple[bool, list, str]:
    """(ok, filas crudas, motivo). ok=False = no se pudo consultar → NO se escribe.

    Distinguir «Aunesa dice que no hay posición» (204 → ok, lista vacía) de «no
    pude preguntar» (timeout/500 → error) es lo que evita que un problema de red
    borre el saldo de una cuenta y la haga desaparecer del control.

    El 401 lo resuelve `core.aunesa.get` (renueva el token con lock compartido y
    reintenta la request). Este daemon vive horas con el mismo token y el token
    vence — `tenencia_live` se comió ese incidente el 2026-08-12.
    """
    params = {"fecha": fecha.strftime(FMT_FECHA)}
    try:
        resp = aunesa.get(PATH.format(idc), params, timeout=90, retries=2)
    except requests.exceptions.Timeout:
        return False, [], "timeout"
    except requests.exceptions.RequestException as e:
        return False, [], type(e).__name__
    if resp.status_code == 204:
        return True, [], ""
    if resp.status_code != 200:
        return False, [], f"http_{resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return False, [], "json_invalido"
    return True, data if isinstance(data, list) else [], ""


def parsear(filas: list, idc: str, denom: str) -> tuple[list[dict], dict[str, int]]:
    """Filas crudas → un registro por moneda. (registros, monedas descartadas).

    Tres cosas pasan acá y las tres están explicadas en el docstring del módulo:
    se filtra a `MONEDAS`, se SUMAN las filas de la misma moneda, y se da vuelta
    el signo.

    El saldo exactamente 0 NO se persiste: la ausencia de fila ES el cero, y
    escribir ~5.000 filas en cero por día para decir «no pasa nada» solo agranda
    la tabla. Como cada cuenta se reescribe entera (DELETE + INSERT), una cuenta
    que pasa de -50.000 a 0 pierde su fila y desaparece del control, que es
    justo lo que tiene que pasar. Lo mismo vale para el umbral de `UMBRALES`: una
    cuenta que baja de -80.000 a -3.000 deja de tener fila y sale del control.
    """
    grupos: dict[str, dict] = {}
    descartadas: dict[str, int] = {}
    for r in filas:
        if not isinstance(r, dict):
            continue
        esp = str(r.get("especie") or "").strip().upper()
        if not esp:
            continue
        if esp not in MONEDAS:
            # Solo se cuentan las que SON moneda: los títulos no son candidatos a
            # entrar acá y contarlos taparía la señal que interesa (¿apareció una
            # moneda que no estamos persistiendo?).
            if str(r.get("tipoTitulo") or "").strip().lower() == "moneda":
                descartadas[esp] = descartadas.get(esp, 0) + 1
            continue
        g = grupos.setdefault(esp, {"liq": 0.0, "pen": 0.0, "n": 0})
        g["liq"] += _num(r.get("cantidadLiquidada"))
        g["pen"] += _num(r.get("cantidadPendienteLiquidar"))
        g["n"] += 1

    cuenta_str = _nombre_cuenta(idc, denom)
    out = []
    for esp, g in grupos.items():
        cantidad = round(SIGNO * g["liq"], 4)
        if cantidad == 0 or abs(cantidad) < UMBRALES.get(esp, 0.0):
            continue
        out.append({
            "id_cuenta": idc, "cuenta": cuenta_str, "ticker": esp,
            "cantidad": cantidad,
            "cantidad_pendiente": round(SIGNO * g["pen"], 4),
            "filas_origen": g["n"],
        })
    return out, descartadas


def _nombre_cuenta(idc: str, denom: str) -> str:
    """'805' + 'MOLLO NICOLAS' → '[805] MOLLO NICOLAS'. IDEMPOTENTE.

    Si `denom` ya viene con el prefijo se lo saca antes de volver a ponerlo. Sin
    esto, pasarle un valor ya formateado agrega un `[805]` por vez y el nombre
    se degrada solo — pasó en prod el 2026-08-19 (`[21] [21] [21] …`) porque el
    ciclo de revisión leía la denominación de la tabla, donde ya estaba armada.
    El llamador correcto es el universo de Aunesa; esto es la red.
    """
    d = (denom or "").strip()
    pref = f"[{idc}]"
    while d.startswith(pref):
        d = d[len(pref):].strip()
    return f"{pref} {d}" if d else pref


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ── escritura ─────────────────────────────────────────────────────────────────
def _escribir(hoy: date, idc: str, registros: list[dict], origen: str) -> None:
    """DELETE + INSERT de esa cuenta en UNA transacción.

    Atómico a propósito: un lector nunca ve la cuenta a medio escribir. Y como
    solo se llama cuando la consulta salió bien, una caída de Aunesa deja la fila
    ANTERIOR intacta (con su `actualizado_at` viejo, que es la señal de que
    envejeció) en vez de dejar la cuenta sin saldo.
    """
    filas = [{**r, "fecha": hoy, "origen": origen} for r in registros]
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM portafolio.control_saldos "
                    "WHERE fecha = %s AND id_cuenta = %s", (hoy, idc))
        if filas:
            cur.executemany(
                "INSERT INTO portafolio.control_saldos "
                "(fecha,id_cuenta,ticker,cuenta,cantidad,cantidad_pendiente,"
                " filas_origen,origen,actualizado_at) "
                "VALUES (%(fecha)s,%(id_cuenta)s,%(ticker)s,%(cuenta)s,%(cantidad)s,"
                " %(cantidad_pendiente)s,%(filas_origen)s,%(origen)s,now())",
                filas)
        conn.commit()


def refrescar_cuenta(idc: str, denom: str, hoy: date,
                     origen: str) -> tuple[bool, str, int, dict[str, int]]:
    """(ok, motivo, filas escritas, monedas descartadas) de una cuenta."""
    ok, crudo, why = _traer(idc, hoy)
    if not ok:
        return False, why, 0, {}
    registros, descartadas = parsear(crudo, idc, denom)
    _escribir(hoy, idc, registros, origen)
    return True, "", len(registros), descartadas


def _refrescar_lote(cuentas: list[tuple[str, str]], hoy: date, origen: str,
                    workers: int) -> tuple[int, int, int, dict[str, int], dict[str, int]]:
    """(ok, fallidas, filas, motivos, monedas descartadas). Paralelo acotado.

    `motivos` cuenta POR QUÉ falló cada una ({'timeout': 3, 'http_500': 9}): sin
    eso, «12 fallidas» no distingue a Aunesa caído de un token vencido, y esa
    diferencia es la que decide si hay que hacer algo.

    `descartadas` cuenta las monedas que se vieron y NO se persistieron, por
    código. Es la única forma de contestar con datos si una moneda hay que sumarla
    (así entró USDC) o si otra se puede sacar — hasta acá esa cuenta solo existía
    en `--dry`, o sea que la corrida REAL, que es la que ve las 1.566 cuentas, no
    la reportaba.
    """
    if not cuentas:
        return 0, 0, 0, {}, {}
    ok = fallo = filas = 0
    motivos: dict[str, int] = {}
    descartadas: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(refrescar_cuenta, idc, dn, hoy, origen): idc
                for idc, dn in cuentas}
        for f in as_completed(futs):
            try:
                bien, why, n, desc = f.result()
            except Exception as e:
                logger.warning("cuenta %s explotó: %s: %s", futs[f], type(e).__name__, e)
                bien, why, n, desc = False, type(e).__name__, 0, {}
            if bien:
                ok += 1
                filas += n
                for k, v in desc.items():
                    descartadas[k] = descartadas.get(k, 0) + v
            else:
                fallo += 1
                motivos[why or "?"] = motivos.get(why or "?", 0) + 1
    return ok, fallo, filas, motivos, descartadas


# ── modo --dry: mirar sin escribir ────────────────────────────────────────────
def _que_las_separa(filas: list[dict]) -> list[str]:
    """Para cada moneda con MÁS DE UNA fila, qué campos tienen valores distintos.

    Es la pregunta abierta del diseño: el endpoint devuelve dos filas de ARS para
    la 805 y hay que saber si las separa un criterio real (y entonces habría que
    respetarlo) o si son dos pedazos del mismo saldo (y entonces sumarlas es lo
    correcto). Imprimir el JSON entero no alcanzó: los campos que importan quedan
    al final y se comen el ancho de la consola. Esto compara clave por clave y
    muestra SOLO lo que difiere.
    """
    por_especie: dict[str, list[dict]] = {}
    for r in filas:
        if isinstance(r, dict) and str(r.get("tipoTitulo") or "").lower() == "moneda":
            por_especie.setdefault(str(r.get("especie") or ""), []).append(r)
    out = []
    for esp, rs in sorted(por_especie.items()):
        if len(rs) < 2:
            continue
        claves = {k for r in rs for k in r}
        difieren = [k for k in sorted(claves)
                    if len({json.dumps(r.get(k), sort_keys=True, default=str)
                            for r in rs}) > 1]
        out.append(f"       {esp}: {len(rs)} filas · campos que DIFIEREN: {difieren}")
        for k in difieren:
            vals = [r.get(k) for r in rs]
            out.append(f"         {k:<28} {vals}")
        # La conclusión se imprime sola: si lo único distinto son los importes, no
        # hay criterio que respetar y sumarlas es la única lectura posible.
        if set(difieren) <= {"cantidadLiquidada", "cantidadPendienteLiquidar"}:
            out.append("         → NADA las separa salvo los importes: son pedazos "
                       "del mismo saldo → SUMAR es correcto.")
        else:
            out.append("         → hay un criterio real que las separa (los campos "
                       "de arriba): revisar antes de seguir sumando.")
    return out


def dry(cuentas: list[tuple[str, str]], hoy: date, n_universo: int = 0) -> int:
    """Imprime lo que persistiría, SIN tocar la base. Cero escrituras.

    Sirve para las preguntas que solo contesta prod: qué separa las filas
    repetidas de la misma moneda, qué monedas aparecen de verdad (así se decidió
    sumar USDC), y cuánto tarda una llamada (→ cuánto dura el barrido de apertura).
    """
    print(f"\n{'=' * 78}\nDRY RUN — no se escribe una sola fila   ·   fecha="
          f"{hoy.strftime(FMT_FECHA)}\n{'=' * 78}")
    print(f"   monedas persistidas: {MONEDAS}   ·   signo aplicado: {SIGNO}")
    print(f"   umbral de ruido (|saldo| menor a esto NO se persiste): {UMBRALES}")
    tot_desc: dict[str, int] = {}
    tiempos: list[float] = []
    negativas = 0
    for idc, denom in cuentas:
        t0 = time.monotonic()
        ok, crudo, why = _traer(idc, hoy)
        ms = (time.monotonic() - t0) * 1000
        tiempos.append(ms)
        if not ok:
            print(f"\n   ▸ {idc:<8} {denom[:40]:<40} ✗ {why}")
            continue
        monedas_crudas = [r for r in crudo if isinstance(r, dict)
                          and str(r.get("tipoTitulo") or "").lower() == "moneda"]
        registros, desc = parsear(crudo, idc, denom)
        for k, v in desc.items():
            tot_desc[k] = tot_desc.get(k, 0) + v
        negativas += sum(1 for r in registros if r["cantidad"] < 0)
        print(f"\n   ▸ {idc:<8} {denom[:40]:<40} {ms:>6.0f}ms · {len(crudo)} filas "
              f"({len(monedas_crudas)} de moneda)")
        if len(cuentas) <= 5:
            # Con pocas cuentas se muestra el CRUDO fila por fila. SIN cortar: la
            # primera versión truncaba a 250 caracteres y se comía justo los
            # importes, que es donde estaba la respuesta.
            for r in sorted(monedas_crudas, key=lambda x: str(x.get("especie"))):
                print(f"       crudo  {json.dumps(r, ensure_ascii=False)}")
            for linea in _que_las_separa(crudo):
                print(linea)
        for r in registros:
            print(f"       →  {r['ticker']:<6} cantidad={r['cantidad']:>18,.2f} "
                  f"pendiente={r['cantidad_pendiente']:>18,.2f} "
                  f"(sumó {r['filas_origen']} fila/s)")
        if not registros:
            print("       → (nada que persistir para esta cuenta)")

    print(f"\n{'─' * 78}\n   RESUMEN")
    if tiempos:
        tiempos.sort()
        p50 = tiempos[len(tiempos) // 2]
        print(f"   latencia por llamada: min {tiempos[0]:.0f}ms · p50 {p50:.0f}ms · "
              f"max {tiempos[-1]:.0f}ms")
        # Proyección del barrido sobre el universo REAL (ya sin contrapartes), no
        # sobre el total de cuentas de Aunesa: el job no las consulta a todas.
        n_cuentas = n_universo or len(cuentas)
        est = n_cuentas * (p50 / 1000) / WORKERS
        print(f"   barrido de {n_cuentas} cuentas con {WORKERS} workers ≈ "
              f"{est / 60:.1f} min")
    print(f"   filas con saldo NEGATIVO en la muestra: {negativas}")
    print(f"   monedas VISTAS y DESCARTADAS (no están en MONEDAS): "
          f"{tot_desc or '(ninguna)'}")
    print("   → una moneda que aparece seguido acá es una decisión de negocio a")
    print("     tomar; una de MONEDAS que nunca aparece se puede sacar gratis.\n")
    return 0


# ── modo --ver: qué quedó en la tabla (sin tocar Aunesa) ──────────────────────
def ver(top: int = 30) -> int:
    """Lee `portafolio.control_saldos` y muestra el control. NO pega a Aunesa.

    Es la respuesta a «¿esto sirve?»: cuántas cuentas quedaron en descubierto,
    cuáles son las peores y qué tan fresco está el dato. Solo SELECTs.
    """
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha), max(actualizado_at), count(*), "
                    "count(DISTINCT id_cuenta) FROM portafolio.control_saldos "
                    "WHERE fecha = (SELECT max(fecha) FROM portafolio.control_saldos)")
        fecha, fresco, n_filas, n_cuentas = cur.fetchone()
        if not fecha:
            print("\n   la tabla está VACÍA — ¿corrió el barrido?\n")
            return 0
        print(f"\n{'=' * 78}\nCONTROL DE SALDOS · fecha {fecha} · {n_filas} filas · "
              f"{n_cuentas} cuentas\n   dato más fresco: {fresco}\n{'=' * 78}")

        cur.execute("SELECT ticker, count(*) FILTER (WHERE cantidad < 0) AS en_rojo, "
                    "       count(*) AS cuentas, "
                    "       sum(cantidad) FILTER (WHERE cantidad < 0) AS total_rojo, "
                    "       sum(cantidad) FILTER (WHERE cantidad > 0) AS total_verde "
                    "FROM portafolio.control_saldos WHERE fecha = %s "
                    "GROUP BY ticker ORDER BY ticker", (fecha,))
        print(f"\n   {'moneda':<8} {'cuentas':>9} {'EN ROJO':>9} {'total rojo':>20} "
              f"{'total a favor':>20}")
        for tk, rojo, cuentas, t_rojo, t_verde in cur.fetchall():
            print(f"   {tk:<8} {cuentas:>9} {rojo:>9} {float(t_rojo or 0):>20,.2f} "
                  f"{float(t_verde or 0):>20,.2f}")

        cur.execute("SELECT cuenta, ticker, cantidad, cantidad_pendiente, filas_origen "
                    "FROM portafolio.control_saldos "
                    "WHERE fecha = %s AND cantidad < 0 "
                    "ORDER BY cantidad ASC LIMIT %s", (fecha, top))
        filas = cur.fetchall()
        print(f"\n   ▸ DESCUBIERTOS (peor primero, top {top}) — {len(filas)} mostrados")
        if not filas:
            print("     (ninguna cuenta en descubierto)")
        for cuenta, tk, cant, pend, n in filas:
            print(f"     {str(cuenta)[:44]:<44} {tk:<6} {float(cant):>18,.2f} "
                  f"(pend {float(pend or 0):>16,.2f} · {n} fila/s)")

        # Una cuenta que sumó más de 2 filas para la misma moneda es rara y hay que
        # mirarla: la regla de agregación se validó contra un caso de 2.
        cur.execute("SELECT count(*) FROM portafolio.control_saldos "
                    "WHERE fecha = %s AND filas_origen > 2", (fecha,))
        raras = cur.fetchone()[0]
        print(f"\n   filas armadas con MÁS de 2 filas de origen: {raras}"
              + ("  ← mirar esas cuentas" if raras else ""))
    print()
    return 0


# ── daemon ────────────────────────────────────────────────────────────────────
def _opt(flag: str, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def run() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    es_dry = "--dry" in sys.argv
    una_pasada = "--una-pasada" in sys.argv
    if "--ver" in sys.argv:      # solo lee la tabla: ni Aunesa ni escrituras
        return ver(int(_opt("--top", 30)))
    subset = _opt("--cuentas")
    muestra = int(_opt("--muestra", 0))
    workers = int(_opt("--workers", WORKERS))
    hoy = _hoy_art()

    print(f"=== CONTROL DE SALDOS · {hoy} · fecha consultada = "
          f"{hoy.strftime(FMT_FECHA)} ===")
    if not es_habil(hoy) and not es_dry:
        print("hoy NO es día hábil — nada que hacer")
        return 0

    headers = autenticar()
    universo, n_contrapartes = universo_cuentas(headers)
    n_universo = len(universo)
    print(f"  cuentas: {n_universo} (excluidas {n_contrapartes} contrapartes)")
    if subset:
        ids = {c.strip() for c in subset.split(",")}
        universo = {k: v for k, v in universo.items() if k in ids}
        # Una cuenta pedida a mano que no está en el universo se sondea igual: si
        # la sacaron las contrapartes, verla es justo el punto de pedirla.
        for i in ids - set(universo):
            universo[i] = ""
    lote = list(universo.items())
    if muestra:
        lote = lote[:muestra]

    if es_dry:
        return dry(lote, hoy, n_universo)

    _ensure_schema()
    purgadas = _purgar(hoy)
    if purgadas:
        print(f"  purgadas {purgadas} filas de días anteriores")

    # El backfill diario de tenencias y este daemon pegan al MISMO custodio a la
    # misma hora. Esperar a que cierre es cortesía con el job que no se puede romper.
    if "--sin-esperar-backfill" not in sys.argv:
        esperado = 0
        while not backfill_diario_termino(hoy):
            if esperado >= MAX_ESPERA_BACKFILL_MIN * 60:
                logger.warning("el backfill diario no cerró en %d min — arranco igual",
                               MAX_ESPERA_BACKFILL_MIN)
                break
            print(f"  esperando a que termine el backfill diario… ({esperado // 60} min)")
            time.sleep(ESPERA_BACKFILL_S)
            esperado += ESPERA_BACKFILL_S

    # ① BARRIDO DE APERTURA
    t0 = time.monotonic()
    ok, fallo, filas, motivos, desc = _refrescar_lote(lote, hoy, "apertura", workers)
    print(f"  ✓ barrido de apertura: {ok} ok · {fallo} fallidas · {filas} filas · "
          f"{time.monotonic() - t0:.0f}s" + (f" · motivos={motivos}" if motivos else ""))
    # Sobre el universo COMPLETO, no sobre una muestra: acá se ve si USDC es
    # sistemático o una rareza, y si USDL existe en algún lado.
    print(f"  monedas vistas y NO persistidas (fuera de {MONEDAS}): {desc or '(ninguna)'}")
    latido("apertura", ok)
    if una_pasada:
        return 0

    # ② + ③ DETECTOR + REFRESCO SELECTIVO, hasta el cierre.
    vistos: dict[str, set[str]] = {}
    ultimo_refresh: dict[str, float] = {}
    errores_seguidos = 0

    while _ahora_art().hour < HORA_CIERRE_ART:
        time.sleep(DETECTOR_S)
        if _hoy_art() != hoy:
            print("cambió el día — termino")
            break
        try:
            movs = detectar_movimientos(hoy)
        except Exception as e:
            logger.warning("detector falló: %s: %s", type(e).__name__, e)
            continue
        # El latido va ACÁ y no después del refresco: la vuelta en la que NO se
        # movió ninguna cuenta es la que hay que poder distinguir de un daemon
        # muerto, y esa vuelta no escribe ni una fila en control_saldos.
        latido("detector")

        # La cola de cada ciclo son TRES fuentes en un solo lote. Se juntan acá y
        # no en tres pasadas separadas para que una cuenta que aparece en dos
        # (se movió Y es prioritaria) se consulte UNA vez: el dict deduplica por
        # id_cuenta y el `origen` guarda por qué entró.
        ahora = time.monotonic()
        cola: dict[str, str] = {}
        origen_de: dict[str, str] = {}

        # ① el detector: reacciona a lo CONCERTADO hoy.
        for idc, comps in movs.items():
            if idc not in universo:
                continue
            nuevos = comps - vistos.get(idc, set())
            if not nuevos:
                continue
            if ahora - ultimo_refresh.get(idc, 0) < DEBOUNCE_S:
                continue
            cola[idc] = universo[idc]
            origen_de[idc] = "boleto"
            vistos.setdefault(idc, set()).update(nuevos)

        # ② + ③ prioritarias y envejecidas: la GARANTÍA de que una cuenta pasa
        # por el control se haya movido o no. El corte por frescura ya viene
        # aplicado en la query, así que lo que llega es lo que hay que consultar.
        n_prio = 0
        for idc in cuentas_a_revisar(hoy):
            if idc not in universo or idc in cola:
                continue
            if ahora - ultimo_refresh.get(idc, 0) < DEBOUNCE_S:
                continue
            # La denominación sale SIEMPRE del universo (la cruda de Aunesa) y
            # nunca de la tabla: la columna `cuenta` ya viene formateada
            # ("[105] LA S…") y usarla como denominación le agregaba un prefijo
            # más en cada pasada — "[21] [21] [21] …" en prod el 2026-08-19.
            cola[idc] = universo[idc]
            origen_de[idc] = "revision"
            n_prio += 1

        if not cola:
            continue
        for idc in cola:
            ultimo_refresh[idc] = ahora
        # El `origen` que se persiste es el del PRIMERO del lote — la columna
        # dice de qué ciclo vino la fila, no por qué entró esa cuenta puntual.
        # Distinguirlo por fila obligaría a partir el lote en dos y perder el
        # paralelismo, para un dato que solo se mira en debug.
        lote = list(cola.items())
        ok, fallo, filas, motivos, _ = _refrescar_lote(
            lote, hoy, "boleto" if n_prio == 0 else "revision", workers)
        print(f"  [{_ahora_art():%H:%M}] refrescadas {ok} cuenta(s) "
              f"({len(lote) - n_prio} por boleto · {n_prio} por revisión)"
              + (f" · {fallo} fallidas {motivos}" if fallo else ""))

        errores_seguidos = errores_seguidos + fallo if fallo else 0
        if errores_seguidos >= ERRORES_PARA_CORTE:
            logger.warning("%d fallos seguidos %s — pausa de %ds",
                           errores_seguidos, motivos, PAUSA_BREAKER_S)
            time.sleep(PAUSA_BREAKER_S)
            errores_seguidos = 0

    print("🏁 cierre de rueda — control_saldos termina")
    return 0


def _salir_limpio(signum, _frame):
    """SIGTERM → SystemExit(0), para que el `with JobRunLogger` alcance a cerrar.

    Morir por señal NO ejecuta los context managers: la corrida quedaría en
    `manager.job_runs` sin `finished_at` y SALUD la lee como un job colgado.
    SystemExit(0) y no SystemExit("texto"): con un string Python sale con código 1,
    systemd lo marca `Failed` y con `Restart=on-failure` lo resucita justo después
    del `systemctl stop` del cierre.
    """
    logger.info("señal %s — cierre ordenado", signum)
    raise SystemExit(0)


def main() -> int:
    import signal

    from core.job_runs import JobRunLogger

    # Ni `--dry` ni `--ver` son corridas del job: no ensucian manager.job_runs
    # (SALUD lee esa tabla y una corrida de 2 segundos parecería un job roto).
    if "--dry" in sys.argv or "--ver" in sys.argv:
        return run()
    signal.signal(signal.SIGTERM, _salir_limpio)
    with JobRunLogger("control_saldos"):
        return run()


if __name__ == "__main__":
    raise SystemExit(main())
