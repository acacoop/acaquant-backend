"""`scripts/diag_ingesta.py` — ¿CUÁNTO TRAJO CADA JOB DE INGESTA, corrida por corrida?

Read-only sobre `manager.job_runs`. Es el dato que necesita la decisión de
`ingesta_encogida` (REGLA #2: medir antes de codear): para cada job que trae
datos de afuera, sus últimas corridas con el contador de volumen, la mediana de
las corridas buenas y cuánto se apartó la última. Sin umbral: se mira y se decide.

Uso:
    python -m scripts.diag_ingesta            # últimas 12 corridas por job
    python -m scripts.diag_ingesta --n 30
"""
from __future__ import annotations

import argparse
import statistics
import sys

# job (tipo en manager.job_runs) → los stats candidatos a ser SU volumen.
CANDIDATOS: dict[str, list[str]] = {
    "operaciones_informes": ["filas", "cuentas_con_ops", "cuentas_fallidas"],
    "negocio_movimientos":  ["boletos", "upsertados", "anulados"],
    "aum":                  ["cuentas_ok", "filas", "cuentas_vacia", "timeouts", "errores"],
    "interbanking_sync":    ["movimientos", "dias", "cuentas_ok", "cuentas_error"],
    "mayor_sync":           ["movimientos_banco", "asientos_api"],
    "sync_comitentes":      ["recibidas", "a_procesar", "upserted", "estado_bajado"],
    "research_mail":        ["mails_vistos", "nuevos"],
    "ap5_portfolio":        ["filas_crudas", "filas_unicas"],
    "aranceles":            ["informes_obtenidos", "match", "sin_match"],
    "snapshot_cierre":      [],
    "mercado_1816_discovery": ["relevados", "catalogo"],
    "precios_acciones_daily": [],
    "argentina_datos":      [],
}


def _corridas(job: str, n: int) -> list[dict]:
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_char(finished_at AT TIME ZONE 'America/Argentina/Buenos_Aires', "
            "               'DD/MM HH24:MI'), status, data->'stats' "
            "  FROM manager.job_runs WHERE tipo = %s AND finished_at IS NOT NULL "
            " ORDER BY finished_at DESC LIMIT %s", (job, n))
        return [{"cuando": r[0], "status": r[1], "stats": dict(r[2] or {})}
                for r in cur.fetchall()]


def _num(v):
    return v if isinstance(v, int | float) and not isinstance(v, bool) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    args = ap.parse_args()

    for job, cands in CANDIDATOS.items():
        filas = _corridas(job, args.n)
        print(f"\n══ {job} ══  ({len(filas)} corridas)")
        if not filas:
            print("  sin corridas en manager.job_runs")
            continue
        ultimo = filas[0]["stats"]
        numericos = sorted(k for k, v in ultimo.items() if _num(v) is not None)
        print("  stats numéricos de la última corrida: " + ", ".join(numericos))
        cols = cands or numericos[:6]
        if not cols:
            continue
        ancho = max(12, *(len(c) for c in cols))
        print("  " + f"{'cuándo':<12}{'status':<9}" + "".join(f"{c:>{ancho}}" for c in cols))
        for f in filas:
            print("  " + f"{f['cuando']:<12}{(f['status'] or '?'):<9}"
                  + "".join(f"{f['stats'].get(c, '·')!s:>{ancho}}" for c in cols))
        # Mediana de las corridas BUENAS anteriores a la última, y el ratio de la última.
        for c in cols:
            hist = [_num(f["stats"].get(c)) for f in filas[1:] if f["status"] == "ok"]
            hist = [h for h in hist if h is not None]
            hoy = _num(ultimo.get(c))
            if len(hist) >= 3 and hoy is not None:
                med = statistics.median(hist)
                ratio = (hoy / med) if med else None
                marca = "  ← ENCOGIDA" if ratio is not None and ratio < 0.5 else ""
                print(f"  {c:<22} última {hoy!s:>8} · mediana({len(hist)} ok) {med:>10.1f}"
                      f" · ratio {ratio if ratio is None else round(ratio, 2)!s:>6}{marca}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
