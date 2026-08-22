"""scripts/diag_ahora.py — imprime EXACTAMENTE lo que muestra la tab AHORA.

Read-only. Llama a la MISMA función que dibuja la pantalla
(`av_agent_centinela.estado()`) y vuelca sus bloques en el mismo orden:

    latido (el punto verde)  →  ROTO AHORA  →  VOLVIÓ  →  APARECIÓ HOY
    →  SE ARREGLÓ (el plegado)  →  el veredicto «hoy no pasó nada»

Como usa la misma fuente, lo que imprime NO puede diferir de la pantalla: si
acá sale otra cosa que en el navegador, el problema es del front (o de un
deploy desparejo), y eso también es un dato.

Uso (Droplet, raíz):
    python -m scripts.diag_ahora           # legible, como la pantalla
    python -m scripts.diag_ahora --json    # el payload crudo completo
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

_ART = ZoneInfo("America/Argentina/Buenos_Aires")


def _hora(iso: str | None) -> str:
    """La misma marca que la fila de la pantalla: HH:MM en hora argentina."""
    if not iso:
        return "--:--"
    try:
        return datetime.fromisoformat(iso).astimezone(_ART).strftime("%d/%m %H:%M")
    except ValueError:
        return str(iso)[:16]


def _bloque(titulo: str, filas: list[dict]) -> None:
    print(f"\n── {titulo} ({len(filas)}) " + "─" * max(0, 58 - len(titulo)))
    if not filas:
        print("  (vacío — este bloque no se dibuja)")
        return
    for f in filas:
        # La MISMA coalescencia de hora que usa la fila del front.
        cuando = _hora(f.get("vuelto_at") or f.get("resuelto_at")
                       or f.get("abierto_at"))
        print(f"  [{f.get('severidad', '?'):5}] {cuando}  "
              f"{f.get('nombre') or f.get('sujeto')}  ·  {f.get('regla')}")
        if f.get("motivo"):
            print(f"          {str(f['motivo'])[:160]}")
        if f.get("detalle"):
            print(f"          detalle: {str(f['detalle'])[:200]}")
        if f.get("muestra"):
            print(f"          log: {str(f['muestra']).splitlines()[0][:160]}")


def main() -> None:
    from api.services import av_agent_centinela

    est = av_agent_centinela.estado()
    if "--json" in sys.argv:
        print(json.dumps(est, ensure_ascii=False, indent=2, default=str))
        return

    lat = est.get("latido") or {}
    print("═" * 68)
    print("AHORA — lo mismo que dibuja la tab, desde la misma función")
    print("═" * 68)
    vivo = "VIVO ✔" if est.get("vivo") else "APAGADO ✘ (el silencio de abajo NO vale)"
    print(f"latido: {vivo}"
          + (f" · última revisión hace {lat.get('hace_s')}s"
             f" · cadencia {lat.get('cadencia_s')}s"
             f" · ciclo #{lat.get('ciclo')}"
             f" · en_rueda={lat.get('en_rueda')}" if lat else " · sin latido"))
    if lat.get("error"):
        print(f"último error del ciclo: {lat['error']}")
    if est.get("habil") is not None:
        print("hoy es día " + ("HÁBIL" if est["habil"] else
              "NO HÁBIL — mercado cerrado: motores apagados a propósito, "
              "nada de rueda se re-evalúa; solo vale SALUD y el detector "
              "de actividad indebida"))

    hoy = est.get("hoy")
    if not hoy:
        print("\n⚠ el backend no manda el corte del día (deploy desparejo): "
              "la tab mostraría el aviso rojo, no «nada».")
        return

    print(f"corte del día (ART): desde {_hora(hoy.get('desde'))}"
          f" · novedades = {hoy.get('novedades')}")

    _bloque("ROTO AHORA (no es del día: está roto en este momento)",
            hoy.get("roto") or [])
    _bloque("VOLVIÓ (se había arreglado y volvió)", hoy.get("volvio") or [])
    _bloque("APARECIÓ HOY (no estaba ayer)", hoy.get("aparecio") or [])
    _bloque("SE ARREGLÓ (plegado en la pantalla)", hoy.get("se_arreglo") or [])

    nada = (hoy.get("novedades") == 0 and not (hoy.get("se_arreglo") or [])
            and not (hoy.get("roto") or []))
    if nada:
        print("\nveredicto de la tab: «hoy no pasó nada nuevo»"
              + ("" if est.get("vivo") else " — PERO el agente está apagado"))

    # El contexto que la tab no dibuja pero explica sus números.
    print(f"\n(backlog acumulado del monitor: {len(est.get('abiertos') or [])} "
          f"abiertos, {est.get('sin_ver')} sin ver — eso vive en "
          f"ENCONTRÓ → VIGILANCIA, no acá)")


if __name__ == "__main__":
    main()
