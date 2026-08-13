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

Uso:
    python -m jobs.control_saldos                    # daemon: corre hasta el cierre
    python -m jobs.control_saldos --una-pasada       # barrido de apertura y termina
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

# Monedas que se persisten. Arranca en las tres que pidió el negocio.
# OJO — en el sondeo de la 805 apareció **USDC** ("Dólar cable") y NO apareció
# USDL. No se agrega USDC por decisión de negocio pendiente: el job CUENTA cuántas
# filas de moneda descartó y con qué código (stat `monedas_descartadas`), así que
# el dato para decidir sale del primer día de corrida y no de una suposición.
MONEDAS: tuple[str, ...] = ("ARS", "USD", "USDL")

# Aunesa manda las tenencias con el signo invertido (ver el docstring). Es la
# MISMA corrección que aplica el job diario de tenencias.
SIGNO = -1

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
    justo lo que tiene que pasar.
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
            # entrar acá y contarlos taparía la señal que interesa (¿existe USDL?
            # ¿cuánto pesa USDC?).
            if str(r.get("tipoTitulo") or "").strip().lower() == "moneda":
                descartadas[esp] = descartadas.get(esp, 0) + 1
            continue
        g = grupos.setdefault(esp, {"liq": 0.0, "pen": 0.0, "n": 0})
        g["liq"] += _num(r.get("cantidadLiquidada"))
        g["pen"] += _num(r.get("cantidadPendienteLiquidar"))
        g["n"] += 1

    cuenta_str = f"[{idc}] {denom}" if denom else f"[{idc}]"
    out = []
    for esp, g in grupos.items():
        cantidad = round(SIGNO * g["liq"], 4)
        if cantidad == 0:
            continue
        out.append({
            "id_cuenta": idc, "cuenta": cuenta_str, "ticker": esp,
            "cantidad": cantidad,
            "cantidad_pendiente": round(SIGNO * g["pen"], 4),
            "filas_origen": g["n"],
        })
    return out, descartadas


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


def refrescar_cuenta(idc: str, denom: str, hoy: date, origen: str) -> tuple[bool, str, int]:
    """(ok, motivo, filas escritas) de una cuenta."""
    ok, crudo, why = _traer(idc, hoy)
    if not ok:
        return False, why, 0
    registros, _ = parsear(crudo, idc, denom)
    _escribir(hoy, idc, registros, origen)
    return True, "", len(registros)


def _refrescar_lote(cuentas: list[tuple[str, str]], hoy: date, origen: str,
                    workers: int) -> tuple[int, int, int, dict[str, int]]:
    """(ok, fallidas, filas, motivos). Paralelo acotado.

    `motivos` cuenta POR QUÉ falló cada una ({'timeout': 3, 'http_500': 9}): sin
    eso, «12 fallidas» no distingue a Aunesa caído de un token vencido, y esa
    diferencia es la que decide si hay que hacer algo.
    """
    if not cuentas:
        return 0, 0, 0, {}
    ok = fallo = filas = 0
    motivos: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(refrescar_cuenta, idc, dn, hoy, origen): idc
                for idc, dn in cuentas}
        for f in as_completed(futs):
            try:
                bien, why, n = f.result()
            except Exception as e:
                logger.warning("cuenta %s explotó: %s: %s", futs[f], type(e).__name__, e)
                bien, why, n = False, type(e).__name__, 0
            if bien:
                ok += 1
                filas += n
            else:
                fallo += 1
                motivos[why or "?"] = motivos.get(why or "?", 0) + 1
    return ok, fallo, filas, motivos


# ── modo --dry: mirar sin escribir ────────────────────────────────────────────
def dry(cuentas: list[tuple[str, str]], hoy: date) -> int:
    """Imprime lo que persistiría, SIN tocar la base. Cero escrituras.

    Sirve para las tres preguntas que quedan abiertas y que solo contesta prod:
    qué separa las filas repetidas de la misma moneda, si USDL existe en algún
    lado, y cuánto tarda una llamada (→ cuánto dura el barrido de apertura).
    """
    print(f"\n{'=' * 78}\nDRY RUN — no se escribe una sola fila   ·   fecha="
          f"{hoy.strftime(FMT_FECHA)}\n{'=' * 78}")
    print(f"   monedas persistidas: {MONEDAS}   ·   signo aplicado: {SIGNO}")
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
            # Con pocas cuentas se muestra el CRUDO fila por fila: es lo único que
            # puede revelar qué separa dos filas de la misma moneda.
            for r in sorted(monedas_crudas, key=lambda x: str(x.get("especie"))):
                print(f"       crudo  {json.dumps(r, ensure_ascii=False)[:250]}")
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
        # Proyección del barrido de apertura sobre el universo real.
        for n_cuentas in (1874,):
            est = n_cuentas * (p50 / 1000) / WORKERS
            print(f"   barrido de {n_cuentas} cuentas con {WORKERS} workers ≈ "
                  f"{est / 60:.1f} min")
    print(f"   filas con saldo NEGATIVO en la muestra: {negativas}")
    print(f"   monedas VISTAS y DESCARTADAS (no están en MONEDAS): "
          f"{tot_desc or '(ninguna)'}")
    print("   → si acá aparece USDC seguido, es la decisión de negocio a tomar;")
    print("     si nunca aparece USDL, sacarlo de MONEDAS es gratis.\n")
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
    print(f"  cuentas: {len(universo)} (excluidas {n_contrapartes} contrapartes)")
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
        return dry(lote, hoy)

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
    ok, fallo, filas, motivos = _refrescar_lote(lote, hoy, "apertura", workers)
    print(f"  ✓ barrido de apertura: {ok} ok · {fallo} fallidas · {filas} filas · "
          f"{time.monotonic() - t0:.0f}s" + (f" · motivos={motivos}" if motivos else ""))
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

        ahora = time.monotonic()
        cola = []
        for idc, comps in movs.items():
            if idc not in universo:
                continue
            nuevos = comps - vistos.get(idc, set())
            if not nuevos:
                continue
            if ahora - ultimo_refresh.get(idc, 0) < DEBOUNCE_S:
                continue
            cola.append((idc, universo[idc]))
            vistos.setdefault(idc, set()).update(nuevos)
            ultimo_refresh[idc] = ahora

        if not cola:
            continue
        ok, fallo, filas, motivos = _refrescar_lote(cola, hoy, "boleto", workers)
        print(f"  [{_ahora_art():%H:%M}] refrescadas {ok} cuenta(s) por boleto"
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

    if "--dry" in sys.argv:        # el dry no es una corrida: no ensucia job_runs
        return run()
    signal.signal(signal.SIGTERM, _salir_limpio)
    with JobRunLogger("control_saldos"):
        return run()


if __name__ == "__main__":
    raise SystemExit(main())
