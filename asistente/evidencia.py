"""El contrato entre una herramienta y la EVIDENCIA: de quién es cada dato.

Una evidencia es un registro de un resultado (una fila o la raíz) con sus
campos escalares, y un SUJETO: la cuenta, el ticker o el fondo del que habla.
El control usa ese sujeto para verificar que el modelo atribuyó bien la cita.

El sujeto de la raíz lo DECLARA la herramienta con `sujeto()` en la clave
`_sujeto`, igual que declara su tabla en `_tabla`. Antes se adivinaba mirando
el resultado y haciéndole `str()` a lo primero que pareciera un nombre: en
`tenencia_actual` eso agarró el diccionario `{"id_cuenta", "nombre"}` entero,
el control exigió que ese texto apareciera en la respuesta (imposible) y el
nombre del titular quedó guardado en `ia.evidencias`. Un sujeto es UN escalar,
y si no hay uno declarado se cae a la cuenta o el ticker del ARGUMENTO, que sí
es escalar por firma. Doc: docs/AvAgentAI.md §12.
"""
from __future__ import annotations

import uuid
from typing import Any

# Claves que, si vienen con un ESCALAR, identifican de quién es una fila.
SUJETOS = ("cuenta", "ticker", "ticker_corto", "instrumento", "fci_id", "nombre", "clave", "moneda")
FECHAS = ("fecha", "fecha_actual", "updated_at", "tenencia_del", "hasta", "vencimiento")
MAX_EVIDENCIAS = 120
GENERAL = "general"


def sujeto(clave: str, valor: Any) -> dict:
    """La declaración de sujeto de una herramienta, para su clave `_sujeto`.
    `valor` tiene que ser un escalar no vacío: un diccionario no es un sujeto."""
    if not clave or not _es_escalar(valor) or valor in (None, ""):
        raise ValueError("un sujeto es una clave y UN valor escalar no vacío")
    return {"clave": str(clave), "valor": str(valor)}


def extraer(nombre: str, acceso: str, args: dict, resultado: dict) -> list[dict]:
    """Las evidencias de un resultado: una por fila de cada lista y una por la
    raíz, cada una con sus campos escalares, su sujeto y su fecha."""
    if not isinstance(resultado, dict) or resultado.get("error"):
        return []
    raiz = _sujeto_raiz(args, resultado)
    # La raíz va PRIMERA: es la que tiene el total, y con el tope de evidencias
    # una tabla de más de MAX_EVIDENCIAS filas la dejaba afuera, y toda cita
    # al total salía «referencia que no existe».
    registros: list[tuple[str, dict]] = [("$", resultado)]
    for campo, valor in resultado.items():
        if str(campo).startswith("_"):
            continue
        if isinstance(valor, list):
            registros.extend((f"$.{campo}[{indice}]", fila) for indice, fila in enumerate(valor)
                             if isinstance(fila, dict))
    evidencias: list[dict] = []
    for ruta, registro in registros:
        campos = {str(k): v for k, v in registro.items()
                  if not str(k).startswith("_") and _es_escalar(v)}
        if not campos:
            continue
        propio = raiz if ruta == "$" else (_escalar_en(registro, SUJETOS) or raiz)
        evidencias.append({
            "ref": uuid.uuid4().hex[:12],
            "herramienta": nombre,
            "acceso": acceso,
            "sujeto": propio,
            "fecha": _escalar_en(registro, FECHAS),
            "ruta": ruta,
            "campos": campos,
        })
        if len(evidencias) >= MAX_EVIDENCIAS:
            break
    return evidencias


def _sujeto_raiz(args: dict, resultado: dict) -> str:
    declarado = resultado.get("_sujeto")
    if isinstance(declarado, dict) and declarado.get("valor") not in (None, ""):
        return str(declarado["valor"])
    return (_escalar_en(resultado, SUJETOS) or _escalar_en(args or {}, SUJETOS) or GENERAL)


def _escalar_en(registro: dict, claves: tuple[str, ...]) -> str | None:
    """El primer valor ESCALAR no vacío entre `claves`. Un diccionario o una
    lista en esa clave no cuenta: no es un sujeto ni una fecha."""
    for clave in claves:
        valor = registro.get(clave)
        if valor not in (None, "") and _es_escalar(valor):
            return str(valor)
    return None


def _es_escalar(valor: Any) -> bool:
    return valor is None or isinstance(valor, (str, int, float, bool))
