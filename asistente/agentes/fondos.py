"""Agente FONDOS (familia mercado): fondos comunes de inversión.
Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import Field

from asistente import pantalla
from asistente.agente import COMUN, Agente

Periodo = Literal["1d", "wtd", "mtd", "ytd", "7d", "30d", "90d", "365d", "tna_30d"]
MAX_FONDOS = 50


def _pct(valor) -> float | None:
  return round(float(valor) * 100, 2) if valor is not None else None


def _fila(fondo: dict) -> dict:
  return {
    "fci_id": fondo.get("fci_id"), "nombre": fondo.get("nombre"),
    "gerente": fondo.get("gerente"), "categoria": fondo.get("categoria"),
    "moneda": fondo.get("moneda"), "plazo": fondo.get("plazo"),
    "simbolo_primary": fondo.get("simbolo_primary"), "unidad": fondo.get("unidad"),
    "fecha": fondo.get("fecha"), "vcp": fondo.get("vcp"), "fuente": fondo.get("fuente"),
    "r_1d_pct": _pct(fondo.get("r_1d")), "r_wtd_pct": _pct(fondo.get("r_wtd")),
    "r_mtd_pct": _pct(fondo.get("r_mtd")), "r_ytd_pct": _pct(fondo.get("r_ytd")),
    "r_7d_pct": _pct(fondo.get("r_7d")), "r_30d_pct": _pct(fondo.get("r_30d")),
    "r_90d_pct": _pct(fondo.get("r_90d")), "r_365d_pct": _pct(fondo.get("r_365d")),
    "tna_30d_pct": _pct(fondo.get("tna_30d")),
  }


def ranking_fondos(
  periodo: Periodo = "30d",
  categoria: str | None = None,
  moneda: str | None = None,
  gerente: str | None = None,
  top: Annotated[int, Field(ge=1, le=MAX_FONDOS)] = 15,
) -> dict:
  """Qué FCI del universo seguido rindieron más en un período.

  Filtra por categoría, moneda y gerente antes de ordenar. Los retornos
  terminados en `_pct` ya están expresados en porcentaje.

  Args:
    periodo: retorno usado para ordenar.
    categoria: categoría exacta del catálogo, si el usuario la nombró.
    moneda: moneda exacta del fondo.
    gerente: nombre de la gerente, por texto contenido.
    top: cantidad máxima de fondos.
  """
  if periodo not in get_args(Periodo):
    return {"error": f"`periodo` tiene que ser uno de {list(get_args(Periodo))}"}
  try:
    limite = max(1, min(int(top), MAX_FONDOS))
  except (TypeError, ValueError):
    return {"error": f"`top` tiene que ser un número entero, llegó {top!r}"}
  from api.services import fci_sql

  try:
    datos = fci_sql.tabla()
  except Exception as e:
    return {"error": f"no pude leer los FCI: {type(e).__name__}: {e}"}
  fondos = list(datos.get("fondos") or [])
  if categoria:
    fondos = [f for f in fondos if str(f.get("categoria") or "").upper() == categoria.upper()]
  if moneda:
    fondos = [f for f in fondos if str(f.get("moneda") or "").upper() == moneda.upper()]
  if gerente:
    clave = gerente.upper()
    fondos = [f for f in fondos if clave in str(f.get("gerente") or "").upper()]
  campo = "tna_30d" if periodo == "tna_30d" else f"r_{periodo}"
  comparables = [f for f in fondos if f.get(campo) is not None]
  comparables.sort(key=lambda f: -float(f[campo]))
  filas = [_fila(f) for f in comparables[:limite]]
  return {
    "periodo": periodo, "categoria": categoria, "moneda": moneda, "gerente": gerente,
    "fecha_max": datos.get("fecha_max"), "fondos": filas,
    "cuantos": len(comparables), "truncado": len(comparables) > limite,
    "sin_dato_periodo": len(fondos) - len(comparables),
    "rinde_mas": filas[0] if filas else None,
    "_tabla": pantalla.tabla(
      "fondos", ["nombre", "gerente", "categoria", "moneda", "fecha",
             f"{campo}_pct"],
      " · ".join(x for x in (f"FCI por {periodo}", categoria, moneda, gerente) if x)),
  }


def ficha_fondo(fondo: str, con_serie: bool = False) -> dict:
  """Qué ES un FCI y cuánto rindió, identificado por ID, símbolo o nombre.

  NO devuelve la composición de la cartera del fondo: esa fuente no está en
  la plataforma. Con `con_serie` agrega hasta 400 días de VCP para dibujar.

  Args:
    fondo: `fci_id`, símbolo Primary, unidad o parte inequívoca del nombre.
    con_serie: true solo si el usuario pidió evolución o histórico del VCP.
  """
  from api.services import fci_sql

  try:
    universo = fci_sql.tabla().get("fondos") or []
  except Exception as e:
    return {"error": f"no pude leer los FCI: {type(e).__name__}: {e}"}
  pedido = str(fondo or "").strip()
  if not pedido:
    return {"error": "`fondo` está vacío"}
  clave = pedido.upper()
  exactos = [f for f in universo if clave in {
    str(f.get("fci_id") or "").upper(), str(f.get("simbolo_primary") or "").upper(),
    str(f.get("unidad") or "").upper(), str(f.get("nombre") or "").upper()}]
  candidatos = exactos or [f for f in universo if clave in str(f.get("nombre") or "").upper()]
  if len(candidatos) != 1:
    return {"error": (f"no encontré {pedido!r}" if not candidatos else
              f"{pedido!r} identifica más de un fondo"),
        "candidatos": [{"fci_id": f.get("fci_id"), "nombre": f.get("nombre"),
                 "gerente": f.get("gerente")} for f in candidatos[:20]]}
  try:
    detalle = fci_sql.ficha(int(candidatos[0]["fci_id"]))
  except Exception as e:
    return {"error": f"no pude leer la ficha: {type(e).__name__}: {e}"}
  if not detalle:
    return {"error": "el fondo ya no está en el universo seguido"}
  salida = {**_fila(detalle), "asset": detalle.get("asset"),
        "fuentes": detalle.get("fuentes"), "puntos_serie": len(detalle.get("serie") or [])}
  if con_serie:
    salida["serie"] = detalle.get("serie") or []
    salida["_tabla"] = pantalla.tabla(
      "serie", ["fecha", "vcp", "fuente"], f"Evolución VCP · {detalle.get('nombre')}")
  return salida

_INSTRUCCION = """
Hablás de FONDOS comunes de inversión: qué es un fondo, cuánto rinde y cuánto
tarda el rescate. Sin importar quién lo tenga. La plataforma no tiene la
composición interna del fondo: si la piden, decí que falta esa fuente.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="fondos",
    tarea="asistente_fondos",
    describe="fondos comunes de inversión (FCI): qué es un fondo, cuánto rinde, cuánto tarda "
         "el rescate y qué fondo de una clase rinde más.",
    instruccion=_instruccion,
    herramientas=(ranking_fondos, ficha_fondo),
    senales=("fondo", "fondos", "fci", "money market", "rescate", "suscripcion", "cuotaparte",
             "cuotapartes"),
    familia="mercado",
    foco=(),
)
