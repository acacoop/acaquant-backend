"""Drop de las colecciones legacy del cron de carteras (deprecation 2026-05-11).

`jobs.carteras` quedó deprecated cuando la vista /portfolios dejó de
usarse en acaquant-web. Esta cadena ya no tiene escritor ni consumidor:

    Valuaciones.Carteras       (escrita por jobs.carteras, ahora borrado)
    PortfolioAPI.CarterasAPI   (copia API derivada vía sync_api_copies)

Ejecutar UNA sola vez en el Droplet:

    python -m scripts.drop_carteras_legacy

Idempotente: si la colección no existe, el drop es no-op.
"""
from core.mongo import get_mongo_client


def main() -> None:
    client = get_mongo_client()
    targets = [
        ("Valuaciones",  "Carteras"),
        ("PortfolioAPI", "CarterasAPI"),
    ]
    for db_name, col_name in targets:
        col = client[db_name][col_name]
        antes = col.estimated_document_count()
        col.drop()
        print(f"✓ {db_name}.{col_name} drop OK (estimated {antes} docs antes)")


if __name__ == "__main__":
    main()
