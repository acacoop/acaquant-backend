"""Agente DERIVADOS (familia mercado): futuros y opciones.
Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import Field

from asistente import estado as EST
from asistente import pantalla
from asistente.agente import COMUN, Agente

TipoOpcion = Literal["CALL", "PUT"]
MAX_CONTRATOS = 80


def curva_futuros_dolar(ticker: str | None = None) -> dict:
  """Dónde cotizan los futuros de dólar y qué tasa implícita tienen.

  Devuelve la curva vigente ordenada por vencimiento. La tasa implícita es la
  persistida por el motor y ya está expresada como porcentaje anual.

  Args:
    ticker: contrato exacto, si se pidió uno; si se omite, devuelve la curva.
  """
  from api.services import mercado_hist_sql

  try:
    contratos = mercado_hist_sql.get_futuros_dlr()
  except Exception as e:
    return {"error": f"no pude leer los futuros DLR: {type(e).__name__}: {e}"}
  pedido = str(ticker or "").strip().upper() or None
  if pedido:
    contratos = [d for d in contratos if str(d.get("ticker") or "").upper() == pedido]
    if not contratos:
      return {"error": f"no encontré el futuro {pedido!r} en la curva vigente"}
  filas = [{
    "ticker": d.get("ticker"), "vencimiento": d.get("vencimiento"),
    "dias_a_vto": d.get("dias_a_vto"), "last": d.get("last"),
    "closing": d.get("closing"), "bid": d.get("bid"), "offer": d.get("offer"),
    "spot": d.get("spot"), "tasa_implicita_tna_pct": d.get("tasa_implicita_tna"),
    "updated_at": d.get("updated_at"),
  } for d in contratos]
  return {
    "ticker": pedido, "contratos": filas, "cuantos": len(filas),
    "_tabla": pantalla.tabla(
      "contratos", ["ticker", "vencimiento", "dias_a_vto", "last", "closing",
              "bid", "offer", "tasa_implicita_tna_pct", "updated_at"],
      f"Futuro DLR {pedido}" if pedido else "Curva de futuros DLR"),
  }


def cadena_opciones(
  instrumento: str | None = None,
  tipo: TipoOpcion | None = None,
  top: Annotated[int, Field(ge=1, le=MAX_CONTRATOS)] = 50,
) -> dict:
  """Qué opciones vigentes hay, con precios, volatilidad y griegas del motor.

  El filtro de `instrumento` busca texto dentro del símbolo del contrato. La
  respuesta incluye la tasa libre de riesgo y referencias usadas por el motor.

  Args:
    instrumento: símbolo completo o fragmento que identifica al subyacente.
    tipo: CALL o PUT.
    top: cantidad máxima de contratos.
  """
  if tipo is not None and tipo not in get_args(TipoOpcion):
    return {"error": f"`tipo` tiene que ser uno de {list(get_args(TipoOpcion))}"}
  try:
    limite = max(1, min(int(top), MAX_CONTRATOS))
  except (TypeError, ValueError):
    return {"error": f"`top` tiene que ser un número entero, llegó {top!r}"}
  pedido = str(instrumento or "").strip().upper() or None
  from api.services import opciones_sql

  try:
    contratos = opciones_sql.get_opciones(instrumento=pedido, tipo=tipo)
    meta = opciones_sql.get_opciones_meta()
  except Exception as e:
    return {"error": f"no pude leer la cadena de opciones: {type(e).__name__}: {e}"}
  contratos.sort(key=lambda d: (str(d.get("vence") or ""), float(d.get("strike") or 0),
                  str(d.get("tipo") or "")))
  filas = contratos[:limite]
  return {
    "instrumento": pedido, "tipo": tipo, "contratos": filas,
    "cuantos": len(contratos), "truncado": len(contratos) > limite,
    "metadata": meta,
    "_tabla": pantalla.tabla(
      "contratos", ["instrumento", "tipo", "vence", "strike", "bid", "offer",
              "last", "spot", "iv", "delta", "gamma", "theta", "vega",
              "updated_at"],
      " · ".join(x for x in ("Cadena de opciones", pedido, tipo) if x)),
  }

_INSTRUCCION = """
Hablás de DERIVADOS: futuros y opciones, dónde cotizan, qué tasa implícita
tienen, qué vencimientos hay. Sin importar quién los tenga.
"""


def _instruccion(foco: dict) -> str:
  return COMUN + _INSTRUCCION + EST.como_texto(foco, ("ticker",))


AGENTE = Agente(
    nombre="derivados",
    tarea="asistente_derivados",
    describe="futuros y opciones: dónde cotiza el dólar futuro, qué tasa implícita tiene, qué "
             "vencimientos hay, qué opciones hay de un papel.",
    instruccion=_instruccion,
    herramientas=(curva_futuros_dolar, cadena_opciones),
    senales=("futuro", "futuros", "opcion", "opciones", "call", "put", "strike", "implicita",
             "rofex", "a3", "dolar futuro"),
    familia="mercado",
    foco=("ticker",),
)
