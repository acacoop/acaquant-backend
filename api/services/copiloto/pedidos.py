"""copiloto/pedidos.py — BUZÓN DE PEDIDOS (decisión user 2026-07-21).

La gente le dice al copiloto lo que le falta al producto mientras trabaja
("estaría bueno filtrar por cartera", "acá falta la columna X", "esto está
mal"). Antes eso se perdía en la conversación; ahora se registra.

Por qué así y no con un formulario: el que tiene la idea la dice DONDE está
trabajando, sin abrir un ticket ni cambiar de pantalla. La fricción es lo que
mata los buzones de sugerencias.

- La tool es COMÚN a todas las vistas (la suma el motor), así el pedido se
  puede hacer desde donde surgió — no solo desde la guía.
- El modelo CLASIFICA (tipo + título corto normalizado): eso es lo que hace
  revisables 40 pedidos de un vistazo, y es justo lo que un LLM hace bien.
- Se guarda el texto REAL de la persona: si la vista tiene aduana, acá se
  destokeniza antes de persistir (la tabla vive en nuestro perímetro).
- Revisión: `python -m scripts.gen_pedidos` los exporta a docs/PEDIDOS.md,
  versionado en git.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

NOMBRE_TOOL = "registrar_pedido"

_TIPOS = ("mejora", "falta_dato", "bug", "otro")
_MAX_TEXTO = 1500
_MAX_TITULO = 120

TOOL_PEDIDO = {
    "type": "function",
    "function": {
        "name": NOMBRE_TOOL,
        "description": (
            "Registra un PEDIDO DE PRODUCTO del usuario: una sugerencia, algo "
            "que falta, o algo que anda mal en la plataforma. Usala cuando la "
            "persona pide una mejora o reporta una falla en vez de hacer una "
            "consulta de datos — por ejemplo 'estaría bueno poder filtrar por "
            "cartera', 'falta la columna X', 'esto muestra mal el total'. NO la "
            "uses para preguntas normales (esas se contestan), ni para pedidos "
            "de datos que sí podés responder."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {
                    "type": "string",
                    "description": "El pedido con las palabras de la persona, completo.",
                },
                "titulo": {
                    "type": "string",
                    "description": "Título corto y claro que resuma el pedido "
                                   "(máx ~10 palabras), en infinitivo o sustantivo: "
                                   "'Filtro por cartera en Operaciones'.",
                },
                "tipo": {
                    "type": "string",
                    "enum": list(_TIPOS),
                    "description": "mejora = funcionalidad nueva · falta_dato = el "
                                   "dato no está o no se encuentra · bug = algo anda "
                                   "mal · otro.",
                },
            },
            "required": ["texto", "titulo", "tipo"],
        },
    },
}


def registrar(*, texto: str, titulo: str, tipo: str, usuario: str | None,
              vista: str | None, contexto: str | None = None) -> int | None:
    """Persiste el pedido. Devuelve el id o None (best-effort: un buzón que
    falla NO puede romper la respuesta del copiloto)."""
    texto = (texto or "").strip()[:_MAX_TEXTO]
    if not texto:
        return None
    tipo = tipo if tipo in _TIPOS else "otro"
    titulo = (titulo or texto)[:_MAX_TITULO].strip()
    try:
        from core.postgres import get_pool

        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO manager.pedidos (usuario, vista, tipo, titulo, texto, contexto)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (usuario, vista, tipo, titulo, texto,
                 (contexto or "").strip()[:_MAX_TEXTO] or None),
            )
            return int(cur.fetchone()[0])
    except Exception as e:
        logger.warning("pedidos: no pude registrar (%s)", e)
        return None


def ejecutar_pedido(args: dict, *, usuario: str | None, vista: str | None,
                    mapping: dict | None = None, contexto: str | None = None) -> str:
    """Ejecutor de la tool. Si la vista tiene aduana, el modelo nos pasa el
    texto TOKENIZADO — se destokeniza acá adentro (el buzón vive en nuestro
    perímetro y lo vamos a leer nosotros, queremos el texto real)."""
    texto = str(args.get("texto") or "")
    titulo = str(args.get("titulo") or "")
    if mapping:
        from core import pii_gateway

        texto = pii_gateway.detokenize(texto, mapping)
        titulo = pii_gateway.detokenize(titulo, mapping)
    pedido_id = registrar(texto=texto, titulo=titulo,
                          tipo=str(args.get("tipo") or "otro"),
                          usuario=usuario, vista=vista, contexto=contexto)
    if pedido_id is None:
        return ("no pude registrar el pedido — decile que lo anote por otro lado, "
                "sin inventar que quedó guardado")
    return (f"pedido #{pedido_id} registrado. Confirmáselo en UNA línea (que quedó "
            "anotado para revisión) y seguí con lo que estaba haciendo. No prometas "
            "fechas ni que se va a hacer.")
