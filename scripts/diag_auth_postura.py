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
import re
import subprocess

from config import (  # config.py hace load_dotenv() al importarse
    API_KEY,
    CF_ACCESS_AUD,
    CF_ACCESS_TEAM,
    CF_TRUSTED_SERVICE_TOKENS,
    DOLAR_INGEST_TOKEN,
    ENV,
    MCP_JWT_SECRET,
)

# POSTGRES_URI no vive en config.py (lo lee core.postgres); lo tomamos del entorno
# ya cargado por el load_dotenv() de config, sin importar el pool.
POSTGRES_URI = os.getenv("POSTGRES_URI", "")


def _estado(nombre: str, valor: str | None, *, requerida_en_prod: bool = False) -> bool:
    """Imprime SETEADA/FALTANTE + longitud (nunca el valor). Devuelve si está seteada."""
    v = (valor or "").strip()
    marca = "🔒" if requerida_en_prod else "  "
    if v:
        print(f"  {marca} {nombre:<20} SETEADA   (largo {len(v)})")
        return True
    print(f"  {marca} {nombre:<20} FALTANTE")
    return False


# El common_name aparece en los dos warnings que emite api/auth.py rama 2:
#   - allowlist vacía  → "...no se puede validar el service token cn='X'..."
#   - token rechazado  → "service token NO autorizado (cn='X')..."
_RE_CN = re.compile(r"cn=['\"]([^'\"]+)['\"]")


def _service_tokens_vistos(horas: int = 168) -> tuple[set[str], str | None]:
    """common_names de service token que la API vio en los últimos `horas`.

    Los saca del journal (mismo mecanismo que la tab LOGS del Manager). Devuelve
    (nombres, error). No son secretos: son identificadores de máquina, y el
    propio auth.py ya los loguea.
    """
    try:
        proc = subprocess.run(
            ["journalctl", "-u", "api.service", f"--since={horas} hours ago",
             "--no-pager", "--output=cat"],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return set(), "journalctl no está disponible (¿no es el Droplet?)"
    except subprocess.TimeoutExpired:
        return set(), "journalctl tardó demasiado"
    if proc.returncode != 0:
        return set(), (proc.stderr or "").strip()[:200] or "journalctl falló"
    vistos = {
        m.group(1).strip().lower()
        for linea in proc.stdout.splitlines() if "service token" in linea
        for m in [_RE_CN.search(linea)] if m
    }
    return vistos, None


def _reporte_service_tokens() -> None:
    """La parte delicada: CF_TRUSTED_SERVICE_TOKENS es TODO-O-NADA."""
    print("\n" + "─" * 68)
    print("SERVICE TOKENS (identidades de MÁQUINA: SSR del front, crons, ingesta)")
    print("─" * 68)

    configurados = sorted(CF_TRUSTED_SERVICE_TOKENS)
    vistos, err = _service_tokens_vistos()

    if configurados:
        print(f"\n  CF_TRUSTED_SERVICE_TOKENS = {', '.join(configurados)}")
    else:
        print("\n  CF_TRUSTED_SERVICE_TOKENS  FALTANTE (allowlist vacía)")
        print("  → Hoy la API acepta el email que afirme CUALQUIER service token")
        print("    válido. El fix de escalada está instalado pero INACTIVO.")

    if err:
        print(f"\n  ⚠️  No pude leer el journal: {err}")
        print("     Corré esto EN EL DROPLET para descubrir los tokens en uso.")
        return

    if not vistos:
        print("\n  El journal (7 días) no registra NINGÚN service token.")
        if configurados:
            print("  ⚠️  Pero la allowlist tiene entradas. O no hubo tráfico de")
            print("      máquina, o los nombres configurados no son los reales.")
        else:
            print("  Puede que el front no esté usando service token, o que el")
            print("  tráfico sea más viejo que 7 días. Volvé a correrlo tras un")
            print("  día hábil con la mesa operando.")
        return

    print(f"\n  Vistos en el journal (7 días): {', '.join(sorted(vistos))}")

    if not configurados:
        print("\n  ✅ VALOR A CONFIGURAR — copiá esta línea EXACTA al unit file:")
        print(f"\n     Environment=\"CF_TRUSTED_SERVICE_TOKENS={','.join(sorted(vistos))}\"")
        print("\n  ⚠️  TODO-O-NADA: apenas la variable tenga UN valor, cualquier")
        print("      service token que no esté listado queda RECHAZADO (cae a")
        print("      'anon' → 403 en todo). Por eso la lista debe salir de acá y")
        print("      no de la memoria. Si una integración corre semanal, esperá a")
        print("      que aparezca antes de configurar.")
        return

    faltantes = vistos - set(configurados)
    sobrantes = set(configurados) - vistos
    if faltantes:
        print(f"\n  ❌ ESTOS ESTÁN EN USO PERO NO EN LA ALLOWLIST: {', '.join(sorted(faltantes))}")
        print("     Están siendo RECHAZADOS ahora mismo (403). Agregalos ya:")
        print(f"\n     Environment=\"CF_TRUSTED_SERVICE_TOKENS={','.join(sorted(vistos | set(configurados)))}\"")
    if sobrantes:
        print(f"\n  ℹ️  Configurados pero sin tráfico en 7 días: {', '.join(sorted(sobrantes))}")
        print("     Puede ser normal (integración esporádica). No los saques sin mirar.")
    if not faltantes and not sobrantes:
        print("\n  ✅ La allowlist coincide exactamente con lo que se está usando.")


def main() -> None:
    print(f"\nPostura de auth · ENV = {ENV!r}\n")
    print("  🔒 = obligatoria en prod (sin ella la API NO arranca)\n")

    ok_api_key = _estado("API_KEY", API_KEY, requerida_en_prod=True)
    ok_team = _estado("CF_ACCESS_TEAM", CF_ACCESS_TEAM, requerida_en_prod=True)
    ok_aud = _estado("CF_ACCESS_AUD", CF_ACCESS_AUD, requerida_en_prod=True)
    print()
    _estado("MCP_JWT_SECRET", MCP_JWT_SECRET)
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

    _reporte_service_tokens()

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
