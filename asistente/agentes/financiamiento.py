"""Agente FINANCIAMIENTO (familia mercado): cauciones y tasas de referencia.
Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import Field

from asistente import pantalla
from asistente.agente import COMUN, Agente

Moneda = Literal["ARS", "USD"]
TasaReferencia = Literal["TAMAR", "BADLAR"]


def cauciones_vigentes(
  moneda: Moneda = "ARS",
  plazo_dias: Annotated[int | None, Field(ge=1, le=365)] = None,
) -> dict:
  """Cuánto paga la caución vigente por moneda y plazo.

  Las tasas `tna_*_pct` ya están expresadas como porcentaje nominal anual.

  Args:
    moneda: ARS o USD.
    plazo_dias: plazo exacto; si se omite, devuelve todos los disponibles.
  """
  if moneda not in get_args(Moneda):
    return {"error": f"`moneda` tiene que ser una de {list(get_args(Moneda))}"}
  if plazo_dias is not None:
    try:
      plazo = int(plazo_dias)
    except (TypeError, ValueError):
      return {"error": f"`plazo_dias` tiene que ser entero, llegó {plazo_dias!r}"}
    if not 1 <= plazo <= 365:
      return {"error": "`plazo_dias` tiene que estar entre 1 y 365"}
  else:
    plazo = None
  from api.services import mercado_hist_sql

  try:
    cauciones = mercado_hist_sql.get_caucion(moneda=moneda)
  except Exception as e:
    return {"error": f"no pude leer las cauciones: {type(e).__name__}: {e}"}
  if plazo is not None:
    cauciones = [d for d in cauciones if d.get("plazo_dias") == plazo]
  cauciones.sort(key=lambda d: (int(d.get("plazo_dias") or 0),
                  str(d.get("updated_at") or "")))
  filas = [{
    "moneda": d.get("moneda"), "plazo_dias": d.get("plazo_dias"),
    "ticker": d.get("ticker"), "tna_last_pct": d.get("tna_last"),
    "tna_bid_pct": d.get("tna_bid"), "tna_offer_pct": d.get("tna_offer"),
    "tna_closing_pct": d.get("tna_closing"), "vol_efectivo": d.get("vol_efectivo"),
    "updated_at": d.get("updated_at"),
  } for d in cauciones]
  return {
    "moneda": moneda, "plazo_dias": plazo, "cauciones": filas, "cuantos": len(filas),
    "_tabla": pantalla.tabla(
      "cauciones", ["moneda", "plazo_dias", "ticker", "tna_last_pct", "tna_bid_pct",
              "tna_offer_pct", "tna_closing_pct", "vol_efectivo", "updated_at"],
      " · ".join(x for x in (f"Cauciones {moneda}", f"{plazo} días" if plazo else None) if x)),
  }


def tasa_referencia(
  tasa: TasaReferencia,
  ventana_dias: Annotated[int, Field(ge=7, le=730)] = 90,
  con_serie: bool = False,
) -> dict:
  """A cuánto está la TAMAR o BADLAR y cómo se ubica en su historia reciente.

  Devuelve nivel actual, fecha, cambios, percentil y z-score. Con `con_serie`
  agrega los puntos diarios de la ventana pedida.

  Args:
    tasa: TAMAR o BADLAR.
    ventana_dias: ventana histórica usada para los cambios y estadísticas.
    con_serie: true si el usuario pidió evolución o historia de la tasa.
  """
  if tasa not in get_args(TasaReferencia):
    return {"error": f"`tasa` tiene que ser una de {list(get_args(TasaReferencia))}"}
  try:
    ventana = int(ventana_dias)
  except (TypeError, ValueError):
    return {"error": f"`ventana_dias` tiene que ser entero, llegó {ventana_dias!r}"}
  if not 7 <= ventana <= 730:
    return {"error": "`ventana_dias` tiene que estar entre 7 y 730"}
  from api.services import macro_sql

  try:
    datos = macro_sql.obtener_serie_macro(tasa.lower(), ventana)
  except Exception as e:
    return {"error": f"no pude leer {tasa}: {type(e).__name__}: {e}"}
  if datos.get("actual") is None:
    return {"error": datos.get("hint") or f"{tasa} no tiene datos", "tasa": tasa}
  salida = {k: v for k, v in datos.items() if k != "serie"}
  salida["tasa"] = tasa
  salida["puntos_serie"] = len(datos.get("serie") or [])
  if con_serie:
    salida["serie"] = datos.get("serie") or []
    salida["_tabla"] = pantalla.tabla(
      "serie", ["fecha", "valor"], f"Evolución {tasa} · {ventana} días")
  return salida

_INSTRUCCION = """
Hablás de FINANCIAMIENTO: caución por plazo y tasas de referencia. Cuánto
cuesta financiarse y cuánto paga colocar.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="financiamiento",
    tarea="asistente_financiamiento",
    describe="caución y tasas: cuánto rinde la caución a N días, a cuánto están las tasas de "
             "referencia (TAMAR, BADLAR), cuánto cuesta financiarse.",
    instruccion=_instruccion,
    herramientas=(cauciones_vigentes, tasa_referencia),
    senales=("caucion", "cauciones", "badlar", "tamar", "financiar", "financiarse", "financiarme",
             "financiamiento", "financiacion", "colocar", "plazo fijo", "tasa de referencia"),
    familia="mercado",
    foco=(),
)
