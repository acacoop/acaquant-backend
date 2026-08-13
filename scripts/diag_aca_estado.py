"""diag_aca_estado — ¿quedó bien parada la vista ACA en prod? READ-ONLY.

POR QUÉ EXISTE (REGLA #2). El schema se aplicó sin errores, pero "el statement
corrió" no es lo mismo que "insertó lo que tenía que insertar": las semillas de
`manager.role_matrix` están guardadas contra la resurrección, así que lo que
hagan depende de cómo esté la matriz REAL de prod, que desde afuera no se puede
ver. El caso que importa: `empleado_aca` se arma COPIANDO los módulos de
`sales`; si en prod `sales` no existe con ese nombre, el rol nuevo queda con la
vista ACA y NADA más (sin HOME, sin renta fija, sin back office) — la persona
entra a la app y no ve casi nada.

Esto lo mide y, si algo falta, dice exactamente qué tocar en el panel. No
escribe nada.

Uso (Droplet, raíz):
    python -m scripts.diag_aca_estado
"""
from __future__ import annotations

from api.services._sql import _q

OK, MAL, OJO = "✅", "❌", "⚠️ "


def _linea(char: str = "─") -> None:
    print(char * 74)


def main() -> None:
    problemas: list[str] = []

    _linea("=")
    print(" ACA — estado en esta base")
    _linea("=")

    # ── 1. Schema ───────────────────────────────────────────────────────────
    print("\n▶ 1. Tablas del schema `aca`")
    tablas = [r["table_name"] for r in _q(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'aca' ORDER BY table_name")]
    esperadas = {"activos", "audit", "clase_destacada", "emisor_destacado",
                 "historico", "moneda_regla", "periodos", "series"}
    faltan = esperadas - set(tablas)
    print(f"   {OK if not faltan else MAL} {len(tablas)}/8 tablas: {', '.join(tablas) or '(ninguna)'}")
    if faltan:
        problemas.append(f"faltan tablas: {', '.join(sorted(faltan))} → correr `python -m scripts.apply_schema`")

    # ── 2. Catálogos ────────────────────────────────────────────────────────
    print("\n▶ 2. Catálogos sembrados")
    for tabla, minimo, que_es in [
        ("moneda_regla", 8, "reglas de moneda (parten Dolarizado/Pesos)"),
        ("clase_destacada", 9, "clases fijas de las métricas"),
        ("emisor_destacado", 25, "emisores fijos de las métricas"),
        ("series", 9, "series del histórico"),
    ]:
        try:
            n = _q(f"SELECT count(*) AS n FROM aca.{tabla}")[0]["n"]
        except Exception as e:
            print(f"   {MAL} aca.{tabla}: {e}")
            problemas.append(f"aca.{tabla} no se pudo leer")
            continue
        marca = OK if n >= minimo else OJO
        print(f"   {marca} aca.{tabla:<17} {n:>3} filas   ({que_es})")
        if n == 0:
            problemas.append(f"aca.{tabla} vacía → la vista va a mostrar ese bloque en blanco")

    # ── 3. RBAC — lo que de verdad puede salir mal ──────────────────────────
    print("\n▶ 3. RBAC")
    matriz: dict[str, set[str]] = {}
    for r in _q("SELECT role, module FROM manager.role_matrix"):
        matriz.setdefault(r["role"], set()).add(r["module"])

    if not matriz:
        print(f"   {OJO} manager.role_matrix está VACÍA → manda DEFAULT_MATRIX del código.")
        print("      En ese caso `empleado_aca` ya tiene todo lo que corresponde.")
    else:
        con_aca = sorted(r for r, mods in matriz.items() if "aca" in mods)
        print(f"   {OK if con_aca else MAL} módulo `aca` asignado a: {', '.join(con_aca) or '(NADIE)'}")
        if not con_aca:
            problemas.append("nadie tiene el módulo `aca` → la vista es invisible. "
                             "Manager → ROLES Y PERMISOS, tildar `aca` en admin y empleado_aca")

        emp = matriz.get("empleado_aca", set())
        sales = matriz.get("sales", set())
        print(f"   ·  empleado_aca: {len(emp)} módulos" +
              (f" → {', '.join(sorted(emp))}" if emp else " (NO EXISTE en la matriz)"))
        print(f"   ·  sales:        {len(sales)} módulos (referencia: empleado_aca = sales + aca)")

        if not emp:
            problemas.append("el rol `empleado_aca` no existe en la matriz → no se puede asignar "
                             "a nadie. Manager → ROLES Y PERMISOS debería listarlo igual (sale de "
                             "DEFAULT_MATRIX); tildale los módulos y guardá")
        else:
            faltantes = sales - emp
            if faltantes:
                print(f"   {OJO} empleado_aca NO tiene, y sales sí: {', '.join(sorted(faltantes))}")
                problemas.append(
                    "a `empleado_aca` le faltan módulos base: " + ", ".join(sorted(faltantes)) +
                    " → Manager → ROLES Y PERMISOS, tildárselos (si no, esa gente entra y no ve casi nada)")
            elif len(emp) <= 1:
                print(f"   {OJO} empleado_aca tiene SOLO {emp or '{}'} — quedó pelado.")
                problemas.append("`empleado_aca` quedó con la vista ACA y nada más (probablemente "
                                 "porque en esta base el rol `sales` no existe con ese nombre) → "
                                 "asignale los módulos a mano en Manager → ROLES Y PERMISOS")

        if "aca" in matriz.get("sales", set()):
            problemas.append("`sales` TIENE el módulo `aca` y no debería: es el rol DEFAULT, así que "
                             "cualquier email nuevo vería la cartera propia. Sacáselo")
        if "aca" in matriz.get("invitado", set()):
            problemas.append("REGLA #8 VIOLADA: el rol `invitado` tiene `aca`. Sacáselo YA")

    # ── 4. Quién puede entrar hoy ───────────────────────────────────────────
    print("\n▶ 4. Usuarios")
    users = _q("SELECT role, count(*) AS n FROM manager.manager_users "
               "WHERE enabled IS NOT false GROUP BY role ORDER BY role")
    for u in users:
        marca = " ← ve ACA" if "aca" in matriz.get(u["role"], set()) else ""
        print(f"   ·  {u['role']:<22} {u['n']:>3} usuarios{marca}")
    n_emp = next((u["n"] for u in users if u["role"] == "empleado_aca"), 0)
    if not n_emp:
        print(f"   {OJO} todavía NADIE tiene el rol `empleado_aca`.")
        problemas.append("asignar el rol `empleado_aca` a la gente de ACA en Manager → USUARIOS "
                         "(es el único paso manual que queda)")

    escritores = _q("SELECT email FROM operaciones.mesa_dinero_escritores ORDER BY email")
    print(f"   ·  pueden ESCRIBIR en ACA (allowlist de Mesa de Dinero): "
          f"{', '.join(e['email'] for e in escritores) or '(solo admin)'}")

    # ── 5. Datos cargados ───────────────────────────────────────────────────
    print("\n▶ 5. Datos")
    periodos = _q("SELECT periodo, fecha_informe, "
                  "(SELECT count(*) FROM aca.activos a WHERE a.periodo = p.periodo) AS n "
                  "FROM aca.periodos p ORDER BY periodo DESC LIMIT 5")
    if not periodos:
        print("   ·  sin informes cargados todavía (esperable recién deployado)")
    for p in periodos:
        print(f"   ·  {p['periodo']}  al {p['fecha_informe']}  — {p['n']} activos")
    print(f"   ·  filas de histórico: {_q('SELECT count(*) AS n FROM aca.historico')[0]['n']}")

    # ── Veredicto ───────────────────────────────────────────────────────────
    print()
    _linea("=")
    if problemas:
        print(f" {OJO}FALTA HACER ESTO ({len(problemas)}):")
        for i, p in enumerate(problemas, 1):
            print(f"   {i}. {p}")
    else:
        print(f" {OK} TODO OK — la vista ACA está lista para usar.")
    _linea("=")


if __name__ == "__main__":
    main()
