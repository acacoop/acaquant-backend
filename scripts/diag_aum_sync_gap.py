"""diag_aum_sync_gap.py — READ-ONLY. Por qué el sync incremental se saltea cuentas de AuM.

Contexto: SQL tiene MENOS cuentas que Mongo en el MISMO snapshot (diag_carteras_sql_vs_mongo).
El incremental (jobs/sync_postgres.sync_aum) trae solo docs con `timestamp >= now-7d`. Si los
docs del snapshot de hoy tienen `timestamp` viejo o nulo, quedan fuera de la ventana y NO se
sincronizan → SQL incompleto. Es la misma clase de bug del watermark `ingestado_en`.

Mide, para el último snapshot de Valuaciones.AuM:
  - cuántos docs tienen `timestamp` DENTRO de la ventana de 7 días, cuántos FUERA, cuántos NULL.
  - lo mismo a nivel CUENTA (una cuenta se sincroniza si AL MENOS un doc suyo cae en la ventana).
  - muestra el `timestamp` de unas cuantas cuentas concretas.

READ-ONLY. Acotado al último snapshot (índice fecha_snapshot).

Uso:
    python -m scripts.diag_aum_sync_gap
    python -m scripts.diag_aum_sync_gap --dias 7
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=7, help="ventana del incremental (igual a DEFAULT_DIAS)")
    args = ap.parse_args()

    cli = get_mongo_client_read()
    aum = cli["Valuaciones"]["AuM"]
    snap = aum.find_one({}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    if not snap:
        print("No hay snapshots en Valuaciones.AuM.")
        return
    fecha = snap["fecha_snapshot"]
    desde = datetime.now(UTC) - timedelta(days=args.dias)
    print(f"=== Diag gap sync AuM — snapshot {fecha} · ventana incremental = timestamp >= {desde:%Y-%m-%d %H:%M} UTC ===\n")

    # ── Buckets a nivel DOC ────────────────────────────────────────────────────
    total = aum.count_documents({"fecha_snapshot": fecha})
    con_ts = aum.count_documents({"fecha_snapshot": fecha, "timestamp": {"$type": "date"}})
    en_ventana = aum.count_documents({"fecha_snapshot": fecha, "timestamp": {"$gte": desde}})
    sin_ts = total - con_ts
    fuera = con_ts - en_ventana
    print("── DOCS del snapshot ──")
    print(f"    total:                 {total}")
    print(f"    con timestamp:         {con_ts}")
    print(f"    timestamp NULL/ausente:{sin_ts}")
    print(f"    DENTRO ventana 7d:     {en_ventana}   → se sincronizan")
    print(f"    FUERA ventana 7d:      {fuera}   → el incremental los saltea")
    print()

    # ── A nivel CUENTA: una cuenta entra si tiene ≥1 doc en la ventana ─────────
    ids_todas = {str(x) for x in aum.distinct("id_cuenta", {"fecha_snapshot": fecha})}
    ids_en_ventana = {str(x) for x in aum.distinct(
        "id_cuenta", {"fecha_snapshot": fecha, "timestamp": {"$gte": desde}})}
    ids_fuera = ids_todas - ids_en_ventana
    print("── CUENTAS del snapshot ──")
    print(f"    totales:                  {len(ids_todas)}")
    print(f"    con ≥1 doc en ventana:    {len(ids_en_ventana)}   → llegan a SQL")
    print(f"    SIN ningún doc en ventana:{len(ids_fuera)}   → NO llegan a SQL (las que 'no aparecen')")
    print()

    if ids_fuera:
        print("    Muestra de cuentas fuera de ventana (id → timestamp de su doc más nuevo):")
        for idc in sorted(ids_fuera)[:15]:
            d = aum.find_one({"fecha_snapshot": fecha, "id_cuenta": idc},
                             {"_id": 0, "timestamp": 1, "cuenta": 1}, sort=[("timestamp", -1)])
            print(f"      {idc:<8} ts={d.get('timestamp') if d else '—'}  {(d or {}).get('cuenta', '')[:40]}")
        print()

    print("=== Lectura ===")
    if len(ids_fuera) > 0:
        print(f"  🔴 {len(ids_fuera)} cuentas del snapshot tienen TODOS sus docs fuera de la ventana de")
        print(f"     {args.dias} días (timestamp viejo o nulo) → el incremental NUNCA las trae. Bug de")
        print("     watermark: el snapshot diario reusa/no bumpea `timestamp` en esas cuentas.")
        print("  → Fix de fondo: que jobs/aum.py setee `timestamp=now` en CADA doc que escribe.")
        print("  → Reparación inmediata de SQL: python -m jobs.sync_postgres --full (lee secondary, throttled).")
    else:
        print("  ✅ Todas las cuentas tienen docs en la ventana → el gap NO es la ventana de timestamp.")
        print("     Revisar si el sync corrió/erroró (Manager.JobRuns) o filtros de sync_aum.")


if __name__ == "__main__":
    main()
