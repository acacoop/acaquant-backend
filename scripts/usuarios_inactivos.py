"""Reporte de usuarios para la revisión periódica de accesos.

Read-only. Lista los usuarios HABILITADOS de Manager.Users ordenados por
inactividad, marcando los que no entran hace > N días o nunca se vieron.
Candidatos obvios a deshabilitar en la revisión trimestral.

NO modifica nada. Para deshabilitar un usuario: /manager → USUARIOS, o
core.roles.upsert_user(email, role, enabled=False).

Uso (en el Droplet):
    python -m scripts.usuarios_inactivos            # umbral 90 días
    python -m scripts.usuarios_inactivos --dias 30
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read


def main() -> int:
    dias = 90
    if "--dias" in sys.argv:
        try:
            dias = int(sys.argv[sys.argv.index("--dias") + 1])
        except (IndexError, ValueError):
            pass

    col = get_mongo_client_read()["Manager"]["Users"]
    ahora = datetime.now(UTC)
    umbral = ahora - timedelta(days=dias)

    usuarios = list(
        col.find(
            {"enabled": {"$ne": False}},
            {"_id": 0, "email": 1, "role": 1, "last_seen_at": 1, "auto_registered": 1},
        )
    )
    if not usuarios:
        print("No hay usuarios habilitados en Manager.Users.")
        return 0

    def _key(u: dict):
        ls = u.get("last_seen_at")
        return ls if isinstance(ls, datetime) else datetime.min.replace(tzinfo=UTC)

    usuarios.sort(key=_key)  # los más viejos / nunca-vistos primero

    inactivos = 0
    print(f"Usuarios habilitados: {len(usuarios)} · umbral de inactividad: {dias} días\n")
    print(f"{'EMAIL':<40} {'ROLE':<8} {'ÚLT. VISTO':<22} FLAG")
    print("-" * 90)
    for u in usuarios:
        ls = u.get("last_seen_at")
        if isinstance(ls, datetime):
            visto = ls.strftime("%Y-%m-%d %H:%M UTC")
            flag = "⚠️ INACTIVO" if ls < umbral else ""
        else:
            visto = "(nunca)"
            flag = "⚠️ NUNCA ENTRÓ"
        if flag:
            inactivos += 1
        auto = " ·auto" if u.get("auto_registered") else ""
        print(f"{u.get('email', '?'):<40} {u.get('role', '?'):<8} {visto:<22} {flag}{auto}")

    print(f"\n{inactivos} candidato(s) a revisar/deshabilitar (inactivos ≥ {dias}d o nunca entraron).")
    print("Para deshabilitar: /manager → USUARIOS (toggle enabled).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
