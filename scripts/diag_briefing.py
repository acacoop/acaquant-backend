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

    _seccion("3) EL GATE: quién tiene el módulo `ia`")
    try:
        from core import roles
        with get_pool().connection() as cx, cx.cursor() as cur:
            cur.execute("SELECT role, modules FROM manager.role_matrix ORDER BY role")
            filas = cur.fetchall()
        if filas:
            for rol, mods in filas:
                tiene = "ia" in (mods or [])
                print(f"   {'✅' if tiene else '  '} {rol:<24} {'ia' if tiene else '— sin ia'}")
        else:
            print("   (role_matrix vacía → rige DEFAULT_MATRIX de core/roles.py)")
            for rol, mods in getattr(roles, "DEFAULT_MATRIX", {}).items():
                print(f"   {'✅' if 'ia' in mods else '  '} {rol:<24}")
    except Exception as e:
        print(f"   ❌ no se pudo leer la matriz: {type(e).__name__}: {e}")

    print(f"\n{SEP}\nSi (1) dice OK y (3) muestra tu rol con `ia`, el backend está bien\n"
          f"y lo que falla es el deploy del front o el proxy.\n{SEP}")


if __name__ == "__main__":
    main()
