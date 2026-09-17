"""Ejecución única de tools: schema runtime, autorización y evidencia.

El modelo, el grafo y los tests pasan por este módulo. Una tool nueva no puede
quedar disponible hasta declarar su clase de acceso en ``ACCESO_POR_TOOL``.
Doc: docs/AvAgentAI.md §2.1.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from asistente import evidencia as EVI
from asistente import herramientas as H
from asistente import permitido
from asistente.agente import Agente


class Acceso(StrEnum):
    READ_PUBLIC = "READ_PUBLIC"
    READ_BUSINESS = "READ_BUSINESS"
    READ_PERSONAL = "READ_PERSONAL"
    PROPOSE = "PROPOSE"
    WRITE = "WRITE"


@dataclass(frozen=True)
class RunContext:
    run_id: str
    usuario: str
    rol: str
    portal: str
    cuentas: tuple[str, ...]

    @classmethod
    def actual(
        cls,
        *,
        run_id: str | None = None,
        usuario: str = "",
        rol: str = "admin",
        portal: str = "trading",
    ) -> RunContext:
        return cls(
            run_id=run_id or uuid.uuid4().hex,
            usuario=usuario,
            rol=rol,
            portal=portal,
            cuentas=tuple(permitido.cuentas()),
        )


@dataclass(frozen=True)
class ResultadoTool:
    resultado: dict
    argumentos: dict | None
    acceso: Acceso | None
    duracion_ms: int
    evidencias: tuple[dict, ...] = ()


ACCESO_POR_TOOL: dict[str, Acceso] = {
    "tenencia_actual": Acceso.READ_BUSINESS,
    "cobros_futuros": Acceso.READ_BUSINESS,
    "alternativas_para_rotar": Acceso.READ_BUSINESS,
    "ficha_cliente": Acceso.READ_PERSONAL,
    "resumen_operaciones": Acceso.READ_BUSINESS,
    "instrumentos_de_la_curva": Acceso.READ_PUBLIC,
    "ficha_bono": Acceso.READ_PUBLIC,
    "panel_cedears": Acceso.READ_PUBLIC,
    "ranking_fondos": Acceso.READ_PUBLIC,
    "ficha_fondo": Acceso.READ_PUBLIC,
    "curva_futuros_dolar": Acceso.READ_PUBLIC,
    "cadena_opciones": Acceso.READ_PUBLIC,
    "cauciones_vigentes": Acceso.READ_PUBLIC,
    "tasa_referencia": Acceso.READ_PUBLIC,
    "tipos_de_cambio": Acceso.READ_PUBLIC,
}

_ROLES_NEGOCIO = {"admin", "trader", "asistente_comercial"}
_ROLES_PERSONALES = {"admin"}


def acceso_de(nombre: str) -> Acceso | None:
    return ACCESO_POR_TOOL.get(nombre)


def _autorizar(nombre: str, acceso: Acceso, args: dict, contexto: RunContext) -> dict | None:
    if not contexto.usuario:
        return {"error": "la herramienta no recibió una identidad autenticada"}
    if contexto.portal != "trading":
        return {"error": f"{nombre} no está disponible desde el portal {contexto.portal!r}"}
    if acceso == Acceso.READ_BUSINESS and contexto.rol not in _ROLES_NEGOCIO:
        return {"error": f"el rol {contexto.rol!r} no puede leer datos del negocio"}
    if acceso == Acceso.READ_PERSONAL and contexto.rol not in _ROLES_PERSONALES:
        return {"error": f"el rol {contexto.rol!r} no puede leer datos personales"}
    if acceso in (Acceso.PROPOSE, Acceso.WRITE):
        return {"error": f"las herramientas {acceso.value} todavía requieren aprobación explícita"}
    cuenta = str(args.get("cuenta") or "").strip() if "cuenta" in args else ""
    if cuenta:
        if not contexto.cuentas:
            return permitido.como_error()
        if cuenta not in contexto.cuentas:
            return {
                "error": f"la cuenta {cuenta!r} no está habilitada para el asistente",
                "cuentas_habilitadas": list(contexto.cuentas),
            }
    return None


def _validar(fn, args: dict) -> tuple[dict | None, dict | None]:
    tool = H.como_tool(fn)
    schema = tool.args_schema
    if schema is None:
        return dict(args), None
    try:
        validados = schema.model_validate(args)
    except ValidationError as error:
        return None, {
            "error": "los argumentos no cumplen el schema de la herramienta",
            "detalle": error.errors(include_url=False),
        }
    return validados.model_dump(), None


def ejecutar(agente: Agente, nombre: str, args: dict | None, contexto: RunContext) -> ResultadoTool:
    """Valida, autoriza y ejecuta una tool. Todo error vuelve como dato."""
    inicio = time.perf_counter()
    fn = agente.por_nombre.get(nombre or "")
    acceso = acceso_de(nombre)
    if fn is None:
        return _resultado({"error": f"no existe una herramienta llamada {nombre!r}",
                           "disponibles": sorted(agente.por_nombre)}, None, acceso, inicio)
    if acceso is None:
        return _resultado({"error": f"la herramienta {nombre!r} no declaró su clase de acceso"},
                          None, None, inicio)
    if not isinstance(args, dict):
        return _resultado({"error": "no pude leer tus argumentos: no son un JSON válido",
                           "que_hacer": "Volvé a pedir la herramienta con argumentos válidos."},
                          None, acceso, inicio)
    validados, error = _validar(fn, args)
    if error:
        return _resultado(error, None, acceso, inicio)
    assert validados is not None
    if corte := _autorizar(nombre, acceso, validados, contexto):
        return _resultado(corte, validados, acceso, inicio)
    try:
        resultado = fn(**validados)
        if not isinstance(resultado, dict):
            resultado = {"resultado": resultado}
    except Exception as error:
        resultado = {"error": f"la herramienta falló: {type(error).__name__}: {error}"}
    evidencias = tuple(EVI.extraer(nombre, acceso.value, validados, resultado))
    if evidencias:
        resultado = {**resultado, "_evidencias": list(evidencias)}
    return _resultado(resultado, validados, acceso, inicio, evidencias)


def _resultado(resultado: dict, argumentos: dict | None, acceso: Acceso | None,
               inicio: float, evidencias: tuple[dict, ...] = ()) -> ResultadoTool:
    return ResultadoTool(
        resultado=resultado,
        argumentos=argumentos,
        acceso=acceso,
        duracion_ms=max(0, int((time.perf_counter() - inicio) * 1000)),
        evidencias=evidencias,
    )
