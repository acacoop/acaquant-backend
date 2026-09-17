"""Agente OPERACIONES: qué HACE la mesa. El libro de operaciones de la empresa:
qué se compró y vendió, en qué títulos, por cuánto, con qué arancel. La cuenta
es un filtro, no el sujeto: el sujeto es la mesa. Doc: docs/AvAgentAI.md.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal, get_args

from pydantic import Field

from asistente import estado as EST
from asistente import pantalla, permitido
from asistente.agente import COMUN, Agente
from asistente.agentes.renta_fija import Fecha, fecha_o_error

Metrica = Literal["bruto", "arancel"]
Dimension = Literal["instrumento", "cliente", "operacion", "mercado", "mes"]
Periodo = Literal["hoy", "semana", "mes", "anio"]
Moneda = Literal["ARS", "USD"]
MAX_FILAS = 50


def _ventana(periodo: str, desde: Fecha | None, hasta: Fecha | None) -> tuple[dict | None, dict | None]:
  hoy = date.today()
  if desde is not None or hasta is not None:
    if desde is None or hasta is None:
      return None, {"error": "`desde` y `hasta` se usan juntos"}
    inicio, error = fecha_o_error(desde, "desde")
    if error:
      return None, error
    fin, error = fecha_o_error(hasta, "hasta")
    if error:
      return None, error
    if inicio > fin:
      return None, {"error": f"la ventana está al revés: {inicio} es posterior a {fin}"}
    return {"desde": inicio, "hasta": fin, "periodo": "explicito"}, None
  if periodo not in get_args(Periodo):
    return None, {"error": f"`periodo` tiene que ser uno de {list(get_args(Periodo))}"}
  inicios = {
    "hoy": hoy,
    "semana": hoy - timedelta(days=hoy.weekday()),
    "mes": hoy.replace(day=1),
    "anio": hoy.replace(month=1, day=1),
  }
  return {"desde": inicios[periodo].isoformat(), "hasta": hoy.isoformat(),
      "periodo": periodo}, None


def resumen_operaciones(
  metrica: Metrica = "bruto",
  periodo: Periodo = "hoy",
  por: Dimension = "instrumento",
  moneda: Moneda = "ARS",
  cuenta: str | None = None,
  ticker: str | None = None,
  desde: Fecha | None = None,
  hasta: Fecha | None = None,
  top: Annotated[int, Field(ge=1, le=MAX_FILAS)] = 15,
) -> dict:
  """Qué OPERÓ la mesa en un período, consolidado por una dimensión.

  Es movimiento: volumen bruto o arancel de boletos. NO es la posición ni el
  patrimonio de una cuenta; para eso está `tenencia_actual`.

  QUÉ DEVUELVE:
    · `filas` — ranking por instrumento, cliente, operación o mercado; con
    `por="mes"` es una serie cronológica.
    · `total` — suma de las filas devueltas, en la moneda indicada.
    · `ventana` — fechas efectivas consultadas.
    · `alcance_cuentas` — cuántas cuentas habilitadas entraron al universo.

  Args:
    metrica: `bruto` para volumen operado; `arancel` para comisiones.
    periodo: ventana relativa a hoy. Si el usuario dio fechas, usá además
      `desde` y `hasta`, que pisan este valor.
    por: dimensión del consolidado.
    moneda: moneda de salida. El arancel siempre vuelve en ARS porque así se
      guarda y calcula en la plataforma.
    cuenta: filtro opcional por `id_cuenta`; tiene que estar habilitada.
    ticker: filtro opcional por instrumento exacto.
    desde: primera fecha incluida, junto con `hasta`.
    hasta: última fecha incluida, junto con `desde`.
    top: cantidad máxima de filas.
  """
  try:
    scope = tuple(permitido.parametros()["cuentas_permitidas"])
  except permitido.SinPermiso:
    return permitido.como_error()
  pedida = str(cuenta or "").strip() or None
  if pedida and pedida not in scope:
    return {"error": f"la cuenta {pedida!r} no está habilitada para el asistente",
        "cuentas_habilitadas": list(scope)}
  if metrica not in get_args(Metrica):
    return {"error": f"`metrica` tiene que ser una de {list(get_args(Metrica))}"}
  if por not in get_args(Dimension):
    return {"error": f"`por` tiene que ser una de {list(get_args(Dimension))}"}
  if moneda not in get_args(Moneda):
    return {"error": f"`moneda` tiene que ser una de {list(get_args(Moneda))}"}
  ventana, error = _ventana(periodo, desde, hasta)
  if error:
    return error
  try:
    limite = max(1, min(int(top), MAX_FILAS))
  except (TypeError, ValueError):
    return {"error": f"`top` tiene que ser un número entero, llegó {top!r}"}

  from api.services import operaciones_sql

  resultado = operaciones_sql.ops_consolidado(
    metrica=metrica, desde=ventana["desde"], hasta=ventana["hasta"],
    por=por, moneda=moneda, top=limite, scope=scope, cuenta=pedida,
    instrumento=str(ticker or "").strip().upper() or None,
  )
  if resultado.get("error"):
    return resultado
  resultado["ventana"] = ventana
  resultado["alcance_cuentas"] = len(scope)
  resultado["cuenta"] = pedida
  resultado["ticker"] = str(ticker or "").strip().upper() or None
  resultado["_tabla"] = pantalla.tabla(
    "filas", ["clave", "valor", "n"],
    f"{metrica.capitalize()} por {por} · {ventana['desde']} a {ventana['hasta']}",
    total="total", moneda=resultado.get("moneda"))
  return resultado

_INSTRUCCION = """
Hablás de lo que la mesa OPERÓ: boletos, volumen, aranceles, por título, por
cliente, por día. Movimiento, no posición: qué se compró y vendió, no qué se
tiene. La cuenta es un filtro más, como el título o la fecha.
"""


def _instruccion(foco: dict) -> str:
  return COMUN + _INSTRUCCION + "\n" + permitido_texto() + EST.como_texto(foco)


def permitido_texto() -> str:
  cuentas = permitido.cuentas()
  if not cuentas:
    return "No hay cuentas habilitadas para consultar.\n"
  return "Cuentas habilitadas: " + ", ".join(cuentas) + ".\n"


AGENTE = Agente(
    nombre="operaciones",
    tarea="asistente_operaciones",
    describe="qué HIZO la mesa: qué se compró y vendió, en qué títulos, por cuánto, con qué "
             "arancel, qué boletos hubo. Movimiento, no posición. La cuenta es un filtro, "
             "no hace falta.",
    instruccion=_instruccion,
    herramientas=(resumen_operaciones,),
    senales=("compro", "compraron", "vendio", "vendieron", "opero", "operaron", "operacion",
             "operaciones", "boleto", "boletos", "arancel", "aranceles", "volumen", "concerto",
             "cierre", "cierres"),
    foco=("cuenta", "ticker"),
)
