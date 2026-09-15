"""Agente DÓLARES (familia mercado): el tipo de cambio. Sus herramientas y su
agente, en un solo archivo. Doc: docs/AvAgentAI.md.

La fuente es la MISMA que la watchlist de la home (`api.services.argy`): si el
asistente y la pantalla dijeran números distintos, no fallaría nada y los dos
parecerían tener razón (REGLA #9).
"""
from __future__ import annotations

from asistente import pantalla
from asistente.agente import COMUN, Agente

# Los tipos de cambio que sabemos contestar, con la etiqueta EXACTA que usa
# `api.services.argy.get_argy_with_returns()`. Si allá cambia una etiqueta, acá
# el dólar desaparece de la respuesta en vez de traer el valor de otro.
DOLARES = {
    "DOLAR MEP": "mep",
    "DOLAR CCL": "ccl",
    "DOLAR OFICIAL": "oficial",
}
# El canje no es un dólar, es la diferencia CCL/MEP en % — viaja aparte.
CANJE = "CANJE"


def _brecha(contra: float | None, base: float | None) -> float | None:
    """Cuánto está por encima del oficial, en %. La hace el código: el modelo
    tiene prohibido calcular, así que si no viene, no lo puede decir."""
    if contra is None or base is None or base == 0:
        return None
    return round((contra - base) / base * 100, 2)


def tipos_de_cambio() -> dict:
    """A cuánto está el dólar HOY: MEP, CCL y oficial, con su variación del día.

    Es el MISMO dato que la watchlist de la home, de la misma fuente. Contesta
    «a cuánto está el MEP», «cuánto subió el CCL hoy», «cuál es la brecha».

    NO es el dólar futuro (eso es ROFEX, otro agente) ni el histórico: es la
    foto de ahora.

    QUÉ DEVUELVE:
      · `dolares` — una fila por tipo: `nombre`, `valor` (en pesos),
        `variacion_dia_pct` (% del día, puede ser null si todavía no hay
        referencia de ayer), `desde` (de cuándo es el dato) y `fuente`.
      · `canje_pct` — el canje CCL/MEP en %, como lo publica la pantalla.
      · `brecha_mep_oficial_pct` y `brecha_ccl_oficial_pct` — contra el
        oficial, en %, YA calculadas.
      · `faltan` — los que la fuente no trajo en este momento: de ésos no hay
        valor, ni exacto ni aproximado.
    """
    try:
        from api.services.argy import get_argy_with_returns

        filas = {f.get("label"): f for f in get_argy_with_returns() or []}
    except Exception as e:
        return {"error": f"no pude leer los tipos de cambio: {type(e).__name__}: {e}"}

    salida, faltan, valores = [], [], {}
    for label, clave in DOLARES.items():
        f = filas.get(label)
        if not f or f.get("value") is None:
            faltan.append(clave)
            continue
        valores[clave] = float(f["value"])
        salida.append({
            "nombre": clave,
            "valor": round(float(f["value"]), 2),
            "variacion_dia_pct": f.get("ret_day"),
            "desde": f.get("ts"),
            "fuente": f.get("source"),
        })
    if not salida:
        return {"error": "la fuente de tipos de cambio no devolvió ningún valor ahora mismo",
                "faltan": faltan}
    canje = filas.get(CANJE) or {}
    return {
        "dolares": salida,
        "canje_pct": canje.get("value"),
        "brecha_mep_oficial_pct": _brecha(valores.get("mep"), valores.get("oficial")),
        "brecha_ccl_oficial_pct": _brecha(valores.get("ccl"), valores.get("oficial")),
        "faltan": faltan,
        "_tabla": pantalla.tabla(
            "dolares", ["nombre", "valor", "variacion_dia_pct"],
            "Tipos de cambio de hoy", moneda="ARS"),
    }


_INSTRUCCION = """
Hablás de DÓLARES: MEP, CCL, oficial, brechas entre ellos. El tipo de cambio,
no los bonos con los que se arma.

Los tres son valores DISTINTOS y cada uno tiene su nombre: nunca digas «el
dólar» a secas si mirás más de uno. Si te preguntan por uno solo, contestá ese
y no recites los otros.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="dolares",
    tarea="asistente_dolares",
    describe="el tipo de cambio: MEP, CCL, oficial, brechas. A cuánto está el dólar hoy.",
    instruccion=_instruccion,
    herramientas=(tipos_de_cambio,),
    senales=("mep", "ccl", "oficial", "brecha", "brechas", "tipo de cambio", "blue", "el dolar",
             "del dolar", "dolar hoy", "dolar futuro"),
    familia="mercado",
    foco=(),
)
