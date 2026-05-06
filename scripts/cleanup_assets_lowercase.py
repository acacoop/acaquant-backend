"""One-shot: borra los campos lowercase duplicados en Valuaciones.Assets.

Los UPPERCASE (CARTERA, EMISOR, ...) son la fuente de verdad. En algún
momento se duplicaron como lowercase y quedaron como ruido. `unidad` se
preserva (es la clave de join legítima).

Uso (desde la raíz del repo, con venv activado):
    python -m scripts.cleanup_assets_lowercase
"""
from __future__ import annotations

from core.mongo import get_mongo_client

LOWERCASE_DUPS = [
    "calificacion", "cartera", "clase_activo",
    "emisor", "ticker", "vencimiento", "instrumento",
]


def main() -> None:
    client = get_mongo_client()
    col = client["Valuaciones"]["Assets"]

    before = col.count_documents({"cartera": {"$exists": True}})
    print(f"docs con `cartera` lowercase antes: {before}")

    result = col.update_many(
        {},
        {"$unset": {f: "" for f in LOWERCASE_DUPS}},
    )
    print(f"matched:  {result.matched_count}")
    print(f"modified: {result.modified_count}")

    after = col.count_documents({"cartera": {"$exists": True}})
    print(f"docs con `cartera` lowercase después: {after}")


if __name__ == "__main__":
    main()
