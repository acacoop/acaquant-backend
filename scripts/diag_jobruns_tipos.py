"""Lista los `tipo` distintos en Manager.JobRuns + su último run (read-only).

Sirve para confirmar/ajustar los `run_tipo` del registro del Diagnóstico
(`api/services/diagnostico_registry.py`). Si una fila del árbol sale "sin_datos",
es porque el `run_tipo` no coincide con el `tipo` real que el cron registra acá.

Uso:
    python -m scripts.diag_jobruns_tipos
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read
from core.tz import AR_TZ, asegurar_aware


def main() -> int:
    col = get_mongo_client_read()["Manager"]["JobRuns"]
    tipos = sorted(col.distinct("tipo"))
    print(f"{len(tipos)} tipos distintos en Manager.JobRuns:\n")
    print(f"{'tipo':<32} {'último run (ART)':<20} {'status':<8} runs")
    print("-" * 72)
    for t in tipos:
        ultimo = col.find_one({"tipo": t}, sort=[("finished_at", -1)])
        n = col.count_documents({"tipo": t})
        fin = ultimo.get("finished_at") if ultimo else None
        fin_s = asegurar_aware(fin).astimezone(AR_TZ).strftime("%Y-%m-%d %H:%M") if fin else "—"
        status = (ultimo or {}).get("status", "—")
        print(f"{t:<32} {fin_s:<20} {status:<8} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
