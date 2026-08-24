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
        # ⚠️ **LA FECHA YA VIENE ELEGIDA POR EL BACKEND** (`cuando` + el verbo).
        # Acá vivía un `coalesce` fijo que nunca miraba `ultimo_at`, así que una
        # fila re-confirmada hace dos minutos imprimía el día que NACIÓ y
        # parecía muerta. Cada bloque pregunta otra cosa y hay una sola fecha
        # que la contesta — y quién la elige es el backend, no dos pantallas.
        cuando = _hora(f.get("cuando") or f.get("abierto_at"))
        verbo = f.get("cuando_dice") or "desde"
        print(f"  [{f.get('severidad', '?'):5}] {verbo} {cuando}  "
              f"{f.get('nombre') or f.get('sujeto')}  ·  {f.get('regla')}")
        if f.get("motivo"):
            print(f"          {str(f['motivo'])[:160]}")
        if f.get("detalle"):
            print(f"          detalle: {str(f['detalle'])[:200]}")
        if f.get("muestra"):
            print(f"          log: {str(f['muestra']).splitlines()[0][:160]}")


_ROTULOS = {
    "roto": "ROTO AHORA (confirmado recién: está roto en este momento)",
    "sin_confirmar": ("NO LO PUDE VERIFICAR (sigue abierto, pero su "
                      "detector no da señales)"),
    "volvio": "VOLVIÓ (se había arreglado y volvió)",
    "aparecio": "APARECIÓ HOY (no estaba ayer)",
    "se_arreglo": "SE ARREGLÓ (plegado en la pantalla)",
}

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

    # ⚠️ **LOS BLOQUES SE DERIVAN DEL PAYLOAD, NO SE LISTAN A MANO.**
    #
    # Este diag existe para que lo que sale acá NO pueda diferir de la pantalla.
    # Con la lista escrita a mano eso duraba hasta el próximo bloque nuevo: el
    # 2026-08-24 se agregó `sin_confirmar` (los hallazgos que nadie pudo
    # verificar) y este script siguió imprimiendo cuatro bloques como si nada —
    # o sea que mostraba «ROTO AHORA: 0» sin decir que había cuatro motores
    # colgados al lado. El silencio leyéndose como verde, otra vez, ahora en el
    # diag que existe para evitarlo.
    #
    # `_ROTULOS` pone el título legible; un bloque que aparezca en el payload y
    # no esté acá igual se imprime con su clave cruda, que es feo y visible —
    # muy distinto de no imprimirse.
    listas = {k: v for k, v in hoy.items() if isinstance(v, list)}
    # Primero los que tienen orden declarado; atrás, cualquiera que aparezca.
    for k in list(_ROTULOS) + [k for k in listas if k not in _ROTULOS]:
        if k not in listas:
            continue
        _bloque(_ROTULOS.get(k, f"{k.upper()} (bloque NUEVO, sin rótulo acá)"),
                listas[k])

    # Y lo que no se pudo verificar se explica aparte: no es una novedad, es una
    # advertencia sobre el AGENTE — «esto no lo estoy mirando».
    sc = hoy.get("sin_confirmar") or []
    if sc:
        print(f"\n⚠ {len(sc)} hallazgo(s) que NADIE pudo confirmar hace rato. "
              f"No es que estén bien ni que sigan mal: es que el detector no "
              f"está corriendo. Por tipo:")
        por_tipo: dict[str, int] = {}
        for f in sc:
            por_tipo[f.get("tipo") or "?"] = por_tipo.get(f.get("tipo") or "?", 0) + 1
        for tipo, n in sorted(por_tipo.items(), key=lambda x: -x[1]):
            edad = max((f.get("sin_confirmar_s") or 0) for f in sc
                       if f.get("tipo") == tipo)
            cuanto = ("nunca se confirmó" if not edad else
                      f"hace {edad // 3600} h" if edad >= 3600 else
                      f"hace {edad // 60} min")
            print(f"    {tipo:20} {n:3}  · {cuanto}")
        print("  → para saber POR QUÉ dejó de correr: "
              "python -m scripts.diag_agente_frescura")

    nada = (hoy.get("novedades") == 0 and not (hoy.get("se_arreglo") or [])
            and not (hoy.get("roto") or []) and not sc)
    if nada:
        print("\nveredicto de la tab: «hoy no pasó nada nuevo»"
              + ("" if est.get("vivo") else " — PERO el agente está apagado"))

    # El contexto que la tab no dibuja pero explica sus números.
    print(f"\n(backlog acumulado del monitor: {len(est.get('abiertos') or [])} "
          f"abiertos, {est.get('sin_ver')} sin ver — eso vive en "
          f"ENCONTRÓ → VIGILANCIA, no acá)")


if __name__ == "__main__":
    main()
