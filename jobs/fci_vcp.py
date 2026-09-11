"""jobs/fci_vcp.py — el VCP diario de cada fondo del universo → `mercado.fci_vcp`.

Doc madre: `docs/FCI.md`. Corre 20:30 UTC L-V, fuera de rueda.

Primary NO guarda histórico (trade history vacío), así que la serie se arma
acá, un día por corrida, con dos fuentes en orden de prioridad:

  1. `primary`  — la BANDA del CATÁLOGO (`get_detailed_instruments`, UNA llamada
                  para los 776 CIO): en una cuotaparte `low == high` y esa banda
                  ES el VCP del día. La fecha sale de `maturityDate`, que Primary
                  mueve con la sesión.

                  ⚠️ **Antes se pedía el `LA` fondo por fondo y no servía**
                  (2026-09-11): el LA es dato de rueda ABIERTA, así que a las
                  17:30 ART daba 627 de 755 fondos sin dato, más 128 errores de
                  los símbolos con `Nº`/acentos que rompen el REST. 0 puntos.
                  El catálogo no tiene ninguno de esos dos problemas.
  2. `tenencia` — el `precio` por cuotaparte de `portafolio.tenencia` del día,
                  para los fondos linkeados a un asset. Es lo que cubre los
                  BILATERALES (que Primary no tiene) y lo que rellena un día que
                  Primary no publicó. Nunca pisa una fila `primary`.

  (3. `manual` — la carga `scripts/fci_admin vcp`; solo entra donde no hay nada.)

BACKFILL desde la tenencia (`--backfill-tenencia --desde 2024-01-01`): toda la
historia del precio por unidad, para los fondos linkeados. Cumple REGLA #4:
scopeado (unidad = ANY de las linkeadas, sobre el índice (fecha, unidad)),
batcheado POR MES con sleep, idempotente (upsert por PK que respeta la
prioridad), y va por `run_job.sh`. `--medir` muestra el EXPLAIN de un mes y
cuenta filas antes de tocar nada.

Uso:
    python -m jobs.fci_vcp                                     # el día
    python -m jobs.fci_vcp --backfill-tenencia --desde 2024-01-01 --medir
    python -m jobs.fci_vcp --backfill-tenencia --desde 2024-01-01
    python -m jobs.fci_vcp --dry --solo 5
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, date, datetime, timedelta

import pyRofex

from core.fci_match import punto_de_catalogo, simbolo_de
from core.job_runs import JobRunLogger
from core.postgres import get_pool
from core.rofex_session import inicializar_sesion

logger = logging.getLogger(__name__)

_PRIORIDAD = {"primary": 3, "tenencia": 2, "manual": 1}
_PAUSA_MES_S = 1.5
_CIO = "CIO"
_MIN_CIO = 200   # debajo de esto la foto de Primary es anómala → no se escribe

# El upsert que respeta la prioridad: una fuente más débil no pisa una más fuerte.
_SQL_UPSERT = (
    "INSERT INTO mercado.fci_vcp (fci_id, fecha, vcp, fuente) VALUES (%s, %s, %s, %s) "
    "ON CONFLICT (fci_id, fecha) DO UPDATE SET vcp = EXCLUDED.vcp, fuente = EXCLUDED.fuente "
    "WHERE CASE mercado.fci_vcp.fuente WHEN 'primary' THEN 3 WHEN 'tenencia' THEN 2 ELSE 1 END "
    "   <= CASE EXCLUDED.fuente WHEN 'primary' THEN 3 WHEN 'tenencia' THEN 2 ELSE 1 END"
)


def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _desde_primary(cur, hoy: date, solo: int, dry: bool, jr: JobRunLogger) -> int:
    """El VCP del día de cada fondo con símbolo, desde el catálogo de Primary."""
    cur.execute("SELECT fci_id, simbolo_primary, nombre FROM mercado.fci "
                "WHERE simbolo_primary IS NOT NULL AND activo ORDER BY nombre")
    filas = cur.fetchall()
    if solo:
        filas = filas[:solo]
    jr.set_stat("primary_fondos", len(filas))
    if not filas:
        return 0

    inicializar_sesion()
    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        jr.error(f"get_detailed_instruments: {(res or {}).get('status')}")
        return 0
    cio = [i for i in (res.get("instruments") or []) if str(i.get("cficode") or "").startswith(_CIO)]
    jr.set_stat("primary_cio", len(cio))
    if len(cio) < _MIN_CIO:
        jr.error(f"foto de Primary anómala: {len(cio)} CIO (< {_MIN_CIO}) — no se escribe")
        return 0
    por_simbolo = {sym: i for i in cio if (sym := simbolo_de(i))}

    n = no_listados = sin_banda = 0
    fechas: dict[date, int] = {}
    for fci_id, sym, nombre in filas:
        inst = por_simbolo.get(sym)
        if inst is None:
            no_listados += 1
            continue
        punto = punto_de_catalogo(inst, hoy)
        if punto is None:
            sin_banda += 1
            if sin_banda <= 5:
                jr.log(f"   sin banda: {nombre}")
            continue
        fecha, px = punto
        fechas[fecha] = fechas.get(fecha, 0) + 1
        if not dry:
            cur.execute(_SQL_UPSERT, (fci_id, fecha, px, "primary"))
        elif n < 5:
            jr.log(f"   {nombre}: {px} @ {fecha}")
        n += 1
    jr.set_stat("primary_puntos", n)
    jr.set_stat("primary_no_listados", no_listados)
    jr.set_stat("primary_sin_banda", sin_banda)
    # La fecha que Primary le puso a la sesión: si no es hoy, el log lo dice (no
    # se corrige nada — la fecha la manda el catálogo, no el reloj del Droplet).
    jr.set_stat("primary_fechas", {f.isoformat(): c for f, c in sorted(fechas.items())})
    if not n:
        jr.error("Primary no dio ni un VCP (¿catálogo sin bandas?)")
    return n


_SQL_TENENCIA = (
    "SELECT t.fecha, f.fci_id, MAX(t.precio) "
    "FROM portafolio.tenencia t JOIN mercado.fci f ON f.unidad = t.unidad "
    "WHERE t.fecha >= %s AND t.fecha < %s AND t.unidad = ANY(%s) "
    "  AND t.precio IS NOT NULL AND t.precio > 0 "
    "GROUP BY t.fecha, f.fci_id"
)


def _unidades(cur) -> list[str]:
    cur.execute("SELECT unidad FROM mercado.fci WHERE unidad IS NOT NULL AND activo")
    return [r[0] for r in cur.fetchall()]


def _desde_tenencia(cur, desde: date, hasta_excl: date, unidades: list[str], dry: bool) -> int:
    cur.execute(_SQL_TENENCIA, (desde, hasta_excl, unidades))
    filas = cur.fetchall()
    if not dry:
        for fecha, fci_id, px in filas:
            cur.execute(_SQL_UPSERT, (fci_id, fecha, float(px), "tenencia"))
    return len(filas)


def _meses(desde: date, hasta: date):
    d = date(desde.year, desde.month, 1)
    while d <= hasta:
        sig = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
        yield max(d, desde), min(sig, hasta + timedelta(days=1))
        d = sig


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill-tenencia", action="store_true",
                    help="cargar la historia del precio de la tenencia (no pega a Primary)")
    ap.add_argument("--desde", type=date.fromisoformat, help="inicio del backfill")
    ap.add_argument("--medir", action="store_true", help="EXPLAIN + conteo de un mes, sin escribir")
    ap.add_argument("--solo", type=int, default=0, help="probar con N fondos")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    hoy = _hoy_art()

    with JobRunLogger("fci_vcp") as jr, get_pool().connection() as conn, conn.cursor() as cur:
        unidades = _unidades(cur)
        jr.set_stat("fondos_con_unidad", len(unidades))

        if a.backfill_tenencia:
            if not a.desde:
                jr.error("--backfill-tenencia necesita --desde")
                return 1
            if a.medir:
                d0, d1 = next(_meses(a.desde, hoy))
                cur.execute("EXPLAIN " + _SQL_TENENCIA, (d0, d1, unidades))
                for (linea,) in cur.fetchall():
                    jr.log("   " + linea)
                cur.execute("SELECT count(*) FROM portafolio.tenencia WHERE fecha >= %s AND fecha < %s "
                            "AND unidad = ANY(%s)", (d0, d1, unidades))
                jr.log(f"   filas de tenencia del primer mes ({d0} → {d1}): {cur.fetchone()[0]}")
                conn.rollback()
                return 0
            total = 0
            for d0, d1 in _meses(a.desde, hoy):
                n = _desde_tenencia(cur, d0, d1, unidades, a.dry)
                total += n
                if not a.dry:
                    conn.commit()
                jr.log(f"   {d0} → {d1}: {n} puntos")
                time.sleep(_PAUSA_MES_S)
            jr.set_stat("tenencia_puntos", total)
            jr.log(f"backfill tenencia{' DRY' if a.dry else ''}: {total} puntos desde {a.desde}")
            return 0

        _desde_primary(cur, hoy, a.solo, a.dry, jr)
        # tenencia de los últimos 3 días (el writer de tenencia corre 11 UTC con T-1)
        n_t = _desde_tenencia(cur, hoy - timedelta(days=3), hoy + timedelta(days=1), unidades, a.dry) \
            if unidades else 0
        jr.set_stat("tenencia_puntos", n_t)
        if a.dry:
            conn.rollback()
        else:
            conn.commit()
        jr.log(f"fci_vcp{' DRY' if a.dry else ''}: {jr.stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
