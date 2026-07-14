"""diag_auth_postura.py — READ-ONLY: ¿la API va a arrancar con el fail-closed nuevo?

Red de seguridad del cambio EXT-AUTH1: en `ENV=prod`, `api.main._validar_postura_auth`
ahora ABORTA el arranque si falta API_KEY **o** si falta CF_ACCESS_TEAM/CF_ACCESS_AUD
(antes solo logueaba un error y la API arrancaba con el RBAC de adorno: sin esas dos
vars el JWT de Cloudflare no se valida y la identidad cae al header forwardeado, que
es spoofeable).

Corré ESTO en el Droplet ANTES de `systemctl restart api.service`:

    python -m scripts.diag_auth_postura

No imprime ningún secreto: solo SETEADA / FALTANTE + longitud.
"""
from __future__ import annotations

import os

from config import (  # config.py hace load_dotenv() al importarse
    API_KEY,
    CF_ACCESS_AUD,
    CF_ACCESS_TEAM,
    DOLAR_INGEST_TOKEN,
    ENV,
    MCP_JWT_SECRET,
)

# POSTGRES_URI no vive en config.py (lo lee core.postgres); lo tomamos del entorno
# ya cargado por el load_dotenv() de config, sin importar el pool.
POSTGRES_URI = os.getenv("POSTGRES_URI", "")

try:  # partner_api tiene su propio settings (no depende de config.py)
    from partner_api.settings import PARTNER_JWT_SECRET
except Exception:  # el diag no debe morir si falta el módulo/env
    PARTNER_JWT_SECRET = ""


def _estado(nombre: str, valor: str | None, *, requerida_en_prod: bool = False) -> bool:
    """Imprime SETEADA/FALTANTE + longitud (nunca el valor). Devuelve si está seteada."""
    v = (valor or "").strip()
    marca = "🔒" if requerida_en_prod else "  "
    if v:
        print(f"  {marca} {nombre:<20} SETEADA   (largo {len(v)})")
        return True
    print(f"  {marca} {nombre:<20} FALTANTE")
    return False


def main() -> None:
    print(f"\nPostura de auth · ENV = {ENV!r}\n")
    print("  🔒 = obligatoria en prod (sin ella la API NO arranca)\n")

    ok_api_key = _estado("API_KEY", API_KEY, requerida_en_prod=True)
    ok_team = _estado("CF_ACCESS_TEAM", CF_ACCESS_TEAM, requerida_en_prod=True)
    ok_aud = _estado("CF_ACCESS_AUD", CF_ACCESS_AUD, requerida_en_prod=True)
    print()
    _estado("MCP_JWT_SECRET", MCP_JWT_SECRET)
    _estado("PARTNER_JWT_SECRET", PARTNER_JWT_SECRET)
    _estado("DOLAR_INGEST_TOKEN", DOLAR_INGEST_TOKEN)
    _estado("POSTGRES_URI", POSTGRES_URI)

    faltan = [
        n
        for n, ok in (
            ("API_KEY", ok_api_key),
            ("CF_ACCESS_TEAM", ok_team),
            ("CF_ACCESS_AUD", ok_aud),
        )
        if not ok
    ]

    print()
    if ENV != "prod":
        print(
            f"ℹ️  ENV={ENV!r} (no es 'prod') → el fail-closed NO aplica: la API arranca igual.\n"
            "   Este chequeo importa en el Droplet, donde el unit de systemd setea ENV=prod."
        )
        if faltan:
            print(f"   (En prod faltarían: {', '.join(faltan)})")
        return

    if faltan:
        print(
            f"❌ FALTA {', '.join(faltan)} → la API NO va a arrancar. "
            f"Agregá {'esa variable' if len(faltan) == 1 else 'esas variables'} al unit file "
            "(/etc/systemd/system/api.service), `systemctl daemon-reload`, y RECIÉN AHÍ "
            "restarteá la API."
        )
        return

    print("✅ La API va a arrancar con el fail-closed nuevo (API_KEY + CF_ACCESS_TEAM/AUD OK).")


if __name__ == "__main__":
    main()
