"""READ-ONLY. ¿Se puede atar la producción de MESA DE DINERO a un operador, y cuánto mueve?

Herramienta: diag · Solo SELECT, cero escrituras.

EL PUNTO
========

La vista OPERADORES mide hoy la producción de un comercial con UNA sola cosa: el
`arancel` de `operaciones.operaciones`, atribuido por `id_cuenta` →
`comitentes.operador_email`. Todo por EMAIL.

MESA DE DINERO produce otra cosa —**resultado de intermediación**, no arancel del
cliente— y hoy muere en su propia tab: `operaciones.mesa_dinero` guarda
`resultado` + `observacion`, y la tab RESULTADOS reparte 50/50 entre el operador
de la observación y la Mesa. Ese 50% es lo que tiene que sumar al operador.

El problema: **`observacion` guarda el NOMBRE del operador, y todo lo demás
atribuye por EMAIL.** `clientes.operadores.email` es la PK; `nombre` no es única
y puede ser NULL. Unir por nombre es el modo de falla de la REGLA #9: si dos
operadores comparten nombre, si alguien corrige uno, o si quedó NULL, la plata se
imputa mal **y el total sigue dando bien**. Nadie se entera.

Antes de escribir el código hay que saber, con números y no con hipótesis:

  1. CATÁLOGO   · ¿`clientes.operadores.nombre` sirve como clave? ¿Hay NULLs?
                  ¿Hay nombres REPETIDOS (dos emails, un nombre)?
  2. MESA       · ¿Qué hay cargado en `observacion`? ¿Cuántas filas apuntan a un
                  operador, cuántas a "Mesa", cuántas a nada?
  3. MATCH      · De las que apuntan a un operador: ¿cuántas resuelven a UN email
                  (sirve), a NINGUNO (huérfana) o a VARIOS (ambigua = peligrosa)?
  4. PLATA      · Cuánto es el 50% en juego por año, y cuánto quedaría SIN imputar
                  por no matchear. Si los huérfanos son plata seria, el backfill
                  necesita una decisión antes, no después.
  5. ALLOWLIST  · La idea de poblar el selector de OBS con la gente de
                  `mesa_dinero_lectores_resultados` (Manager → MESA) SOLO funciona
                  si esos emails son operadores. `lectores_resultados` se llena
                  desde `manager.manager_users`, NO desde `clientes.operadores`:
                  que coincidan es plausible, no es un hecho. Acá se mide.
  6. MAGNITUD   · El 50% de Mesa vs el arancel de mercado del MISMO operador y
                  MISMO período. Es lo que dice si esto mueve la aguja o es ruido
                  (y si algún ranking del tablero se da vuelta).

El predicado de arancel se IMPORTA de `comercial_sql`, no se copia: si mañana
cambia, este diag cambia con él.

Uso:
    python -m scripts.diag_produccion_operador
    python -m scripts.diag_produccion_operador --top 40
"""
from __future__ import annotations

import argparse

from api.services.comercial_sql import _arancel_where
from api.services.mesa_dinero import OBSERVACION_MESA
from core.postgres import get_job_pool


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _f(x) -> float:
    return float(x or 0)


def _m(x) -> str:
    """Monto en ARS, sin decimales, con separador de miles."""
    return f"{_f(x):,.0f}".replace(",", ".")


def _t(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


# ── 1. CATÁLOGO DE OPERADORES ────────────────────────────────────────────────
def catalogo_operadores() -> None:
    _t("1. CATÁLOGO clientes.operadores — ¿el NOMBRE sirve como clave?")
    r = _q("SELECT count(*) AS n, "
           "count(*) FILTER (WHERE nombre IS NULL OR btrim(nombre) = '') AS sin_nombre "
           "FROM clientes.operadores")[0]
    print(f"  operadores            : {r['n']}")
    print(f"  sin nombre (NULL/'')  : {r['sin_nombre']}"
          f"{'   ← no pueden aparecer en OBS' if r['sin_nombre'] else ''}")

    dup = _q("SELECT upper(btrim(nombre)) AS nom, count(*) AS n, "
             "       string_agg(email, ', ' ORDER BY email) AS emails "
             "FROM clientes.operadores "
             "WHERE nombre IS NOT NULL AND btrim(nombre) <> '' "
             "GROUP BY 1 HAVING count(*) > 1 ORDER BY 2 DESC, 1")
    if dup:
        print(f"\n  ⚠️  NOMBRES REPETIDOS: {len(dup)} — unir por nombre es AMBIGUO acá")
        for d in dup:
            print(f"      {d['nom']:<34} {d['n']}  →  {d['emails']}")
    else:
        print("\n  ✅ sin nombres repetidos HOY (pero `nombre` no tiene UNIQUE: "
              "nada impide que mañana sí)")


# ── 2 + 3. MESA: qué hay en OBSERVACIÓN y si resuelve a un email ─────────────
# Una observación resuelve a N operadores. N=1 sirve; N=0 es huérfana; N>1 es
# ambigua — y la ambigua es la peligrosa: elegir cualquiera da un total correcto
# con la plata en el operador equivocado.
_MATCH = """
SELECT o.observacion,
       count(*)              AS filas,
       SUM(o.resultado) / 2  AS mitad,
       (SELECT count(*) FROM clientes.operadores c
         WHERE upper(btrim(c.nombre)) = upper(btrim(o.observacion))) AS n_match
FROM operaciones.mesa_dinero o
WHERE o.observacion IS NOT NULL
  AND btrim(o.observacion) <> ''
  AND btrim(o.observacion) <> %(mesa)s
GROUP BY o.observacion
ORDER BY 3 DESC
"""


def mesa_observaciones() -> list[dict]:
    _t("2. MESA DE DINERO — qué hay cargado en `observacion`")
    r = _q("SELECT count(*) AS n, "
           "count(*) FILTER (WHERE observacion IS NULL OR btrim(observacion) = '') AS vacia, "
           "count(*) FILTER (WHERE btrim(observacion) = %(mesa)s) AS mesa, "
           "min(fecha) AS desde, max(fecha) AS hasta "
           "FROM operaciones.mesa_dinero", {"mesa": OBSERVACION_MESA})[0]
    otras = r["n"] - r["vacia"] - r["mesa"]
    print(f"  filas totales         : {r['n']}   (de {r['desde']} a {r['hasta']})")
    print(f"  observación vacía     : {r['vacia']}   ← sin operador, no se imputa a nadie")
    print(f"  observación '{OBSERVACION_MESA}'      : {r['mesa']}   ← es de la Mesa, no de un operador")
    print(f"  apuntan a un operador : {otras}   ← ESTAS son las que hay que atar")

    _t("3. MATCH observación (nombre) → clientes.operadores (email)")
    filas = _q(_MATCH, {"mesa": OBSERVACION_MESA})
    if not filas:
        print("  (no hay ninguna observación que apunte a un operador)")
        return filas
    print(f"  {'OBSERVACIÓN':<34}{'FILAS':>7}{'50% ARS':>18}  MATCH")
    for f in filas:
        n = f["n_match"]
        marca = "✅ 1" if n == 1 else (f"❌ {n} (HUÉRFANA)" if n == 0 else f"⚠️  {n} (AMBIGUA)")
        print(f"  {(f['observacion'] or '')[:33]:<34}{f['filas']:>7}{_m(f['mitad']):>18}  {marca}")
    return filas


# ── 4. PLATA EN JUEGO ────────────────────────────────────────────────────────
def plata_en_juego(filas: list[dict]) -> None:
    _t("4. PLATA EN JUEGO — el 50% que pasaría a sumarle al operador")
    por_anio = _q(
        "SELECT EXTRACT(YEAR FROM fecha)::int AS anio, count(*) AS filas, "
        "       SUM(resultado) / 2 AS mitad "
        "FROM operaciones.mesa_dinero "
        "WHERE observacion IS NOT NULL AND btrim(observacion) <> '' "
        "  AND btrim(observacion) <> %(mesa)s "
        "GROUP BY 1 ORDER BY 1", {"mesa": OBSERVACION_MESA})
    print(f"  {'AÑO':<8}{'FILAS':>8}{'50% ARS':>20}")
    for a in por_anio:
        print(f"  {a['anio']:<8}{a['filas']:>8}{_m(a['mitad']):>20}")

    ok = sum(_f(f["mitad"]) for f in filas if f["n_match"] == 1)
    huerf = sum(_f(f["mitad"]) for f in filas if f["n_match"] == 0)
    amb = sum(_f(f["mitad"]) for f in filas if f["n_match"] > 1)
    tot = ok + huerf + amb
    print(f"\n  se imputa OK (match 1) : {_m(ok):>20}"
          f"   {ok / tot * 100 if tot else 0:5.1f}%")
    print(f"  HUÉRFANA (match 0)     : {_m(huerf):>20}"
          f"   {huerf / tot * 100 if tot else 0:5.1f}%"
          f"{'   ← ⚠️ hay que decidir qué hacer' if huerf else ''}")
    print(f"  AMBIGUA (match >1)     : {_m(amb):>20}"
          f"   {amb / tot * 100 if tot else 0:5.1f}%"
          f"{'   ← ⚠️ NO codear hasta resolver' if amb else ''}")


# ── 5. ALLOWLIST DE MANAGER → MESA vs CATÁLOGO DE OPERADORES ─────────────────
def allowlist_vs_operadores() -> None:
    _t("5. ¿El selector de OBS puede salir de Manager → MESA (lectores_resultados)?")
    print("  La idea: que OBS ofrezca a los operadores que tienen acceso a la tab")
    print("  RESULTADOS. Eso ata por EMAIL y resuelve todo — PERO esa allowlist se")
    print("  llena desde manager.manager_users, no desde clientes.operadores.\n")
    r = _q("SELECT count(*) AS n, "
           "count(*) FILTER (WHERE EXISTS "
           "  (SELECT 1 FROM clientes.operadores c WHERE c.email = l.email)) AS son_op "
           "FROM operaciones.mesa_dinero_lectores_resultados l")[0]
    print(f"  emails en la allowlist        : {r['n']}")
    print(f"  que SON operadores            : {r['son_op']}")
    print(f"  que NO son operadores         : {r['n'] - r['son_op']}"
          f"{'   ← no tendrían nombre ni cuentas' if r['n'] != r['son_op'] else ''}")

    faltan = _q(
        "SELECT DISTINCT o.observacion FROM operaciones.mesa_dinero o "
        "WHERE o.observacion IS NOT NULL AND btrim(o.observacion) <> '' "
        "  AND btrim(o.observacion) <> %(mesa)s "
        "  AND NOT EXISTS (SELECT 1 FROM operaciones.mesa_dinero_lectores_resultados l "
        "                  JOIN clientes.operadores c ON c.email = l.email "
        "                  WHERE upper(btrim(c.nombre)) = upper(btrim(o.observacion))) "
        "ORDER BY 1", {"mesa": OBSERVACION_MESA})
    if faltan:
        print(f"\n  ⚠️  {len(faltan)} observación(es) YA CARGADAS cuyo operador NO está en la")
        print("      allowlist. Si el selector sale solo de ahí, estas dejan de ser")
        print("      elegibles (lo viejo se sigue viendo, pero no se puede recargar):")
        for f in faltan:
            print(f"      · {f['observacion']}")
    else:
        print("\n  ✅ toda observación ya cargada tiene a su operador en la allowlist")


# ── 6. MAGNITUD: Mesa vs arancel de mercado, mismo operador y período ────────
_MAGNITUD = f"""
WITH rango AS (
    SELECT min(fecha) AS d, max(fecha) AS h FROM operaciones.mesa_dinero
),
mesa AS (
    SELECT upper(btrim(o.observacion)) AS nom, SUM(o.resultado) / 2 AS mitad
    FROM operaciones.mesa_dinero o
    WHERE o.observacion IS NOT NULL AND btrim(o.observacion) <> ''
      AND btrim(o.observacion) <> %(mesa)s
    GROUP BY 1
),
merc AS (
    SELECT upper(btrim(c2.nombre)) AS nom, SUM(op.arancel) AS arancel
    FROM operaciones.operaciones op
    JOIN clientes.comitentes cm ON cm.id_cuenta = op.id_cuenta
    JOIN clientes.operadores c2 ON c2.email = cm.operador_email
    WHERE {_arancel_where('op')} AND op.anulado_en IS NULL
      AND op.concertacion >= (SELECT d FROM rango)
      AND op.concertacion <= (SELECT h FROM rango)
    GROUP BY 1
)
SELECT COALESCE(mesa.nom, merc.nom) AS nom,
       COALESCE(merc.arancel, 0)    AS arancel,
       COALESCE(mesa.mitad, 0)      AS mitad
FROM mesa FULL OUTER JOIN merc ON merc.nom = mesa.nom
WHERE COALESCE(mesa.mitad, 0) <> 0
ORDER BY COALESCE(merc.arancel, 0) + COALESCE(mesa.mitad, 0) DESC
LIMIT %(top)s
"""


def magnitud(top: int) -> None:
    _t("6. MAGNITUD — ¿mueve la aguja? (mismo período que tiene cargado Mesa)")
    print("  Compara, por operador: el ARANCEL de mercado que ya se le cuenta contra")
    print("  el 50% de intermediación que pasaría a sumarle. Si el % es grande, el")
    print("  ranking del tablero se va a mover y la jefatura tiene que saberlo.\n")
    filas = _q(_MAGNITUD, {"mesa": OBSERVACION_MESA, "top": top})
    if not filas:
        print("  (sin datos para comparar)")
        return
    print(f"  {'OPERADOR':<30}{'ARANCEL MERC':>18}{'50% MESA':>16}{'MESA/TOTAL':>12}")
    for f in filas:
        a, m = _f(f["arancel"]), _f(f["mitad"])
        tot = a + m
        print(f"  {(f['nom'] or '')[:29]:<30}{_m(a):>18}{_m(m):>16}"
              f"{(m / tot * 100 if tot else 0):>11.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=25, help="operadores en la tabla de magnitud")
    args = ap.parse_args()

    catalogo_operadores()
    filas = mesa_observaciones()
    if filas:
        plata_en_juego(filas)
    allowlist_vs_operadores()
    magnitud(args.top)
    print("\n" + "=" * 78)
    print("READ-ONLY: este script no escribió nada.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
