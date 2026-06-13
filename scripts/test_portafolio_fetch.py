"""scripts/test_portafolio_fetch.py — PRUEBA de fetch eficiente de posiciones (Portafolio).

Para una fecha: trae el listado de cuentas (ya filtrado a Comitente+Propia activas),
y consulta `posicionValuada` por cada una EN PARALELO, con:
  * timeout ADAPTATIVO — default 60s; cuentas pesadas (HEAVY_IDS + CDC por nombre) 240s.
  * fallas AISLADAS — si una da timeout/error, NO arrastra al resto.
  * 1 reintento ante timeout transitorio + re-auth con lock si expira el token.

NO escribe nada (es prueba del mecanismo + timeouts). Reporta por cuenta:
estado · tiempo · n posiciones (Acumulado) · timeout aplicado, y un resumen final.

Recordá: `desde=fecha` trae la posición del día hábil ANTERIOR (corrimiento confirmado).

Uso:
    python -m scripts.test_portafolio_fetch 30/05/2026
    python -m scripts.test_portafolio_fetch 30/05/2026 --cuentas 255,101,463   # subset
    python -m scripts.test_portafolio_fetch 30/05/2026 --limit 50 --workers 8
"""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from jobs.aum import autenticar, consultar_posicion, obtener_cuentas

# ════════════ knobs (tech-lead: razonable, no exagerado) ════════════
TIMEOUT_DEFAULT = 60        # la mayoría resuelve rápido
TIMEOUT_HEAVY   = 240       # 4 min para las pesadas (no 5) — sin esto dan error
HEAVY_IDS       = {"101", "106", "175", "463", "255", "194"}
MAX_WORKERS     = 8         # paralelismo; el slowest define el wall-clock, no la suma
# ════════════════════════════════════════════════════════════════════

_lock = threading.Lock()
_hdr: dict = {}


def _opt(flag: str, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _timeout_for(idc: str, denom: str) -> int:
    if idc in HEAVY_IDS:
        return TIMEOUT_HEAVY
    if "CDC" in (denom or "").upper():
        return TIMEOUT_HEAVY
    return TIMEOUT_DEFAULT


def _reauth():
    with _lock:
        _hdr["h"] = autenticar()


def fetch(idc: str, denom: str, fecha: str) -> tuple:
    to = _timeout_for(idc, denom)
    for intento in (1, 2):
        t0 = time.monotonic()
        try:
            data, reauth = consultar_posicion(idc, _hdr["h"], desde=fecha, timeout=to)
            if reauth:                                  # 401 → re-auth y reintento
                _reauth()
                data, reauth = consultar_posicion(idc, _hdr["h"], desde=fecha, timeout=to)
            dt = time.monotonic() - t0
            if data is None:
                return (idc, denom, to, "error_http", None, dt)
            n = sum(1 for r in data if isinstance(r, dict)
                    and r.get("informacion") == "Acumulado") if isinstance(data, list) else 0
            return (idc, denom, to, "ok", n, dt)
        except requests.exceptions.Timeout:
            if intento == 1:
                continue                                # 1 reintento ante blip transitorio
            return (idc, denom, to, "TIMEOUT", None, time.monotonic() - t0)
        except Exception as e:
            return (idc, denom, to, f"error:{type(e).__name__}", None, time.monotonic() - t0)


def main() -> int:
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not pos:
        print("Falta la fecha. Uso: python -m scripts.test_portafolio_fetch 30/05/2026")
        return 1
    fecha = pos[0]
    workers = int(_opt("--workers", MAX_WORKERS))
    limit = _opt("--limit")
    subset = _opt("--cuentas")

    print("Autenticando y trayendo listado de cuentas (Comitente+Propia activas)…")
    _hdr["h"] = autenticar()
    df = obtener_cuentas(_hdr["h"])
    universo = [(str(r["id"]), str(r["denominacion"])) for _, r in df.iterrows()]

    if subset:
        ids = {c.strip() for c in subset.split(",")}
        universo = [u for u in universo if u[0] in ids]
    if limit:
        universo = universo[: int(limit)]

    print(f"Cuentas a consultar: {len(universo)}  ·  fecha desde={fecha}  ·  workers={workers}")
    print(f"  (pesadas con timeout {TIMEOUT_HEAVY}s: {sorted(HEAVY_IDS)} + las CDC; resto {TIMEOUT_DEFAULT}s)\n")

    t_ini = time.monotonic()
    resultados: list[tuple] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, idc, denom, fecha): idc for idc, denom in universo}
        for f in as_completed(futs):
            r = f.result()
            resultados.append(r)
            idc, denom, to, estado, n, dt = r
            print(f"  [{len(resultados):>4}/{len(universo)}] {estado:<11} {idc:<7} "
                  f"{dt:>6.1f}s  n={'' if n is None else n:<5} to={to}s  {denom[:32]}")

    wall = time.monotonic() - t_ini
    ok = [r for r in resultados if r[3] == "ok"]
    tos = [r for r in resultados if r[3] == "TIMEOUT"]
    errs = [r for r in resultados if r[3] not in ("ok", "TIMEOUT")]
    total_pos = sum(r[4] or 0 for r in ok)

    print("\n" + "=" * 64)
    print(f"RESUMEN  ·  wall-clock total: {wall:.1f}s")
    print(f"  OK: {len(ok)}   TIMEOUT: {len(tos)}   ERROR: {len(errs)}   "
          f"posiciones (Acumulado) totales: {total_pos:,}")
    if tos:
        print(f"  Cuentas con TIMEOUT (subir su timeout): {[r[0] for r in tos]}")
    if errs:
        print(f"  Cuentas con ERROR: {[(r[0], r[3]) for r in errs]}")
    lentas = sorted(ok, key=lambda r: -r[5])[:10]
    if lentas:
        print("  Top 10 más lentas (OK):")
        for r in lentas:
            print(f"    {r[0]:<7} {r[5]:>6.1f}s  to={r[2]}s  {r[1][:32]}")
    print("\n(NO se escribió nada — es prueba del mecanismo de fetch + timeouts.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
