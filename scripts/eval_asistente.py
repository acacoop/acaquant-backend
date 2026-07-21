"""eval_asistente — harness de regresión del ASISTENTE DE NEGOCIO (QuantAI P7).

Un asistente financiero sin evaluación sistemática regresiona en silencio:
cambiás un prompt o una tool y una respuesta que era correcta deja de serlo
sin que nadie lo note. Este runner corre los casos de `evals/asistente.json`
contra el flujo REAL (con la DB de prod al lado) y compara con la verdad.

CÓMO CORRERLO (Droplet, desde /root/TradingAV):
    python -m scripts.eval_asistente                  # todos los casos
    python -m scripts.eval_asistente --solo-noleak    # solo el no-leak (0 tokens)

Tipos de caso:
- "noleak"  — EN SECO, 0 tokens: toma un cliente REAL del catálogo, arma la
  pregunta, e intercepta el transporte (core.llm.chat) ANTES de salir: junta
  todos los payloads que habrían viajado al proveedor y asserta que el
  nombre/id/tokens del cliente NO aparecen. Nada se persiste (transcript y
  mapping parcheados a no-op). Es la garantía de la aduana medida end-to-end.
- "exacto"  — CON tokens (una llamada real): computa la verdad con el MISMO
  SQL de la tool (ej. AuM total), corre el flujo completo y exige que el
  número aparezca en la respuesta (en millones o miles de millones,
  ±tolerancia_pct). Detecta tools rotas, prompts que inventan y regresiones
  de formato.

CÓMO AGREGAR CASOS: una entrada nueva en evals/asistente.json. Para "exacto",
si la verdad nueva no es el AuM total, agregar su función acá en _VERDADES
(mismo SQL que use la tool — la verdad y la tool no pueden divergir).
REGLA del programa: cada fallo real de producción se congela como caso.

Identidad usada: EVAL_EMAIL (env, default eval@acaquant.local) — así las
trazas/presupuesto del eval no se mezclan con usuarios reales.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from core import llm, pii_gateway

EMAIL = os.getenv("EVAL_EMAIL", "eval@acaquant.local")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Verdades (mismo SQL que las tools — jamás divergir) ──────────────────────

def _verdad_aum_total() -> float | None:
    from api.services import asistente_tools as at
    fecha = at._fecha_snapshot()
    if not fecha:
        return None
    total, _n = at._aum_totales(fecha)
    return total


_VERDADES = {"aum_total": _verdad_aum_total}


# ── Caso NO-LEAK (en seco, 0 tokens) ─────────────────────────────────────────

def _tokens_distintivos(nombre: str) -> list[str]:
    """Las palabras del nombre que SÍ identifican: largas, fuera de la
    stoplist y de los sufijos societarios ('sa'/'ltda' no son identidad)."""
    stop = pii_gateway._stoplist()
    return [t for t in nombre.split()
            if len(t) >= pii_gateway._MIN_TOKEN_LEN and t not in stop
            and t not in pii_gateway._SUFIJOS_SOCIETARIOS]


def _cliente_de_prueba() -> tuple[str, str] | None:
    """Un cliente REAL del catálogo con al menos un token DISTINTIVO + su id
    (así el no-leak prueba una identidad de verdad, no un genérico)."""
    cat = pii_gateway._catalogo()
    if not cat:
        return None
    for nombre, idc in cat["nombres"].items():
        if len(nombre.split()) >= 2 and idc and _tokens_distintivos(nombre):
            return nombre, idc
    return None


def _correr_noleak(caso: dict) -> tuple[bool, str]:
    from api.services import asistente

    elegido = _cliente_de_prueba()
    if not elegido:
        return False, "no pude tomar un cliente del catálogo (¿DB accesible?)"
    nombre, id_cuenta = elegido
    pregunta = caso["pregunta_plantilla"].format(nombre=nombre.title())

    payloads: list[str] = []
    respuesta_seca = llm.RespuestaLLM(
        ok=True, texto="CLIENTE_1 está estable.", mensaje={"content": "x"})

    def chat_interceptado(mensajes, **kw):
        for m in mensajes:
            payloads.append(str(m.get("content") or ""))
        return respuesta_seca

    # parches: transporte interceptado + nada se persiste ni se traza
    from core import ai
    originales = (llm.chat, ai._trazar, asistente._persistir,
                  pii_gateway.guardar_mapping, ai.motivo_presupuesto)
    llm.chat = chat_interceptado
    ai._trazar = lambda *a, **kw: None
    asistente._persistir = lambda *a, **kw: None
    pii_gateway.guardar_mapping = lambda *a, **kw: None
    ai.motivo_presupuesto = lambda u: None
    os.environ.setdefault("DEEPSEEK_API_KEY", "eval-en-seco")
    try:
        r = asistente.responder(mensaje=pregunta, email=EMAIL)
    finally:
        (llm.chat, ai._trazar, asistente._persistir,
         pii_gateway.guardar_mapping, ai.motivo_presupuesto) = originales

    if not r.get("ok"):
        return False, f"el flujo no respondió: {r.get('motivo')} — {r.get('mensaje')}"
    viajado = pii_gateway._norm(" ".join(payloads))
    # lo prohibido = lo que IDENTIFICA: tokens distintivos + el id de cuenta.
    # Un 'sa'/'ltda'/'renta' suelto no identifica a nadie y el matcher los
    # excluye a propósito (calibración 2026-07-21).
    prohibidas = set(_tokens_distintivos(nombre)) | {id_cuenta}
    leaks = [p for p in prohibidas
             if re.search(rf"\b{re.escape(p)}\b", viajado)]
    if leaks:
        return False, f"LEAK: {leaks} en el payload que habría ido al proveedor"
    return True, (f"pregunta con '{nombre.title()}' → {len(payloads)} mensajes "
                  "interceptados, cero identidades afuera")


# ── Caso EXACTO (gasta tokens: flujo real) ───────────────────────────────────

def _numeros_de(texto: str) -> list[float]:
    encontrados = []
    for m in re.finditer(r"\d[\d.,]*", texto):
        crudo = m.group(0).rstrip(".,")
        try:
            # normalización tolerante: "1.500,3" / "1,500.3" / "1500.3"
            limpio = crudo.replace(".", "").replace(",", ".") \
                if crudo.count(",") == 1 and crudo.rfind(",") > crudo.rfind(".") \
                else crudo.replace(",", "")
            encontrados.append(float(limpio))
        except ValueError:
            continue
    return encontrados


def _correr_exacto(caso: dict) -> tuple[bool, str]:
    from api.services import asistente

    verdad_fn = _VERDADES.get(caso["verdad"])
    if not verdad_fn:
        return False, f"verdad desconocida: {caso['verdad']} (agregarla en _VERDADES)"
    verdad = verdad_fn()
    if verdad is None:
        return False, "no pude computar la verdad (¿snapshot de tenencia vacío?)"

    r = asistente.responder(mensaje=caso["pregunta"], email=EMAIL)
    if not r.get("ok"):
        return False, f"el asistente no respondió: {r.get('motivo')} — {r.get('mensaje')}"
    respuesta = r["respuesta"]

    tol = float(caso.get("tolerancia_pct", 2.0)) / 100.0
    representaciones = [verdad, verdad / 1e6, verdad / 1e9]  # crudo / millones / miles de M
    for n in _numeros_de(respuesta):
        for rep in representaciones:
            if rep and abs(n - rep) / abs(rep) <= tol:
                return True, (f"verdad {verdad:,.0f} encontrada como {n} "
                              f"en: «{respuesta[:160]}…»")
    return False, (f"la verdad {verdad:,.0f} NO aparece (±{tol:.0%}) en la respuesta: "
                   f"«{respuesta[:300]}»")


# ── Runner ───────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-noleak", action="store_true",
                    help="solo los casos noleak (en seco, 0 tokens)")
    args = ap.parse_args()

    with open(os.path.join(RAIZ, "evals", "asistente.json"), encoding="utf-8") as f:
        casos = json.load(f)["casos"]

    resultados = []
    for caso in casos:
        if args.solo_noleak and caso["tipo"] != "noleak":
            continue
        if caso["tipo"] == "noleak":
            ok, detalle = _correr_noleak(caso)
        elif caso["tipo"] == "exacto":
            ok, detalle = _correr_exacto(caso)
        else:
            ok, detalle = False, f"tipo desconocido: {caso['tipo']}"
        marca = "PASS" if ok else "FAIL"
        print(f"[{marca}] {caso['id']}: {detalle}")
        resultados.append(ok)

    total, buenos = len(resultados), sum(resultados)
    print(f"\n{buenos}/{total} casos OK")
    sys.exit(0 if buenos == total else 1)


if __name__ == "__main__":
    main()
