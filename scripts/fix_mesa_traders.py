"""Fix one-shot: nombres completos de traders en Mesa de Dinero.

La planilla original (y el backfill) usaba nombres de pila; en la app ya
conviven con los nombres completos → unificar a nombre completo:

    Mariano   → Mariano Erretegui
    Valentina → Valentina Amarilla
    Javier    → Javier Curzel
    (Compartido y cualquier otro quedan como están)

Actualiza `operaciones.mesa_dinero.trader`, asegura los nombres completos en
el catálogo `mesa_dinero_traders` y borra los nombres cortos del catálogo.
Idempotente: re-correrlo no cambia nada. Deja evento de auditoría.

Uso (Droplet): python -m scripts.fix_mesa_traders
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services.mesa_dinero import _audit
from core.postgres import get_pool

MAP = {
    "Mariano": "Mariano Erretegui",
    "Valentina": "Valentina Amarilla",
    "Javier": "Javier Curzel",
}


def main() -> None:
    cambios = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        for corto, completo in MAP.items():
            cur.execute(
                "UPDATE operaciones.mesa_dinero SET trader = %s WHERE trader = %s",
                (completo, corto),
            )
            n_ops = cur.rowcount
            cur.execute(
                "INSERT INTO operaciones.mesa_dinero_traders (nombre, creado_por, creado_at) "
                "VALUES (%s, %s, %s) ON CONFLICT (nombre) DO NOTHING",
                (completo, "fix_mesa_traders", datetime.now(UTC)),
            )
            cur.execute(
                "DELETE FROM operaciones.mesa_dinero_traders WHERE nombre = %s", (corto,)
            )
            cambios[corto] = n_ops
            print(f"{corto!r} → {completo!r}: {n_ops} ops actualizadas")
        conn.commit()

        cur.execute("SELECT nombre FROM operaciones.mesa_dinero_traders ORDER BY nombre")
        print("Catálogo final:", ", ".join(r[0] for r in cur.fetchall()))

    _audit("fix_mesa_traders", "rename_traders", "mesa_dinero", {"map": MAP, "ops": cambios})


if __name__ == "__main__":
    main()
