"""¿QUIÉN puede ver SALUD (y todo Manager)? — read-only, no toca nada.

Incidente 2026-08-10: reporte de que las alertas de SALUD las estaba viendo gente
que no es admin. El código está bien —`/api/manager/salud*` exige el módulo
`manager` y devuelve 403 a trader / sales / asistente_comercial / back_office /
invitado— así que si alguien las ve es porque **su rol tiene `manager` en la
matriz VIVA** (`manager.role_matrix`, que PISA a `DEFAULT_MATRIX`) o porque su
usuario está cargado como `admin`.

Esto responde las dos preguntas con datos de la base real:

  1. ¿Qué ROLES tienen hoy el módulo `manager`? (matriz viva vs. default)
  2. ¿Qué USUARIOS caen en esos roles? — la lista concreta de quién lo ve.

    python -m scripts.diag_salud_quien_ve

No escribe nada. Salida por stdout.
"""
from __future__ import annotations

from core.roles import DEFAULT_MATRIX, MODULES, get_matrix

# Módulos que abren la puerta de /api/manager/* (el gate base de api/main.py).
# `manager` es el que habilita SALUD y el resto de las tabs de admin.
_MODULO_SALUD = "manager"


def _usuarios() -> list[dict]:
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT email, role, enabled, last_seen_at "
            "FROM manager.manager_users ORDER BY role, email"
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def main() -> None:
    viva = get_matrix()

    print("=" * 78)
    print("1) ROLES CON EL MÓDULO `manager` (el que habilita SALUD)")
    print("=" * 78)
    con_manager = sorted(r for r, mods in viva.items() if _MODULO_SALUD in mods)
    default_con = sorted(r for r, mods in DEFAULT_MATRIX.items() if _MODULO_SALUD in mods)
    print(f"   matriz VIVA (manager.role_matrix): {con_manager or '—'}")
    print(f"   default del código               : {default_con or '—'}")
    de_mas = sorted(set(con_manager) - set(default_con))
    if de_mas:
        print(f"   ⚠️  ROLES DE MÁS: {de_mas}")
        print("       → esos roles ven SALUD y TODO Manager. Se destilda "
              "`manager` en /manager → ROLES Y PERMISOS.")
    else:
        print("   ✅ la matriz viva no le da `manager` a nadie de más")

    print()
    print("=" * 78)
    print("2) DIFERENCIAS DE LA MATRIZ VIVA CONTRA EL DEFAULT (todos los módulos)")
    print("=" * 78)
    hubo = False
    for rol in sorted(set(viva) | set(DEFAULT_MATRIX)):
        v, d = set(viva.get(rol, ())), set(DEFAULT_MATRIX.get(rol, ()))
        extra, falta = sorted(v - d), sorted(d - v)
        if extra or falta:
            hubo = True
            print(f"   {rol}:")
            if extra:
                print(f"      + de más : {extra}")
            if falta:
                print(f"      - de menos: {falta}")
    if not hubo:
        print("   (la matriz viva es idéntica al default)")

    desconocidos = sorted({m for mods in viva.values() for m in mods} - set(MODULES))
    if desconocidos:
        print(f"\n   ⚠️  módulos en la matriz que NO existen en core/roles.MODULES: "
              f"{desconocidos} (no gatean nada)")

    print()
    print("=" * 78)
    print("3) USUARIOS QUE HOY VEN SALUD")
    print("=" * 78)
    try:
        users = _usuarios()
    except Exception as e:  # diag: informar y salir prolijo
        print(f"   no pude leer manager.manager_users: {type(e).__name__}: {e}")
        return

    ven = [u for u in users if _MODULO_SALUD in viva.get(u.get("role") or "", ())
           and u.get("enabled", True)]
    print(f"   {len(ven)} de {len(users)} usuarios habilitados lo ven:\n")
    for u in ven:
        visto = str(u.get("last_seen_at") or "")[:16] or "nunca"
        print(f"      {u['email']:45s} rol={u.get('role'):20s} últ. visto {visto}")

    por_rol: dict[str, int] = {}
    for u in users:
        por_rol[u.get("role") or "?"] = por_rol.get(u.get("role") or "?", 0) + 1
    print("\n   usuarios por rol:", dict(sorted(por_rol.items())))
    print("\n   Nota: si acá aparece alguien que NO debería, hay dos arreglos "
          "posibles y son distintos —")
    print("     · el ROL tiene `manager` de más  → destildar en /manager → ROLES Y PERMISOS")
    print("     · la PERSONA está cargada como admin → cambiarle el rol en /manager → USUARIOS")


if __name__ == "__main__":
    main()
