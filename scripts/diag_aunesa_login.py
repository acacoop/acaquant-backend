"""Diag MÍNIMO del login de Aunesa. Solo eso: loguearse.

Sirve para contestar UNA pregunta con evidencia: ¿el 500 es de ellos o nuestro?

La prueba que lo decide es la #4: se manda una credencial BASURA a propósito.

  · si con basura responde 401  → su validación funciona ⇒ el problema son NUESTRAS
    credenciales (vencidas, cambiadas, usuario bloqueado).
  · si con basura responde 500  → revienta ANTES de mirar quién sos ⇒ es un bug de
    ELLOS y no hay nada que cambiar de este lado.

Los pasos previos van descartando lo de más abajo hacia arriba: DNS, TCP/TLS, y si
el servidor está vivo para otras rutas.

READ-ONLY: solo hace requests de login, no toca la base ni escribe nada.

Uso:
    python -m scripts.diag_aunesa_login
"""
from __future__ import annotations

import socket
import time
from urllib.parse import urlparse

import requests

import config

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
TIMEOUT = 20


def _mascara(v: str | None) -> str:
    """Muestra lo suficiente para ver si está cargada y si cambió, sin filtrarla."""
    if not v:
        return "(VACÍA)"
    s = str(v)
    return f"{s[:2]}…{s[-2:]} ({len(s)} caracteres)"


def paso1_config() -> None:
    print("\n── 1) ¿Están cargadas las credenciales? ──────────────────────")
    print(f"   clientId : {config.AUNESA_CLIENT_ID or '(VACÍO)'}")
    print(f"   username : {config.AUNESA_USERNAME or '(VACÍO)'}")
    print(f"   password : {_mascara(config.AUNESA_PASSWORD)}")
    if not all([config.AUNESA_CLIENT_ID, config.AUNESA_USERNAME, config.AUNESA_PASSWORD]):
        print("   ⚠ falta alguna: revisar el .env del Droplet")


def paso2_red() -> None:
    print("\n── 2) ¿Llegamos al servidor? (DNS + TCP) ─────────────────────")
    host = urlparse(AUTH_URL).hostname or ""
    try:
        ip = socket.gethostbyname(host)
        print(f"   DNS  {host} → {ip}  ✓")
    except Exception as e:
        print(f"   DNS  {host} → ✗ {type(e).__name__}: {e}")
        return
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, 443), timeout=10):
            print(f"   TCP  {host}:443 abierto  ✓  ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    except Exception as e:
        print(f"   TCP  {host}:443 → ✗ {type(e).__name__}: {e}")


def _post(nombre: str, payload: dict) -> int | None:
    """POST al login mostrando TODO lo que contesta."""
    print(f"\n   ▸ {nombre}")
    t0 = time.perf_counter()
    try:
        r = requests.post(AUTH_URL, json=payload,
                          headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
    except Exception as e:
        print(f"     ✗ sin respuesta: {type(e).__name__}: {e}")
        return None
    ms = (time.perf_counter() - t0) * 1000
    print(f"     HTTP {r.status_code}   ({ms:.0f} ms)")
    # El servidor y el content-type dicen quién contesta: Tomcat con text/html es la
    # página de error por defecto, o sea una excepción sin manejar de su aplicación.
    for h in ("server", "content-type", "x-powered-by"):
        if r.headers.get(h):
            print(f"     {h}: {r.headers[h]}")
    cuerpo = (r.text or "").strip()
    if "<html" in cuerpo[:200].lower():
        titulo = ""
        if "<title>" in cuerpo.lower():
            i = cuerpo.lower().index("<title>") + 7
            titulo = cuerpo[i:cuerpo.lower().index("</title>", i)]
        print(f"     cuerpo: HTML de error del servidor → «{titulo or cuerpo[:120]}»")
    else:
        print(f"     cuerpo: {cuerpo[:300] or '(vacío)'}")
    if r.status_code == 200:
        try:
            print(f"     token: {'SÍ' if r.json().get('token') else 'NO vino token'}")
        except Exception:
            print("     ⚠ 200 pero la respuesta no es JSON")
    return r.status_code


def paso3_login_real() -> int | None:
    print("\n── 3) LOGIN con las credenciales reales ──────────────────────")
    return _post("credenciales de config", {
        "clientId": config.AUNESA_CLIENT_ID,
        "username": config.AUNESA_USERNAME,
        "password": config.AUNESA_PASSWORD,
    })


def paso4_login_basura() -> int | None:
    """LA prueba que separa 'es de ellos' de 'es nuestro'."""
    print("\n── 4) LOGIN con credenciales BASURA (la prueba clave) ────────")
    print("   Si su validación funciona, esto TIENE que dar 401.")
    return _post("usuario/clave inventados", {
        "clientId": "no-existe", "username": "no-existe", "password": "no-existe",
    })


def veredicto(real: int | None, basura: int | None) -> None:
    print(f"\n{'=' * 66}\nVEREDICTO\n{'=' * 66}")
    if real == 200:
        print("✓ El login FUNCIONA. Si algo falla, no es la autenticación.")
    elif basura == 500 and real == 500:
        print("✗ Es de AUNESA. Con credenciales inventadas también da 500: su servidor")
        print("  revienta ANTES de validar quién sos, así que no hay nada que cambiar")
        print("  de nuestro lado. Con este output pueden buscar la excepción en su log.")
    elif basura in (400, 401, 403) and real == 500:
        print("⚠ RARO: su validación funciona (la basura da " + str(basura) + ") pero NUESTRAS")
        print("  credenciales hacen reventar el servidor. Puede ser un dato del usuario")
        print("  nuestro que les rompe algo. Vale reportarlo igual.")
    elif real in (401, 403):
        print("✗ Es NUESTRO: las credenciales fueron rechazadas. Revisar el .env del")
        print("  Droplet — clave vencida, cambiada, o el usuario bloqueado.")
    elif real is None:
        print("✗ Ni siquiera contesta: mirar el paso 2 (DNS/TCP). Puede ser red o firewall.")
    else:
        print(f"? Sin patrón conocido (real={real}, basura={basura}). Mandar este output.")
    print()


def main() -> int:
    print(f"\n{'=' * 66}\nDIAG AUNESA — solo el LOGIN\n{AUTH_URL}\n{'=' * 66}")
    paso1_config()
    paso2_red()
    real = paso3_login_real()
    basura = paso4_login_basura()
    veredicto(real, basura)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
