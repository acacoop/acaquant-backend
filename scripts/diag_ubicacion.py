"""scripts/diag_ubicacion.py — DÓNDE está hosteada cada pieza (read-only).

Responde con datos duros la pregunta previa a cualquier decisión de latencia:
¿en qué región vive el Droplet, en qué región vive Supabase, y cuánto cuesta
el viaje entre los dos?

Contexto (2026-08-13): Vercel corre las Functions en `iad1` (Washington DC) y
la pregunta es si conviene moverlas. Lo que decide eso NO es dónde está
Supabase: las funciones de Next son un PROXY — las 20 rutas de `src/app/api`
pegan a `api.acaquant.com` y el front no tiene cliente de base (verificado en
package.json). O sea, la pata que paga Vercel es Vercel → DROPLET, y el viaje
Droplet → Supabase lo paga el backend, lo mueva Vercel a donde lo mueva.

Imprime:
  1. Droplet  — región DigitalOcean (metadata service), hostname, IP pública.
  2. Supabase — host (SIN credenciales), IP, región si el hostname la declara.
  3. El viaje — TCP handshake puro + RTT de query, para separar red de protocolo.

Uso (en el Droplet):
    python -m scripts.diag_ubicacion

One-shot: cuando la decisión de región esté tomada, se borra (REGLA #5).
"""
from __future__ import annotations

import os
import re
import socket
import statistics
import time
import urllib.request
from urllib.parse import urlparse

from core.postgres import get_pool

# Metadata de DigitalOcean: link-local, sin auth, solo lectura. Si el script
# corre fuera del Droplet no existe → timeout corto y seguimos.
_DO_METADATA = "http://169.254.169.254/metadata/v1"
_TIMEOUT_S = 2.0
_N_PINGS = 20
_N_HANDSHAKES = 5

# Los poolers de Supabase llevan la región AWS EN EL HOSTNAME
# (`aws-0-us-east-1.pooler.supabase.com` → us-east-1). La conexión directa
# (`db.<ref>.supabase.co`) no la declara: ahí solo queda la IP.
_RE_REGION_POOLER = re.compile(r"aws-\d+-([a-z]{2}-[a-z]+-\d)\.")


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def _metadata(campo: str) -> str | None:
    try:
        with urllib.request.urlopen(f"{_DO_METADATA}/{campo}", timeout=_TIMEOUT_S) as r:
            return r.read().decode().strip() or None
    except Exception:
        return None


def _droplet() -> None:
    print("1) DROPLET (DigitalOcean)")
    region = _metadata("region")
    if region:
        # El sufijo numérico es el datacenter dentro de la ciudad (nyc1/nyc3…).
        ciudad = {"nyc": "Nueva York", "sfo": "San Francisco", "tor": "Toronto",
                  "ams": "Ámsterdam", "fra": "Fráncfort", "lon": "Londres",
                  "sgp": "Singapur", "blr": "Bangalore", "syd": "Sídney",
                  "atl": "Atlanta"}.get(region[:3], "?")
        print(f"   región      : {region}  ({ciudad})")
    else:
        print("   región      : no disponible (¿corriendo fuera del Droplet?)")
    print(f"   hostname    : {_metadata('hostname') or socket.gethostname()}")
    ip = _metadata("interfaces/public/0/ipv4/address")
    print(f"   IP pública  : {ip or 'no disponible'}")


def _supabase() -> tuple[str | None, int]:
    """Imprime dónde vive la base. Devuelve (host, puerto) para medir después."""
    print("\n2) BASE (Supabase / Postgres)")
    uri = os.environ.get("POSTGRES_URI", "")
    if not uri:
        print("   POSTGRES_URI no está en el entorno — nada que inspeccionar.")
        return None, 0
    # urlparse deja usuario/contraseña en .username/.password: NO se imprimen.
    p = urlparse(uri)
    host, puerto = p.hostname, (p.port or 5432)
    print(f"   host        : {host}:{puerto}")
    m = _RE_REGION_POOLER.search(host or "")
    if m:
        print(f"   región AWS  : {m.group(1)}  (declarada en el hostname del pooler)")
    else:
        print("   región AWS  : el hostname no la declara (conexión directa) — "
              "mirarla en el dashboard de Supabase → Settings → General")
    try:
        print(f"   IP          : {socket.gethostbyname(host)}")
    except OSError as e:
        print(f"   IP          : no resuelve ({e})")
    return host, puerto


def _viaje(host: str | None, puerto: int) -> None:
    """Separa la RED (handshake TCP) del PROTOCOLO (query ida y vuelta).

    Si los dos números son parecidos, lo que se paga es distancia física pura y
    lo único que la baja es acercar las piezas. La diferencia entre ambos es lo
    que agrega el pooler + Postgres, que NO se arregla mudando nada."""
    print("\n3) EL VIAJE Droplet → base")
    if not host:
        print("   sin POSTGRES_URI no hay nada que medir.")
        return
    if host:
        hs: list[float] = []
        for _ in range(_N_HANDSHAKES):
            t0 = time.perf_counter()
            try:
                with socket.create_connection((host, puerto), timeout=_TIMEOUT_S):
                    hs.append(_ms(t0))
            except OSError as e:
                print(f"   handshake TCP: falló ({e})")
                break
        if hs:
            print(f"   handshake TCP: min {min(hs):5.1f} ms   "
                  f"(red pura, sin Postgres de por medio)")

    pool = get_pool()
    tiempos: list[float] = []
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")  # warm-up fuera de la muestra
        for _ in range(_N_PINGS):
            t0 = time.perf_counter()
            cur.execute("SELECT 1")
            cur.fetchone()
            tiempos.append(_ms(t0))
    print(f"   RTT query    : min {min(tiempos):5.1f} ms   "
          f"p50 {statistics.median(tiempos):5.1f} ms   max {max(tiempos):5.1f} ms")
    print(f"   → cada query del sistema paga ~{min(tiempos):.0f} ms de peaje, "
          "haga lo que haga la query.")


def main() -> int:
    _droplet()
    host, puerto = _supabase()
    _viaje(host, puerto)
    print("""
LECTURA
  · Droplet y base en la MISMA región (RTT < 5 ms) → no hay peaje que recortar
    ahí; lo que sobre son queries + Python.
  · RTT 20-80 ms → están lejos. Es la pata más cara del sistema y NO la arregla
    mover Vercel: la paga el backend en cada query.
  · Para elegir la región de Vercel importa OTRA pata — Vercel → Droplet — que
    se mide desde Vercel, no desde acá.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
