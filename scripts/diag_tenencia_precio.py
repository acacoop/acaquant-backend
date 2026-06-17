"""diag_tenencia_precio — aísla el 502 del editor de precio de Tenencia HD.

Llama a `actualizar_precio_posicion` DIRECTO (sin Cloudflare/Vercel), usando el
precio ACTUAL de una posición → el write es idempotente (NO cambia ningún valor),
solo prueba que la ruta backend funcione. Si imprime "RESULTADO OK", el backend
está sano y el 502 es de la capa CF/uvicorn/deploy. Si tira traceback, ahí está.

Uso (en el Droplet):  python -m scripts.diag_tenencia_precio
"""
from __future__ import annotations

import traceback


def main() -> None:
    from api.services.tenencia_hd import (
        actualizar_precio_posicion,
        tenencia_dias,
        tenencia_posiciones,
    )

    dias = tenencia_dias()
    f = dias.get("ultima_fecha")
    print(f"última fecha: {f}  ·  {len(dias.get('dias', []))} días")
    if not f:
        print("✗ no hay días en Valuaciones.TenenciaHD — ¿corrió el backfill?")
        return

    pos = tenencia_posiciones(fecha=f)
    ps = pos.get("posiciones", [])
    con_cant = [p for p in ps if "cant" in p and p.get("precio") is not None]
    print(f"posiciones: {len(ps)}  ·  con cant+precio: {len(con_cant)}")
    if not con_cant:
        print("✗ ninguna posición tiene cant+precio → el editor no puede operar. "
              "Re-corré: python -m jobs.tenencia_hd --backfill --desde 2026-04-01")
        return

    p = con_cant[0]
    print(f"probando unidad={p['unidad']!r} con su precio ACTUAL={p['precio']} (write idempotente)…")
    try:
        r = actualizar_precio_posicion(fecha=f, unidad=p["unidad"], precio=float(p["precio"]))
        print(f"✓ RESULTADO OK: {r}")
        print("→ El backend funciona. El 502 es de la capa CF/uvicorn/deploy, "
              "no del código. Revisá: systemctl status api.service + journalctl.")
    except Exception:
        print("✗ EXCEPCIÓN en el backend (acá está la causa del 502):")
        traceback.print_exc()


if __name__ == "__main__":
    main()
