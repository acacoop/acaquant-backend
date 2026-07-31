"""scripts/dump_openapi.py — exporta el spec OpenAPI de la API principal.

Escribe `docs/openapi/openapi_main.json` desde el app FastAPI (`app.openapi()`).
Uso:
    python -m scripts.dump_openapi
"""
from __future__ import annotations

import json
from pathlib import Path

_OUT = Path("docs/openapi")


def _dump(app, nombre: str) -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    # FastAPI emite OpenAPI 3.1 por default; forzamos 3.0.3 para maximizar
    # compatibilidad con consumidores externos de spec.
    app.openapi_version = "3.0.3"
    app.openapi_schema = None
    spec = app.openapi()
    (path := _OUT / nombre).write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   {path}  ->  {len(spec.get('paths', {}))} paths")


def main() -> int:
    print("Generando spec OpenAPI de la API principal:")
    from api.main import app as api_app
    _dump(api_app, "openapi_main.json")
    print("Listo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
