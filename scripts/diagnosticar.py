"""scripts/diagnosticar.py — EL DIAGNÓSTICO del AV AGENT, desde la consola.

Corre las cinco etapas de `asistente/diagnostico.py` sobre UN hallazgo y
muestra la conclusión con su evidencia, o encola los pendientes como lo haría
el daemon, o lista qué diagnosticaría. Es cómo se prueba contra un aviso real
sin esperar al daemon, y cómo se relee lo que concluyó.

Uso (Droplet, raíz):
    python -m scripts.diagnosticar --hallazgo 123        # corre ya, sincrónico, imprime
    python -m scripts.diagnosticar --ver 123             # muestra el diagnóstico guardado
    python -m scripts.diagnosticar --pendientes          # qué encolaría el daemon ahora
    python -m scripts.diagnosticar --encolar             # encola (topes de config.py), aunque
                                                         # el automático del LAB esté apagado:
                                                         # una persona en la consola es MANUAL
"""
from __future__ import annotations

import argparse
import json
import uuid

from psycopg.rows import dict_row

from core.postgres import get_pool


def _imprimir(d: dict) -> None:
    print(f"\n  causa:   {d.get('causa')}")
    print(f"  acción:  {d.get('accion')} — {d.get('accion_detalle')}")
    print(f"  resumen: {d.get('resumen')}")
    for v in d.get("verificado") or []:
        print(f"    [{v.get('estado')}] {v.get('afirmacion')}  {v.get('cita') or ''}")
    for n in d.get("no_hacer") or []:
        print(f"  NO: {n}")
    if d.get("archivo") or d.get("motivo_codigo"):
        print(f"  código: {d.get('archivo')} — {d.get('motivo_codigo')}")
    if d.get("escalar_a"):
        print(f"  escalar a: {d.get('escalar_a')}")
    cambios = (d.get("validado") or {}).get("cambios") or []
    if cambios:
        print("  el validador ajustó: " + " · ".join(cambios))
    ctl = d.get("control") or {}
    if ctl and not ctl.get("ok", True):
        print("  control: " + " · ".join(h.get("que_paso", "") for h in ctl.get("hallazgos") or []))
    print(f"  tokens: {d.get('tokens_in')} in / {d.get('tokens_out')} out · {d.get('vueltas')} vuelta(s)")
    if d.get("modelos"):
        print("  modelos: " + " · ".join(f"{k}={v}" for k, v in d["modelos"].items()))
    if d.get("error_investigacion") or d.get("error_conclusion"):
        print(f"  errores: {d.get('error_investigacion')} / {d.get('error_conclusion')}")
    if d.get("conclusion_cruda"):
        print("\n  LA CONCLUSIÓN NO PARSEÓ. Texto crudo del modelo (recortado):\n  "
              + str(d["conclusion_cruda"])[:1500].replace("\n", "\n  "))
    if d.get("causa") == "sin_verificar" and d.get("notas"):
        print("\n  NOTAS DEL INVESTIGADOR (recortadas):\n  " + str(d["notas"])[:1500].replace("\n", "\n  "))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hallazgo", type=int)
    ap.add_argument("--ver", type=int)
    ap.add_argument("--pendientes", action="store_true")
    ap.add_argument("--encolar", action="store_true")
    ap.add_argument("--notas", action="store_true", help="con --ver, imprime las notas del investigador")
    a = ap.parse_args()
    from asistente import diagnostico

    if a.hallazgo:
        run_id = uuid.uuid4().hex
        print(f"diagnosticando hallazgo #{a.hallazgo} (run {run_id[:8]})…")
        r = diagnostico.correr(a.hallazgo, run_id=run_id)
        if r.get("error"):
            print(f"  ERROR: {r['error']}")
            return 1
        _imprimir(r["diagnostico"])
        return 0
    if a.ver:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT diagnostico, diagnosticado_at FROM agente.hallazgos WHERE id = %s", (a.ver,))
            fila = cur.fetchone()
        if not fila or not fila["diagnostico"]:
            print("sin diagnóstico guardado")
            return 1
        print(f"diagnosticado {fila['diagnosticado_at']}")
        _imprimir(fila["diagnostico"])
        if a.notas:
            print("\nNOTAS:\n" + str(fila["diagnostico"].get("notas") or ""))
        return 0
    if a.pendientes or a.encolar:
        if a.encolar:
            r = diagnostico.encolar_pendientes(forzar=True)
            print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
            return 0
        from datetime import UTC, datetime

        from agente import tipos as T
        from config import DIAGNOSTICO_REFRESCO_H, DIAGNOSTICO_TOPE_DIA, DIAGNOSTICO_TOPE_PASADA
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, habilidad, sujeto, regla, estado, severidad, detectado_at,"
                        " diagnosticado_at, diagnostico FROM agente.hallazgos WHERE estado = ANY(%s)",
                        (list(T.ABIERTOS),))
            abiertos = [dict(r) for r in cur.fetchall()]
        ids = diagnostico._elegir(abiertos, set(), 0, datetime.now(UTC), tope_pasada=DIAGNOSTICO_TOPE_PASADA,
                                  tope_dia=DIAGNOSTICO_TOPE_DIA, refresco_h=DIAGNOSTICO_REFRESCO_H)
        por_id = {h["id"]: h for h in abiertos}
        print(f"abiertos: {len(abiertos)} · diagnosticaría ahora (tope {DIAGNOSTICO_TOPE_PASADA}): {len(ids)}")
        for i in ids:
            h = por_id[i]
            print(f"  #{i} {h['habilidad']} · {h['sujeto']} · {h['regla']} · {h['estado']} · {h['severidad']}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
