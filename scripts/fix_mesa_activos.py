"""Fix one-shot: ACTIVO en mayúsculas en Mesa de Dinero.

Los registros existentes tienen el activo en mixed-case (tzxd6, Tzxd6, …).
El backend ya normaliza a mayúsculas en cada alta/edición — este script
empareja lo histórico:

    UPDATE operaciones.mesa_dinero SET activo = UPPER(activo)  (solo si cambia)

Idempotente: re-correrlo no cambia nada. Deja evento de auditoría.

Uso (Droplet): python -m scripts.fix_mesa_activos
"""
from __future__ import annotations

from api.services.mesa_dinero import _audit
from core.postgres import get_pool


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operaciones.mesa_dinero SET activo = UPPER(activo) "
            "WHERE activo IS NOT NULL AND activo <> UPPER(activo)"
        )
        n = cur.rowcount
        conn.commit()
    print(f"{n} ops actualizadas a mayúsculas")
    _audit("fix_mesa_activos", "uppercase_activos", "mesa_dinero", {"ops": n})


if __name__ == "__main__":
    main()
