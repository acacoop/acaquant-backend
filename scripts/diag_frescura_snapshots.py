"""Diag read-only — frescura de las colecciones snapshot de mercado.

Objetivo (REGLA #2): contestar, SIN asumir, una sola pregunta por colección:
con el mercado cerrado / motores apagados, ¿la última foto de ayer sigue
viva o la colección quedó vacía?

De esa respuesta depende el fix de "tablas vacías a la mañana":
  - retiene foto de ayer  → basta relajar el filtro "solo hoy" + flag de frescura.
  - queda vacía           → hay que leer del cierre persistido (SnapshotsCierre).

NO escribe nada. Para cada colección reporta:
  - count (estimado, sin scan)
  - el updated_at más nuevo y el más viejo
  - antigüedad del más nuevo en horas (vs ahora)
  - 2 docs de muestra (key + updated_at + algún precio)

Uso:
    python -m scripts.diag_frescura_snapshots

Correr idealmente con el mercado CERRADO (ej. 9-10am ART) para ver el estado
real que ve el usuario que entra a la mañana.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client_read

# (db, colección, campo_key, campo_precio_muestra)
TARGETS = [
    ("Trading",  "MarketSnapshot",      "ticker", "metrics.last_price"),  # renta fija, sintéticos, forwards, curvas
    ("Trading",  "CedearsSnapshot",     "ticker", "last"),                 # CEDEAR (sabemos que retiene)
    ("Trading",  "AdrSnapshot",         "ticker", "c"),                    # ADR live (finnhub)
    ("Trading",  "AgroSnapshot",        "ticker", "last"),                 # agro futuros
    ("Trading",  "AgroOpcionesSnapshot","symbol", "last"),                 # agro opciones
    ("Trading",  "FuturosDLRSnapshot",  "ticker", "last"),                 # sintéticos (futuros DLR)
    ("Opciones", "OptionsSnapshot",     "symbol", "last"),                 # opciones ROFEX
    ("Trading",  "SnapshotsCierre",     "ticker", "metrics.last_price"),   # cierre persistido (bonos)
]


def _dig(doc: dict, path: str):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _fmt_ts(ts) -> str:
    if ts is None:
        return "—"
    if isinstance(ts, datetime):
        return ts.isoformat(sep=" ", timespec="seconds")
    return str(ts)


def _age_horas(ts) -> str:
    if not isinstance(ts, datetime):
        return "—"
    # updated_at lo escribe el motor con datetime.now() (naive == UTC en el Droplet).
    now = datetime.now(UTC).replace(tzinfo=None)
    base = ts.replace(tzinfo=None) if ts.tzinfo else ts
    delta_h = (now - base).total_seconds() / 3600
    return f"{delta_h:.1f}h"


def main() -> None:
    client = get_mongo_client_read()
    ahora = datetime.now(UTC).replace(tzinfo=None)
    print(f"=== Frescura de snapshots — ahora (UTC) = {ahora.isoformat(sep=' ', timespec='seconds')} ===\n")

    # Catálogo real de colecciones por DB (para detectar nombres mal escritos)
    cols_por_db: dict[str, set[str]] = {}
    for db_name in {t[0] for t in TARGETS}:
        cols_por_db[db_name] = set(client[db_name].list_collection_names())

    for db_name, col_name, key_field, price_field in TARGETS:
        existe = col_name in cols_por_db.get(db_name, set())
        print(f"--- {db_name}.{col_name} ---")
        if not existe:
            print("  ⚠️  NO EXISTE esa colección con ese nombre. "
                  f"Colecciones en {db_name}: {sorted(cols_por_db.get(db_name, set()))}\n")
            continue

        col = client[db_name][col_name]
        n = col.estimated_document_count()  # metadata, sin scan
        print(f"  count (estimado): {n}")
        if n == 0:
            print("  → VACÍA. Mostrar 'cierre' exige leer de otra fuente (SnapshotsCierre).\n")
            continue

        # Más nuevo y más viejo por updated_at (sort en colección chica, in-memory).
        newest = list(col.find({"updated_at": {"$ne": None}}, {key_field: 1, "updated_at": 1, price_field.split(".")[0]: 1})
                      .sort("updated_at", -1).limit(2))
        oldest = list(col.find({"updated_at": {"$ne": None}}, {"updated_at": 1})
                      .sort("updated_at", 1).limit(1))

        if not newest:
            print("  ⚠️  Ningún doc tiene campo 'updated_at'. No puedo medir frescura por ahí.\n")
            continue

        top = newest[0]
        ts_new = top.get("updated_at")
        ts_old = oldest[0].get("updated_at") if oldest else None
        print(f"  updated_at más NUEVO: {_fmt_ts(ts_new)}   (antigüedad: {_age_horas(ts_new)})")
        print(f"  updated_at más VIEJO: {_fmt_ts(ts_old)}")
        for d in newest:
            print(f"    muestra: {key_field}={d.get(key_field)!r}  "
                  f"{price_field}={_dig(d, price_field)}  updated_at={_fmt_ts(d.get('updated_at'))}")
        print()

    print("=== Lectura ===")
    print("  antigüedad < ~1h  → motor corriendo (mercado abierto).")
    print("  antigüedad ~15-20h y count>0 → RETIENE la foto de ayer (fix barato: relajar filtro + flag).")
    print("  count=0 → se vacía (fix: leer de SnapshotsCierre / cierre persistido).")


if __name__ == "__main__":
    main()
