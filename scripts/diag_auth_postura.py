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


# api/auth.py rama 2 emite TRES líneas distintas, y hay que separarlas porque
# significan cosas opuestas:
#   ACEPTADO   → "service token ACEPTADO por allowlist (cn='X')"   ✅ evidencia positiva
#   RECHAZADO  → "service token NO autorizado (cn='X')"            ❌ nombre mal puesto
#   SIN LISTA  → "...no se puede validar el service token cn='X'"  ⚠️  allowlist vacía
_RE_CN = re.compile(r"cn=['\"]([^'\"]+)['\"]")


def _scan_journal(horas: int = 168) -> tuple[dict[str, set[str]], str | None]:
    """Clasifica los common_names vistos por la API en los últimos `horas`.

    Devuelve ({aceptados, rechazados, sin_lista}, error). No son secretos: son
    identificadores de máquina, y el propio auth.py ya los loguea.
    """
    vacio: dict[str, set[str]] = {"aceptados": set(), "rechazados": set(), "sin_lista": set()}
    try:
        proc = subprocess.run(
            ["journalctl", "-u", "api.service", f"--since={horas} hours ago",
             "--no-pager", "--output=cat"],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return vacio, "journalctl no está disponible (¿no es el Droplet?)"
    except subprocess.TimeoutExpired:
        return vacio, "journalctl tardó demasiado"
    if proc.returncode != 0:
        return vacio, (proc.stderr or "").strip()[:200] or "journalctl falló"

    for linea in proc.stdout.splitlines():
        if "service token" not in linea:
            continue
        m = _RE_CN.search(linea)
        if not m:
            continue
        cn = m.group(1).strip().lower()
        if "ACEPTADO" in linea:
            vacio["aceptados"].add(cn)
        elif "NO autorizado" in linea:
            vacio["rechazados"].add(cn)
        else:
            vacio["sin_lista"].add(cn)
    return vacio, None


def _reporte_service_tokens() -> None:
    """La parte delicada: CF_TRUSTED_SERVICE_TOKENS es TODO-O-NADA."""
    print("\n" + "─" * 68)
    print("SERVICE TOKENS (identidades de MÁQUINA: SSR del front, crons, ingesta)")
    print("─" * 68)

    configurados = sorted(CF_TRUSTED_SERVICE_TOKENS)
    scan, err = _scan_journal()

    if configurados:
        print(f"\n  CF_TRUSTED_SERVICE_TOKENS = {', '.join(configurados)}")
    else:
        print("\n  CF_TRUSTED_SERVICE_TOKENS  FALTANTE (allowlist vacía)")
        print("  → Hoy la API acepta el email que afirme CUALQUIER service token")
        print("    válido. El fix de escalada está instalado pero INACTIVO.")

    if err:
        print(f"\n  ⚠️  No pude leer el journal: {err}")
        print("     Corré esto EN EL DROPLET para ver el estado real.")
        return

    aceptados, rechazados = scan["aceptados"], scan["rechazados"]
    sin_lista = scan["sin_lista"]

    # ── Lo urgente primero: tokens que se están rechazando AHORA ──
    if rechazados:
        print(f"\n  ❌ RECHAZÁNDOSE AHORA MISMO: {', '.join(sorted(rechazados))}")
        print("     Esas integraciones están recibiendo 403. Si son legítimas,")
        print("     agregalas y reiniciá:")
        todos = sorted(set(configurados) | rechazados | aceptados)
        print(f"\n     Environment=\"CF_TRUSTED_SERVICE_TOKENS={','.join(todos)}\"")

    if not configurados:
        # Allowlist vacía: el log trae todos los cn vistos → armar el valor.
        candidatos = sin_lista | aceptados | rechazados
        if candidatos:
            print("\n  ✅ VALOR A CONFIGURAR — copiá esta línea EXACTA al unit file:")
            print(f"\n     Environment=\"CF_TRUSTED_SERVICE_TOKENS={','.join(sorted(candidatos))}\"")
            print("\n  ⚠️  TODO-O-NADA: apenas la variable tenga UN valor, cualquier")
            print("      service token que no esté listado queda RECHAZADO (cae a")
            print("      'anon' → 403 en todo). Por eso la lista sale de acá y no")
            print("      de la memoria.")
        else:
            print("\n  El journal (7 días) no registra ningún service token.")
            print("  Volvé a correrlo tras un día hábil con la mesa operando.")
        return

    # ── Allowlist configurada: ¿hay evidencia de que está BIEN puesta? ──
    if aceptados:
        print(f"\n  ✅ EN USO Y ACEPTADOS: {', '.join(sorted(aceptados))}")
        print("     Hay tráfico real de máquina pasando la allowlist. La")
        print("     configuración es correcta y está activa.")
        sobrantes = set(configurados) - aceptados
        if sobrantes:
            print(f"\n  ℹ️  Listados pero sin tráfico: {', '.join(sorted(sobrantes))}")
            print("     Puede ser normal (integración esporádica). No los saques")
            print("     sin confirmar que ya no se usan.")
        return

    if not rechazados:
        print("\n  ⏳ SIN EVIDENCIA TODAVÍA — y esto NO es un problema.")
        print("     El camino feliz es silencioso: la API sólo loguea los")
        print("     rechazos. Que no haya NADA en el log significa que no hubo")
        print("     ningún token rechazado, que es justamente lo que se quiere.")
        print("\n     Lo que falta es la confirmación positiva, que se emite una")
        print("     vez por proceso desde esta versión. Para obtenerla:")
        print("       1. systemctl restart api.service")
        print("       2. entrá a la web y navegá un poco (genera tráfico del SSR)")
        print("       3. volvé a correr este diagnóstico")
        print("\n     Si entonces aparece '✅ EN USO Y ACEPTADOS', está todo bien.")
        print("     Si aparece '❌ RECHAZÁNDOSE', el nombre configurado no es el")
        print("     real: sacá la variable, reiniciá (volvés al estado anterior)")
        print("     y usá el nombre que el propio diagnóstico te muestre.")


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
