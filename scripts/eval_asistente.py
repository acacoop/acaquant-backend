"""Eval semántico integral del asistente contra fixtures sin datos reales.

Mide selección de tool, argumentos, transformación, permisos, evidencia e
inyección. Usa el grafo y el modelo configurado en producción.

    python -m scripts.eval_asistente
    python -m scripts.eval_asistente --estricto
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import yaml

from asistente import ejecutor as EXE
from asistente import grafo

ARCHIVO = Path(__file__).resolve().parents[1] / "evals" / "asistente.yaml"


def cargar(archivo: Path) -> list[dict]:
    casos = yaml.safe_load(archivo.read_text(encoding="utf-8")) or []
    nombres: set[str] = set()
    for numero, caso in enumerate(casos, 1):
        if not isinstance(caso, dict) or not caso.get("nombre") or not caso.get("q"):
            raise SystemExit(f"{archivo}: caso {numero} sin nombre o q")
        if caso["nombre"] in nombres:
            raise SystemExit(f"{archivo}: nombre repetido {caso['nombre']!r}")
        nombres.add(caso["nombre"])
    return casos


def _fake(caso: dict):
    def ejecutar(agente, nombre, args, contexto):
        acceso = EXE.acceso_de(nombre)
        if caso.get("categoria") == "permiso":
            return EXE.ejecutar(agente, nombre, args, contexto)
        if nombre != caso.get("herramienta"):
            return EXE.ResultadoTool(
                {"error": f"el eval no habilitó {nombre}"}, args, acceso, 0)
        resultado = dict(caso.get("fixture") or {})
        evidencias = tuple(caso.get("evidencias") or [])
        if evidencias:
            resultado["_evidencias"] = list(evidencias)
        return EXE.ResultadoTool(resultado, args, acceso, 0, evidencias)
    return ejecutar


def _contiene(texto: str, fragmento: object) -> bool:
    return str(fragmento).casefold() in texto.casefold()


def correr(caso: dict) -> dict:
    with patch.object(grafo, "_ejecutar_detalle", _fake(caso)):
        salida = grafo.preguntar(
            caso["q"], usuario="eval@acaquant", rol=caso.get("rol", "admin"),
            portal=caso.get("portal", "trading"))
    pedidos = [e for e in salida["eventos"] if e.get("tipo") == "pide"]
    resultados = [e for e in salida["eventos"] if e.get("tipo") == "resultado"]
    errores: list[str] = []

    esperados = caso.get("agentes")
    if esperados is not None and set(salida["agentes"]) != set(esperados):
        errores.append(f"agentes: salió {salida['agentes']}, esperaba {esperados}")
    herramienta = caso.get("herramienta")
    pedido = next((e for e in pedidos if e.get("herramienta") == herramienta), None)
    if herramienta and pedido is None:
        errores.append(f"no pidió {herramienta}; pidió {[e.get('herramienta') for e in pedidos]}")
    if pedido and caso.get("argumentos"):
        args = pedido.get("argumentos") or {}
        distintos = {k: (args.get(k), v) for k, v in caso["argumentos"].items()
                     if args.get(k) != v}
        if distintos:
            errores.append(f"argumentos distintos: {distintos}")
    prohibidas = set(caso.get("herramientas_prohibidas") or [])
    usadas = {e.get("herramienta") for e in pedidos}
    if usadas & prohibidas:
        errores.append(f"usó herramientas prohibidas: {sorted(usadas & prohibidas)}")

    respuesta = str(salida.get("respuesta") or "")
    for fragmento in caso.get("respuesta_contiene") or []:
        if not _contiene(respuesta, fragmento):
            errores.append(f"la respuesta no contiene {fragmento!r}")
    for fragmento in caso.get("respuesta_no_contiene") or []:
        if _contiene(respuesta, fragmento):
            errores.append(f"la respuesta contiene lo prohibido {fragmento!r}")
    if cita := caso.get("cita"):
        if cita not in respuesta:
            errores.append(f"falta la cita {cita}")
    if esperado := caso.get("resultado_error_contiene"):
        evento = next((e for e in resultados if e.get("herramienta") == herramienta), {})
        error = str((evento.get("resultado") or {}).get("error") or "")
        if not _contiene(error, esperado):
            errores.append(f"error de permiso: {error!r}")
    return {"nombre": caso["nombre"], "categoria": caso.get("categoria"),
            "errores": errores, "respuesta": respuesta}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archivo", type=Path, default=ARCHIVO)
    parser.add_argument("--estricto", action="store_true")
    args = parser.parse_args()
    resultados = [correr(caso) for caso in cargar(args.archivo)]
    for resultado in resultados:
        marca = "OK" if not resultado["errores"] else "FALLÓ"
        print(f"{marca:5} {resultado['categoria']}: {resultado['nombre']}")
        for error in resultado["errores"]:
            print(f"      - {error}")
    fallidos = sum(bool(resultado["errores"]) for resultado in resultados)
    print(f"\n{len(resultados) - fallidos}/{len(resultados)} casos pasaron")
    return 1 if args.estricto and fallidos else 0


if __name__ == "__main__":
    sys.exit(main())