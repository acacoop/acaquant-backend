"""copiloto/verificacion.py — guardrails estructurales (anti-alucinación).

Verificación mecánica de números (todo número del output debe existir en el
contexto), detector de jerga interna y de derrame de razonamiento. Código, no
prompt: el patrón rector del copiloto (lo que el modelo rompe dos veces baja a
código). Autónomo — no importa otros submódulos.
"""
from __future__ import annotations

import re

_RE_NUM = re.compile(r"(-?\d[\d.,]*)(?:\s*([kKmMbB])(?![A-Za-z]))?")
_ESCALAS = {"k": 1e3, "m": 1e6, "b": 1e9}


def _candidatos_numericos(token: str) -> list[float]:
    """Interpretaciones posibles de un número escrito: '10.793' puede ser
    diez mil setecientos noventa y tres (formato es-AR, el de la mesa) o
    10.793 — se prueban AMBAS contra el contexto. Bug real del shadow: la
    vista trading (precios en miles) quedaba bloqueada entera porque el
    modelo escribía a la argentina y el parser leía decimales."""
    # Puntuación de FRASE pegada al final ("operó 634.100, y…" → token
    # '634.100,'): rompía TODOS los parseos y bloqueaba respuestas correctas
    # (batería ONs 2026-07-14 — 3 de las 4 fallas eran esto).
    token = token.strip().lstrip("-").rstrip(".,")
    out: list[float] = []
    # es-AR: 1.234.567,89 o 10.793 (puntos de miles, coma decimal)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", token):
        try:
            out.append(float(token.replace(".", "").replace(",", ".")))
        except ValueError:
            pass
    # coma decimal simple: 6,65
    if re.fullmatch(r"\d+,\d+", token):
        try:
            out.append(float(token.replace(",", ".")))
        except ValueError:
            pass
    # lectura literal (punto decimal, sin separadores)
    try:
        out.append(float(token.replace(",", "")))
    except ValueError:
        pass
    return out


def _numeros_sin_respaldo(respuesta: str, contexto: str) -> tuple[list[str], int]:
    """(lista de números sin respaldo tal como aparecen, total_chequeados).
    Un número 'tiene respaldo' si ALGUNA de sus interpretaciones (literal,
    formato es-AR, abreviación K/M/B) aparece en el contexto con tolerancia
    de redondeo. Se ignoran enteros chicos (rankings, cantidades) y años."""
    ctx: set[float] = set()
    for m in _RE_NUM.finditer(contexto):
        try:
            ctx.add(abs(float(m.group(1).replace(",", ""))))
        except ValueError:
            continue
    total = 0
    malos: list[str] = []
    for m in _RE_NUM.finditer(respuesta):
        candidatos = _candidatos_numericos(m.group(1))
        if not candidatos:
            continue
        sufijo = (m.group(2) or "").lower()
        es_entero = all(float(c).is_integer() for c in candidatos)
        if not sufijo and es_entero and all(c <= 31 or 1900 <= c <= 2100 for c in candidatos):
            continue  # rankings, cantidades, fechas
        total += 1
        if sufijo:
            candidatos = candidatos + [c * _ESCALAS[sufijo] for c in candidatos]
        # Redondeo legítimo: "5,9%" cuando el dato es 5.85 es media unidad del
        # último decimal escrito — se acepta (batería home 2026-07-12: el
        # modelo redondea a 1 decimal y la respuesta moría). "4,2" para 4.11
        # NO pasa: eso es un redondeo mal hecho, sigue siendo error.
        frac = re.search(r"[.,](\d+)\s*$", m.group(1))
        tol_redondeo = 0.51 * 10 ** -len(frac.group(1)) if frac else 0.0

        def _match(c: float) -> bool:
            for v in candidatos:  # noqa: B023 — se consume dentro del mismo loop
                if abs(c - v) <= max(0.011, 0.001 * v, tol_redondeo):  # noqa: B023
                    return True
                if v.is_integer() and round(c) == v:
                    return True
                if sufijo and abs(c - v) <= 0.015 * v:  # noqa: B023
                    return True
            return False

        if not any(_match(c) for c in ctx):
            malos.append(m.group(1) + (m.group(2) or ""))
    return malos, total


# Términos internos que JAMÁS deben llegar al usuario. El prompt ya lo pide,
# pero el modelo lo rompe cada tanto (shadow: "ret_7d", "monto_usd_ny") →
# guardrail estructural: se detectan por código y disparan la auto-corrección.
_JERGA_FIJA = {"es_ia", "adr", "tsv", "zona_piv", "piv_anual", "piv_mensual",
               "z-score", "zscore", "mtd", "wtd", "ytd", "columna", "header"}

# Derrame de razonamiento en la respuesta visible (autocorrecciones tipo
# "Corrijo:", "— no, X también…"): dispara la misma reescritura.
_RE_DERRAME = re.compile(
    r"[Cc]orrijo|[Rr]evisar:|[Pp]erd[óo]n|—\s*no,|[Ee]ntonces respuesta final"
)
# palabras de mesa legítimas aunque coincidan con headers
_NO_ES_JERGA = {"nombre", "sector", "rubro", "pais", "ticker", "ratio", "vwap",
                "spread", "compra", "venta", "nominales", "ia", "subyacente"}


def _jerga_en_respuesta(respuesta: str, cfg: dict, pregunta: str) -> list[str]:
    """Headers/campos del contexto que se colaron en la respuesta. Un término
    queda permitido si el USUARIO lo usó en su pregunta (si él habla de
    'zona_piv', se le puede contestar igual)."""
    resp = respuesta.lower()
    preg = (pregunta or "").lower()
    terminos: set[str] = set(_JERGA_FIJA)
    for c in cfg.get("columnas") or []:
        campo, header = c if isinstance(c, tuple) else (c, c)
        terminos.update((campo.strip("%").lower(), header.strip("%").lower()))
    permitidos_vista = {str(t).lower() for t in cfg.get("jerga_permitida") or ()}
    out = []
    for t in sorted(terminos - _NO_ES_JERGA - permitidos_vista):
        if len(t) < 3 or t in preg:
            continue
        # OJO: el "%" NO delimita — "ret_año%" en la respuesta ES el término
        # "ret_año" (bug real del shadow: el % del header lo hacía invisible)
        if re.search(rf"(?<![a-z0-9_]){re.escape(t)}(?![a-z0-9_])", resp):
            out.append(t)
    # Nomenclatura de pivots (PP/R1-R3/S1-S3): prohibida salvo que el usuario
    # hable de pivots/niveles ("zona >R3 anual" seguía apareciendo — R1/S3
    # esquivaban el filtro de longitud mínima). En vistas de trading es el
    # idioma nativo (cfg permitir_pivots) y no se filtra.
    if not cfg.get("permitir_pivots") and not re.search(r"pivot|nivel|\bpp\b|\b[rs][1-3]\b", preg):
        if re.search(r"\b(?:PP|[RS][1-3])\b", respuesta):
            out.append("nomenclatura de pivots (PP/R1/S3)")
        # …y también DELETREADA: el modelo esquivaba el filtro escribiendo
        # "punto pivote semanal", "resistencia anual en 36.96" (caso EWZ
        # 2026-07-20). Fuera de trading, los niveles técnicos no se nombran:
        # se piensan y se traducen ("no está extendido", "zona razonable de
        # entrada, no estás comprando un techo").
        if _RE_NIVELES_NOMBRADOS.search(respuesta):
            out.append("niveles técnicos con nombre (punto pivote/resistencia/soporte) — "
                       "traducilos: 'no está extendido', 'zona razonable de entrada'")
    # Estadística NOMBRADA (beta/z-score/correlación/volatilidad anualizada):
    # la regla del system exige traducirla y el modelo la siguió nombrando
    # (caso EWZ 2026-07-20: "beta 0.74, correlación 0.41, vol anualizada
    # 23.5% a 60 ruedas") → baja a código. Permitida SOLO si el usuario usó
    # el término en su pregunta.
    for m in _RE_ESTAT_NOMBRADA.finditer(respuesta):
        t = m.group(0).lower()
        if t not in preg:
            out.append(f"'{m.group(0)}' (estadística nombrada: traducila a lenguaje "
                       "de mesa, sin el término técnico ni su valor crudo)")
            break  # con señalar una alcanza para la reescritura
    return out


# CUALQUIER mención de pivots/equilibrio fuera de trading (v1.67: "zona de
# pivots anual" y "quedó en equilibrio" esquivaban la regex de formas
# puntuales — se prohíbe la familia entera de términos). "inflación de
# equilibrio" queda exenta (es el lenguaje legítimo de breakevens en RF).
_RE_NIVELES_NOMBRADOS = re.compile(
    r"\bpivots?\b|\bpivotes?\b|"
    r"(?<!inflación de )\bequilibrio\b|"
    r"(?:resistencia|soporte)\s+(?:anual|mensual|semanal|diaria?|de\s+corto|de\s+largo)",
    re.IGNORECASE)

_RE_ESTAT_NOMBRADA = re.compile(
    r"z[- ]?score|\bbeta\b|correlaci[oó]n|volatilidad\s+anualizada|"
    r"vol\.?\s+anualizada|desv[ií]o\s+est[aá]ndar|a\s+\d+\s+ruedas",
    re.IGNORECASE)


def _periodos_sin_respaldo(respuesta: str, contexto: str) -> list[str]:
    """Años mencionados en la respuesta que NO existen en los datos. El caso
    EWZ (2026-07-20): el modelo justificó una recomendación con "un 2025
    flojo" — año inventado (los datos arrancan en 2026). Los años están
    excluidos del chequeo numérico a propósito (fechas) → este chequeo aparte
    cierra esa ventana: si el año no aparece en el contexto, el modelo no
    puede afirmar NADA sobre ese período."""
    anios_ctx = set(re.findall(r"\b(19[89]\d|20[0-9]\d)\b", contexto))
    return sorted({a for a in re.findall(r"\b(19[89]\d|20[0-9]\d)\b", respuesta)
                   if a not in anios_ctx})


def _exceso_de_cifras(respuesta: str, pregunta: str) -> int:
    """Cifras 'de dato' en la respuesta (excluye enteros chicos y años). El
    caso EWZ (2026-07-20): pregunta por UN papel respondida con 8+ números —
    ilegible. Si la pregunta NO pide lista/ranking/tabla, más de 5 cifras es
    exceso → devuelve el conteo para el mensaje de autocorrección (0 = ok)."""
    if re.search(r"top|ranking|tabla|cu[aá]les|list[aá]|mejores|peores|compar",
                 (pregunta or "").lower()):
        return 0
    n = 0
    for m in _RE_NUM.finditer(respuesta):
        try:
            v = abs(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
        if not m.group(2) and v.is_integer() and (v <= 31 or 1900 <= v <= 2100):
            continue
        n += 1
    return n if n > 5 else 0


# ── Derivación a otras vistas (pedido del user 2026-07-12) ──────────────────
# "¿Qué bono rinde más?" preguntado en Renta Variable moría en "eso no está en
# esta tabla". El modelo no puede RESPONDER fuera de su vista (no tiene esos
# datos), pero sí puede DECIR dónde se responde: el prompt recibe la lista de
# las otras vistas con copiloto que el usuario puede usar (RBAC — jamás derivar
# a una puerta cerrada) y, si deriva, cierra con un marcador [[VISTA:clave]]
# que el CÓDIGO valida contra el registro y convierte en botón en el panel.



# ── Batería completa + autocorrección (compartida por preguntar() del copiloto;
#    el asistente reusa el FRAME del prompt con su propia regla — ver
#    asistente._verificar_cifras: permite aritmética con '~' y compara con <=,
#    diferencias A PROPÓSITO, no unificar) ─────────────────────────────────────


def detectar_problemas(texto: str, contexto: str, cfg: dict, pregunta: str) -> dict:
    """Corre los 5 detectores sobre una respuesta. Devuelve las detecciones
    CRUDAS (la política estricta y el keep-best las necesitan, no solo los
    mensajes): {malos, chequeados, jerga, derrame, exceso, fantasmas}.
    Antes esta batería estaba copy-pasteada dos veces en motor.preguntar()
    (pre y post reintento)."""
    malos, chequeados = _numeros_sin_respaldo(texto, contexto)
    return {
        "malos": malos,
        "chequeados": chequeados,
        "jerga": _jerga_en_respuesta(texto, cfg, pregunta),
        "derrame": bool(_RE_DERRAME.search(texto)),
        "exceso": _exceso_de_cifras(texto, pregunta),
        "fantasmas": _periodos_sin_respaldo(texto, contexto),
    }


def score_problemas(det: dict) -> int:
    """Score para la regla 'la corregida solo gana si NO empeoró' — misma
    aritmética que usaba preguntar() inline: exceso cuenta binario."""
    return (len(det["malos"]) + len(det["jerga"]) + int(det["derrame"])
            + int(bool(det["exceso"])) + len(det["fantasmas"]))


def mensajes_de_problemas(det: dict) -> list[str]:
    """Detecciones → instrucciones de corrección para el modelo (texto EXACTO
    que venía inline en preguntar())."""
    problemas: list[str] = []
    if det["malos"]:
        problemas.append(
            f"estos números NO aparecen en los datos: {', '.join(det['malos'])} — usá solo "
            "números exactos de los datos (si abreviás un monto con M, redondeá el "
            "real; y si redondeás un %, redondeá AL MÁS CERCANO con un decimal: "
            "-83.56 se escribe -83.6, jamás -83)"
        )
    if det["jerga"]:
        problemas.append(
            "usaste jerga interna del sistema que el usuario JAMÁS debe ver: "
            f"{', '.join(det['jerga'])} — traducila a lenguaje de mesa"
        )
    if det["derrame"]:
        problemas.append(
            "mostraste correcciones o razonamiento intermedio — entregá SOLO la "
            "respuesta final, limpia"
        )
    if det["exceso"]:
        problemas.append(
            f"usaste {det['exceso']} cifras para una pregunta puntual — elegí MÁXIMO 3 "
            "números (los que sostienen la conclusión) y contá el resto en "
            "palabras (fuerte, apenas, casi plano); la respuesta tiene que "
            "leerse de un tirón"
        )
    if det["fantasmas"]:
        problemas.append(
            f"afirmaste algo sobre {', '.join(det['fantasmas'])} pero tus datos NO tienen "
            "ese período — eliminá TODA referencia y juicio sobre períodos que no "
            "están en los datos (no los reemplaces por otra afirmación inventada)"
        )
    return problemas


def prompt_autocorreccion(contexto: str, texto: str, detalle: str) -> str:
    """FRAME estándar del prompt de autocorrección (compartido con el
    asistente): contexto + respuesta previa + verificación + consigna de
    reescritura. `detalle` es la instrucción específica (los problemas del
    copiloto, o la regla de cifras/aritmética del asistente)."""
    return (
        f"{contexto}\n[tu respuesta previa]\n{texto}\n"
        f"[verificación automática] {detalle} Reescribí la respuesta "
        "COMPLETA corregida, mismo formato y largo, sin mencionar esta corrección."
    )
