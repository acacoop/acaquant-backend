"""Diagnóstico del módulo Estrategias Agro — chequea los 4 puntos típicos
donde puede romperse el 502 que ve el front:

1. ¿El motor motor_agro_opciones está corriendo? (systemctl)
2. ¿La colección Trading.AgroOpcionesSnapshot existe y tiene docs?
3. ¿El service get_panel_opciones devuelve algo sin reventar?
4. ¿La API local (uvicorn) tiene registrado el endpoint nuevo?

Uso (en el Droplet):
    python -m scripts.diag_agro_opciones
"""
from __future__ import annotations

import subprocess

from core.mongo import get_mongo_client_read


def check_motor() -> None:
    print("=== 1. motor_agro_opciones.service ===")
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "motor_agro_opciones.service"],
            capture_output=True, text=True, timeout=5,
        )
        status = out.stdout.strip() or out.stderr.strip()
        print(f"   estado: {status}")
        if status != "active":
            print("   → corré: systemctl start motor_agro_opciones.service")
        # Mostrar logs recientes para ver si arrancó OK.
        log = subprocess.run(
            ["journalctl", "-u", "motor_agro_opciones.service", "-n", "10", "--no-pager"],
            capture_output=True, text=True, timeout=5,
        )
        print("   últimas 10 líneas del log:")
        for line in (log.stdout or "").splitlines()[-10:]:
            print(f"     {line}")
    except Exception as e:
        print(f"   error chequeando systemd: {e}")


def check_mongo() -> None:
    print("\n=== 2. Trading.AgroOpcionesSnapshot ===")
    try:
        db = get_mongo_client_read()
        col = db["Trading"]["AgroOpcionesSnapshot"]
        n = col.count_documents({})
        print(f"   total docs: {n}")
        if n == 0:
            print("   → colección vacía. El motor todavía no escribió nada.")
            print("   → si el motor está active, esperá 5-10s y reintentá.")
            return
        # Stats por commodity y tipo.
        pipeline = [
            {"$group": {
                "_id": {"commodity": "$commodity", "tipo": "$tipo"},
                "n": {"$sum": 1},
                "with_last": {"$sum": {"$cond": [{"$ne": ["$last_price", None]}, 1, 0]}},
            }},
            {"$sort": {"_id.commodity": 1, "_id.tipo": 1}},
        ]
        for r in col.aggregate(pipeline):
            k = r["_id"]
            print(
                f"   {k.get('commodity'):>5} {k.get('tipo')}: "
                f"{r['n']:>3} docs ({r['with_last']} con last_price)"
            )
        # Sample
        sample = col.find_one({})
        if sample:
            print(f"   sample doc keys: {sorted(sample.keys())}")
    except Exception as e:
        print(f"   error: {e}")


def check_service() -> None:
    print("\n=== 3. service get_panel_opciones('TRIGO') ===")
    try:
        from api.services.derivados_agro import get_panel_opciones
        out = get_panel_opciones("TRIGO")
        print(f"   commodity: {out.get('commodity')}")
        print(f"   vencimientos: {len(out.get('vencimientos') or [])}")
        for v in (out.get("vencimientos") or [])[:3]:
            print(
                f"     · {v.get('vencimiento')}  futuro={v.get('futuro_last')}  "
                f"strikes={len(v.get('strikes') or [])}"
            )
    except Exception as e:
        import traceback
        print(f"   ROMPIÓ: {type(e).__name__}: {e}")
        traceback.print_exc()


def check_endpoint_local() -> None:
    print("\n=== 4. API local: ¿endpoint registrado? ===")
    try:
        from api.main import app
        rutas = sorted(
            (r.path for r in app.routes if "agro" in getattr(r, "path", "").lower()),
        )
        for r in rutas:
            print(f"   {r}")
        esperados = [
            "/api/derivados/agro",
            "/api/derivados/agro/opciones/{commodity}",
            "/api/derivados/agro/estrategia/simular",
        ]
        for e in esperados:
            if e in rutas:
                print(f"   ✓ {e}")
            else:
                print(f"   ✗ FALTA: {e}  (¿restart api.service?)")
    except Exception as e:
        print(f"   error: {e}")


if __name__ == "__main__":
    check_motor()
    check_mongo()
    check_service()
    check_endpoint_local()
