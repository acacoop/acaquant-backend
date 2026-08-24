"""scripts/diag_agente_vista.py — ¿POR QUÉ DESAPARECIÓ EL BOTÓN DEL AGENTE?

Read-only. El botón de la barra se esconde solo con UNA condición
(`av-agent-modal.tsx`): que la llamada a `/api/ia/av-agent/vista` **falle**.

    if (!data && errorVista) return null;   // «un botón que abre un modal
                                            //  vacío es peor que no tenerlo»

Así que la pregunta es una sola: **qué contesta esa vista.** Este diag llama a
las MISMAS funciones que el router, en el mismo orden, y si algo levanta imprime
el traceback completo en vez de tragárselo — que es justo lo que hace la API
para no tumbar la barra, y por eso el error no se ve en ningún lado.

    python -m scripts.diag_agente_vista
"""
from __future__ import annotations

import time
import traceback


def _probar(nombre: str, fn) -> bool:
    t0 = time.perf_counter()
    try:
        r = fn()
    except Exception:
        print(f"\n  ✖ {nombre} LEVANTA — esto es lo que esconde el botón:\n")
        traceback.print_exc()
        return False
    ms = int((time.perf_counter() - t0) * 1000)
    if isinstance(r, dict):
        print(f"  ✔ {nombre}  ({ms} ms)")
        for k in ("ok", "vivo", "miro", "error"):
            if k in r:
                print(f"      {k} = {r[k]!r}")
        for k, v in r.items():
            if isinstance(v, list):
                print(f"      {k}: {len(v)} filas")
            elif isinstance(v, dict) and k == "hoy":
                print("      hoy: " + " · ".join(
                    f"{a}={len(b)}" for a, b in v.items() if isinstance(b, list)))
    else:
        print(f"  ✔ {nombre}  ({ms} ms) → {type(r).__name__}")
    return True


def main() -> int:
    print("DIAG — por qué el botón del AV AGENT no aparece\n")
    print("Las DOS llamadas que hace la barra:")
    from api.services import av_agent_centinela, av_agent_vista

    ok_v = _probar("vista()   → el modal y el botón", av_agent_vista.vista)
    ok_c = _probar("estado()  → el círculo verde", av_agent_centinela.estado)

    print("\nLo que hay en la memoria del agente ahora mismo:")
    from core.postgres import get_pool
    tablas = ("agente.av_agent_items", "agente.av_agent_hallazgos",
              "agente.av_agent_evaluado", "agente.av_agent_latido",
              "agente.av_agent_avisos", "agente.av_agent_preguntas",
              "manager.controles_datos")
    with get_pool().connection() as conn, conn.cursor() as cur:
        for t in tablas:
            try:
                cur.execute(f"SELECT count(*) FROM {t}")
                print(f"  {int(cur.fetchone()[0]):>7,}  {t}")
            except Exception as e:
                print(f"       ✖  {t} — {str(e)[:70]}")

    print("\nCómo se lee esto:")
    if ok_v and ok_c:
        print("  Las dos andan. Entonces el botón NO se esconde por el backend:")
        print("  · o el front todavía no deployó (mirá Deployments en Vercel —")
        print("    CLAUDE.md: si algo no aparece, Vercel ANTES que el código);")
        print("  · o la llamada muere en el proxy/permiso: probá la URL desde el")
        print("    navegador logueado y mirá el status en la pestaña Network.")
    else:
        print("  Ahí arriba está el traceback: ESA es la causa. La API se lo")
        print("  traga para no tumbar la barra, y por eso no aparece en ningún log.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
