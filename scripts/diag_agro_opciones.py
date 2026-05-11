"""Diagnóstico del módulo Estrategias Agro — chequea los 5 puntos típicos
donde puede romperse el 502 que ve el front:

1. ¿El motor motor_agro_opciones está corriendo? (systemctl)
2. ¿La colección Trading.AgroOpcionesSnapshot existe y tiene docs?
3. ¿El service get_panel_opciones devuelve algo sin reventar? ¿Con
   futuro_last poblado (post-fix)?
4. ¿La API local (uvicorn) tiene registrado el endpoint nuevo?
5. ¿El api.service running está usando el código nuevo? (curl local
   al endpoint público de simulación)

Uso (en el Droplet):
    python -m scripts.diag_agro_opciones
"""
from __future__ import annotations

import json
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
    print("\n=== 3. service get_panel_opciones — código en disco ===")
    try:
        from api.services.derivados_agro import get_panel_opciones
        for com in ("TRIGO", "MAIZ", "SOJA"):
            out = get_panel_opciones(com)
            ven_list = out.get("vencimientos") or []
            print(f"   {com}: {len(ven_list)} vencimientos")
            for v in ven_list[:4]:
                f_ticker = v.get("futuro_ticker") or "—"
                f_last = v.get("futuro_last")
                f_vto = v.get("futuro_vto") or "—"
                o_vto = v.get("vencimiento") or "—"
                strikes = len(v.get("strikes") or [])
                print(
                    f"     · {f_ticker:<18} fut_vto={f_vto}  opt_vto={o_vto}  "
                    f"fut_last={f_last}  strikes={strikes}"
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


def check_api_running() -> None:
    """Curl al api.service real (port 8000) — si responde con el shape post-fix
    sabemos que el restart se hizo. Si responde con shape viejo, hay que
    reiniciar el service."""
    print("\n=== 5. api.service running — pega a /api/derivados/agro/opciones/SOJA ===")
    try:
        st = subprocess.run(
            ["systemctl", "show", "api.service", "-p", "ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5,
        )
        print(f"   {st.stdout.strip()}")
    except Exception as e:
        print(f"   no pude leer ActiveEnterTimestamp: {e}")

    # Pega a localhost. La auth la maneja CF Access más arriba, pero el
    # api.service expone 8000 con auth Bearer del env. Probamos sin headers
    # para ver el status; si dice 401 al menos sabemos que el endpoint
    # responde y luego comparamos shape con curl autenticado si necesario.
    try:
        r = subprocess.run(
            [
                "curl", "-s", "-o", "/tmp/diag_agro.json", "-w", "%{http_code}",
                "-H", "Authorization: Bearer dev",
                "http://127.0.0.1:8000/api/derivados/agro/opciones/SOJA",
            ],
            capture_output=True, text=True, timeout=10,
        )
        code = r.stdout.strip()
        print(f"   HTTP {code}")
        with open("/tmp/diag_agro.json") as f:
            body = f.read()[:2000]
        try:
            j = json.loads(body)
            ven = j.get("vencimientos") or []
            if ven:
                print(f"   vencimientos en response: {len(ven)}")
                v0 = ven[0]
                has_new = "futuro_vto" in v0
                print(f"   ¿shape post-fix (campo 'futuro_vto')?: {has_new}")
                if not has_new:
                    print("   → api.service tiene código VIEJO. Hacé:")
                    print("        systemctl restart api.service")
                else:
                    print(f"   sample vto[0]: futuro_ticker={v0.get('futuro_ticker')} "
                          f"futuro_last={v0.get('futuro_last')}")
            else:
                print(f"   body (recortado): {body[:400]}")
        except json.JSONDecodeError:
            print(f"   body no-JSON (recortado): {body[:400]}")
    except Exception as e:
        print(f"   error en curl: {e}")


if __name__ == "__main__":
    check_motor()
    check_mongo()
    check_service()
    check_endpoint_local()
    check_api_running()
