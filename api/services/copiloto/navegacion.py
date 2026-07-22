"""copiloto/navegacion.py — NAVEGACIÓN ASISTIDA: el guía te LLEVA (idea del user).

En vez de dictar los pasos ("andá a Operaciones, filtrá mercado BYMA, poné el
rango"), el guía resuelve una INTENCIÓN de navegación y el frontend la aplica:
abre la vista con los filtros ya puestos.

## Por qué esto es lo más seguro que tenemos

El modelo NO ve ni un dato del negocio: para llevarte a un lugar solo necesita
el CATÁLOGO de filtros (qué mercados existen, qué segmentos), que es metadata.
Lo que viaja al proveedor es una intención ("mercado BYMA, junio"), jamás un
número. Y como el dato lo pinta la VISTA, es imposible que lo alucine: no hay
número que inventar. Por eso esta capacidad puede vivir en el proveedor barato
sin ninguna consideración de privacidad.

## La jaula

El modelo elige un DESTINO de la whitelist y valores de filtro; el código
valida TODO contra los catálogos vivos (los mismos que llenan los selectores
de la vista) y arma el estado. Un destino inexistente, un filtro desconocido o
un valor fuera del catálogo se RECHAZAN con un mensaje que le enseña los
válidos — nunca se pasa nada crudo al frontend.

## Cómo se aplica del otro lado

Las vistas ya persisten sus filtros con `usePersistedState` (sessionStorage,
claves estables tipo `ops.mercado`). El panel escribe esas claves y navega:
la vista rehidrata sola. NO hay que tocar cada vista — solo declarar acá qué
clave corresponde a qué filtro.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from api.cache import cached

logger = logging.getLogger(__name__)

_RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Catálogos vivos: nombre → función que devuelve la lista de valores válidos.
# Son los MISMOS que llenan los selectores de la vista → nunca quedan stale.
_CATALOGOS = {
    "mercados": lambda: (_ops().ops_mercados() or {}).get("mercados") or [],
    "segmentos": lambda: (_ops().ops_segmentos() or {}).get("segmentos") or [],
    "niveles3": lambda: (_ops().ops_niveles3() or {}).get("niveles3") or [],
    "tipos_operacion": lambda: (_ops().ops_tipos_operacion() or {}).get("tipos") or [],
    "carteras": lambda: (_ops().ops_carteras() or {}).get("carteras") or [],
}


def _ops():
    from api.services import operaciones_sql
    return operaciones_sql


@cached(ttl=3600)
def _valores(catalogo: str) -> list[str]:
    """Valores vigentes de un catálogo (cache 1h — casi no cambian)."""
    try:
        return [str(v) for v in _CATALOGOS[catalogo]() if v]
    except Exception as e:
        logger.warning("navegacion: catálogo %s no disponible (%s)", catalogo, e)
        return []


# ── Filtros reutilizables ────────────────────────────────────────────────────
# `clave` = la clave de sessionStorage que persiste ese filtro en la vista
# (usePersistedState). `con` = estado extra que hay que setear junto (ej. las
# fechas solo aplican si el modo del selector es RANGO).

def _f(clave: str, tipo: str, *, catalogo: str | None = None,
       valores: list[str] | None = None, con: dict | None = None,
       ayuda: str = "") -> dict:
    return {"clave": clave, "tipo": tipo, "catalogo": catalogo,
            "valores": valores, "con": con or {}, "ayuda": ayuda}


_RANGO = {"ops.modo": "RANGO"}
_RANGO_AR = {"ar.modo": "RANGO"}

_DESTINOS: dict[str, dict] = {
    "operaciones_volumen": {
        "titulo": "Operaciones → OPERACIONES",
        "ruta": "/operaciones",
        "modulo": "operaciones",
        "cuando": "cuánto se OPERÓ (volumen, boletos): por mercado, segmento, tipo de "
                  "operación, cuenta o título, en un día o un rango de fechas",
        "estado_base": {"operaciones.tab": "operaciones"},
        "filtros": {
            "mercado": _f("ops.mercado", "enum", catalogo="mercados"),
            "segmento": _f("ops.segmento", "enum", catalogo="segmentos",
                           ayuda="segmento nivel 1 del cliente"),
            "nivel_3": _f("ops.nivel3", "enum", catalogo="niveles3",
                          ayuda="segmento del boleto (nivel 3)"),
            "cartera": _f("ops.cartera", "enum", catalogo="carteras",
                          ayuda="cartera del TÍTULO operado (HD, DL, ARS, FCI…)"),
            "moneda": _f("ops.moneda", "enum", valores=["ARS", "USD", "USD_DOL"]),
            # tipo `cuenta`: el CÓDIGO resuelve el nombre/número que dio el
            # usuario contra el catálogo real de cuentas y deja seleccionada la
            # denominación EXACTA (el filtro de verdad de la vista). Sin esto
            # solo se escribía texto en el buscador y no filtraba nada.
            "cuenta": _f("ops.denominacion", "cuenta",
                         ayuda="el CLIENTE: nombre o número de cuenta tal como lo "
                               "dijo el usuario"),
            "operador": _f("ops.operador", "operador",
                           ayuda="el COMERCIAL que atiende las cuentas (empleado), "
                                 "no el cliente"),
            "desde": _f("ops.desde", "fecha", con=_RANGO),
            "hasta": _f("ops.hasta", "fecha", con=_RANGO),
        },
    },
    "operaciones_aranceles": {
        "titulo": "Operaciones → ARANCELES",
        "ruta": "/operaciones",
        "modulo": "operaciones",
        "cuando": "lo FACTURADO (aranceles, comisiones cobradas) por período, segmento "
                  "u operador",
        "estado_base": {"operaciones.tab": "aranceles"},
        "filtros": {
            "segmento": _f("ar.segmento", "enum", catalogo="segmentos"),
            "desde": _f("ar.desde", "fecha", con=_RANGO_AR),
            "hasta": _f("ar.hasta", "fecha", con=_RANGO_AR),
            "abrir_por": _f("ar.dim", "enum",
                            valores=["nivel3", "operacion", "operador"],
                            ayuda="cómo se abre la tabla principal"),
        },
    },
    "operaciones_depositos": {
        "titulo": "Operaciones → DEPÓSITOS & EXTRACCIONES",
        "ruta": "/operaciones",
        "modulo": "operaciones",
        "cuando": "movimientos de DINERO de clientes (depósitos y extracciones), no "
                  "operaciones bursátiles",
        "estado_base": {"operaciones.tab": "depositos"},
        "filtros": {},
    },
    "operaciones_agro": {
        "titulo": "Operaciones → AGRO",
        "ruta": "/operaciones",
        "modulo": "operaciones",
        "cuando": "share de mercado de granos (volumen agro contra el mercado)",
        "estado_base": {"operaciones.tab": "agro"},
        "filtros": {},
    },
    "back_office_tenencia": {
        "titulo": "Back Office → TENENCIA VALORIZADA",
        "ruta": "/back-office",
        "modulo": "back-office",
        "cuando": "tenencia histórica día a día; incluye los TÍTULOS EN GARANTÍA "
                  "(filtro SOLO GAR) y el alquiler",
        "estado_base": {"backoffice.tab": "tenencia"},
        "filtros": {
            "garantia": _f("tenencia.garMode.v2", "enum",
                           valores=["todos", "sin_gar", "solo_gar", "sin_alquiler"],
                           ayuda="solo_gar = SOLO los títulos en garantía"),
            "cartera": _f("tenencia.cartera", "enum", valores=["HD", "ARS"]),
        },
    },
    "back_office_acreencias": {
        "titulo": "Back Office → ACREENCIAS CLIENTES",
        "ruta": "/back-office",
        "modulo": "back-office",
        "cuando": "cupones, rentas y amortizaciones a cobrar por fecha",
        "estado_base": {"backoffice.tab": "acreencias"},
        "filtros": {},
    },
    "back_office_tesoreria": {
        "titulo": "Back Office → TESORERÍA",
        "ruta": "/back-office",
        "modulo": "back-office",
        "cuando": "ingresos y egresos de dinero del DÍA",
        "estado_base": {"backoffice.tab": "tesoreria"},
        "filtros": {},
    },
    "referidos": {
        "titulo": "Referidos",
        "ruta": "/referidos",
        "modulo": "operaciones",
        "cuando": "comisiones por saldos de FCI referidos, por gerente o cooperativa",
        "estado_base": {},
        "filtros": {
            "moneda": _f("referidos.moneda", "enum", valores=["ARS", "USD"]),
            # valores REALES del selector (RANGOS en referidos-view.tsx)
            "rango": _f("referidos.rango", "enum",
                        valores=["MTD", "1M", "3M", "YTD", "1A", "ALL"]),
        },
    },
}


# ── Resolución de fechas habladas ────────────────────────────────────────────

def _hoy() -> datetime:
    return datetime.now(UTC) - timedelta(hours=3)  # ART


def _validar_fecha(v: str) -> str | None:
    v = str(v).strip()
    return v if _RE_ISO.match(v) else None


@cached(ttl=3600)
def _cuentas() -> list[tuple[str, str]]:
    """(id_cuenta, denominación) del catálogo real. Cache 1h."""
    try:
        filas = (_ops().ops_cuentas_list() or {}).get("cuentas") or []
        return [(str(c.get("cuenta") or ""), str(c.get("denominacion") or ""))
                for c in filas if c.get("denominacion")]
    except Exception as e:
        logger.warning("navegacion: catálogo de cuentas no disponible (%s)", e)
        return []


_RE_FICHA = re.compile(
    r"^(?:CLIENTE|CTA|DOC|OPERADOR|REFERIDO|CONTRAPARTE|USUARIO)_\d+$")


@cached(ttl=3600)
def _operadores() -> list[tuple[str, str]]:
    """(email, nombre) de los operadores comerciales — el otro catálogo de
    PERSONAS del sistema. La vista filtra por EMAIL."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT email, COALESCE(nombre, email) FROM clientes.operadores")
            return [(str(e), str(n)) for e, n in cur.fetchall() if e]
    except Exception as e:
        logger.warning("navegacion: catálogo de operadores no disponible (%s)", e)
        return []


def _texto_original(texto: str, mapping: dict | None) -> str:
    """Si viene una FICHA de la aduana, recupera lo que escribió el usuario
    (adentro del perímetro). Si no, devuelve el texto tal cual."""
    t = str(texto or "").strip()
    if mapping and _RE_FICHA.match(t):
        return str((mapping.get("fichas") or {}).get(t) or "").strip()
    return t


def _match_persona(tl: str, catalogo: list[tuple[str, str]]) -> str | None:
    """Match de una persona por palabras: TODAS las que dijo el usuario tienen
    que estar en el nombre, y el dueño tiene que ser único. Tolera el orden
    invertido ('nicolas mollo' ↔ 'MOLLO, NICOLAS EZEQUIEL')."""
    palabras = [p for p in tl.split() if len(p) >= 3]
    if not palabras:
        return None
    hits = [clave for clave, nombre in catalogo
            if all(p in _norm_txt(nombre) for p in palabras)]
    return hits[0] if len(hits) == 1 else None


def clasificar_persona(texto: str, mapping: dict | None = None) -> dict:
    """¿Lo que nombró el usuario es una CUENTA (cliente) o un OPERADOR
    (empleado)? El sistema tiene los dos catálogos: esto NO se adivina, se
    consulta (caso real 2026-07-21: el asistente asumía 'operador' y erraba).

    Devuelve {cuenta, operador} con lo que resolvió de cada lado — si los dos
    vienen con valor, es AMBIGUO y hay que preguntarle al usuario."""
    t = _texto_original(texto, mapping)
    if not t:
        return {"cuenta": None, "operador": None}
    tl = _norm_txt(t)
    cuentas = _cuentas()
    cuenta = None
    for idc, den in cuentas:
        if idc and idc.lower() == tl:
            cuenta = den
            break
    if cuenta is None:
        for _idc, den in cuentas:
            if _norm_txt(den) == tl:
                cuenta = den
                break
    if cuenta is None:
        cuenta = _match_persona(tl, [(den, den) for _i, den in cuentas])
    operador = _match_persona(tl, _operadores())
    return {"cuenta": cuenta, "operador": operador}


def _resolver_cuenta(texto: str, mapping: dict | None = None) -> str | None:
    """Resuelve lo que dijo el usuario ('nicolas mollo', '805') a la
    DENOMINACIÓN EXACTA del catálogo ('MOLLO, NICOLAS EZEQUIEL') — que es lo
    que la vista usa para filtrar de verdad.

    Corre DENTRO del perímetro y el resultado va al FRONTEND, no al modelo
    (ver `ejecutor`): el proveedor nunca recibe la denominación canónica.
    Match por número de cuenta exacto, denominación exacta, y por PALABRAS
    (todas las que dijo el usuario tienen que estar) con dueño único —
    'nicolas mollo' matchea 'MOLLO, NICOLAS EZEQUIEL' aunque esté al revés."""
    t = str(texto or "").strip()
    if not t:
        return None
    # TOKEN-IN: si la vista tiene aduana, el modelo nos pasa una FICHA
    # (CLIENTE_1) porque jamás vio el nombre. Acá adentro (perímetro) se
    # recupera lo que escribió el usuario y se resuelve contra el catálogo.
    if mapping and _RE_FICHA.match(t):
        original = (mapping.get("fichas") or {}).get(t)
        if not original:
            return None
        t = str(original).strip()
    cuentas = _cuentas()
    if not cuentas:
        return None
    tl = _norm_txt(t)
    for idc, den in cuentas:
        if idc and idc.lower() == tl:
            return den
    for _idc, den in cuentas:
        if _norm_txt(den) == tl:
            return den
    palabras = [p for p in tl.split() if len(p) >= 3]
    if not palabras:
        return None
    candidatas = [den for _i, den in cuentas
                  if all(p in _norm_txt(den) for p in palabras)]
    return candidatas[0] if len(candidatas) == 1 else None


def _norm_txt(s: str) -> str:
    """minúsculas, sin acentos ni puntuación — para comparar nombres."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"\s+", " ", s.lower())).strip()


def _match_catalogo(valor: str, opciones: list[str]) -> str | None:
    """Match tolerante → devuelve el valor CANÓNICO del catálogo. Tres pasadas:
    exacta (case-insensitive), parcial con dueño único, y por ÚLTIMO fuzzy —
    la gente escribe 'byam' por BYMA y el filtro no tiene por qué fallar por
    un typo (caso real 2026-07-21). El fuzzy exige una sola candidata."""
    import difflib

    v = str(valor).strip().lower()
    if not v or not opciones:
        return None
    por_lower = {o.lower(): o for o in opciones}
    if v in por_lower:
        return por_lower[v]
    parciales = [o for o in opciones if v in o.lower()]
    if len(parciales) == 1:
        return parciales[0]
    cerca = difflib.get_close_matches(v, list(por_lower), n=2, cutoff=0.75)
    return por_lower[cerca[0]] if len(cerca) == 1 else None


def destinos_para(usuario: str | None) -> list[dict]:
    """Destinos que ESE usuario puede abrir (gate por módulo RBAC). El guía
    solo ofrece lo que la persona puede ver."""
    from .derivacion import _acceso

    out = []
    for clave, d in _DESTINOS.items():
        try:
            if usuario and not _acceso(usuario, d):
                continue
        except Exception as e:
            logger.warning("navegacion: no pude validar acceso a %s (%s)", clave, e)
            continue
        out.append({"id": clave, **d})
    return out


def descripcion_destinos(usuario: str | None) -> str:
    """Bloque para el prompt: qué destinos hay, cuándo usarlos y qué filtros
    acepta cada uno CON sus valores vigentes. Es metadata pura (cero datos)."""
    destinos = destinos_para(usuario)
    if not destinos:
        return ""
    lineas = [
        "[destinos navegables — para la herramienta abrir_vista]",
        "REGLA DE PERSONAS: cuando el usuario nombra a alguien, NO adivines si es "
        "un CLIENTE (cuenta) o un OPERADOR comercial (empleado de la mesa): pasalo "
        "en el filtro `cuenta` y el sistema lo resuelve contra los dos catálogos y "
        "te corrige si es lo otro. Si te avisa que el nombre existe en AMBOS, "
        "preguntale al usuario cuál quiere — no elijas vos.",
    ]
    for d in destinos:
        lineas.append(f"- {d['id']} → {d['titulo']}: {d['cuando']}")
        for nombre, f in (d["filtros"] or {}).items():
            if f["tipo"] == "enum":
                vals = f["valores"] or _valores(f["catalogo"]) if (
                    f["valores"] or f["catalogo"]) else []
                # sin catálogo NO se ofrece el filtro como libre: `resolver`
                # lo rechazaría igual (jaula) — mejor decirlo acá
                detalle = ("uno de: " + ", ".join(vals[:25])) if vals else (
                    "no disponible ahora — no lo uses")
            elif f["tipo"] == "fecha":
                detalle = "fecha YYYY-MM-DD"
            elif f["tipo"] in ("cuenta", "operador"):
                detalle = ("pasá el nombre o número TAL CUAL lo dijo el usuario — "
                           "el sistema lo resuelve y te avisa si te equivocaste "
                           "de tipo de persona")
            else:
                detalle = "texto libre"
            extra = f" ({f['ayuda']})" if f["ayuda"] else ""
            lineas.append(f"    · {nombre}{extra}: {detalle}")
    return "\n".join(lineas)


def resolver(destino: str, filtros: dict | None, usuario: str | None,
             mapping: dict | None = None) -> dict:
    """Valida la intención y devuelve {ok, ruta, estado, resumen} o {ok:false,
    error}. TODO se valida contra la whitelist y los catálogos vivos: es la
    jaula que impide que el modelo mande cualquier cosa al frontend."""
    d = _DESTINOS.get(str(destino or "").strip())
    if d is None:
        validos = [x["id"] for x in destinos_para(usuario)]
        return {"ok": False, "error": f"destino desconocido: {destino!r}. "
                                     f"Válidos: {', '.join(validos)}"}
    from .derivacion import _acceso
    try:
        if usuario and not _acceso(usuario, d):
            return {"ok": False,
                    "error": f"el usuario no tiene permiso para {d['titulo']} — "
                             "decíselo y no ofrezcas ese destino"}
    except Exception as e:
        logger.warning("navegacion: acceso no verificable (%s)", e)
        return {"ok": False, "error": "no pude verificar el permiso de esa vista"}

    estado = dict(d["estado_base"])
    resumen: list[str] = []        # para el BOTÓN (frontend, usuario autorizado)
    resumen_llm: list[str] = []    # para el MODELO — sin identidades
    rechazos: list[str] = []
    for nombre, valor in (filtros or {}).items():
        f = (d["filtros"] or {}).get(str(nombre).strip())
        if f is None:
            rechazos.append(f"{nombre} (no existe en {d['titulo']})")
            continue
        if valor is None or str(valor).strip() == "":
            continue
        if f["tipo"] == "enum":
            opciones = f["valores"] or _valores(f["catalogo"]) if (
                f["valores"] or f["catalogo"]) else []
            canonico = _match_catalogo(str(valor), opciones) if opciones else None
            if canonico is None:
                rechazos.append(
                    f"{nombre}={valor!r} (valores: {', '.join(opciones[:15]) or 'ninguno'})")
                continue
            estado[f["clave"]] = canonico
            resumen.append(f"{nombre}: {canonico}")
            resumen_llm.append(f"{nombre}: {canonico}")
        elif f["tipo"] == "fecha":
            iso = _validar_fecha(str(valor))
            if iso is None:
                rechazos.append(f"{nombre}={valor!r} (formato YYYY-MM-DD)")
                continue
            estado[f["clave"]] = iso
            resumen.append(f"{nombre}: {iso}")
            resumen_llm.append(f"{nombre}: {iso}")
        elif f["tipo"] == "cuenta":
            # NO se asume qué es la persona: se consulta a los dos catálogos.
            quien = clasificar_persona(str(valor), mapping)
            if quien["cuenta"] and quien["operador"]:
                return {"ok": False, "ambiguo": True,
                        "error": "ese nombre existe COMO CUENTA de cliente Y COMO "
                                 "OPERADOR comercial. NO elijas vos: preguntale al "
                                 "usuario cuál de los dos quiere ver, y volvé a "
                                 "llamarme con el filtro `cuenta` o `operador`."}
            if not quien["cuenta"] and quien["operador"]:
                return {"ok": False,
                        "error": "eso NO es una cuenta de cliente: es un OPERADOR "
                                 "comercial. Volvé a llamarme usando el filtro "
                                 "`operador` en vez de `cuenta`."}
            den = quien["cuenta"]
            if den is None:
                rechazos.append(
                    f"no encontré una cuenta ni un operador para {str(valor)[:40]!r} — "
                    "pedile al usuario el número de cuenta o el nombre como figura")
                continue
            estado[f["clave"]] = den
            # el buscador muestra el texto; el filtro real es la denominación
            estado["ops.search"] = den
            resumen.append(f"cuenta: {den}")
            # al MODELO no le vuelve la denominación canónica (es identidad de
            # un cliente y este copiloto habla con el proveedor barato)
            resumen_llm.append("cuenta: la que pidió el usuario")
        elif f["tipo"] == "operador":
            quien = clasificar_persona(str(valor), mapping)
            if not quien["operador"]:
                rechazos.append(
                    f"{str(valor)[:40]!r} no figura como operador comercial"
                    + (" (sí como cuenta de cliente: usá el filtro `cuenta`)"
                       if quien["cuenta"] else ""))
                continue
            estado[f["clave"]] = quien["operador"]   # la vista filtra por email
            nombre = next((n for e, n in _operadores() if e == quien["operador"]),
                          quien["operador"])
            resumen.append(f"operador: {nombre}")
            resumen_llm.append("operador: el que pidió el usuario")
        else:
            estado[f["clave"]] = str(valor)[:80]
            resumen.append(f"{nombre}: {valor}")
            resumen_llm.append(f"{nombre}: {valor}")
        estado.update(f["con"])

    if rechazos:
        return {"ok": False, "error": "no pude aplicar: " + " · ".join(rechazos)}
    return {"ok": True, "ruta": d["ruta"], "titulo": d["titulo"],
            "estado": estado, "resumen": " · ".join(resumen),
            "resumen_llm": " · ".join(resumen_llm)}


# ── La tool que ve el modelo ─────────────────────────────────────────────────

TOOLS_NAVEGACION = [
    {
        "type": "function",
        "function": {
            "name": "abrir_vista",
            "description": (
                "Abre una vista de la plataforma con los filtros YA APLICADOS, para "
                "que el usuario vea el dato él mismo. Usala SIEMPRE que pidan ver o "
                "saber algo que vive en una vista ('cuánto se operó en BYMA', 'quiero "
                "ver los títulos en garantía', 'mostrame lo facturado del semestre'): "
                "es mejor que explicar los pasos. Elegí el destino de la lista de "
                "destinos navegables y pasá los filtros que la pregunta pida. Traducí "
                "los períodos hablados a fechas exactas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "destino": {
                        "type": "string",
                        "description": "El id del destino, tal cual figura en la lista "
                                       "de destinos navegables (ej. operaciones_volumen).",
                    },
                    "filtros": {
                        "type": "object",
                        "description": "Filtros a aplicar, con los nombres y valores "
                                       "que ese destino declara. Vacío si no hace falta "
                                       "filtrar.",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["destino"],
            },
        },
    }
]


def ejecutor(contenedor: dict, usuario: str | None):
    """Factory del ejecutor de tools del guía. `contenedor` es el buzón donde
    se deja la navegación resuelta para que el motor la devuelva al panel
    (el texto del modelo NO es el canal: el frontend necesita datos, no prosa)."""

    def _ejecutar(nombre: str, args: dict) -> str:
        if nombre != "abrir_vista":
            return f"herramienta desconocida: {nombre}"
        # el mapping de la aduana (si la vista la tiene) viaja en el buzón:
        # con él las fichas se resuelven a identidad real acá adentro
        r = resolver(args.get("destino", ""), args.get("filtros") or {}, usuario,
                     mapping=contenedor.get("_mapping"))
        if not r.get("ok"):
            return r["error"]
        contenedor["navegacion"] = {
            "ruta": r["ruta"], "estado": r["estado"],
            "titulo": r["titulo"], "resumen": r["resumen"],
        }
        # OJO: al modelo le vuelve el resumen SIN identidades (resumen_llm);
        # el completo (con la denominación real de la cuenta) va en el buzón,
        # que lo consume el frontend del usuario autorizado.
        return (f"listo: se le va a ofrecer abrir {r['titulo']}"
                + (f" con {r['resumen_llm']}" if r["resumen_llm"] else " sin filtros")
                + ". Confirmale en UNA frase qué va a ver ahí (sin listar los pasos "
                  "de navegación, el botón ya lo lleva) y NO inventes ningún número.")

    return _ejecutar
