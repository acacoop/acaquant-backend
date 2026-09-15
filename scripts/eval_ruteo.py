"""EVAL DEL RUTEO: ¿a los agentes correctos les llega cada pregunta?

No es un test. Un test dice si el CÓDIGO anda; esto dice si el ruteo DECIDE
BIEN, contra preguntas reales de la mesa (`evals/ruteo.yaml`). Corre el MISMO
código que producción (`asistente.grafo.ruteo`), no una copia.

Los dos errores del ruteo no cuestan lo mismo, y por eso se miden aparte:
  · FALTA un agente  → la respuesta sale, se ve bien y está INCOMPLETA. Caro.
  · SOBRA un agente  → unos centavos y unos segundos. Barato.
La nota que manda es la COBERTURA. La precisión es presupuesto, no corrección.

    python -m scripts.eval_ruteo --sin-modelo    # solo capa 1: gratis, no sale a ningún proveedor
    python -m scripts.eval_ruteo                 # completo: la capa 2 llama al modelo real
    python -m scripts.eval_ruteo --estricto      # sale 1 si la cobertura no es 100%
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from asistente import grafo
from asistente import ruteo as RUT
from asistente.agentes import AGENTES

ARCHIVO = Path(__file__).resolve().parents[1] / "evals" / "ruteo.yaml"
USUARIO = "eval@acaquant"

VERDE, ROJO, AMARILLO, GRIS, NEGRITA, FIN = (
    "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[1m", "\033[0m")


def cargar(archivo: Path) -> list[dict]:
    """Las filas del eval, validadas. Un agente mal escrito acá se ve como una
    falla del ruteo y se persigue en el lugar equivocado: se corta antes."""
    filas = yaml.safe_load(archivo.read_text(encoding="utf-8")) or []
    for i, f in enumerate(filas, 1):
        if not isinstance(f, dict) or not f.get("q"):
            raise SystemExit(f"{archivo}: la fila {i} no tiene `q`")
        if "agentes" not in f or not isinstance(f["agentes"], list):
            raise SystemExit(f"{archivo}: «{f['q']}» no declara `agentes` (lista, puede ir vacía)")
        if desconocidos := [a for a in f["agentes"] if a not in AGENTES]:
            raise SystemExit(f"{archivo}: «{f['q']}» nombra agentes que no existen: "
                             f"{', '.join(desconocidos)} (hay: {', '.join(AGENTES)})")
    return filas


def capa_de(motivo: str) -> int:
    """En qué capa cerró, leído del motivo que el ruteo deja en su evento. 1 =
    una regla (gratis), 2 = eligió el modelo, 3 = no se entendió y van todos."""
    if "van todos" in motivo:
        return 3
    return 2 if "eligió el modelo" in motivo or "falló" in motivo else 1


def sin_modelo(pregunta: str) -> tuple[list[str], str, int]:
    """Solo la capa 1. Lo que caería al modelo se reporta sin ejecutarlo: así
    esto corre en CI sin clave, sin costo y sin escribir en `ia.llamadas`."""
    d = RUT.por_reglas(pregunta)
    if d is None:
        return list(AGENTES), "no hay regla: iría al modelo entre TODOS", 2
    if d.tipo == "contesta":
        return [], d.motivo, 1
    if d.tipo == "van":
        return list(d.agentes), d.motivo, 1
    return list(d.agentes), f"{d.motivo} · iría al modelo entre esos", 2


def con_modelo(pregunta: str) -> tuple[list[str], str, int]:
    """El nodo de producción, entero. La capa 2 sale al proveedor de verdad."""
    r = grafo.ruteo({"pregunta": pregunta, "foco": {}, "usuario": USUARIO,
                     "sesion": grafo.sesion_valida(None)})
    ev = next(e for e in r["eventos"] if e["tipo"] == "ruteo")
    return list(r.get("agentes") or []), ev["motivo"], capa_de(ev["motivo"])


def correr(filas: list[dict], *, usar_modelo: bool) -> list[dict]:
    salidas = []
    for f in filas:
        esperados = set(f["agentes"])
        agentes, motivo, capa = (con_modelo if usar_modelo else sin_modelo)(f["q"])
        salidos = set(agentes)
        salidas.append({
            "q": f["q"], "nota": f.get("nota"), "motivo": motivo, "capa": capa,
            "esperados": sorted(esperados), "salidos": sorted(salidos),
            "faltan": sorted(esperados - salidos), "sobran": sorted(salidos - esperados),
        })
    return salidas


def informe(salidas: list[dict], *, usar_modelo: bool) -> bool:
    """Imprime el informe y devuelve si la cobertura fue completa."""
    n = len(salidas)
    cubiertas = [s for s in salidas if not s["faltan"]]
    de_mas = sum(len(s["sobran"]) for s in salidas)
    capas = {c: sum(1 for s in salidas if s["capa"] == c) for c in (1, 2, 3)}
    modo = "CON el modelo real" if usar_modelo else "SIN modelo (solo capa 1)"

    print(f"\n{NEGRITA}EVAL DE RUTEO — {n} preguntas · {modo}{FIN}")
    for s in salidas:
        if s["faltan"]:
            marca, color = "✖", ROJO
        elif s["sobran"]:
            marca, color = "~", AMARILLO
        else:
            marca, color = "✔", VERDE
        print(f"\n  {color}{marca}{FIN} {s['q']}")
        print(f"    {GRIS}esperado:{FIN} {', '.join(s['esperados']) or '(ninguno)'}"
              f"    {GRIS}salió:{FIN} {', '.join(s['salidos']) or '(ninguno)'}"
              f"    {GRIS}capa {s['capa']}{FIN}")
        if s["faltan"]:
            print(f"    {ROJO}FALTA: {', '.join(s['faltan'])}{FIN} "
                  f"{GRIS}— la respuesta saldría incompleta y nadie se entera{FIN}")
        if s["sobran"]:
            print(f"    {AMARILLO}de más: {', '.join(s['sobran'])}{FIN} {GRIS}— cuesta, no daña{FIN}")
        print(f"    {GRIS}{s['motivo']}{FIN}")
        if s["nota"]:
            print(f"    {GRIS}nota: {s['nota']}{FIN}")

    pct = 100 * len(cubiertas) / n if n else 0.0
    color = VERDE if pct == 100 else ROJO
    print(f"\n{NEGRITA}{'─' * 70}{FIN}")
    print(f"  {NEGRITA}cobertura{FIN}  {color}{len(cubiertas)}/{n} ({pct:.0f}%){FIN}"
          f"  {GRIS}← la nota: ninguna pregunta puede perder un agente{FIN}")
    print(f"  {NEGRITA}de más{FIN}     {de_mas} agente(s) en total"
          f"  {GRIS}← presupuesto, no corrección{FIN}")
    print(f"  {NEGRITA}capas{FIN}      1: {capas[1]} ({100 * capas[1] // n if n else 0}% gratis)"
          f" · 2: {capas[2]} · 3: {capas[3]}"
          f"  {GRIS}← cuánto cuesta rutear tus preguntas{FIN}")
    if faltas := [s for s in salidas if s["faltan"]]:
        print(f"\n{ROJO}{NEGRITA}  QUÉ ARREGLAR{FIN}")
        for s in faltas:
            print(f"    «{s['q']}» no llega a {', '.join(s['faltan'])}: "
                  f"falta una señal en ese agente, o la fila del eval está mal.")
    return not faltas


def main() -> int:
    p = argparse.ArgumentParser(description="Eval del ruteo del asistente")
    p.add_argument("--sin-modelo", action="store_true",
                   help="solo la capa 1: no sale a ningún proveedor, no cuesta nada")
    p.add_argument("--archivo", type=Path, default=ARCHIVO, help=f"default {ARCHIVO}")
    p.add_argument("--estricto", action="store_true", help="sale 1 si la cobertura no es 100%%")
    a = p.parse_args()

    filas = cargar(a.archivo)
    salidas = correr(filas, usar_modelo=not a.sin_modelo)
    ok = informe(salidas, usar_modelo=not a.sin_modelo)
    return 1 if a.estricto and not ok else 0


if __name__ == "__main__":
    sys.exit(main())
