"""jobs/portafolio_backfill.py — backfill de portafolio.tenencia (SQL), self-healing.

Para cada día hábil del rango (default 01/06 → 17/06/2026), consulta la posición
de TODAS las cuentas Comitente+Propia y la inserta en `portafolio.tenencia`.

Principio (sin adivinar fechas): para la tenencia AL día D se consulta Aunesa con
`desde = D + 1 día hábil` (corrimiento confirmado: desde=X devuelve X-1) y se guarda
con `fecha = D`. Una fecha = la tenencia de esa fecha.

Características:
  * EFICIENTE: fetch en paralelo (timeout adaptativo), escritura BULK por fecha.
  * SELF-HEALING / RESUMABLE: cada (fecha, cuenta) se loguea en portafolio.backfill_log;
    re-correr saltea las OK/vacía y reintenta las que fallaron. Si una fecha rompe,
    sigue con la siguiente.
  * SIN exclusiones (incluye CDC). Guarda NOMINALES (cantidad) + precio + valuación.
  * 204 = cuenta sin posición (vacía), NO error.

Uso:
    python -m jobs.portafolio_backfill --diario             # CRON: snapshot del día hábil
        # anterior (= writer diario de SQL, corre 11:00 UTC L-V; ver deploy/crontab.txt)
    python -m jobs.portafolio_backfill                      # 01/06 → 17/06/2026
    python -m jobs.portafolio_backfill --desde 2026-06-01 --hasta 2026-06-17
    python -m jobs.portafolio_backfill --force              # re-hace todo (ignora log)
    python -m jobs.portafolio_backfill --cuentas 255,101    # subset (debug)
    python -m jobs.portafolio_backfill --meses 2026-01,2026-02,2026-03,2026-04
        # SOLO el último día hábil de cada mes listado (cierres del período fiscal)
"""
from __future__ import annotations

import sys
import threading
import time
from calendar import monthrange
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import holidays
import requests

from core.mongo import get_mongo_client_read
from core.postgres import get_pool
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)
from jobs.aum import _SESSION, POSICION_URL, _calcular_valuacion, autenticar, obtener_cuentas

# ── knobs ─────────────────────────────────────────────────────────────────────
TIMEOUT_DEFAULT = 60
TIMEOUT_HEAVY   = 240
HEAVY_IDS       = {"101", "106", "175", "463", "255", "194"}
MAX_WORKERS     = 10
RETRIES         = 3
_PARAMS_BASE = {
    "hasta": "", "tipoCuenta": "Comitentes y propias",
    "nivel": "Especie x cuenta", "ocultarCerradas": "true",
}

_FERIADOS = holidays.Argentina()
_lock = threading.Lock()
_hdr: dict = {}
# Contrapartes para marcar `aum` ('si'/'no') al insertar. Se cargan en main().
_CONT_IDS: frozenset[str] = frozenset()
_CONT_NAMES: frozenset[str] = frozenset()


# ── fechas ────────────────────────────────────────────────────────────────────
def _es_habil(d: date) -> bool:
    return d.weekday() < 5 and d not in _FERIADOS


def _prox_habil(d: date) -> date:
    d = d + timedelta(days=1)
    while not _es_habil(d):
        d = d + timedelta(days=1)
    return d


def _habiles(desde: date, hasta: date) -> list[date]:
    out, d = [], desde
    while d <= hasta:
        if _es_habil(d):
            out.append(d)
        d = d + timedelta(days=1)
    return out


def _ultimo_habil_del_mes(anio: int, mes: int) -> date:
    """Último día hábil del mes (camina hacia atrás desde el último día calendario)."""
    d = date(anio, mes, monthrange(anio, mes)[1])
    while not _es_habil(d):
        d = d - timedelta(days=1)
    return d


def _timeout_for(idc: str, denom: str) -> int:
    if idc in HEAVY_IDS or "CDC" in (denom or "").upper():
        return TIMEOUT_HEAVY
    return TIMEOUT_DEFAULT


def _reauth():
    with _lock:
        _hdr["h"] = autenticar()


# ── schema ────────────────────────────────────────────────────────────────────
def _ensure_schema():
    ddl = [
        "CREATE SCHEMA IF NOT EXISTS portafolio",
        """CREATE TABLE IF NOT EXISTS portafolio.tenencia (
            fecha     date NOT NULL, id_cuenta text NOT NULL, cuenta text,
            unidad    text NOT NULL, ticker text, cartera text,
            cantidad  numeric, precio numeric, valuacion numeric, moneda text,
            PRIMARY KEY (fecha, id_cuenta, unidad))""",
        """CREATE TABLE IF NOT EXISTS portafolio.backfill_log (
            fecha date NOT NULL, id_cuenta text NOT NULL, status text,
            n integer, detalle text, actualizado timestamptz DEFAULT now(),
            PRIMARY KEY (fecha, id_cuenta))""",
        "CREATE INDEX IF NOT EXISTS ix_tenencia_cuenta_fecha ON portafolio.tenencia (id_cuenta, fecha)",
        # `aum` ('si'/'no'): si la fila cuenta como AuM (filtro _aum_filters). La vista /aum
        # filtra aum='si'. Se setea al insertar (acá) — no hace falta correr marcar_aum aparte.
        "ALTER TABLE portafolio.tenencia ADD COLUMN IF NOT EXISTS aum text",
    ]
    with get_pool().connection() as conn, conn.cursor() as cur:
        for stmt in ddl:
            cur.execute(stmt)
        conn.commit()


def cargar_contrapartes() -> None:
    """Carga las listas de contrapartes en los globals — para marcar `aum` ('si'/'no')
    al insertar (regla _aum_filters). Lo llaman main() y reparar_timeouts."""
    global _CONT_IDS, _CONT_NAMES
    _CONT_IDS = load_contrapartes_id_cuentas()
    _CONT_NAMES = load_contrapartes_names()


def _load_assets_map() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for a in get_mongo_client_read()["Valuaciones"]["Assets"].find(
            {}, {"_id": 0, "unidad": 1, "TICKER": 1, "CARTERA": 1}):
        if a.get("unidad"):
            out[a["unidad"]] = {"ticker": (a.get("TICKER") or "").strip() or None,
                                "cartera": (a.get("CARTERA") or "").strip() or None}
    return out


def _ya_hechas(iso: str) -> set[str]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta FROM portafolio.backfill_log "
                    "WHERE fecha = %s AND status IN ('ok', 'vacia')", (iso,))
        return {r[0] for r in cur.fetchall()}


# ── fetch + parse (worker) ────────────────────────────────────────────────────
def _fetch_parse(idc: str, denom: str, desde: str, fecha_iso: str, amap: dict) -> tuple:
    """Devuelve (id_cuenta, status, registros). status: ok|vacia|timeout|error_*."""
    to = _timeout_for(idc, denom)
    params = {"desde": desde, **_PARAMS_BASE}
    for intento in range(1, RETRIES + 1):
        try:
            resp = _SESSION.get(POSICION_URL.format(idc), params=params,
                                headers=_hdr["h"], timeout=to)
            if resp.status_code == 401:
                _reauth()
                continue
            if resp.status_code == 204:
                return idc, "vacia", []
            if resp.status_code != 200:
                if intento < RETRIES:
                    time.sleep(2 ** intento)
                    continue
                return idc, f"error_http_{resp.status_code}", []
            data = resp.json()
            return idc, "ok", _parse(data, idc, denom, fecha_iso, amap)
        except requests.exceptions.Timeout:
            if intento < RETRIES:
                continue
            return idc, "timeout", []
        except Exception as e:
            if intento < RETRIES:
                time.sleep(2 ** intento)
                continue
            return idc, f"error:{type(e).__name__}", []
    return idc, "error_reauth", []


def _valuacion(cartera: str | None, precio: float, cantidad: float, tipo: str) -> float:
    """Valuación de una posición.

    El cash / cartera MONEDAS (ARS, USD efectivo) NUNCA se divide por 100 — es
    `precio × cantidad` y listo. El resto sigue la regla por `tipoTitulo` de
    `jobs/aum._calcular_valuacion` (÷100 para renta fija que cotiza en paridad).

    A diferencia del writer Mongo (`jobs/aum.py::procesar`), acá la CARTERA está
    disponible (viene del assets map) → blindamos el ÷100 del cash en el ORIGEN.
    Esto evita el bug histórico de MONEDAS subvaluado a 1/100 (ej. ARS).
    """
    if (cartera or "").strip().upper() == "MONEDAS":
        return round(precio * cantidad, 6)
    return _calcular_valuacion({"precio": precio, "cantidad": cantidad, "tipoTitulo": tipo})


def _parse(data, idc: str, denom: str, fecha_iso: str, amap: dict) -> list[dict]:
    """Acumulado · cantidad×-1 · groupby (unidad,tipoTitulo) · valuación. SIN exclusiones."""
    if not isinstance(data, list):
        return []
    cuenta_str = f"[{idc}] {denom}"
    grupos: dict[tuple, dict] = defaultdict(
        lambda: {"cantidad": 0.0, "precio": 0.0, "tipo": "", "moneda": None})
    for r in data:
        if not isinstance(r, dict) or r.get("informacion") != "Acumulado":
            continue
        unidad = r.get("unidad")
        if not unidad:
            continue
        cta = r.get("cuenta") or cuenta_str
        try:
            cant = float(r.get("cantidad") or 0) * -1
        except (TypeError, ValueError):
            cant = 0.0
        try:
            prec = float(r.get("precio") or 0)
        except (TypeError, ValueError):
            prec = 0.0
        tipo = r.get("tipoTitulo") or ""
        g = grupos[(unidad, tipo, cta)]
        g["cantidad"] += cant
        g["precio"] = max(g["precio"], prec)
        g["moneda"] = r.get("moneda") or g["moneda"]
    out = []
    for (unidad, tipo, cta), g in grupos.items():
        if g["cantidad"] == 0:
            continue
        a = amap.get(unidad, {})
        val = _valuacion(a.get("cartera"), g["precio"], g["cantidad"], tipo)
        aum = "no" if is_excluded(cta, unidad, id_cuenta=idc,
                                  contrapartes_ids=_CONT_IDS, contrapartes_names=_CONT_NAMES) else "si"
        out.append({
            "fecha": fecha_iso, "id_cuenta": idc, "cuenta": cta, "unidad": unidad,
            "ticker": a.get("ticker"), "cartera": a.get("cartera"),
            "cantidad": round(g["cantidad"], 4), "precio": round(g["precio"], 6),
            "valuacion": val, "moneda": g["moneda"], "aum": aum,
        })
    return out


# ── escritura por fecha (bulk, 1 transacción) ─────────────────────────────────
def _write_date(iso: str, registros: list[dict], status_by: dict[str, tuple]):
    cuentas = list(status_by.keys())
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM portafolio.tenencia WHERE fecha = %s AND id_cuenta = ANY(%s)",
                    (iso, cuentas))
        if registros:
            cur.executemany(
                "INSERT INTO portafolio.tenencia "
                "(fecha,id_cuenta,cuenta,unidad,ticker,cartera,cantidad,precio,valuacion,moneda,aum) "
                "VALUES (%(fecha)s,%(id_cuenta)s,%(cuenta)s,%(unidad)s,%(ticker)s,%(cartera)s,"
                "%(cantidad)s,%(precio)s,%(valuacion)s,%(moneda)s,%(aum)s)",
                registros)
        logrows = [{"fecha": iso, "id_cuenta": c, "status": s[0], "n": s[1], "detalle": s[2]}
                   for c, s in status_by.items()]
        cur.executemany(
            "INSERT INTO portafolio.backfill_log (fecha,id_cuenta,status,n,detalle,actualizado) "
            "VALUES (%(fecha)s,%(id_cuenta)s,%(status)s,%(n)s,%(detalle)s,now()) "
            "ON CONFLICT (fecha,id_cuenta) DO UPDATE SET "
            "status=EXCLUDED.status, n=EXCLUDED.n, detalle=EXCLUDED.detalle, actualizado=now()",
            logrows)
        conn.commit()


def _opt(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main() -> int:
    force = "--force" in sys.argv
    workers = int(_opt("--workers", MAX_WORKERS))
    subset = _opt("--cuentas")
    meses_arg = _opt("--meses")

    if "--diario" in sys.argv:
        # Modo DIARIO (cron): snapshotea el día hábil ANTERIOR a hoy, igual que jobs/aum.py.
        # desde = D+1 hábil (= hoy). Es el writer que mantiene SQL al día.
        d = datetime.now().date() - timedelta(days=1)
        while not _es_habil(d):
            d -= timedelta(days=1)
        dias = [d]
        etiqueta = f"diario (día hábil anterior: {d.isoformat()})"
    elif meses_arg:
        # Modo "fines de mes": un solo día por mes = el último hábil. Mismo
        # corrimiento corregido (desde = D+1 hábil) que el resto del backfill.
        dias = sorted({_ultimo_habil_del_mes(int(ym.split("-")[0]), int(ym.split("-")[1]))
                       for ym in meses_arg.split(",") if ym.strip()})
        etiqueta = f"último hábil de [{meses_arg}]"
    else:
        desde_arg = _opt("--desde", "2026-06-01")
        hasta_arg = _opt("--hasta", "2026-06-17")
        d0 = datetime.strptime(desde_arg, "%Y-%m-%d").date()
        d1 = datetime.strptime(hasta_arg, "%Y-%m-%d").date()
        dias = _habiles(d0, d1)
        etiqueta = f"{desde_arg} → {hasta_arg}"

    print(f"=== BACKFILL portafolio.tenencia · {etiqueta} · "
          f"{len(dias)} días · workers={workers} · force={force} ===")
    _ensure_schema()
    cargar_contrapartes()   # marca `aum` al insertar (lo lee _parse vía los globals)
    amap = _load_assets_map()
    _hdr["h"] = autenticar()
    df = obtener_cuentas(_hdr["h"])
    universo = [(str(r["id"]), str(r["denominacion"])) for _, r in df.iterrows()]
    if subset:
        ids = {c.strip() for c in subset.split(",")}
        universo = [u for u in universo if u[0] in ids]
    print(f"  cuentas: {len(universo)}  ·  assets en mapa: {len(amap)}\n")

    t_run = time.monotonic()
    for D in dias:
        iso = D.isoformat()
        desde = _prox_habil(D).strftime("%d/%m/%Y")   # ← regla: desde = D + 1 hábil
        hechas = set() if force else _ya_hechas(iso)
        pend = [(idc, dn) for idc, dn in universo if idc not in hechas]
        if not pend:
            print(f"[{iso}] ya completo ({len(hechas)} cuentas) — skip")
            continue

        print(f"[{iso}] desde={desde} · pendientes={len(pend)} (ya hechas={len(hechas)})")
        t0 = time.monotonic()
        registros: list[dict] = []
        status_by: dict[str, tuple] = {}
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_fetch_parse, idc, dn, desde, iso, amap): idc
                        for idc, dn in pend}
                for done, f in enumerate(as_completed(futs), 1):
                    idc, status, recs = f.result()
                    status_by[idc] = (status if status in ("ok", "vacia", "timeout")
                                      else status[:40], len(recs), "")
                    registros.extend(recs)
                    if done % 200 == 0:
                        print(f"    … {done}/{len(pend)}")
            _write_date(iso, registros, status_by)
            ok = sum(1 for s in status_by.values() if s[0] == "ok")
            vac = sum(1 for s in status_by.values() if s[0] == "vacia")
            to = sum(1 for s in status_by.values() if s[0] == "timeout")
            er = len(status_by) - ok - vac - to
            print(f"  ✓ {iso}: OK={ok} vacía={vac} TIMEOUT={to} ERROR={er} · "
                  f"filas insertadas={len(registros)} · {time.monotonic()-t0:.0f}s")
        except Exception as e:
            print(f"  ✗ {iso}: la fecha falló ({type(e).__name__}: {e}) — sigo con la próxima")
            continue

    print(f"\n🏁 Backfill terminado en {time.monotonic()-t_run:.0f}s. "
          f"Re-corré para reintentar lo que haya quedado en TIMEOUT/ERROR (self-healing).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
