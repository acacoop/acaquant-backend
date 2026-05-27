"""actividad_mensual.py — snapshot mensual de CUENTAS ACTIVAS.

"Cuenta activa en el mes M" = id_cuenta con ≥1 boleto operativo
(`_CATS_OPERACIONES`) con fecha dentro del mes calendario M. Es la métrica que
pidió el jefe comercial. Se precalcula offline acá y se persiste en
`Clientes.ActividadMensual` (1 doc por mes×cuenta) para que la serie histórica
sea instantánea — mismo patrón que `Valuaciones.ConsolidadoCuentas`.

Punto clave (point-in-time): la ACTIVIDAD sale de `NegocioMovimientos` (dato
inmutable). El operador/segmento se toma de `Clientes.Comitentes` al momento de
correr el job y se CONGELA en el doc. Los meses que se backfillean ahora usan la
asignación actual (es lo único reconstruible); de acá en más, cada corrida
estampa la asignación vigente → historia point-in-time real.

Uso:
    python -m jobs.actividad_mensual                 # mes corriente (ART)
    python -m jobs.actividad_mensual --mes 2026-04   # un mes puntual
    python -m jobs.actividad_mensual --backfill      # todos los meses en NM
    python -m jobs.actividad_mensual --backfill --dry-run
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from typing import Any

from api.services.comercial import _CATS_OPERACIONES, _PESIF
from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client


def _mes_actual_art() -> str:
    """Mes calendario actual en horario Argentina (UTC-3), 'YYYY-MM'."""
    return (datetime.now(UTC) - timedelta(hours=3)).date().isoformat()[:7]


def _agregar(mov, cats: list[str], meses: list[str] | None) -> list[dict[str, Any]]:
    """Agrega NegocioMovimientos por (year_month, id_cuenta). `meses=None` = todos."""
    match: dict[str, Any] = {"categoria": {"$in": cats}, "id_cuenta": {"$ne": None}}
    if meses:
        # fecha es string ISO; rango lexicográfico [primer mes, mes siguiente al último).
        lo = min(meses) + "-01"
        hi = _mes_siguiente(max(meses)) + "-01"
        match["fecha"] = {"$gte": lo, "$lt": hi}
    return list(mov.aggregate([
        {"$match": match},
        {"$group": {
            "_id": {"ym": {"$substrBytes": ["$fecha", 0, 7]}, "id": "$id_cuenta"},
            "n_ops": {"$sum": 1},
            "volumen_ars": {"$sum": _PESIF},
        }},
    ]))


def _mes_siguiente(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Snapshot mensual de cuentas activas.")
    ap.add_argument("--mes", help="mes puntual YYYY-MM (default: mes corriente ART)")
    ap.add_argument("--backfill", action="store_true", help="todos los meses presentes en NM")
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo informa")
    args = ap.parse_args()

    client = get_mongo_client()
    mov = client["CashFlow"]["NegocioMovimientos"]
    comitentes = client["Clientes"]["Comitentes"]
    destino = client["Clientes"]["ActividadMensual"]
    cats = list(_CATS_OPERACIONES)

    with JobRunLogger("actividad_mensual") as run:
        if args.backfill:
            meses = None
            run.log("Backfill: agregando TODOS los meses presentes en NegocioMovimientos…")
        else:
            mes = args.mes or _mes_actual_art()
            meses = [mes]
            run.log(f"Procesando mes {mes}…")

        filas = _agregar(mov, cats, meses)
        if not filas:
            run.log("⚠ 0 filas agregadas — la colección NO se toca.")
            return

        # Operador/segmento vigente (se congela en el doc) — 1 sola lectura.
        info: dict[str, dict[str, Any]] = {
            str(c["id_cuenta"]): c
            for c in comitentes.find(
                {}, {"_id": 0, "id_cuenta": 1, "operador_email": 1,
                     "operador_nombre": 1, "nivel_1": 1},
            )
        }
        ahora = datetime.now(UTC)
        docs: list[dict[str, Any]] = []
        meses_tocados: set[str] = set()
        for f in filas:
            ym = f["_id"]["ym"]
            idc = str(f["_id"]["id"])
            meta = info.get(idc, {})
            meses_tocados.add(ym)
            docs.append({
                "year_month": ym,
                "id_cuenta": idc,
                "operador_email": meta.get("operador_email"),
                "operador_nombre": meta.get("operador_nombre"),
                "nivel_1": meta.get("nivel_1"),
                "n_ops": f["n_ops"],
                "volumen_ars": round(f.get("volumen_ars", 0.0), 2),
                "computed_at": ahora,
            })

        run.set_stat("meses", sorted(meses_tocados))
        run.set_stat("docs", len(docs))
        run.log(f"{len(docs)} cuentas-activas en {len(meses_tocados)} mes(es): "
                f"{', '.join(sorted(meses_tocados))}")

        if args.dry_run:
            run.log("DRY-RUN: no se escribió nada.")
            return

        # Idempotente: reemplaza por completo los meses (re)calculados.
        destino.delete_many({"year_month": {"$in": sorted(meses_tocados)}})
        destino.insert_many(docs)
        run.log(f"✅ {len(docs)} docs persistidos en Clientes.ActividadMensual.")


if __name__ == "__main__":
    main()
