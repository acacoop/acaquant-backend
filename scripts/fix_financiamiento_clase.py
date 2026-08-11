"""fix_financiamiento_clase.py — corrige el HD/DL escrito con el umbral VIEJO.

QUÉ PASÓ
--------
La regla `financiamiento_clase` (jobs/assets_autofill.py) nació con el umbral en
**5.000** y clasificó como DL a cientos de pagarés que son HD: papeles de 200k a
600k nominales, que a esa escala rinden 6-7% anual (tasa de dólar). El umbral
correcto es **5.000.000** — ver el comentario de `_UMBRAL_HD`.

POR QUÉ HACE FALTA ESTE SCRIPT Y NO ALCANZA CON RE-CORRER EL JOB
----------------------------------------------------------------
El job tiene un invariante que NO se toca: **nunca pisa un valor ya cargado**.
Eso es exactamente lo que lo hace seguro (la máquina propone, el humano corrige
en Manager y el job no vuelve a opinar) — pero también significa que un valor
que el propio job escribió mal se queda ahí para siempre. Corregirlo es una
decisión explícita y puntual, y por eso vive acá y no en el job.

CÓMO EVITA PISAR TRABAJO HUMANO (dos guardas, hay que cumplir LAS DOS)
----------------------------------------------------------------------
1. `actualizado_por = 'job:assets_autofill'` — el último que tocó la fila fue la
   máquina. Si un humano la editó, no se toca.
2. La clase actual es EXACTAMENTE la que daba el umbral viejo y DISTINTA de la
   que da el nuevo. Así solo se corrige lo que lleva la huella del bug; un
   asset donde los dos umbrales coinciden no se toca aunque esté mal.

Lo que queda afuera por la guarda 1 se REPORTA (no se corrige en silencio): son
los que hay que mirar a mano en Manager → ASSETS.

Es idempotente: correrlo dos veces no hace nada la segunda (después de la
primera pasada ya ninguna fila cumple la guarda 2).

Uso:
    python -m scripts.fix_financiamiento_clase              # DRY-RUN (default)
    python -m scripts.fix_financiamiento_clase --aplicar    # escribe

Cuando el tema cierre, este script se BORRA (REGLA #5).
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from core.postgres import get_pool
from jobs.assets_autofill import _RE_FINANCIAMIENTO, _UMBRAL_HD

# El umbral con el que se escribieron los valores malos. Queda hardcodeado
# porque es historia, no configuración: describe lo que pasó, no lo que debería.
_UMBRAL_VIEJO = 5_000.0
_ACTOR_JOB = "job:assets_autofill"
_ACTOR = "script:fix_financiamiento_clase"
_BATCH = 500


def _clase(nominal: float, umbral: float) -> str:
    return "HD" if nominal <= umbral else "DL"


def _candidatos() -> tuple[list[tuple[str, str, str, float]], list[tuple[str, str, float]]]:
    """(a_corregir, bloqueados_por_humano).

    `a_corregir` = (unidad, clase_vieja, clase_nueva, nominal).
    `bloqueados`  = (unidad, clase_actual, nominal) — los tocó un humano.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT a.unidad, a.clase_activo, a.actualizado_por, sum(t.cantidad) "
            "FROM portafolio.assets a "
            "JOIN portafolio.tenencia t ON t.unidad = a.unidad "
            "WHERE t.fecha = (SELECT max(fecha) FROM portafolio.tenencia) "
            "  AND t.cantidad IS NOT NULL "
            "GROUP BY a.unidad, a.clase_activo, a.actualizado_por")
        rows = cur.fetchall()

    corregir: list[tuple[str, str, str, float]] = []
    bloqueados: list[tuple[str, str, float]] = []
    for unidad, clase, actor, nominal in rows:
        # Solo financiamiento: la firma de la unidad es el criterio, igual que en el job.
        if not _RE_FINANCIAMIENTO.match((unidad or "").strip()):
            continue
        clase = (clase or "").strip().upper()
        if not clase or nominal is None:
            continue                     # sin clase → lo completa el job, no este script
        n = float(nominal)
        vieja, nueva = _clase(n, _UMBRAL_VIEJO), _clase(n, _UMBRAL_HD)
        # Guarda 2: solo lo que lleva la huella del umbral viejo.
        if clase != vieja or vieja == nueva:
            continue
        # Guarda 1: el último que escribió tiene que haber sido el job.
        if (actor or "").strip() != _ACTOR_JOB:
            bloqueados.append((unidad, clase, n))
            continue
        corregir.append((unidad, vieja, nueva, n))
    return corregir, bloqueados


def _aplicar(corregir: list[tuple[str, str, str, float]]) -> int:
    ts = datetime.now(UTC)
    n = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for i in range(0, len(corregir), _BATCH):
            lote = [{"clase": nueva, "unidad": u, "actor": _ACTOR, "ts": ts}
                    for u, _vieja, nueva, _nom in corregir[i:i + _BATCH]]
            cur.executemany(
                "UPDATE portafolio.assets SET clase_activo = %(clase)s, "
                "actualizado_por = %(actor)s, actualizado_at = %(ts)s "
                "WHERE unidad = %(unidad)s", lote)
            n += len(lote)
        conn.commit()
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Corrige el HD/DL del umbral viejo.")
    ap.add_argument("--aplicar", action="store_true",
                    help="escribe (sin esto es dry-run y no toca nada)")
    args = ap.parse_args()

    print(f"umbral viejo {_UMBRAL_VIEJO:,.0f} → nuevo {_UMBRAL_HD:,.0f}\n")
    corregir, bloqueados = _candidatos()

    if not corregir and not bloqueados:
        print("nada que corregir (¿ya se corrió?).")
        return

    cambios: dict[str, int] = {}
    for _u, vieja, nueva, _n in corregir:
        cambios[f"{vieja} → {nueva}"] = cambios.get(f"{vieja} → {nueva}", 0) + 1
    print(f"A CORREGIR: {len(corregir)} asset(s)")
    for k, v in sorted(cambios.items()):
        print(f"  {k}: {v}")
    for u, vieja, nueva, n in corregir[:15]:
        print(f"    {n:>18,.2f}  {vieja} → {nueva}   {u[:52]}")
    if len(corregir) > 15:
        print(f"    … +{len(corregir) - 15} más")

    if bloqueados:
        print(f"\n⚠ {len(bloqueados)} asset(s) NO se tocan: los editó un humano "
              f"(actualizado_por ≠ {_ACTOR_JOB}).")
        print("  Si alguno quedó mal, corregirlo en Manager → ASSETS:")
        for u, clase, n in bloqueados[:15]:
            print(f"    {n:>18,.2f}  clase={clase}   {u[:52]}")

    if not args.aplicar:
        print("\nDRY-RUN: no se escribió nada. Para aplicar: "
              "python -m scripts.fix_financiamiento_clase --aplicar")
        return
    print(f"\n✅ {_aplicar(corregir)} asset(s) corregidos "
          f"(la API los relee en ≤5 min, TTL assets_sql).")


if __name__ == "__main__":
    main()
