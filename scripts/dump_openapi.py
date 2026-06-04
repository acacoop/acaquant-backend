"""scripts/dump_openapi.py — exporta los specs OpenAPI de las dos APIs.

Escribe docs/postman/openapi_main.json y openapi_partner.json desde los apps
FastAPI (`app.openapi()`). Importás esos archivos en Postman y tenés la
colección completa autogenerada. **Re-corré esto cada vez que agregues o cambies
endpoints** y re-importá en Postman para mantener todo en sync con el código.

La Partner API tiene `openapi_url=None` (no sirve el spec por HTTP a propósito),
pero `app.openapi()` lo genera igual desde acá.

Uso:
    python -m scripts.dump_openapi
"""
from __future__ import annotations

import json
from pathlib import Path

_OUT = Path("docs/postman")


def _dump(app, nombre: str) -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    # FastAPI emite OpenAPI 3.1 por default; muchas versiones de Postman solo
    # importan 3.0 ("Incorrect format"). Forzamos 3.0.3 (limpiando la cache del
    # schema para regenerar con esa versión).
    app.openapi_version = "3.0.3"
    app.openapi_schema = None
    spec = app.openapi()
    (path := _OUT / nombre).write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   {path}  ->  {len(spec.get('paths', {}))} paths")


def main() -> int:
    print("Generando specs OpenAPI para Postman:")
    from api.main import app as api_app
    _dump(api_app, "openapi_main.json")
    from partner_api.main import app as partner_app
    _dump(partner_app, "openapi_partner.json")
    print("Listo. En Postman: Import -> File -> elegi estos .json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
