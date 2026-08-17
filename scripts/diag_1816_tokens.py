"""El presupuesto de 1816: tokens, créditos y peticiones por segundo.

**Los tres límites del plan no son el mismo** y el que nos rompió no era el que
mirábamos (verificado en el panel, 2026-08-17):

    Créditos diarios       3.863 / 100.000     ← sobra, nunca fue el problema
    Máx. peticiones/seg    1
    **Máx. tokens por día    50**              ← el «auth HTTP 429»

Un token dura **24 h**, así que UNO alcanza para todo el día. Hasta el 2026-08-17
vivía en un dict de módulo —memoria de cada proceso— y los procesos que tocan
1816 son muchos: `jobs/tamar_1816` corre 15 veces por día, `mercado_1816_series`
una, `api.service` pide uno nuevo **en cada restart** (o sea en cada deploy), más
cada corrida manual del agente. Cada uno quemaba un token de los 50.

Este diag responde las dos preguntas que hay que poder contestar antes de apretar
nada: **cuántos logins van hoy** y **quién los está gastando**.

    python -m scripts.diag_1816_tokens
"""

from __future__ import annotations

from core.mercado_1816 import _LOGINS_MAX_DIA, estado_token
from core.postgres import get_pool


def main() -> int:
    est = estado_token()
    print("\n1816 — PRESUPUESTO DE TOKENS")
    print("=" * 60)
    print(f"  logins de hoy      {est['logins_hoy']} / {_LOGINS_MAX_DIA} "
          f"(tope del plan: 50)")
    print(f"  quedan             {est['restantes']}")
    print(f"  token vigente      {'sí' if est['token_vigente'] else 'NO'}"
          + (f"  ·  expira en {est['expira_en_s'] // 3600}h "
             f"{est['expira_en_s'] % 3600 // 60}m" if est["token_vigente"] else ""))

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT obtenido_por, obtenido_at, dia, llamadas_at "
                        "FROM manager.tokens_externos WHERE proveedor = '1816'")
            r = cur.fetchone()
    except Exception as e:
        print(f"\n  ⚠ no se pudo leer manager.tokens_externos ({e})")
        print("    ¿corrió `python -m scripts.apply_schema`?")
        return 1

    if not r:
        print("\n  Todavía no hay fila: nadie pidió un token desde que existe la "
              "tabla.\n  El primero que la necesite la crea.")
        return 0
    print(f"\n  último login       {r[1]}  ·  lo pidió: {r[0]}")
    print(f"  día del contador   {r[2]}")
    print(f"  última petición    {r[3]}")
    print("\n  Si el número de logins crece rápido, el sospechoso es un proceso "
          "que\n  arranca seguido (deploys) o un job en loop: `obtenido_por` dice "
          "cuál.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
