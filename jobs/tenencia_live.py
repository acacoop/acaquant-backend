"""jobs/tenencia_live.py — posición T0/T1 del día, refrescada durante la rueda.

QUÉ ES Y POR QUÉ EXISTE
=======================
`portafolio.tenencia` (el writer diario `portafolio_backfill --diario`) guarda la
posición **liquidada de ayer**, una vez por día a las 11 UTC. Está bien y no se
toca: es la foto conciliada, inmutable, y de ahí cuelga TODO el negocio.

Lo que no tiene es el PRESENTE. Este daemon lo agrega **al lado**, en una tabla
propia, sin tocar una sola fila de lo que ya existe.

Guarda DOS versiones de la posición del día, las dos con `fecha = hoy`:

  t0  →  se consulta con `desde = hoy + 1 hábil`  →  posición LIQUIDADA A HOY.
         Lo que está hoy en custodia: lo que se puede entregar, garantizar, caucionar.

  t1  →  se consulta con `desde = hoy + 2 hábiles` →  posición liquidada a MAÑANA.
         Ya con lo concertado hoy adentro (los 24hs liquidan mañana). Es la
         posición "económica": lo que ya es del cliente aunque no haya liquidado.

La regla que hace que eso funcione (medida el 2026-08-11, ver `docs/API.md` y el
endpoint `/api/manager/aunesa/posicion`): **`desde = X` devuelve la posición
liquidada al día hábil ANTERIOR a X.** Por eso t0 pide hoy+1 y t1 pide hoy+2.
La columna `desde_consultado` guarda la fecha que efectivamente se mandó, así la
fila se explica sola y nadie tiene que reconstruir la regla para auditarla.

NO ES ACUMULATIVA: siempre tiene el día de hoy y nada más. El primer barrido del
día borra lo anterior.

CÓMO HACE PARA NO SER CARO (el diseño)
======================================
Refrescar 1.870 cuentas cada 20 minutos serían >100.000 llamadas por día contra un
custodio del que depende el job que sí importa. En vez de eso, tres ritmos:

  1. BARRIDO DE APERTURA — una vez, todas las cuentas. Es el 83% del costo del día
     y es lo que da la garantía de que la tabla está completa.
  2. DETECTOR — cada 3 minutos, **UNA sola llamada** a `consolidadosGenerales`, que
     devuelve los movimientos del día de TODAS las cuentas (no tiene parámetro de
     cuenta). Los comprobantes nuevos dicen exactamente quién se movió.
  3. REFRESCO SELECTIVO — solo esas cuentas. Medido con el user: operan como máximo
     ~100 cuentas por día, así que la cola siempre es chica.

Total estimado ~4.500 llamadas/día contra las ~1.800 del job diario. El detector
ve mucho más que compraventas: `consolidadosGenerales` trae también acreencias
(renta/amortización/dividendos), depósitos, transferencias, extracciones y
cauciones — por eso no hace falta un barrido de respaldo.

LO QUE ESTE JOB NO HACE (a propósito)
=====================================
* NO escribe en `portafolio.tenencia`, `portafolio.assets`, AuM, carteras, PnL ni
  valuaciones. Solo su tabla.
* NO da de alta assets nuevos. El job diario sí lo hace (`_alta_assets_nuevos`);
  replicarlo acá sería exactamente el efecto lateral que este job debe evitar. Una
  unidad desconocida se guarda igual, sin metadata.
* NO arranca su barrido hasta que el backfill diario terminó (lo chequea en
  `manager.job_runs`). Los dos pegan al MISMO endpoint de Aunesa y el que no se
  puede romper es el viejo.

Uso:
    python -m jobs.tenencia_live                 # daemon: corre hasta el cierre
    python -m jobs.tenencia_live --una-pasada    # barrido de apertura y termina
    python -m jobs.tenencia_live --cuentas 805,1346   # subset (debug)
    python -m jobs.tenencia_live --sin-esperar-backfill  # no espera al job diario
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta

import requests

from core import aunesa
from core.calendario import es_habil, proximo_habil
from core.postgres import get_job_pool
from jobs.aum import _SESSION, POSICION_URL, autenticar, obtener_cuentas
from jobs.portafolio_backfill import (
    _PARAMS_BASE,
    _load_assets_map,
    _parse,
    _timeout_for,
    cargar_contrapartes,
)

logger = logging.getLogger("jobs.tenencia_live")

# ── knobs ─────────────────────────────────────────────────────────────────────
HORA_CIERRE_ART = 18          # el daemon termina a esta hora (ART)
DETECTOR_S      = 180         # cada cuánto se pregunta quién se movió
DEBOUNCE_S      = 60          # una cuenta no se refresca más seguido que esto
WORKERS         = 6           # MENOS que los 10 del job diario: este no es el importante
ESPERA_BACKFILL_S = 60        # cada cuánto se re-chequea si el job diario terminó
MAX_ESPERA_BACKFILL_MIN = 90  # techo de la espera (si no corrió, se arranca igual)

# Circuit breaker: si Aunesa se cae (incidente 2026-08-07: 1868 cuentas con HTTP
# 500), este daemon NO puede seguir martillando — le agregaría presión al custodio
# del que depende el job diario.
ERRORES_PARA_CORTE = 10
PAUSA_BREAKER_S    = 300

# `consolidadosGenerales` filtra por tipo de cuenta. El job de negocio usa
# "Comitente"; las PROPIAS (ej. [1839] ACA VALORES TRADING, que es la que más se
# mueve por cauciones) necesitan su propia llamada. El valor exacto para propias no
# está confirmado contra la API — si devuelve error se loguea y se sigue: el barrido
# de apertura las cubre igual, solo se pierde la detección intradía de esas cuentas.
TIPOS_CUENTA_DETECTOR = ("Comitente", "Propia")

_RE_ID_CUENTA = re.compile(r"^\[(\d+)\]")

# Token de Aunesa COMPARTIDO por los workers. El daemon vive horas y el token
# vence: se renueva en un solo lugar, con lock, para que 6 workers que reciben
# 401 a la vez no disparen 6 logins.
_hdr: dict = {}
_hdr_lock = threading.Lock()
_HORIZONTES = ("t0", "t1")


# ── fechas ────────────────────────────────────────────────────────────────────
def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _ahora_art() -> datetime:
    return datetime.now(UTC) - timedelta(hours=3)


def desde_de(horizonte: str, hoy: date) -> date:
    """Qué `desde` hay que mandarle a Aunesa para cada horizonte.

    Regla H1: `desde = X` devuelve la posición liquidada al día hábil ANTERIOR a X.
      t0 (liquidada a HOY)    → desde = hoy + 1 hábil
      t1 (liquidada a MAÑANA) → desde = hoy + 2 hábiles
    """
    d = proximo_habil(hoy)
    return d if horizonte == "t0" else proximo_habil(d)


# ── schema ────────────────────────────────────────────────────────────────────
def _ensure_schema() -> None:
    ddl = [
        "CREATE SCHEMA IF NOT EXISTS portafolio",
        """CREATE TABLE IF NOT EXISTS portafolio.tenencia_live (
            fecha            date NOT NULL,
            horizonte        text NOT NULL,
            id_cuenta        text NOT NULL,
            unidad           text NOT NULL,
            cuenta           text,
            ticker           text,
            cartera          text,
            cantidad         numeric,
            precio           numeric,
            valuacion        numeric,
            moneda           text,
            aum              text,
            tipo_titulo      text,
            gar_cantidad     numeric,
            desde_consultado date,
            origen           text,
            actualizado_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (fecha, horizonte, id_cuenta, unidad))""",
        "CREATE INDEX IF NOT EXISTS ix_tlive_cuenta "
        "ON portafolio.tenencia_live (id_cuenta, fecha, horizonte)",
        "CREATE INDEX IF NOT EXISTS ix_tlive_frescura "
        "ON portafolio.tenencia_live (fecha, actualizado_at)",
    ]
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        for stmt in ddl:
            cur.execute(stmt)
        conn.commit()


def _purgar(hoy: date) -> int:
    """La tabla tiene SIEMPRE el día de hoy y nada más (no es acumulativa)."""
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM portafolio.tenencia_live WHERE fecha < %s", (hoy,))
        n = cur.rowcount or 0
        conn.commit()
    return n


# ── esperar al job diario ─────────────────────────────────────────────────────
def backfill_diario_termino(hoy: date) -> bool:
    """True si la corrida de hoy del backfill diario ya cerró.

    Los dos jobs pegan al MISMO endpoint de Aunesa. En vez de hardcodear cuánto
    tarda el diario (que cambia), se mira si ya terminó.
    """
    try:
        with get_job_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT finished_at FROM manager.job_runs "
                "WHERE tipo = 'aum' AND started_at >= %s "
                "ORDER BY started_at DESC LIMIT 1", (hoy,))
            row = cur.fetchone()
    except Exception as e:
        logger.warning("no pude leer job_runs (%s) — sigo sin esperar", type(e).__name__)
        return True
    return bool(row and row[0])


# ── detector: UNA llamada, todas las cuentas ──────────────────────────────────
def detectar_movimientos(hoy: date) -> dict[str, set[str]]:
    """{id_cuenta: {comprobantes del día}} con UNA llamada por tipo de cuenta.

    `consolidadosGenerales` NO tiene parámetro de cuenta: devuelve los movimientos
    del día de toda la casa. Por eso el detector cuesta 1 llamada y no 1.870 — es
    lo que hace viable refrescar cada 3 minutos.
    """
    dia = hoy.strftime("%d/%m/%Y")
    out: dict[str, set[str]] = {}
    for tipo in TIPOS_CUENTA_DETECTOR:
        try:
            resp = aunesa.get("operaciones/consolidadosGenerales",
                              {"tiposCuenta": tipo, "concertacionDesde": dia,
                               "concertacionHasta": dia}, timeout=90)
            if resp.status_code == 204:
                continue
            if resp.status_code != 200:
                logger.warning("detector tiposCuenta=%s → HTTP %s (%s)",
                               tipo, resp.status_code, (resp.text or "")[:120])
                continue
            data = resp.json() if (resp.text or "").strip() else []
        except Exception as e:
            logger.warning("detector tiposCuenta=%s falló: %s: %s", tipo, type(e).__name__, e)
            continue
        for m in data if isinstance(data, list) else []:
            if not isinstance(m, dict):
                continue
            mm = _RE_ID_CUENTA.match(m.get("cuenta") or "")
            comp = m.get("comprobante")
            if mm and comp:
                out.setdefault(mm.group(1), set()).add(str(comp))
    return out


# ── refresco de una cuenta ────────────────────────────────────────────────────
def _reauth() -> dict:
    """Token nuevo, compartido por todos los workers (una sola vez, no N)."""
    with _hdr_lock:
        _hdr["h"] = autenticar()
        return _hdr["h"]


def _traer(idc: str, denom: str, desde: date) -> tuple[bool, list, str]:
    """(ok, filas crudas, motivo). ok=False = no se pudo consultar → NO se escribe.

    Distinguir "Aunesa dijo que no hay posición" (204 → ok, lista vacía) de "no
    pude preguntar" (timeout/500 → error) es lo que evita que un problema de red
    borre la posición de un cliente.

    ⚠️ EL 401 SE RE-AUTENTICA (incidente 2026-08-12). Este daemon vive horas con
    el MISMO token, y el token de Aunesa vence. La primera versión trataba el 401
    como un error más: al vencer, TODAS las cuentas empezaban a fallar y el
    circuit breaker se disparaba con «¿Aunesa caído?» cuando el custodio estaba
    perfecto. El job diario ya lo manejaba (`portafolio_backfill._fetch_parse`)
    porque dura minutos y raro que lo pise; acá es inevitable.

    `motivo` viaja hasta el log: sin él, «12 fallidas» no dice si fue timeout,
    500 o token vencido — que fue exactamente lo que costó diagnosticar.
    """
    params = {**_PARAMS_BASE, "desde": desde.strftime("%d/%m/%Y")}
    to = _timeout_for(idc, denom)
    for intento in (1, 2):
        try:
            resp = _SESSION.get(POSICION_URL.format(idc), params=params,
                                headers=_hdr.get("h") or _reauth(), timeout=to)
        except requests.exceptions.Timeout:
            return False, [], "timeout"
        except requests.exceptions.RequestException as e:
            return False, [], type(e).__name__
        if resp.status_code == 401 and intento == 1:
            _reauth()
            continue
        if resp.status_code == 204:
            return True, [], ""
        if resp.status_code != 200:
            return False, [], f"http_{resp.status_code}"
        try:
            data = resp.json()
        except Exception:
            return False, [], "json_invalido"
        return True, data if isinstance(data, list) else [], ""
    return False, [], "http_401"


def _escribir(hoy: date, horizonte: str, idc: str, desde: date,
              registros: list[dict], origen: str) -> None:
    """DELETE + INSERT de esa cuenta/horizonte en UNA transacción.

    Atómico a propósito: un lector nunca ve la cuenta a medio escribir. Y como solo
    se llama cuando la consulta salió bien, una caída de Aunesa deja la fila
    ANTERIOR intacta (con su `actualizado_at` viejo, que es la señal de que está
    envejecida) en vez de dejar al cliente sin posición.
    """
    filas = [{**r, "fecha": hoy, "horizonte": horizonte, "desde_consultado": desde,
              "origen": origen} for r in registros]
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM portafolio.tenencia_live "
            "WHERE fecha = %s AND horizonte = %s AND id_cuenta = %s", (hoy, horizonte, idc))
        if filas:
            cur.executemany(
                "INSERT INTO portafolio.tenencia_live "
                "(fecha,horizonte,id_cuenta,unidad,cuenta,ticker,cartera,cantidad,precio,"
                " valuacion,moneda,aum,tipo_titulo,gar_cantidad,desde_consultado,origen,"
                " actualizado_at) "
                "VALUES (%(fecha)s,%(horizonte)s,%(id_cuenta)s,%(unidad)s,%(cuenta)s,"
                " %(ticker)s,%(cartera)s,%(cantidad)s,%(precio)s,%(valuacion)s,%(moneda)s,"
                " %(aum)s,%(tipo_titulo)s,%(gar_cantidad)s,%(desde_consultado)s,%(origen)s,"
                " now())",
                filas)
        conn.commit()


def refrescar_cuenta(idc: str, denom: str, hoy: date, amap: dict,
                     origen: str) -> tuple[bool, str]:
    """Los DOS horizontes de una cuenta. (ok, motivo del primer fallo)."""
    ok_total, motivo = True, ""
    for horizonte in _HORIZONTES:
        desde = desde_de(horizonte, hoy)
        ok, crudo, why = _traer(idc, denom, desde)
        if not ok:
            ok_total = False
            motivo = motivo or why
            continue
        _escribir(hoy, horizonte, idc, desde,
                  _parse(crudo, idc, denom, hoy.isoformat(), amap), origen)
    return ok_total, motivo


def _refrescar_lote(cuentas: list[tuple[str, str]], hoy: date, amap: dict,
                    origen: str, workers: int) -> tuple[int, int, dict[str, int]]:
    """(ok, fallidas, motivos). Paralelo acotado — menos workers que el job diario.

    `motivos` cuenta POR QUÉ falló cada una ({'timeout': 3, 'http_500': 9}). Sin
    eso, «12 fallidas» no distingue a Aunesa caído de un token vencido — y esa
    diferencia es la que decide si hay que hacer algo o no."""
    if not cuentas:
        return 0, 0, {}
    ok = fallo = 0
    motivos: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(refrescar_cuenta, idc, dn, hoy, amap, origen): idc
                for idc, dn in cuentas}
        for f in as_completed(futs):
            try:
                bien, why = f.result()
            except Exception as e:
                logger.warning("cuenta %s explotó: %s: %s", futs[f], type(e).__name__, e)
                bien, why = False, type(e).__name__
            if bien:
                ok += 1
            else:
                fallo += 1
                motivos[why or "?"] = motivos.get(why or "?", 0) + 1
    return ok, fallo, motivos


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
    una_pasada = "--una-pasada" in sys.argv
    subset = _opt("--cuentas")
    workers = int(_opt("--workers", WORKERS))
    hoy = _hoy_art()

    print(f"=== TENENCIA LIVE · {hoy} · t0=desde {desde_de('t0', hoy)} · "
          f"t1=desde {desde_de('t1', hoy)} ===")
    if not es_habil(hoy):
        print("hoy NO es día hábil — nada que hacer")
        return 0

    _ensure_schema()
    purgadas = _purgar(hoy)
    if purgadas:
        print(f"purgadas {purgadas} filas de días anteriores")

    # El backfill diario y este job pegan al MISMO endpoint. Esperar a que cierre.
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

    cargar_contrapartes()
    amap = _load_assets_map()
    headers = _reauth()
    df = obtener_cuentas(headers)
    universo = {str(r["id"]): str(r["denominacion"]) for _, r in df.iterrows()}
    if subset:
        ids = {c.strip() for c in subset.split(",")}
        universo = {k: v for k, v in universo.items() if k in ids}
    print(f"  cuentas: {len(universo)} · assets: {len(amap)} · workers: {workers}")

    # ① BARRIDO DE APERTURA — el 83% del costo del día, una sola vez.
    t0 = time.monotonic()
    ok, fallo, motivos = _refrescar_lote(list(universo.items()), hoy, amap,
                                         "apertura", workers)
    print(f"  ✓ barrido de apertura: {ok} ok · {fallo} fallidas · "
          f"{time.monotonic() - t0:.0f}s" + (f" · motivos={motivos}" if motivos else ""))
    if una_pasada:
        return 0

    # ② + ③ DETECTOR + REFRESCO SELECTIVO, hasta el cierre.
    vistos: dict[str, set[str]] = {}      # id_cuenta → comprobantes ya procesados
    ultimo_refresh: dict[str, float] = {}  # id_cuenta → monotonic del último refresco
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

        ahora = time.monotonic()
        cola = []
        for idc, comps in movs.items():
            if idc not in universo:
                continue
            nuevos = comps - vistos.get(idc, set())
            if not nuevos:
                continue
            # Debounce: una cuenta con 20 boletos no se refresca 20 veces. Lo que
            # entre mientras tanto lo toma la pasada siguiente.
            if ahora - ultimo_refresh.get(idc, 0) < DEBOUNCE_S:
                continue
            cola.append((idc, universo[idc]))
            vistos.setdefault(idc, set()).update(nuevos)
            ultimo_refresh[idc] = ahora

        if not cola:
            continue
        ok, fallo, motivos = _refrescar_lote(cola, hoy, amap, "boleto", workers)
        print(f"  [{_ahora_art():%H:%M}] refrescadas {ok} cuenta(s) por boleto"
              + (f" · {fallo} fallidas {motivos}" if fallo else ""))

        # Circuit breaker: si Aunesa se cayó, frenar en vez de martillar.
        errores_seguidos = errores_seguidos + fallo if fallo else 0
        if errores_seguidos >= ERRORES_PARA_CORTE:
            # El motivo va en el mensaje: un 401 en masa es token vencido (se
            # renueva solo en _traer) y NO es Aunesa caído. Decir «¿Aunesa caído?»
            # a secas mandó a buscar un problema donde no había (2026-08-12).
            logger.warning("%d fallos seguidos %s — pausa de %ds",
                           errores_seguidos, motivos, PAUSA_BREAKER_S)
            time.sleep(PAUSA_BREAKER_S)
            errores_seguidos = 0
            _reauth()

    print("🏁 cierre de rueda — tenencia_live termina")
    return 0


def _salir_limpio(signum, _frame):
    """SIGTERM → SystemExit, para que el `with JobRunLogger` alcance a cerrar.

    El daemon termina SOLO al cierre de rueda, así que el `systemctl stop` de las
    21:05 UTC casi nunca lo alcanza. Pero si lo alcanza (o si alguien lo para a
    mano), la muerte por señal NO ejecuta los context managers: la corrida quedaría
    en `manager.job_runs` sin `finished_at`, y SALUD la lee como un job colgado.
    Convertirla en excepción hace que el cierre sea normal.
    """
    # SystemExit(0), NO SystemExit("texto"): con un string Python sale con código 1
    # y systemd lo marca `Failed with result 'exit-code'` — y con `Restart=on-failure`
    # lo RESUCITA después del `systemctl stop` de las 21:05, que es justo lo que el
    # unit quería evitar. Un cierre por señal es un cierre normal.
    logger.info("señal %s — cierre ordenado", signum)
    raise SystemExit(0)


def main() -> int:
    import signal

    from core.job_runs import JobRunLogger

    signal.signal(signal.SIGTERM, _salir_limpio)
    with JobRunLogger("tenencia_live"):
        return run()


if __name__ == "__main__":
    raise SystemExit(main())
