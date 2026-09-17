"""Agente RENTA VARIABLE (familia mercado): acciones y CEDEARs.
Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import Field

from asistente import estado as EST
from asistente import pantalla
from asistente.agente import COMUN, Agente

Orden = Literal["variacion_dia", "variacion_usd", "volumen", "efectivo"]
MAX_FILAS = 50


def panel_cedears(
  ticker: str | None = None,
  ordenar_por: Orden = "efectivo",
  top: Annotated[int, Field(ge=1, le=MAX_FILAS)] = 15,
) -> dict:
  """Cómo cotizan HOY los CEDEARs en pesos y cuál es su variación.

  Con `ticker` devuelve ese papel; sin `ticker`, un ranking del panel. Es el
  mercado, no la tenencia de una cuenta.

  QUÉ DEVUELVE:
    · `cedears` — precio, puntas, variación intradía, variación contra cierre,
    variación descontando CCL, volumen, efectivo y hora de actualización.
    · `resumen` — cantidad con precio y extremos del universo consultado.
    · `truncado` — true si el ranking tiene más filas que las mostradas.

  Args:
    ticker: ticker corto BYMA. Si se omite, devuelve el panel.
    ordenar_por: criterio del ranking; no cambia el filtro.
    top: cantidad máxima de filas.
  """
  if ordenar_por not in get_args(Orden):
    return {"error": f"`ordenar_por` tiene que ser uno de {list(get_args(Orden))}"}
  try:
    limite = max(1, min(int(top), MAX_FILAS))
  except (TypeError, ValueError):
    return {"error": f"`top` tiene que ser un número entero, llegó {top!r}"}

  from api.services import scanner_sql

  try:
    filas = scanner_sql.get_cedears_scanner()
  except Exception as e:
    return {"error": f"no pude leer el panel de CEDEARs: {type(e).__name__}: {e}"}
  pedido = str(ticker or "").strip().upper() or None
  if pedido:
    filas = [f for f in filas if str(f.get("ticker_corto") or "").upper() == pedido]
    if not filas:
      return {"error": f"{pedido} no está en el universo activo de CEDEARs"}

  campos = {
    "variacion_dia": "vs_1d_pct",
    "variacion_usd": "vs_1d_usd_pct",
    "volumen": "volume",
    "efectivo": "total_money",
  }
  campo = campos[ordenar_por]
  ordenadas = sorted(filas, key=lambda f: (f.get(campo) is None, -(f.get(campo) or 0)))
  con_variacion = [f for f in filas if f.get("vs_1d_pct") is not None]
  sube = max(con_variacion, key=lambda f: f["vs_1d_pct"], default=None)
  baja = min(con_variacion, key=lambda f: f["vs_1d_pct"], default=None)
  salida = {
    "ticker": pedido,
    "ordenado_por": ordenar_por,
    "cedears": ordenadas[:limite],
    "cuantos": len(ordenadas),
    "truncado": len(ordenadas) > limite,
    "resumen": {
      "con_precio": sum(f.get("last") is not None for f in filas),
      "sube_mas": ({"ticker": sube["ticker_corto"], "variacion_dia_pct": sube["vs_1d_pct"]}
             if sube else None),
      "baja_mas": ({"ticker": baja["ticker_corto"], "variacion_dia_pct": baja["vs_1d_pct"]}
             if baja else None),
    },
    "_tabla": pantalla.tabla(
      "cedears", ["ticker_corto", "last", "vs_1d_pct", "vs_1d_usd_pct",
            "volume", "total_money", "updated_at"],
      f"CEDEAR {pedido}" if pedido else f"Panel CEDEARs por {ordenar_por}"),
  }
  return salida

_INSTRUCCION = """
Hablás de RENTA VARIABLE: acciones y CEDEARs, cómo cotizan, cuánto varían,
qué panel, qué subyacente. Sin importar quién las tenga.
"""


def _instruccion(foco: dict) -> str:
  return COMUN + _INSTRUCCION + EST.como_texto(foco, ("ticker",))


AGENTE = Agente(
    nombre="renta_variable",
    tarea="asistente_renta_variable",
    describe="acciones y CEDEARs: cómo cotiza una acción, cuánto varió, qué hay en un panel, "
             "qué subyacente tiene un CEDEAR.",
    instruccion=_instruccion,
    herramientas=(panel_cedears,),
    senales=("accion", "acciones", "cedear", "cedears", "merval", "panel", "subyacente", "papel",
             "papeles", "equity"),
    familia="mercado",
    foco=("ticker",),
)
