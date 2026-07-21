"""core/pii_gateway.py — la ADUANA de datos privados hacia el LLM (asistente de negocio).

TODO texto que sale hacia el proveedor LLM (o vuelve de él) pasa por acá.
Garantía que implementa (decisión del user 2026-07-21, ver docs/QUANTAI.md P7):
las IDENTIDADES de clientes (nombres, números de cuenta, documentos) JAMÁS
salen del perímetro — se reemplazan por FICHAS opacas (CLIENTE_1, CTA_2,
DOC_3). Las cifras (AuM, P&L) viajan pseudonimizadas: atadas a una ficha,
imposibles de vincular a una persona real desde afuera.

API:
  tokenize(texto, mapping)   → (texto_limpio, mapping)  — tacha identidades.
  detokenize(texto, mapping) → texto                    — restaura fichas.
  cargar_mapping / guardar_mapping                      — persistencia por chat
                                                          (manager.asistente_mappings,
                                                          TTL 48h, nunca sale).
  id_cuenta_de_ficha(ficha, mapping)                    — resolución ficha→id
                                                          DENTRO del perímetro
                                                          (la usan las tools).
  catalogo_disponible()                                 — para el fail-closed
                                                          del orquestador.

DETECCIÓN EN CAPAS (defensa en profundidad — el orden importa):
  1. Números identificatorios (regex): CUIT/CUIL/DNI, "cuenta 805",
     ids de cuenta del catálogo como palabra suelta.
  2. Catálogo real de clientes (clientes.cuentas + clientes.comitentes):
     nombre completo, tokens sueltos del nombre (apellido solo), inicial +
     apellido ("J. Pérez") y fuzzy contra typos.
  3. Enmascarado defensivo conservador: pares de palabras Capitalizadas que
     PARECEN nombre propio se tachan aunque no estén en el catálogo (mejor
     tachar de más). Vocabulario de mercado whitelisteado.

⚠️ CALIBRACIÓN PENDIENTE (REGLA #2 — no inventar umbrales a ojo): el umbral
del fuzzy y la stoplist de palabras genéricas dependen de CÓMO son los
nombres reales de prod (que Claude no puede ver). Defaults conservadores;
el diag read-only `scripts/diag_pii_matcher.py` mide contra el catálogo real
y el user ajusta:
  ASISTENTE_FUZZY_UMBRAL   — ratio difflib mínimo del fuzzy (default 0.90;
                             más alto = menos tachaduras por similitud).
  ASISTENTE_STOPLIST_EXTRA — palabras extra (coma-separadas) que NUNCA
                             disparan match de catálogo por token suelto
                             (ej. genéricos societarios que el diag revele).

Postura ante fallos: si el catálogo NO se puede leer, la capa 1 y 3 siguen
pero la garantía completa no está → `catalogo_disponible()` devuelve False y
el orquestador se niega a responder (fail-closed). Acá NUNCA se levanta
excepción hacia el caller.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
import unicodedata

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

_MIN_TOKEN_LEN = 4        # tokens de nombre más cortos no disparan match suelto
_MAX_TEXTO_CHARS = 8000   # techo de texto a procesar (un mensaje de chat sobra)
_CATALOGO_TTL_S = 600
_MAPPING_TTL_HORAS = 48   # TTL del mapping persistido (espejo del chat, corto)

# Palabras que JAMÁS disparan match de catálogo por token suelto (ni como
# token del catálogo ni como candidato del texto): genéricos societarios +
# vocabulario de NEGOCIO que aparece en las preguntas normales de un jefe
# ("¿cuál es el AuM total administrado?" no puede tacharse — incidente del
# primer smoke 2026-07-21). Se EXTIENDE con lo que revele el diag (env
# ASISTENTE_STOPLIST_EXTRA) — no se achica sin correr el diag de nuevo.
_STOPLIST_BASE = {
    "cooperativa", "agricola", "agropecuaria", "ganadera", "limitada",
    "sociedad", "anonima", "hermanos", "sucesion", "sucesores", "grupo",
    "agro", "campo", "campos", "cereales", "granos", "semillas",
    "establecimiento", "estancia", "estancias", "agroindustrial",
    "comercial", "industrial", "servicios", "inversiones", "consultora",
    "asociacion", "federacion", "mutual", "centro", "union", "casa",
    "santa", "santo", "gral", "general", "norte", "sur", "este", "oeste",
    # vocabulario de negocio (preguntas típicas — jamás son un cliente)
    "total", "totales", "administrado", "administrada", "administradora",
    "administracion", "resumen", "rendimiento", "rendimientos", "cartera",
    "carteras", "patrimonio", "saldo", "saldos", "fondo", "fondos",
    "comision", "comisiones", "arancel", "aranceles", "movimiento",
    "movimientos", "operaciones", "valores", "inversora", "nacional",
    "provincial", "renta", "fija", "variable", "ahorro", "pesos", "plus",
    "abierto", "abierta", "mixta", "mixto", "pymes", "acciones", "bonos",
}

# Sufijos societarios: no identifican a nadie por sí solos (quedan FUERA del
# índice de tokens) pero se ABSORBEN en la tachadura cuando siguen a un
# nombre matcheado ("Molinos Rio SA" → CLIENTE_1 entero, sin dejar el "SA").
_SUFIJOS_SOCIETARIOS = {
    "sa", "s.a", "s.a.", "srl", "s.r.l", "s.r.l.", "sacif", "s.a.c.i.f",
    "saic", "saica", "sca", "scs", "ltda", "ltda.", "ltd", "inc", "llc",
    "sas", "s.a.s", "s.a.s.", "sau", "s.a.u", "bvsa", "coop",
}
_SUFIJO_RE = re.compile(
    r"^(?:[\s,]+(?:s\.?a\.?(?:c\.?i\.?f\.?)?(?:u\.?|s\.?)?|s\.?r\.?l\.?|ltda\.?|ltd\.?"
    r"|inc\.?|llc|saic|saica|coop\.?|bvsa))+", re.IGNORECASE)

# Vocabulario de dominio que la capa DEFENSIVA no tacha aunque venga
# Capitalizado ("Renta Fija", "Banco Nación", "Buenos Aires"...).
_WHITELIST_DEFENSIVA = {
    "renta", "fija", "variable", "merval", "rofex", "byma", "matba", "mae",
    "dolar", "dólar", "peso", "pesos", "banco", "nacion", "nación", "central",
    "buenos", "aires", "argentina", "argentino", "cedear", "cedears", "adr",
    "lecap", "lecaps", "boncer", "bopreal", "bonar", "bonares", "global",
    "globales", "caucion", "caución", "cauciones", "futuro", "futuros",
    "opcion", "opción", "opciones", "mercado", "mercados", "tesoro",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
    "lunes", "martes", "miercoles", "miércoles", "jueves", "viernes",
    "aum", "carry", "trade", "research", "manager",
}

_FICHA_RE = re.compile(r"\b(CLIENTE|CTA|DOC)_(\d+)\b")


def _fuzzy_umbral() -> float:
    """Umbral del fuzzy — CALIBRADO con el diag el 2026-07-21 (catálogo real,
    1907 tokens): a 0.90 el 14% de los tokens confunde OTRO apellido; a 0.95
    solo el 1%. Default 0.95 (medido, no a ojo). Override: ASISTENTE_FUZZY_UMBRAL."""
    try:
        return float(os.getenv("ASISTENTE_FUZZY_UMBRAL", "0.95"))
    except ValueError:
        return 0.95


def _token_max_clientes() -> int:
    """Corte por FRECUENCIA (calibrable): un token que aparece en más de N
    clientes distintos no identifica a nadie (el diag midió 'ltda' en 97,
    'renta' en 84, nombres de pila en 20-54) → queda FUERA del índice de
    match. Auto-stoplist basada en datos. Override: ASISTENTE_TOKEN_MAX_CLIENTES."""
    try:
        return int(os.getenv("ASISTENTE_TOKEN_MAX_CLIENTES", "5"))
    except ValueError:
        return 5


def _stoplist() -> set[str]:
    extra = {w.strip().lower() for w in os.getenv("ASISTENTE_STOPLIST_EXTRA", "").split(",")
             if w.strip()}
    return _STOPLIST_BASE | extra


def _norm(s: str) -> str:
    """minúsculas + sin acentos + espacios colapsados — la clave de matching."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


# ── Catálogo real de clientes (perímetro) ────────────────────────────────────

_catalogo_cache: dict = {"ts": 0.0, "valor": None}


def _leer_catalogo_sql() -> dict:
    """Lee clientes.cuentas (+ documentos de comitentes) y arma los índices de
    matching. Estructura:
      ids        — set de id_cuenta ("805")
      nombres    — {nombre_normalizado: id_cuenta} (denominación sin el "[805] ")
      tokens     — {token_normalizado: id_cuenta} (palabras del nombre, len≥4,
                    fuera de la stoplist)
      documentos — set de nro_doc normalizados (solo dígitos)
    """
    from core.postgres import get_pool

    ids: set[str] = set()
    nombres: dict[str, str] = {}
    duenios_por_token: dict[str, set[str]] = {}
    documentos: set[str] = set()
    stop = _stoplist()
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.id_cuenta, c.denominacion, m.nro_doc "
            "FROM clientes.cuentas c LEFT JOIN clientes.comitentes m USING (id_cuenta)"
        )
        for id_cuenta, denominacion, nro_doc in cur.fetchall():
            if id_cuenta:
                ids.add(str(id_cuenta).strip())
            if nro_doc:
                solo_digitos = re.sub(r"\D", "", str(nro_doc))
                if len(solo_digitos) >= 7:
                    documentos.add(solo_digitos)
            if not denominacion:
                continue
            # "[805] NOMBRE APELLIDO" → "NOMBRE APELLIDO"
            nombre = re.sub(r"^\[?\s*\d+\s*\]?\s*", "", str(denominacion)).strip()
            n = _norm(nombre)
            if len(n) < 3:
                continue
            idc = str(id_cuenta).strip()
            nombres[n] = idc
            # el nombre SIN sufijos societarios también matchea completo
            # ("Molinos Rio" pregunta vs "molinos rio s.a." catálogo)
            sin_suf = " ".join(t for t in n.split() if t not in _SUFIJOS_SOCIETARIOS)
            if sin_suf and sin_suf != n and len(sin_suf) >= 3:
                nombres.setdefault(sin_suf, idc)
            for tok in n.split():
                if (len(tok) >= _MIN_TOKEN_LEN and tok not in stop
                        and tok not in _SUFIJOS_SOCIETARIOS and not tok.isdigit()):
                    duenios_por_token.setdefault(tok, set()).add(idc)
    # Corte por FRECUENCIA (auto-stoplist medida): token en >N clientes no
    # identifica → fuera del índice. 2..N dueños → "" (detecta, no resuelve).
    corte = _token_max_clientes()
    tokens = {
        tok: (duenios.copy().pop() if len(duenios) == 1 else "")
        for tok, duenios in duenios_por_token.items()
        if len(duenios) <= corte
    }
    return {"ids": ids, "nombres": nombres, "tokens": tokens, "documentos": documentos}


def _catalogo() -> dict | None:
    """Catálogo cacheado (TTL 600s). None = no disponible (DB caída y sin
    valor previo) → el orquestador debe negarse a responder (fail-closed)."""
    ahora = time.monotonic()
    if _catalogo_cache["valor"] is not None and ahora - _catalogo_cache["ts"] < _CATALOGO_TTL_S:
        return _catalogo_cache["valor"]
    try:
        valor = _leer_catalogo_sql()
        _catalogo_cache.update(ts=ahora, valor=valor)
        return valor
    except Exception as e:
        logger.warning("pii_gateway: no pude leer el catálogo de clientes (%s)", e)
        return _catalogo_cache["valor"]  # último conocido, o None


def catalogo_disponible() -> bool:
    """False = la aduana no puede garantizar la capa de catálogo → el
    asistente NO debe responder (fail-closed, lo aplica el orquestador)."""
    return _catalogo() is not None


def invalidar_catalogo() -> None:
    _catalogo_cache.update(ts=0.0, valor=None)


# ── Mapping (la tabla de traducción — VIVE EN EL PERÍMETRO) ─────────────────

def _mapping_nuevo() -> dict:
    return {"fichas": {}, "valores": {}, "contadores": {}}


def _asignar_ficha(mapping: dict, tipo: str, valor_original: str) -> str:
    """Ficha ESTABLE: el mismo valor (normalizado) recibe siempre la misma
    ficha dentro del mapping. La primera forma vista queda como canónica
    para detokenizar."""
    clave = f"{tipo}:{_norm(valor_original)}"
    existente = mapping["valores"].get(clave)
    if existente:
        return existente
    n = int(mapping["contadores"].get(tipo, 0)) + 1
    mapping["contadores"][tipo] = n
    ficha = f"{tipo}_{n}"
    mapping["valores"][clave] = ficha
    mapping["fichas"][ficha] = valor_original
    return ficha


def detokenize(texto: str, mapping: dict) -> str:
    """Restaura las fichas a sus valores reales. Fichas desconocidas quedan
    tal cual (jamás inventar un nombre)."""
    if not texto:
        return texto

    def _rep(m: re.Match) -> str:
        return mapping.get("fichas", {}).get(m.group(0), m.group(0))

    return _FICHA_RE.sub(_rep, texto)


# ── tokenize: las 3 capas ────────────────────────────────────────────────────

# Palabra "de nombre": letras (con acentos), 2+ chars, con al menos una minúscula
# o toda mayúscula (los nombres en el catálogo suelen venir en MAYÚSCULAS).
_PALABRA_RE = re.compile(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{2,}")
_CUIT_RE = re.compile(r"\b\d{2}-?\d{7,9}-?\d\b")
_DIGITOS_LARGOS_RE = re.compile(r"\b\d{7,11}\b")
_CUENTA_KEYWORD_RE = re.compile(
    r"\b(?:cuenta|comitente|cta|cte)\.?\s*(?:n[°ºo]?\.?\s*)?(\d{1,8})\b", re.IGNORECASE)
# "dni 20.123.456", "cuit 20-12345678-9": el keyword es la señal — un número
# con puntos SIN keyword puede ser un monto y no se tacha (salvo catálogo).
_DOC_KEYWORD_RE = re.compile(
    r"\b(?:dni|documento|doc|cuit|cuil)\.?\s*(?:n[°ºo]?\.?\s*)?(\d[\d.\-]{5,14}\d)",
    re.IGNORECASE)
# Inicial de nombre ("J. Pérez"): letra suelta + punto al final del texto
# previo. El caller verifica que ANTES haya espacio/inicio (no comerse la
# última letra de una palabra como "cliente").
_INICIAL_RE = re.compile(r"[A-Za-zÁÉÍÓÚÑ]\.\s*$")
# Par (o más) de palabras Capitalizadas consecutivas — la forma de un nombre
# propio (con conectores: "Juan de Souza"). NO matchea tickers (AL30, GGAL:
# sin minúsculas después de la 1ra) ni palabras sueltas (el arranque de
# oración capitaliza y sería tachar todo).
_NOMBRE_PROPIO_RE = re.compile(
    r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñü]{2,}"
    r"(?:(?:\s+(?:de|del|la|los|las|y|e))?\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñü]{2,})+")


def _spans_numeros(texto: str, catalogo: dict | None) -> list[tuple[int, int, str, str]]:
    """Capa 1: CUIT/DNI, 'cuenta N', ids del catálogo como palabra suelta."""
    spans: list[tuple[int, int, str, str]] = []
    for m in _CUIT_RE.finditer(texto):
        spans.append((m.start(), m.end(), "DOC", m.group(0)))
    for m in _DIGITOS_LARGOS_RE.finditer(texto):
        spans.append((m.start(), m.end(), "DOC", m.group(0)))
    for m in _CUENTA_KEYWORD_RE.finditer(texto):
        spans.append((m.start(1), m.end(1), "CTA", m.group(1)))
    for m in _DOC_KEYWORD_RE.finditer(texto):
        spans.append((m.start(1), m.end(1), "DOC", m.group(1)))
    if catalogo:
        for m in re.finditer(r"\b\d{1,6}\b", texto):
            if m.group(0) in catalogo["ids"]:
                spans.append((m.start(), m.end(), "CTA", m.group(0)))
        # documentos del catálogo escritos con puntos ("20.123.456")
        for m in re.finditer(r"\b\d{1,3}(?:\.\d{3}){2,3}\b", texto):
            if re.sub(r"\D", "", m.group(0)) in catalogo["documentos"]:
                spans.append((m.start(), m.end(), "DOC", m.group(0)))
    return spans


def _spans_catalogo(texto: str, catalogo: dict) -> list[tuple[int, int, str, str]]:
    """Capa 2: nombres del catálogo — n-gramas completos, tokens sueltos
    (apellido), inicial+apellido y fuzzy por token."""
    spans: list[tuple[int, int, str, str]] = []
    palabras = [(m.start(), m.end(), m.group(0)) for m in _PALABRA_RE.finditer(texto)]
    normales = [_norm(p[2]) for p in palabras]
    umbral = _fuzzy_umbral()
    stop = _stoplist()
    tokens_idx = catalogo["tokens"]
    claves_tokens = list(tokens_idx.keys())

    # n-gramas (4→2 palabras, el más largo gana) contra nombres completos
    usadas: set[int] = set()
    for largo in (4, 3, 2):
        for i in range(len(palabras) - largo + 1):
            if any(j in usadas for j in range(i, i + largo)):
                continue
            frase = " ".join(normales[i:i + largo])
            if frase in catalogo["nombres"]:
                ini, fin = palabras[i][0], palabras[i + largo - 1][1]
                spans.append((ini, fin, "CLIENTE", texto[ini:fin]))
                usadas.update(range(i, i + largo))

    # tokens sueltos + fuzzy — el vocabulario de negocio/sufijos JAMÁS es
    # candidato (lado texto): "total"/"administrado" de una pregunta normal
    # no puede terminar tachado (incidente del primer smoke 2026-07-21)
    for i, (ini, fin, _cruda) in enumerate(palabras):
        if i in usadas:
            continue
        n = normales[i]
        if len(n) < _MIN_TOKEN_LEN or n in stop or n in _SUFIJOS_SOCIETARIOS:
            continue
        match = n in tokens_idx
        if not match and claves_tokens:
            # typo → fuzzy contra los tokens del catálogo (umbral CALIBRABLE)
            cerca = difflib.get_close_matches(n, claves_tokens, n=1, cutoff=umbral)
            match = bool(cerca)
        if match:
            # "J. Pérez" → la inicial previa entra en la tachadura (solo si
            # es una letra SUELTA: precedida de espacio o inicio de texto)
            previo = texto[:ini]
            m_ini = _INICIAL_RE.search(previo)
            if m_ini and (m_ini.start() == 0 or previo[m_ini.start() - 1].isspace()):
                ini = m_ini.start()
            spans.append((ini, fin, "CLIENTE", texto[ini:fin]))
    return _absorber_sufijos(texto, spans)


def _absorber_sufijos(texto: str,
                      spans: list[tuple[int, int, str, str]]) -> list[tuple[int, int, str, str]]:
    """'Molinos Rio SA' → la tachadura del nombre se EXTIENDE sobre el sufijo
    societario que lo sigue: el 'SA' suelto al lado de una ficha delata la
    forma societaria del cliente (leak del primer no-leak e2e, 2026-07-21)."""
    extendidos = []
    for ini, fin, tipo, _valor in spans:
        m = _SUFIJO_RE.match(texto[fin:])
        if m:
            fin += m.end()
        extendidos.append((ini, fin, tipo, texto[ini:fin]))
    return extendidos


def _spans_defensivos(texto: str) -> list[tuple[int, int, str, str]]:
    """Capa 3: lo que PARECE nombre propio (par Capitalizado) se tacha aunque
    no esté en el catálogo — mejor tachar de más. Whitelist de dominio."""
    spans = []
    for m in _NOMBRE_PROPIO_RE.finditer(texto):
        tokens = [_norm(t) for t in _PALABRA_RE.findall(m.group(0))]
        if tokens and all(t in _WHITELIST_DEFENSIVA for t in tokens):
            continue
        spans.append((m.start(), m.end(), "CLIENTE", m.group(0)))
    return spans


def _aplicar_spans(texto: str, spans: list[tuple[int, int, str, str]], mapping: dict) -> str:
    """Reemplaza los spans por fichas, de derecha a izquierda, descartando
    solapados (gana el que empieza antes; a igual inicio, el más largo)."""
    elegidos: list[tuple[int, int, str, str]] = []
    for s in sorted(spans, key=lambda x: (x[0], -(x[1] - x[0]))):
        if all(s[0] >= e[1] or s[1] <= e[0] for e in elegidos):
            elegidos.append(s)
    for ini, fin, tipo, valor in sorted(elegidos, key=lambda x: -x[0]):
        ficha = _asignar_ficha(mapping, tipo, valor)
        texto = texto[:ini] + ficha + texto[fin:]
    return texto


def tokenize(texto: str, mapping: dict | None = None) -> tuple[str, dict]:
    """Tacha toda identidad detectada y devuelve (texto_limpio, mapping).
    El mapping entra/sale para que las fichas sean estables en el chat.
    NUNCA levanta: ante error interno devuelve el texto ÍNTEGRAMENTE tachado
    (fail-closed: jamás dejar pasar texto sin procesar)."""
    mapping = mapping if mapping is not None else _mapping_nuevo()
    if not texto:
        return texto or "", mapping
    try:
        texto = texto[:_MAX_TEXTO_CHARS]
        catalogo = _catalogo()
        spans = _spans_numeros(texto, catalogo)
        if catalogo:
            spans += _spans_catalogo(texto, catalogo)
        # fichas ya presentes (texto re-tokenizado, ej. resultados de tools):
        # intocables — jamás re-tachar una ficha
        fichas = [(m.start(), m.end()) for m in _FICHA_RE.finditer(texto)]
        spans = [s for s in spans
                 if all(s[1] <= f0 or s[0] >= f1 for f0, f1 in fichas)]
        limpio = _aplicar_spans(texto, spans, mapping)
        # capa defensiva sobre el texto YA tachado (las fichas no re-matchean:
        # CLIENTE_1 no tiene forma de nombre propio)
        limpio = _aplicar_spans(limpio, _spans_defensivos(limpio), mapping)
        return limpio, mapping
    except Exception as e:
        logger.error("pii_gateway.tokenize: fallo interno (%s) — texto retenido", e)
        return "[TEXTO RETENIDO POR LA ADUANA]", mapping


# ── Resolución ficha → cuenta (para las tools, DENTRO del perímetro) ────────

def id_cuenta_de_ficha(ficha: str, mapping: dict) -> str | None:
    """Resuelve una ficha al id_cuenta real — SOLO se llama dentro del
    perímetro (las tools). CTA_n → el número; CLIENTE_n → lookup del nombre
    en el catálogo. None si no se puede resolver."""
    valor = (mapping or {}).get("fichas", {}).get((ficha or "").strip())
    if not valor:
        return None
    if ficha.startswith("CTA_"):
        return re.sub(r"\D", "", valor) or None
    catalogo = _catalogo()
    if not catalogo:
        return None
    n = _norm(valor)
    if n in catalogo["nombres"]:
        return catalogo["nombres"][n]
    # el valor puede ser un token suelto (apellido): resuelve SOLO si el
    # dueño es único y sin ambigüedad ("" = apellido compartido)
    duenios = {catalogo["tokens"][tok] for tok in n.split() if tok in catalogo["tokens"]}
    if len(duenios) == 1:
        unico = duenios.pop()
        return unico or None
    return None


# ── Persistencia del mapping por chat (perímetro, TTL corto) ─────────────────

def cargar_mapping(chat_id: str, email: str) -> dict | None:
    """Mapping persistido del chat. None = el chat existe pero es de OTRO
    email (seguridad: nadie continúa el chat de otro). Chat inexistente o
    vencido → mapping nuevo."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT email, mapping FROM manager.asistente_mappings "
                "WHERE chat_id = %s AND updated_at > now() - make_interval(hours => %s)",
                (chat_id, _MAPPING_TTL_HORAS))
            fila = cur.fetchone()
        if not fila:
            return _mapping_nuevo()
        if (fila[0] or "").lower() != (email or "").lower():
            logger.warning("pii_gateway: chat %s pertenece a otro usuario — rechazado", chat_id)
            return None
        m = fila[1] if isinstance(fila[1], dict) else json.loads(fila[1])
        return {**_mapping_nuevo(), **m}
    except Exception as e:
        logger.warning("pii_gateway: no pude cargar el mapping de %s (%s)", chat_id, e)
        return _mapping_nuevo()


def guardar_mapping(chat_id: str, email: str, mapping: dict) -> None:
    """Upsert del mapping + limpieza oportunista de vencidos. Best-effort:
    si falla, el chat sigue (las fichas del turno ya viajaron consistentes;
    un turno futuro re-asigna). El mapping JAMÁS sale del perímetro."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO manager.asistente_mappings (chat_id, email, mapping) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (chat_id) DO UPDATE "
                "SET mapping = EXCLUDED.mapping, updated_at = now()",
                (chat_id, (email or "").lower(), json.dumps(mapping, ensure_ascii=False)))
            cur.execute(
                "DELETE FROM manager.asistente_mappings "
                "WHERE updated_at < now() - make_interval(hours => %s)", (_MAPPING_TTL_HORAS,))
    except Exception as e:
        logger.warning("pii_gateway: no pude guardar el mapping de %s (%s)", chat_id, e)
