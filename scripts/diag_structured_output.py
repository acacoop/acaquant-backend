"""¿Se llevan bien STRUCTURED OUTPUT y TOOL CALLING? — medición, read-only.

Antes de meterle structured output al asistente hay que saber una cosa que NO se
puede deducir leyendo docs: si el proveedor acepta `response_format` **en la
misma llamada** que trae `tools`, y qué hace cuando las dos cosas conviven.

Son tres preguntas, y cada una cambia el diseño:

  1. ¿Acepta `response_format` a secas? (sin herramientas)
  2. ¿Lo acepta CON `tools` en el mismo pedido?
  3. Con las dos puestas y una pregunta que NECESITA la herramienta: ¿pide la
     herramienta igual, o se salta el ciclo y contesta cualquier cosa con tal de
     cumplir el esquema?

La 3 es la que importa. Si un modelo prefiere cumplir el esquema antes que pedir
la herramienta, structured output le rompe el ciclo al asistente — y no falla:
contesta, sin datos.

Corre contra los DOS proveedores y compara. No escribe nada, no toca la base, y
cada llamada es de pocos tokens.

    python -m scripts.diag_structured_output
    python -m scripts.diag_structured_output --proveedor openai
"""
from __future__ import annotations

import argparse
import json

from core import llm

# El esquema de prueba: chico y con un campo obligatorio, que es lo que hace que
# se note si el proveedor lo está respetando o lo está ignorando.
ESQUEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "respuesta_prueba",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "respuesta": {"type": "string"},
                "seguro": {"type": "boolean"},
            },
            "required": ["respuesta", "seguro"],
            "additionalProperties": False,
        },
    },
}

# Una herramienta que el modelo NO puede resolver de memoria: el saldo de una
# cuenta inventada. Si contesta sin pedirla, está inventando.
HERRAMIENTA = [{
    "type": "function",
    "function": {
        "name": "saldo_de_cuenta",
        "description": ("El saldo actual de una cuenta. Es el ÚNICO modo de "
                        "saberlo: no lo podés deducir ni recordar."),
        "parameters": {
            "type": "object",
            "properties": {"cuenta": {"type": "string"}},
            "required": ["cuenta"],
        },
    },
}]

PREGUNTA_LIBRE = [{"role": "user", "content": "Decime en una frase qué es un bono."}]
PREGUNTA_CON_DATO = [
    {"role": "system", "content": "Usás las herramientas que tenés. No inventes datos."},
    {"role": "user", "content": "¿Cuál es el saldo de la cuenta 99999?"},
]


def _llamar(proveedor: str, mensajes: list[dict], *, herramientas=None,
            esquema=None) -> tuple[str, str]:
    """→ (veredicto, detalle). Nunca levanta: el punto es MEDIR qué pasa."""
    try:
        r = llm.chat(mensajes, modelo=llm.modelo("flash", proveedor), max_tokens=200,
                     timeout_s=45, thinking="disabled", proveedor=proveedor,
                     herramientas=herramientas, formato=esquema)
    except TypeError:
        return "SIN SOPORTE", ("`llm.chat` todavía no acepta `formato`: este diag "
                               "se corre DESPUÉS de agregarle el parámetro")
    except Exception as e:
        return "EXCEPCIÓN", f"{type(e).__name__}: {e}"

    if not r.ok:
        return "RECHAZADO", (r.error or "")[:300]
    if r.pedidos:
        return "PIDIÓ HERRAMIENTA", ", ".join(p.get("nombre", "?") for p in r.pedidos)
    texto = (r.texto or "").strip()
    if esquema is None:
        return "CONTESTÓ TEXTO", texto[:160]
    try:
        d = json.loads(texto)
    except Exception:
        return "⚠ NO ES JSON", texto[:200]
    faltan = [k for k in ("respuesta", "seguro") if k not in d]
    if faltan:
        return "⚠ JSON INCOMPLETO", f"faltan {faltan}: {texto[:160]}"
    return "JSON OK", texto[:160]


def main(proveedores: list[str]) -> int:
    print("¿STRUCTURED OUTPUT + TOOL CALLING? — una llamada por caso, read-only\n")
    for prov in proveedores:
        if not llm.configurado(prov):
            print(f"── {prov}: sin API key, salteado\n")
            continue
        print(f"── {prov}  (modelo flash: {llm.modelo('flash', prov)})")

        casos = [
            ("1. esquema solo, sin herramientas",
             PREGUNTA_LIBRE, None, ESQUEMA),
            ("2. esquema + herramientas, pregunta que NO necesita la herramienta",
             PREGUNTA_LIBRE, HERRAMIENTA, ESQUEMA),
            ("3. esquema + herramientas, pregunta que SÍ la necesita",
             PREGUNTA_CON_DATO, HERRAMIENTA, ESQUEMA),
            ("4. (control) herramientas SIN esquema, la misma pregunta",
             PREGUNTA_CON_DATO, HERRAMIENTA, None),
        ]
        for nombre, msgs, tools, esq in casos:
            veredicto, detalle = _llamar(prov, msgs, herramientas=tools, esquema=esq)
            print(f"   {nombre}\n     → {veredicto}: {detalle}")
        print()

    print("CÓMO SE LEE:")
    print("  · El caso 3 es el que decide. Si dice PIDIÓ HERRAMIENTA, structured")
    print("    output y el ciclo conviven y se puede aplicar el esquema en todas")
    print("    las vueltas.")
    print("  · Si el 3 dice JSON OK pero el 4 dice PIDIÓ HERRAMIENTA, el esquema")
    print("    le GANA a la herramienta: el modelo prefiere cumplir la forma antes")
    print("    que buscar el dato. Ahí el esquema sólo puede ir en la ÚLTIMA vuelta,")
    print("    cuando ya no quedan herramientas por pedir.")
    print("  · Si el 1 dice RECHAZADO, ese proveedor no soporta `response_format`")
    print("    y para él no hay structured output: hay que contemplar las dos ramas.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--proveedor", default="", help="uno solo; vacío = todos")
    args = ap.parse_args()
    raise SystemExit(main([args.proveedor] if args.proveedor else llm.proveedores()))
