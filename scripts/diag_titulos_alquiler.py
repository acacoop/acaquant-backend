"""diag_titulos_alquiler.py — READ-ONLY: por qué la vista Títulos en Alquiler sale vacía.

El diag anterior mostró que HAY posiciones (44 al 08/07). Si la vista igual sale
vacía, el endpoint se está cayendo. Este diag reproduce lo que hace el endpoint y
lo aísla en partes para ver cuál falla:
  1. ¿Existe la tabla portafolio.alquiler? (la crea el propio código on-demand).
  2. ¿Se puede crear/alterar (permiso)? → prueba _ensure_alquiler_table.
  3. Llama al service titulos_en_alquiler() y muestra cuántas posiciones trae, o
     el error EXACTO si se cae.

Corré:  python -m scripts.diag_titulos_alquiler
"""
from __future__ import annotations

import traceback

from core.postgres import get_pool


def main() -> None:
    # 1. ¿Existe la tabla?
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('portafolio.alquiler')")
        existe = cur.fetchone()[0]
    print(f"1. tabla portafolio.alquiler existe: {existe is not None}  ({existe})")

    # 2. ¿Se puede crear/alterar? (aislado, con su propio error).
    from api.services.tenencia_hd import _ensure_alquiler_table
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            _ensure_alquiler_table(cur)
            conn.commit()
        print("2. _ensure_alquiler_table (CREATE/ALTER): OK")
    except Exception as e:
        print(f"2. _ensure_alquiler_table FALLÓ: {type(e).__name__}: {e}")
        print("   → ESTA es la causa (permiso de CREATE/ALTER sobre el schema portafolio).")

    # 3. El service completo.
    from api.services.tenencia_hd import titulos_en_alquiler
    try:
        r = titulos_en_alquiler()
        n = len(r.get("posiciones", []))
        print(f"3. titulos_en_alquiler(): OK → {n} posiciones · última fecha {r.get('ultima_fecha')}")
        if n == 0:
            print("   (0 posiciones → el problema NO es el endpoint; es la query de posiciones)")
        else:
            print("   → el endpoint devuelve datos. Si la vista sigue vacía, es del lado del")
            print("     frontend/proxy (revisar /api/back-office/tenencia-hd/en-alquiler en el navegador).")
    except Exception:
        print("3. titulos_en_alquiler() SE CAYÓ:")
        print(traceback.format_exc())


if __name__ == "__main__":
    main()
