"""scripts/diag_sync_gap_operaciones.py — boletos que están en Mongo Operaciones
pero FALTAN en SQL operaciones (read-only). Ese agujero rompe la vista (lee SQL).

El sync incremental (jobs/sync_postgres.sync_operaciones) trae los docs con
`ingestado_en >= hoy-7d`. Si un boleto quedó con `ingestado_en` fuera de esa
ventana (o nunca se sincronizó por una corrida fallida), está en Mongo pero NO en
SQL → en la vista falta y el filtro por cuenta da cualquier cosa.

Mide: el boleto de ejemplo + el agujero total de los últimos N días + la EDAD del
`ingestado_en` de los que faltan (para saber si es la ventana de 7d la que los deja
afuera).

Uso (en el Droplet):
    python -m scripts.diag_sync_gap_operaciones
    python -m scripts.diag_sync_gap_operaciones "BOL 2026093575"
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read
from core.postgres import connect

_DIAS = 12
_EJEMPLO = "BOL 2026093575"


def main() -> int:
    boleto = " ".join(sys.argv[1:]).strip() or _EJEMPLO
    ops = get_mongo_client_read()["CashFlow"]["Operaciones"]
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()
    desde = (hoy - timedelta(days=_DIAS)).isoformat()
    ahora = datetime.now(UTC)

    # 1) El boleto de ejemplo.
    print(f"== Boleto de ejemplo: {boleto!r} ==")
    d = ops.find_one({"boleto": boleto}, {"_id": 0, "boleto": 1, "ingestado_en": 1,
                                          "concertacion": 1, "bruto": 1, "operacion": 1,
                                          "etapa": 1, "es_cierre": 1})
    if not d:
        print("  Mongo Operaciones: NO existe.")
    else:
        ing = d.get("ingestado_en")
        edad = (ahora - ing).days if isinstance(ing, datetime) else "?"
        print(f"  Mongo: concertacion={d.get('concertacion')}  bruto={d.get('bruto')}  "
              f"operacion={d.get('operacion')}  etapa={d.get('etapa')}  es_cierre={d.get('es_cierre')}")
        print(f"         ingestado_en={ing}  (edad={edad} días → "
              f"{'DENTRO' if isinstance(edad, int) and edad <= 7 else 'FUERA'} de la ventana de 7d)")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT boleto, bruto, concertacion::text FROM operaciones WHERE boleto = %s", (boleto,))
        row = cur.fetchone()
        print(f"  SQL operaciones: {'existe → ' + str(row) if row else '⚠ NO existe (acá está el bug)'}")

        # 2) Agujero total últimos N días.
        mongo_bol = {str(x["boleto"]) for x in ops.find(
            {"concertacion": {"$gte": desde}}, {"_id": 0, "boleto": 1}) if x.get("boleto")}
        cur.execute("SELECT boleto FROM operaciones WHERE concertacion >= %s", (desde,))
        sql_bol = {r[0] for r in cur.fetchall()}

    falta = mongo_bol - sql_bol
    print(f"\n== Agujero del sync (últimos {_DIAS}d, por concertacion) ==")
    print(f"  Mongo Operaciones: {len(mongo_bol):,} boletos")
    print(f"  SQL operaciones:   {len(sql_bol):,} boletos")
    print(f"  FALTAN en SQL:     {len(falta):,}")

    if falta:
        print("\n  Edad del ingestado_en de los que faltan (días):")
        edades: Counter = Counter()
        muestra = []
        for b in falta:
            x = ops.find_one({"boleto": b}, {"_id": 0, "ingestado_en": 1, "concertacion": 1, "operacion": 1})
            ing = x.get("ingestado_en") if x else None
            edad = (ahora - ing).days if isinstance(ing, datetime) else -1
            edades[edad] += 1
            if len(muestra) < 12:
                muestra.append((b, edad, x.get("concertacion") if x else None, x.get("operacion") if x else None))
        for edad in sorted(edades):
            tag = "  ← fuera de ventana 7d" if edad > 7 else ""
            print(f"    edad={edad:>3}d: {edades[edad]:>5} boletos{tag}")
        print("\n  Muestra de los que faltan:")
        for b, edad, conc, op in muestra:
            print(f"    {b:16} edad={edad}d  conc={conc}  {op}")

    print("\nLECTURA: si los que faltan tienen ingestado_en > 7 días → la ventana "
          "incremental los deja afuera. Fix inmediato: `python -m jobs.sync_postgres --full`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
