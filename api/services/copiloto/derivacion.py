"""copiloto/derivacion.py — derivación entre vistas: el marcador [[VISTA:x]], el
bloque de otras vistas para el prompt, y los helpers de acceso RBAC
(vistas_para / puede_usar). Depende del registro VISTAS."""
from __future__ import annotations

import logging
import re

from .registro import VISTAS

logger = logging.getLogger(__name__)

_RE_VISTA_MARKER = re.compile(r"\s*\[\[VISTA:([a-z_]+)\]\]\s*")
# Cualquier cosa que PAREZCA el marcador, aunque esté malformada — el caso real
# 2026-07-20: el modelo escribió "[[VISTA:clave:ayuda]]" (copió la palabra
# "clave" del ejemplo de la instrucción) y la regex estricta no lo matcheaba →
# el marcador crudo LLEGÓ AL USUARIO. Todo residuo [[VISTA…]] se borra SIEMPRE.
_RE_VISTA_RESIDUO = re.compile(r"\s*\[\[\s*VISTA[^\]]*\]\]\s*", re.IGNORECASE)


def _bloque_otras_vistas(usuario: str | None, vista_actual: str) -> str:
    if not usuario:
        return ""
    try:
        from core.roles import has_access

        otras = [
            (clave, c) for clave, c in VISTAS.items()
            if clave != vista_actual and c.get("dominio")
            and has_access(usuario, c["modulo"])
        ]
    except Exception as e:
        logger.warning("copiloto: otras vistas no disponibles (%s)", e)
        return ""
    if not otras:
        return ""
    lineas = [f"- {c['titulo']} (clave: {clave}): {c['dominio']}" for clave, c in otras]
    return (
        "\n\nOTRAS VISTAS CON COPILOTO — para DERIVAR, jamás para responder por ellas:\n"
        + "\n".join(lineas)
        + "\nSi la pregunta pertenece a uno de esos dominios y no a esta vista, NO "
        "contestes solo \"eso no está en esta tabla\": respondé en UNA frase que esa "
        "consulta se hace desde la vista indicada (nombrala por su título) y no "
        "inventes ni un dato de ese dominio. Cerrá esa respuesta con el marcador en la "
        "última línea, usando EXACTAMENTE la palabra entre paréntesis de la lista de "
        "arriba: [[VISTA:renta_fija]], [[VISTA:ayuda]], etc. — nada más adentro de los "
        "corchetes. No es texto para el usuario: el panel lo convierte en un botón. "
        "Y si la pregunta es de NAVEGACIÓN (\"¿cómo/dónde VEO tal cosa en la "
        "plataforma?\"): NO adivines nombres de secciones — decí en una frase que eso "
        "te lo responde la Guía de la plataforma y cerrá con [[VISTA:ayuda]]. "
        "REGLA DE CIERRE: si la consulta pide un dato que NO tenés y su dominio "
        "TAMPOCO está en la lista de arriba (ej. operaciones de clientes, carteras, "
        "back office, cosas del negocio), la respuesta es SIEMPRE la misma: una frase "
        "(\"acá no tengo ese dato\") + [[VISTA:ayuda]] para que la Guía le muestre "
        "dónde vive. PROHIBIDO TERMINANTEMENTE inventar o adivinar nombres de vistas, "
        "secciones o módulos que no estén en la lista — nombrar una vista que no "
        "existe es peor que no responder."
    )


def _extraer_vista_sugerida(
    texto: str, vista_actual: str, usuario: str | None
) -> tuple[str, dict | None]:
    """Saca el marcador [[VISTA:x]] del texto y lo valida por código (existe,
    no es la vista actual, el usuario tiene acceso). Marcador inválido =
    se borra y no hay sugerencia — el modelo jamás manda a una puerta cerrada."""
    m = _RE_VISTA_MARKER.search(texto)
    if not m:
        # sin marcador BIEN formado: puede haber uno malformado ("[[VISTA:clave:
        # ayuda]]") — se intenta rescatar una clave registrada de su interior y,
        # pase lo que pase, TODO residuo [[VISTA…]] se borra del texto.
        m2 = _RE_VISTA_RESIDUO.search(texto)
        if not m2:
            return texto, None
        texto = _RE_VISTA_RESIDUO.sub("\n", texto).strip()
        clave = next((k for k in VISTAS if re.search(rf"\b{k}\b", m2.group(0))), None)
        if not clave:
            return texto, None
    else:
        texto = _RE_VISTA_RESIDUO.sub("\n", texto).strip()  # borra válidos Y residuos
        clave = m.group(1)
    cfg = VISTAS.get(clave)
    if not cfg or clave == vista_actual:
        return texto, None
    try:
        from core.roles import has_access

        if usuario and has_access(usuario, cfg["modulo"]):
            return texto, {"vista": clave, "titulo": cfg["titulo"]}
    except Exception as e:
        logger.warning("copiloto: no pude validar la vista sugerida (%s)", e)
    return texto, None


def vistas_para(email: str) -> list[dict]:
    """Vistas del copiloto que este usuario puede usar (gate por módulo RBAC
    de cada vista — el gate del módulo `ia` ya lo puso el montaje del router).
    Los alias (misma config bajo dos claves) se devuelven una sola vez."""
    from core.roles import has_access

    out, vistos = [], set()
    for clave, cfg in VISTAS.items():
        if id(cfg) in vistos or not has_access(email, cfg["modulo"]):
            continue
        vistos.add(id(cfg))
        out.append({"vista": clave, "titulo": cfg["titulo"],
                    "chips": cfg.get("chips") or []})
    return out


def puede_usar(email: str, vista: str) -> bool:
    from core.roles import has_access

    cfg = VISTAS.get(vista)
    return bool(cfg) and has_access(email, cfg["modulo"])


