"""diag_mongo_connections.py — lista las IPs conectadas a Atlas AHORA.

Sirve para validar, ANTES de sacar el 0.0.0.0/0 de la whitelist de Atlas, que
no haya nada conectándose desde afuera del Droplet. Corré esto en el Droplet
(la conexión sale de la IP del Droplet) y mirá qué IPs de cliente aparecen:
si las únicas son la del Droplet + IPs internas de Atlas, podés cerrar tranquilo.

Idealmente corrélo VARIAS veces a lo largo de un día hábil (con motores y crons
corriendo) para no perderte una conexión esporádica (un cron que corre 1 vez).

Usa `$currentOp` con idleConnections → ve TODAS las conexiones (activas e
idle), no solo las que están ejecutando algo. Requiere privilegio de monitoreo
(el usuario admin/atlasAdmin del cluster lo tiene; un app-user limitado quizás no).

Uso (en el Droplet, desde la raíz):
    python -m scripts.diag_mongo_connections
"""
from __future__ import annotations

from collections import defaultdict

from pymongo.errors import OperationFailure

from core.mongo import get_mongo_client


def main() -> int:
    client = get_mongo_client()
    try:
        ops = list(client.admin.aggregate([
            {"$currentOp": {"allUsers": True, "idleConnections": True}},
        ]))
    except OperationFailure as e:
        print(f"❌ No pude correr $currentOp (falta privilegio de monitoreo): {e}")
        print("   Alternativa: Atlas UI → Cluster → Metrics → Connections, o descargá")
        print("   los logs del cluster (Atlas → ... → Download Logs) y filtrá 'connection accepted'.")
        return 1

    # Agrupa por IP de cliente (el campo `client` viene como 'IP:puerto').
    por_ip: dict[str, int] = defaultdict(int)
    detalle: dict[str, set[str]] = defaultdict(set)
    for op in ops:
        cli = op.get("client") or op.get("client_s") or ""
        ip = cli.rsplit(":", 1)[0] if cli else "(sin client / interno)"
        por_ip[ip] += 1
        meta = op.get("clientMetadata") or {}
        app = meta.get("application", {}).get("name") or ""
        drv = (meta.get("driver") or {}).get("name") or ""
        if app or drv:
            detalle[ip].add(f"{app}/{drv}".strip("/"))

    print(f"Conexiones vistas: {len(ops)} | IPs distintas: {len(por_ip)}\n")
    for ip, n in sorted(por_ip.items(), key=lambda kv: -kv[1]):
        apps = ", ".join(sorted(detalle.get(ip, set()))) or "—"
        print(f"   {ip:<22} {n:>4} conexiones   [{apps}]")

    print("\n→ Si la única IP 'real' (no interna de Atlas) es la del Droplet, podés")
    print("  sacar el 0.0.0.0/0. Si aparece otra IP, identificá qué es ANTES de cerrar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
