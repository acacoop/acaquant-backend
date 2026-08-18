"""diag_briefing — POR QUÉ desapareció el botón BRIEFING. Read-only.

El botón del briefing se esconde solo cuando `GET /api/ia/briefing` no devuelve
200. Del lado del front eso se veía IGUAL para dos causas muy distintas:

  · 401/403 → el usuario no tiene el módulo `ia` (el gate, y está bien);
  · 500/502 → el gate PASÓ y lo que se rompió es el briefing.

Este diag corre el service directo, sin HTTP y sin permisos de por medio, y
muestra el traceback COMPLETO si explota. Además chequea, fuente por fuente, de
cuál viene el dato que falta — el briefing arma cuatro bloques y cualquiera de
ellos puede tumbar el endpoint entero.

    python -m scripts.diag_briefing
"""
from __future__ import annotations

import traceback

SEP = "─" * 72


def _seccion(t: str) -> None:
    print(f"\n{SEP}\n{t}\n{SEP}")


def main() -> None:
    _seccion("1) EL ENDPOINT: ¿briefing_hoy() levanta o explota?")
    try:
        from api.services import briefing
        r = briefing.briefing_hoy()
        print("✅ OK — el service NO es el problema.")
        print(f"   fecha={r.get('fecha')}  generado={r.get('generado')}")
        for k in ("futuros", "oficial", "financieros", "cauciones",
                  "futuros_dlr", "bonos_off", "pagan_hoy"):
            v = r.get(k)
            print(f"   {k:<12} {len(v) if isinstance(v, list) else v}")
        rh = r.get("research_hoy")
        print(f"   research_hoy {'sí' if rh else 'no llegó hoy'}")
    except Exception:
        print("❌ EXPLOTA — este es el 500 que hace desaparecer el botón:\n")
        traceback.print_exc()

    _seccion("2) LAS FUENTES, una por una")
    from core.postgres import get_pool
    tablas = [
        ("home.market_quotes", "futuros + anclas WTD/MTD"),
        ("valuaciones.dolar_oficial_live", "mayorista MAE (feed de oficina)"),
        ("macro.series_macro", "A3500 del BCRA"),
        ("valuaciones.dolar", "MEP/CCL histórico"),
        ("ia.research", "mail 1816 del día"),
    ]
    with get_pool().connection() as cx, cx.cursor() as cur:
        for t, para in tablas:
            try:
                cur.execute(f"SELECT count(*) FROM {t}")
                n = cur.fetchone()[0]
                print(f"   {'✅' if n else '⚠️ '} {t:<34} {n:>8} filas   ({para})")
            except Exception as e:
                print(f"   ❌ {t:<34} {type(e).__name__}: {e}")

    _seccion("3) EL GATE: ¿quién puede ver /api/ia/* ?")
    # Se ejercita el MISMO camino que el gate (`require_module("ia")` →
    # `has_access`), no la tabla cruda: la matriz se filtra contra MODULES y
    # tiene fallbacks, así que leer las filas a mano puede decir una cosa y el
    # gate decidir otra.
    try:
        from core import roles
        print(f"   `ia` ∈ MODULES: {'sí' if 'ia' in roles.MODULES else '❌ NO — nadie pasa'}")
        mat = roles.get_matrix()
        if mat:
            for rol in sorted(mat):
                tiene = "ia" in mat[rol]
                print(f"   {'✅' if tiene else '  '} {rol:<24} {'ia' if tiene else '— sin ia'}")
        else:
            print("   (matriz vacía → rige DEFAULT_MATRIX)")
            for rol, mods in roles.DEFAULT_MATRIX.items():
                print(f"   {'✅' if 'ia' in mods else '  '} {rol:<24}")
    except Exception:
        traceback.print_exc()

    _seccion("4) VOS: el veredicto que da el backend para tu email")
    # Es LA pregunta. Si acá sale False, el backend devuelve 403 y el botón se
    # esconde por diseño; el problema es el permiso, no el briefing.
    try:
        from core import roles
        with get_pool().connection() as cx, cx.cursor() as cur:
            cur.execute("SELECT email, role, enabled FROM manager.manager_users "
                        "ORDER BY last_seen_at DESC NULLS LAST LIMIT 12")
            users = cur.fetchall()
        for email, rol, en in users:
            try:
                ok = roles.has_access(email, "ia")
            except Exception as e:
                ok = f"error: {e}"
            hab = "" if en is not False else "  (DESHABILITADO)"
            print(f"   {'✅' if ok is True else '❌'} {email:<38} rol={rol}{hab}")
    except Exception:
        traceback.print_exc()

    print(f"\n{SEP}\nSi (1) dice OK y (4) te da ✅, el backend está bien: el briefing\nresponde 200 y el problema está del lado del front / el deploy.\n{SEP}")


if __name__ == "__main__":
    main()
