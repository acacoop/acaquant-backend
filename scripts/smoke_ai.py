"""scripts/smoke_ai.py — smoke E2E del gateway de IA (core/ai.py).

Prueba, en orden:
  1. DEEPSEEK_API_KEY presente en el .env.
  2. Tabla ia.trazas accesible (si no existe → correr scripts.apply_schema).
  3. GET /models del proveedor → imprime los IDs reales y avisa si el modelo
     default del gateway no está en la lista (REGLA #2: verificar, no asumir).
  4. Una completion real vía completar("smoke", ...) + su traza en ia.trazas.

Uso (Droplet, raíz del repo):  python -m scripts.smoke_ai
Exit code 0 = todo OK; 1 = algo falló (el detalle se imprime).
"""
from __future__ import annotations

import os
import sys

from core import ai
from core.postgres import connect


def main() -> int:
    ok = True

    # 1) key
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("✗ DEEPSEEK_API_KEY no está en el .env — el gateway queda apagado")
        return 1
    print("✓ DEEPSEEK_API_KEY presente")

    # 2) tabla de trazas
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ia.trazas")
            print(f"✓ ia.trazas accesible ({cur.fetchone()[0]} filas)")
    except Exception as e:
        print(f"✗ ia.trazas no accesible ({type(e).__name__}: {e})")
        print("  → correr primero: python -m scripts.apply_schema")
        return 1

    # 3) modelos disponibles en el proveedor
    import requests
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    flash = os.getenv("AI_MODEL_FLASH", "deepseek-v4-flash")
    try:
        resp = requests.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"},
            timeout=30,
        )
        ids = [m.get("id") for m in resp.json().get("data", [])]
        print(f"✓ {base}/models → {ids}")
        if ids and flash not in ids:
            print(f"⚠ el modelo default del gateway ({flash!r}) NO está en la lista.")
            print("  → setear AI_MODEL_FLASH (y AI_MODEL_PRO) en el .env con IDs de la lista.")
            ok = False
    except Exception as e:
        print(f"✗ /models falló: {type(e).__name__}: {e}")
        ok = False

    # 4) completion real + traza
    texto = ai.completar(
        "smoke",
        system="Sos un healthcheck. Respondé exactamente: OK",
        user="ping",
        usuario="smoke",
    )
    if texto:
        print(f"✓ completion: {texto[:80]!r}")
    else:
        print("✗ completar() devolvió None (ver warnings arriba)")
        ok = False
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ts, tarea, modelo, tokens_in, tokens_out, latencia_ms, ok, error"
                " FROM ia.trazas ORDER BY id DESC LIMIT 1"
            )
            print(f"  última traza: {cur.fetchone()}")
    except Exception as e:
        print(f"✗ no pude leer la última traza: {e}")
        ok = False

    print("\n" + ("SMOKE OK — gateway operativo de punta a punta." if ok
                  else "SMOKE CON FALLAS — ver arriba."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
