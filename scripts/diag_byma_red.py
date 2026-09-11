"""¿POR QUÉ EL DROPLET NO LLEGA A BYMA? — diagnóstico de RED, read-only.

El job `custodia_cvsa` falla en el Droplet con `ConnectTimeout` contra
`api.byma.com.ar:443`, mientras que desde la PC del user (con AppGate/Okta
conectado) las mismas llamadas funcionan.

Un timeout de conexión —no un "connection refused", no un 403— significa que los
paquetes se están DROPEANDO en silencio. Eso pasa en dos lugares y hay que saber
en cuál, porque la solución es completamente distinta:

  (A) BYMA filtra por IP de origen. La PC entra por la red corporativa; el
      Droplet es una IP de nube y no está en su lista. → Se resuelve pidiéndoles
      que habiliten nuestra IP, y para eso hay que saber cuál es (este diag la
      imprime).

  (B) El Droplet bloquea la salida. → Se resuelve acá, tocando el firewall.

Este script las separa. NO escribe nada, no usa credenciales y no manda una sola
request autenticada: solo abre sockets y mira si contestan.

Uso:
    python -m scripts.diag_byma_red
"""
from __future__ import annotations

import socket
import ssl
import subprocess
import urllib.request

TIMEOUT_S = 8

# Hosts a probar. Los de CONTROL son APIs externas que el sistema YA usa todos
# los días: si esas conectan y BYMA no, el problema no es nuestra salida a
# internet — es específico de BYMA.
OBJETIVOS = [
    ("api.byma.com.ar",      443, "BYMA producción       ", "objetivo"),
    ("hs-api.byma.com.ar",   443, "BYMA homologación     ", "objetivo"),
    ("clearing-api.byma.com.ar", 443, "BYMA clearing         ", "objetivo"),
    ("aca.aunesa.com",       443, "Aunesa (el custodio)  ", "control"),
    ("api.bcra.gob.ar",      443, "BCRA                  ", "control"),
    ("finnhub.io",           443, "Finnhub               ", "control"),
]


def _dns(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except OSError as e:
        return f"NO RESUELVE ({type(e).__name__})"


def _tcp(host: str, puerto: int) -> tuple[bool, str]:
    """¿Abre el TCP? Distingue timeout (filtrado) de refused (nadie escucha)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT_S)
    try:
        s.connect((host, puerto))
        return True, "conecta"
    except TimeoutError:
        # El caso que importa: nadie contesta ni para decir que no. Firewall.
        return False, f"TIMEOUT ({TIMEOUT_S}s) — paquetes dropeados en silencio"
    except ConnectionRefusedError:
        return False, "REFUSED — llegó, pero nadie escucha ese puerto"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        s.close()


def _tls(host: str) -> str:
    """Si el TCP abre, ¿completa el handshake TLS? (descarta inspección SSL)."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=TIMEOUT_S) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
                cn = dict(x[0] for x in cert.get("subject", ())).get("commonName", "?")
                return f"TLS OK (cert: {cn})"
    except Exception as e:  # un diag no se cae por nada
        return f"TLS falla: {type(e).__name__}: {e}"


def _ip_publica() -> str:
    """Con qué IP nos ve el mundo. ES EL DATO QUE HAY QUE PASARLE A BYMA."""
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:
                return r.read().decode().strip()
        except Exception:
            continue
    return "no se pudo averiguar"


def _firewall() -> list[str]:
    """¿Hay reglas de SALIDA en el Droplet? (la hipótesis B)."""
    out = []
    for cmd, nombre in ((["ufw", "status"], "ufw"),
                        (["iptables", "-S", "OUTPUT"], "iptables OUTPUT")):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            txt = (r.stdout or r.stderr).strip()
            out.append(f"  {nombre}: {txt.splitlines()[0] if txt else '(vacío)'}")
            for linea in txt.splitlines()[1:6]:
                out.append(f"      {linea}")
        except Exception as e:
            out.append(f"  {nombre}: no se pudo consultar ({type(e).__name__})")
    return out


def main() -> int:
    print("=" * 74)
    print("¿POR QUÉ EL DROPLET NO LLEGA A BYMA?")
    print("=" * 74)
    print(f"\nNuestra IP pública de salida: {_ip_publica()}")
    print("  (es la que hay que pedirle a BYMA que habilite, si el problema es de ellos)\n")

    resultados = {}
    for host, puerto, etiqueta, clase in OBJETIVOS:
        ip = _dns(host)
        ok, detalle = (False, "sin DNS") if "NO RESUELVE" in ip else _tcp(host, puerto)
        resultados[host] = (ok, clase)
        marca = "OK  " if ok else "FALLA"
        print(f"  [{marca}] {etiqueta} {host:32} {ip:18} {detalle}")
        if ok and "byma" in host:
            print(f"           {_tls(host)}")

    print("\nFirewall local:")
    for linea in _firewall():
        print(linea)

    bymas = [h for h, (ok, c) in resultados.items() if c == "objetivo" and ok]
    controles_ok = [h for h, (ok, c) in resultados.items() if c == "control" and ok]
    controles_total = sum(1 for _, _, _, c in OBJETIVOS if c == "control")

    print("\n" + "=" * 74)
    print("VEREDICTO")
    print("=" * 74)
    if bymas:
        print(f"Alguno de BYMA SÍ conecta: {', '.join(bymas)}")
        print("→ No es un bloqueo general. Usar ese host, o mirar qué tiene de")
        print("  distinto el que falla.")
    elif len(controles_ok) == controles_total:
        print("NINGÚN host de BYMA conecta, pero las OTRAS APIs externas SÍ.")
        print("→ La salida a internet del Droplet funciona perfecto. El bloqueo es")
        print("  ESPECÍFICO DE BYMA, y como es timeout (no refused, no 403), es un")
        print("  filtro de red del lado de ellos.")
        print("→ HAY QUE PEDIRLE A BYMA que habilite la IP pública de arriba. El")
        print("  panel de la aplicación dice 'Todas las IPs permitidas', así que el")
        print("  filtro NO está en la capa de la app: está antes, en su perímetro.")
    elif not controles_ok:
        print("NO CONECTA NADA, ni siquiera las APIs que el sistema usa todos los días.")
        print("→ El problema es del Droplet (red o firewall), no de BYMA. Mirar las")
        print("  reglas de arriba.")
    else:
        print(f"Conectan {len(controles_ok)}/{controles_total} controles y ningún BYMA.")
        print("→ Mixto: mirar caso por caso antes de reclamarle a nadie.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
