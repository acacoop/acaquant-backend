"""scripts/diag_logs_motores.py — QUÉ VIENEN DICIENDO LOS MOTORES.

Doc madre: **`docs/AGENT.md`** §0.ac.

Pedido del user (2026-08-19): *«es fundamental que el agente tenga presente los
logs de los motores constantemente»*.

**Esto todavía NO es el detector, y es a propósito.** Para que el agente cante un
problema hay que decidir qué es un problema: cuántas repeticiones, de qué nivel,
en cuánto tiempo. Ninguno de esos números se puede elegir sin haber mirado nunca
los logs de producción (REGLA #2) — y un detector mal calibrado grita todos los
días hasta que alguien lo silencia, que es peor que no tenerlo.

Así que primero se MIDE. Esto imprime, para las últimas N horas:

  1. cuántas líneas dejó cada motor y de qué nivel
  2. los patrones que más se repiten, con su ventana de tiempo
  3. lo grave (error o peor) aparte, aunque haya salido una sola vez

Con eso en la mano se eligen los umbrales del detector, en el próximo paso.

    python -m scripts.diag_logs_motores                 # últimas 24h
    python -m scripts.diag_logs_motores --horas 72
    python -m scripts.diag_logs_motores --todo          # también notice/info
    python -m scripts.diag_logs_motores --unidad motor_rofex

Es SOLO LECTURA: corre `journalctl` y no toca nada.
"""
from __future__ import annotations

import argparse
from datetime import datetime


def _cuando(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")


def _ventana(g: dict) -> str:
    """Cuánto duró la ráfaga. **Importa tanto como la cuenta**: 300 repeticiones
    en dos minutos es un motor peleando contra algo; las mismas 300 repartidas en
    un día son ruido de fondo, y no se responden igual."""
    seg = max(0.0, g["ultima"] - g["primera"])
    if g["veces"] < 2:
        return "una vez"
    if seg < 120:
        return f"{int(seg)}s"
    if seg < 7200:
        return f"{int(seg / 60)} min"
    return f"{seg / 3600:.1f} h"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horas", type=int, default=24)
    ap.add_argument("--unidad", default="", help="una sola (default: todos los motores)")
    ap.add_argument("--todo", action="store_true", help="incluir notice/info")
    ap.add_argument("--ver", type=int, default=0,
                    help="imprimir las últimas N líneas crudas, sin filtrar")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()

    from api.services import logs_sistema as ls
    from api.services.diagnostico_registry import unidades_motores

    unidades = [a.unidad] if a.unidad else sorted(unidades_motores())
    print(f"\n  {len(unidades)} unidad(es), últimas {a.horas} h, "
          f"nivel {'todo' if a.todo else 'warn o peor'}\n")

    # ── PASO 0: ¿ESCRIBEN ALGO? ─────────────────────────────────────────────
    # La primera corrida devolvió CERO en 24 h y la pregunta era cuál de dos
    # cosas: los motores están tranquilos, o el filtro no encuentra nada. Esta
    # muestra lo decide de una: si 400 líneas cubren tres minutos son
    # conversadores y el cero es del filtro; si cubren un día, están tranquilos.
    m = ls.leer(unidades, desde=f"-{a.horas}h", prioridad=7, lineas=400)
    if not m["disponible"]:
        print(f"  ⚠ NO PUDE LEER LOS LOGS: {m['motivo']}")
        print("    (no es «no hay errores» — es «no sé»)\n")
        return 1
    muestra = m["lineas"]
    if not muestra:
        print(f"  ⚠ Los motores NO ESCRIBIERON NADA en {a.horas} h, de ningún "
              "nivel.\n    Eso no es silencio sano: son 14 procesos que "
              "deberían contar lo que hacen.\n")
        return 0
    span = max(x["ts"] for x in muestra) - min(x["ts"] for x in muestra)
    con_nivel = sum(1 for x in muestra if x["lo_dice_el_texto"])
    print(f"  MUESTRA: las últimas {len(muestra)} líneas cubren "
          f"{_ventana({'veces': 2, 'primera': 0, 'ultima': span})}")
    print(f"    {con_nivel} de {len(muestra)} dicen su nivel en el texto")
    if con_nivel == 0:
        # Sin el nivel escrito no hay forma de encontrar un error, ni para el
        # agente ni para una persona. Ver `AGENT.md` §0.ac.
        print("    ⚠ NINGUNA lo dice: ese motor está corriendo con el formato "
              "viejo.\n      Se arregla al reiniciarlo (el deploy no toca "
              "motores).")
    print()

    r = ls.leer(unidades, desde=f"-{a.horas}h", prioridad=7) if a.todo \
        else ls.atencion(unidades, desde=f"-{a.horas}h")
    if not r["disponible"]:
        # **El silencio se lee igual que un verde**: si no se pudo leer hay que
        # decirlo, no devolver una lista vacía y que parezca que está todo bien.
        print(f"  ⚠ NO PUDE LEER LOS LOGS: {r['motivo']}")
        print("    (no es «no hay errores» — es «no sé»)\n")
        return 1

    lineas = r["lineas"]
    if not lineas:
        print(f"  Ningún motor escribió un ERROR ni un WARNING en {a.horas} h.")
        if con_nivel:
            print("  Y el nivel SÍ se está escribiendo, así que el cero es real.\n")
        else:
            print("  ⚠ Pero ninguna línea dice su nivel: este cero NO prueba "
                  "que no haya errores,\n    prueba que no se pueden "
                  "encontrar. Reiniciá los motores fuera de rueda.\n")
        return 0

    print(f"  {len(lineas)} línea(s)\n")

    # ── 1) el reparto por motor ──────────────────────────────────────────────
    print("  POR MOTOR")
    porun = ls.por_unidad(lineas)
    ancho = max(len(u) for u in porun)
    for unidad, c in sorted(porun.items(), key=lambda kv: -sum(kv[1].values())):
        detalle = " ".join(f"{n}:{v}" for n, v in c.most_common())
        print(f"    {unidad.ljust(ancho)}  {sum(c.values()):>6}   {detalle}")

    grupos = ls.agrupar(lineas)

    # ── 2) lo GRAVE, aunque haya pasado una sola vez ─────────────────────────
    graves = [g for g in grupos if g["peor"] <= 3]     # error, crit, alert, emerg
    print(f"\n  GRAVE (error o peor): {len(graves)} patrón(es)")
    if not graves:
        print("    nada")
    for g in graves[: a.top]:
        print(f"    [{g['nivel']}] {g['unidad']} ×{g['veces']} en {_ventana(g)}"
              f"  (última {_cuando(g['ultima'])})")
        print(f"        {g['muestra'].splitlines()[0][:150]}")

    # ── 3) lo que MÁS se repite ──────────────────────────────────────────────
    print(f"\n  LO QUE MÁS SE REPITE (top {a.top} de {len(grupos)} patrones)")
    for g in sorted(grupos, key=lambda x: -x["veces"])[: a.top]:
        print(f"    ×{g['veces']:<6} [{g['nivel']}] {g['unidad']} — en {_ventana(g)}")
        print(f"        {g['patron']}")

    if a.ver:
        # Para leerlas con los ojos cuando el resumen no alcanza.
        print(f"\n  ÚLTIMAS {a.ver} LÍNEAS (crudas)")
        for x in muestra[-a.ver:]:
            print(f"    {_cuando(x['ts'])} [{x['nivel']}] {x['unidad']}: "
                  f"{x['mensaje'].splitlines()[0][:140]}")

    print("\n  Con estos números se eligen los umbrales del detector.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
