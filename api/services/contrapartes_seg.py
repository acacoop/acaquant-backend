"""api/services/contrapartes_seg.py — segmentación + conciliador de `clientes.contrapartes` (SQL).

Backend de la vista MANAGER → CONTRAPARTES (módulo `manager_contrapartes`). **Fuente única
SQL** (Mongo CashFlow.Contrapartes deprecado). Servicio puro (sin FastAPI).

Tabla `clientes.contrapartes` (clave = `id_cuenta`):
  {id_cuenta, denominacion (de Aunesa), contraparte (editable), segmento (editable),
   codigo_mae (editable — código DESTINO del MAE: FXXX fondo / C+CUIT comitente /
   SXXX aseguradora; lo consume el futuro Excel MAE de SENEBIS resolviendo por la
   cc de la orden), origen, actualizado_por/at}. `denominacion` se prefiere de la
   fila; si falta, del JOIN a `cuentas`.

⚠️ Efectos colaterales (INTENCIONALES) al AGREGAR una contraparte — el front avisa:
  - la cuenta SALE del AuM (jobs/_aum_filters.py reglas 3 y 4: match por id o por nombre).
  - su nivel_3 pasa a "PJ GRANDE" (api/services/segmentacion.py::clasificar_nivel_3).

Conciliador: lista cuentas que están en Aunesa (`cuentas/listadoCuentas`) pero NO en
contrapartes, y cuya `denominacion` contiene el nombre de alguna contraparte conocida.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from api.services._sql import _q
from api.services.segmentacion import _TIPOS_PH  # tipo_cliente de persona física
from core import aunesa
from core.postgres import get_pool

# Valores de `contraparte` que no son nombres reales (mismo criterio que _aum_filters).
_PLACEHOLDERS: frozenset[str] = frozenset({"", "NO APLICA", "N/A", "NONE", "NULL", "-"})
_MIN_KEYWORD_LEN = 3
# denominacion preferida: la de la fila; si NULL, la del JOIN a cuentas.
_DEN = "COALESCE(c.denominacion, u.denominacion)"


def _exec(sql: str, params: dict) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n


def _s(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def inferir_segmento(denominacion: str | None, contraparte: str | None) -> str | None:
    """Sugerencia de segmento (FCI→Fondos, ALYC, BANCO→Bancos)."""
    den = (denominacion or "").upper()
    cp = (contraparte or "").upper()
    if "FCI" in den:
        return "Fondos"
    if "ALYC" in cp:
        return "ALYC"
    if "BANCO" in den:
        return "Bancos"
    return None


def _norm_keyword(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if not s or s in _PLACEHOLDERS or len(s) < _MIN_KEYWORD_LEN:
        return None
    return s


def listar_contrapartes(*, segmento: str | None = None, contraparte: str | None = None,
                        q: str | None = None) -> dict:
    """Lista las contrapartes (panel izquierdo). Filtros por segmento/contraparte exactos
    y `q` (cada token en denominacion o cuenta, AND sin orden)."""
    where = ["c.id_cuenta IS NOT NULL"]
    p: dict = {}
    if segmento:
        where.append("c.segmento = %(seg)s")
        p["seg"] = segmento
    if contraparte:
        where.append("c.contraparte = %(cp)s")
        p["cp"] = contraparte
    for i, tok in enumerate((q or "").split()):
        where.append(f"({_DEN} ILIKE %(t{i})s OR c.id_cuenta ILIKE %(t{i})s)")
        p[f"t{i}"] = f"%{tok}%"
    rows = _q(
        f"SELECT c.id_cuenta AS cuenta, {_DEN} AS denominacion, c.contraparte, c.segmento, "
        f"c.codigo_mae "
        f"FROM contrapartes c LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
        f"WHERE {' AND '.join(where)} ORDER BY denominacion NULLS LAST LIMIT 5000", p)
    return {"contrapartes": [
        {"cuenta": _s(r["cuenta"]), "denominacion": _s(r["denominacion"]),
         "contraparte": _s(r["contraparte"]), "segmento": _s(r["segmento"]),
         "codigo_mae": _s(r["codigo_mae"])}
        for r in rows], "n": len(rows)}


def segmentos_distinct() -> dict:
    """Valores distintos de segmento + contraparte para los datalist (autocomplete)."""
    def _clean(rows: list[dict], col: str) -> list[str]:
        return sorted({
            r[col].strip() for r in rows
            if isinstance(r[col], str) and r[col].strip()
            and r[col].strip().upper() not in _PLACEHOLDERS})
    segs = _q("SELECT DISTINCT segmento FROM contrapartes WHERE segmento IS NOT NULL")
    cps = _q("SELECT DISTINCT contraparte FROM contrapartes WHERE contraparte IS NOT NULL")
    return {"segmentos": _clean(segs, "segmento"), "contrapartes": _clean(cps, "contraparte")}


def update_contraparte(*, cuenta: str, contraparte: str | None = None,
                       segmento: str | None = None, codigo_mae: str | None = None,
                       actor: str | None = None) -> dict:
    """Edita contraparte/segmento/codigo_mae de una cuenta existente. El router
    traduce updated=False/reason a 400/404. codigo_mae con string vacío BORRA
    el código (queda NULL); None = no tocar (semántica PATCH)."""
    cuenta = _s(cuenta) or ""
    if not cuenta:
        return {"updated": False, "reason": "cuenta_vacia"}
    sets: dict[str, Any] = {}
    if contraparte is not None:
        sets["contraparte"] = _s(contraparte)
    if segmento is not None:
        sets["segmento"] = _s(segmento)
    if codigo_mae is not None:
        s = _s(codigo_mae)
        sets["codigo_mae"] = s.upper() if s else None
    if not sets:
        return {"updated": False, "reason": "sin_campos"}
    sets["actualizado_por"] = actor
    sets["actualizado_at"] = datetime.now(UTC)
    cols = ", ".join(f"{k} = %({k})s" for k in sets)
    n = _exec(f"UPDATE contrapartes SET {cols} WHERE id_cuenta = %(idc)s",
              {**sets, "idc": cuenta})
    if n == 0:
        return {"updated": False, "reason": "not_found"}
    row = _q(f"SELECT c.id_cuenta AS cuenta, {_DEN} AS denominacion, c.contraparte, c.segmento, "
             f"c.codigo_mae "
             f"FROM contrapartes c LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
             f"WHERE c.id_cuenta = %(idc)s", {"idc": cuenta})
    d = row[0] if row else {}
    return {"updated": True, "cuenta": str(d.get("cuenta")), "denominacion": d.get("denominacion"),
            "contraparte": d.get("contraparte"), "segmento": d.get("segmento"),
            "codigo_mae": d.get("codigo_mae")}


def add_contraparte(*, cuenta: str, denominacion: str | None, contraparte: str | None,
                    segmento: str | None, actor: str | None = None) -> dict:
    """Alta de 1 click desde el conciliador. Idempotente (upsert por id_cuenta)."""
    cuenta = _s(cuenta) or ""
    if not cuenta:
        return {"added": False, "reason": "cuenta_vacia"}
    p = {"idc": cuenta, "den": _s(denominacion), "cp": _s(contraparte),
         "seg": _s(segmento), "por": actor, "at": datetime.now(UTC)}
    _exec(
        "INSERT INTO contrapartes (id_cuenta, denominacion, contraparte, segmento, origen, "
        "actualizado_por, actualizado_at) "
        "VALUES (%(idc)s, %(den)s, %(cp)s, %(seg)s, 'reconciler', %(por)s, %(at)s) "
        "ON CONFLICT (id_cuenta) DO UPDATE SET denominacion = EXCLUDED.denominacion, "
        "contraparte = EXCLUDED.contraparte, segmento = EXCLUDED.segmento, origen = 'reconciler', "
        "actualizado_por = EXCLUDED.actualizado_por, actualizado_at = EXCLUDED.actualizado_at", p)
    return {"added": True, "cuenta": cuenta, "denominacion": p["den"],
            "contraparte": p["cp"], "segmento": p["seg"]}


_IMPORT_UPD = (
    "UPDATE contrapartes SET "
    "contraparte = COALESCE(%(contraparte)s::text, contraparte), "
    "segmento = COALESCE(%(segmento)s::text, segmento), "
    "codigo_mae = COALESCE(%(codigo_mae)s::text, codigo_mae), "
    "actualizado_por = %(por)s, actualizado_at = %(at)s "
    "WHERE id_cuenta = %(idc)s"
)


def _norm_den(v: Any) -> str | None:
    """Denominación comparable: sin acentos, mayúsculas, espacios colapsados."""
    s = _s(v)
    if not s:
        return None
    s = unicodedata.normalize("NFD", s.upper())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip() or None


# ── SUGERIR LA CONTRAPARTE POR PARECIDO CON LO QUE YA HAY ─────────────────
#
# ⚠️⚠️ **EL NOMBRE DE LA CONTRAPARTE NO SE LEE DE LA CUENTA: SE APRENDE DE LAS
# QUE YA ESTÁN CARGADAS.** Es la diferencia entera, y el caso del user lo dice
# mejor que cualquier explicación (§0.es):
#
#     «FCI Consultatio Estrategia I / II / III / IV»
#
# Leyendo eso cualquiera pondría «Consultatio». Está MAL: en la tabla, las
# cuentas Consultatio ya cargadas tienen contraparte **ONE618**. Un sugeridor
# que mire el nombre y no la historia se equivoca con seguridad — y como el que
# tilda ve una propuesta que suena razonable, la acepta.
#
# Por eso el índice va al revés: de cada contraparte YA cargada se sacan las
# palabras distintivas de sus cuentas, y una cuenta nueva hereda la contraparte
# de la palabra que comparte. La evidencia viaja con la propuesta («CONSULTATIO
# está en 3 cuentas de ONE618»), así se puede rechazar sin abrir nada.

# ⚠️⚠️ **LA VERSIÓN 1 ACERTABA 42% Y CONTRADECÍA 27 VECES. ESTO ES LO QUE
# ENSEÑARON ESOS 27** (medido con `diag_contrapartes` §5b, leave-one-out):
#
#   FCI SBS MULTIACTIVOS          → decía SCHRODER, es SBS
#   FCI ADCAP BALANCE MULTIACTIVO → decía ONE618,   es ADCAP
#   FCI MAX MONEY MARKET          → decía TORONTO,  es MAX
#   FCI ALLARIA DÓLAR PERFORMANCE → decía SCHRODER, es ALLARIA
#
# Tres defectos, y los tres se ven en esas cuatro líneas:
#
# 1. **La v1 elegía la palabra con MÁS cuentas detrás.** Está exactamente al
#    revés: una palabra que aparece mucho es COMÚN («PERFORMANCE», «BALANCE»,
#    «MONEY», «ACTIVOS» son palabras de PRODUCTO), y la que identifica es la
#    marca. El docstring de la v1 decía «gana la más rara» y el código hacía
#    `max()` por cantidad — el comentario y el código no decían lo mismo.
# 2. **Tiraba las palabras de menos de cuatro letras**, que es donde viven las
#    marcas: SBS, MAX, BM, IAM. Y los números, donde vive 1810 (Credicoop).
# 3. **Exigía unicidad absoluta**, así que «ALLARIA» quedaba descartada por
#    existir «ALLARIA - ALYC» con UNA cuenta, y ganaba una palabra de producto.
#
# La v2 usa la estructura que estos nombres tienen de verdad: **la marca es de
# las primeras palabras, y el producto va después.** Se lee de izquierda a
# derecha y se corta en la primera que resuelva.

# Palabras que no identifican a nadie: formas jurídicas, conectores y —lo que
# enseñó la medición— PALABRAS DE PRODUCTO. Sin estas últimas, «PERFORMANCE»
# resolvía a Schroder y le ganaba a la marca que estaba al lado.
_GENERICAS: frozenset[str] = frozenset({
    # forma jurídica y conectores
    "FCI", "FONDO", "FONDOS", "COMUN", "COMUNES", "INVERSION", "INVERSIONES",
    "SOCIEDAD", "SOCIEDADES", "ANONIMA", "COMPANIA", "COMPANIAS", "LIMITADA",
    "ADMINISTRADORA", "GERENTE", "SGFCI", "SA", "SRL", "DE", "DEL", "LA",
    "EL", "LOS", "LAS", "Y", "SD",
    "SEGURO", "SEGUROS", "ASEGURADORA", "RETIRO", "VIDA", "GENERALES",
    "PERSONAS", "RIESGO", "RIESGOS", "TRABAJO", "COOPERATIVA", "MUTUAL",
    "FIDEICOMISO", "FINANCIERO", "FINANCIERA",
    # PALABRAS DE PRODUCTO — cada una salió de una contradicción medida
    "PERFORMANCE", "BALANCE", "MULTIACTIVO", "MULTIACTIVOS", "SMART",
    "MONEY", "MARKET", "AHORRO", "AHORROS", "ACTIVOS", "ACTIVO", "COBERTURA",
    "DINAMICA", "DINAMICO", "SUSTENTABLE", "CORTO", "LARGO", "PLAZO", "ASG",
    "CAPITAL", "CAPITALES", "ASSET", "MANAGEMENT", "RENTA", "RENTAS",
    "PESOS", "DOLAR", "DOLARES", "CLASE", "SERIE", "ESTRATEGIA", "PLUS",
    "LIQUIDEZ", "AHORRISTA", "CRECIMIENTO", "BALANCEADO", "MIXTO", "GLOBAL",
})
# Dos letras alcanzan: BM es Bull Market. Lo que filtra no es el largo — es la
# lista de arriba y, sobre todo, que la palabra ESTÉ en el índice.
_MIN_TOKEN = 2
# Cuántas palabras del principio se miran. La marca está adelante; más allá de
# la tercera lo que hay es producto, y ahí es donde la v1 se equivocaba.
_VENTANA_MARCA = 3
# Qué tan dominante tiene que ser una contraparte para esa palabra. 0.8 deja
# pasar «ALLARIA» (53 cuentas) conviviendo con «ALLARIA - ALYC» (1), que es el
# mismo grupo escrito con otro detalle, y sigue frenando un empate real.
_DOMINANCIA = 0.8


def _palabras(denominacion: Any) -> list[str]:
    """Las palabras candidatas a MARCA, **en orden**. La posición es el dato:
    en «FCI ALLARIA DÓLAR PERFORMANCE» la marca es la primera y el resto es
    producto, y la v1 perdió justamente por ignorarlo."""
    d = _norm_den(denominacion) or ""
    d = re.sub(r"^\[[^\]]*\]\s*", "", d)          # el prefijo «[805] » de Aunesa
    fuera = []
    for w in re.findall(r"[A-Z0-9]+", d):
        if len(w) < _MIN_TOKEN or w in _GENERICAS:
            continue
        if w not in fuera:
            fuera.append(w)
    return fuera


def indice_contrapartes(filas: list[dict] | None = None) -> dict[str, dict[str, int]]:
    """`{palabra: {contraparte: en cuántas cuentas aparece}}`, de lo YA cargado.

    `filas` se acepta para poder medirlo dejando una fuera (`diag_contrapartes`
    §5b lo usa para el leave-one-out): sin eso, cualquier medición se evaluaría
    contra un índice que ya contiene la respuesta y daría 100% sin decir nada.
    """
    if filas is None:
        filas = _q(f"SELECT {_DEN} AS den, c.contraparte AS cp "
                   "  FROM contrapartes c "
                   "  LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
                   " WHERE c.contraparte IS NOT NULL AND btrim(c.contraparte) <> ''")
    idx: dict[str, dict[str, int]] = {}
    for f in filas:
        cp = _norm_keyword(f.get("cp"))
        if not cp:
            continue
        original = str(f.get("cp")).strip()
        # Sólo las primeras palabras entran al índice: si entrara el nombre
        # completo, «PERFORMANCE» quedaría asociada a Schroder y volveríamos al
        # problema de la v1 por la puerta de atrás.
        for w in _palabras(f.get("den"))[:_VENTANA_MARCA]:
            idx.setdefault(w, {})
            idx[w][original] = idx[w].get(original, 0) + 1
    return idx


def sugerir_contraparte(denominacion: Any,
                        idx: dict[str, dict[str, int]]) -> tuple[str, str]:
    """→ `(contraparte, por qué)`. Vacío si no hay evidencia LIMPIA.

    Lee las primeras palabras **de izquierda a derecha** y se queda con la
    PRIMERA que resuelva: en estos nombres la marca va adelante y el producto
    atrás, así que la primera que el índice conoce es la marca.

    Para resolver, esa palabra tiene que apuntar a una contraparte que se lleve
    al menos el 80% de sus cuentas y tenga **más de una**: una sola cuenta no es
    un patrón, es una coincidencia.

    ⚠️ Devolver `("", "")` es una respuesta y no una falla. Callarse deja la
    fila para escribir a mano —que es como estaba—; proponer mal hace que
    alguien tilde una cuenta equivocada, y eso mueve el AuM.
    """
    for palabra in _palabras(denominacion)[:_VENTANA_MARCA]:
        candidatos = idx.get(palabra) or {}
        if not candidatos:
            continue
        # ⚠️ **DESEMPATE POR EL NOMBRE DE LA PROPIA CONTRAPARTE.** Varias
        # comparten marca y se distinguen por un sufijo que dice QUÉ entidad es
        # («ALLARIA» el fondo, «ALLARIA - ALYC» el agente). La cuenta
        # «ALLARIA S.A. ALYC» trae ese sufijo en su denominación: si una sola
        # candidata lo tiene en el nombre, es ésa. Sin esto ganaba siempre la
        # que tiene más cuentas, que es la otra.
        propias = set(_palabras(denominacion))
        afines = {c: len(propias & set(_palabras(c))) for c in candidatos}
        mejor = max(afines.values())
        if mejor > 0 and sum(1 for v in afines.values() if v == mejor) == 1:
            candidatos = {c: n for c, n in candidatos.items() if afines[c] == mejor}
        total = sum(candidatos.values())
        cp, n = max(candidatos.items(), key=lambda kv: (kv[1], kv[0]))
        if n < 2 or n / total < _DOMINANCIA:
            # La palabra existe pero está repartida: no se usa, y tampoco se
            # sigue buscando — si la MARCA es ambigua, lo que venga después es
            # producto y sería peor.
            return "", ""
        return cp, f"«{palabra}» está en {n} cuenta(s) de {cp}"
    return "", ""


def importar_masivo(rows: list[dict], *, actor: str | None = None) -> dict:
    """Import de Excel: completa contraparte/segmento/codigo_mae de filas YA existentes.

    Matchea por `cuenta` (exacta) o, si no vino, por `denominacion` normalizada.
    Solo ESCRIBE: los valores vacíos no pisan nada y nunca da de alta cuentas nuevas
    (una denominación que matchea 2+ cuentas se reporta ambigua y se saltea)."""
    existentes = _q(
        f"SELECT c.id_cuenta AS cuenta, {_DEN} AS denominacion "
        f"FROM contrapartes c LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta", {})
    cuentas = {str(r["cuenta"]).strip() for r in existentes if r["cuenta"] is not None}
    por_den: dict[str, set[str]] = {}
    for r in existentes:
        d = _norm_den(r["denominacion"])
        if d and r["cuenta"] is not None:
            por_den.setdefault(d, set()).add(str(r["cuenta"]).strip())

    # Última fila gana si el Excel repite la misma cuenta.
    sets_por_cuenta: dict[str, dict] = {}
    sin_datos = sin_clave = 0
    no_encontradas: list[str] = []
    ambiguas: list[str] = []
    for row in rows:
        vals = {k: _s(row.get(k)) for k in ("contraparte", "segmento", "codigo_mae")}
        if vals["codigo_mae"]:
            vals["codigo_mae"] = vals["codigo_mae"].upper()
        if not any(vals.values()):
            sin_datos += 1
            continue
        cuenta = _s(row.get("cuenta"))
        den = _norm_den(row.get("denominacion"))
        if cuenta:
            if cuenta not in cuentas:
                no_encontradas.append(cuenta)
                continue
        elif den:
            hits = por_den.get(den) or set()
            if not hits:
                no_encontradas.append(str(row.get("denominacion") or "")[:120])
                continue
            if len(hits) > 1:
                ambiguas.append(str(row.get("denominacion") or "")[:120])
                continue
            cuenta = next(iter(hits))
        else:
            sin_clave += 1
            continue
        sets_por_cuenta.setdefault(cuenta, {}).update({k: v for k, v in vals.items() if v})

    if not sets_por_cuenta:
        return {"actualizadas": 0, "sin_datos": sin_datos, "sin_clave": sin_clave,
                "n_no_encontradas": len(no_encontradas), "no_encontradas": no_encontradas[:50],
                "n_ambiguas": len(ambiguas), "ambiguas": ambiguas[:50]}

    now = datetime.now(UTC)
    params = [{"contraparte": None, "segmento": None, "codigo_mae": None, **s,
               "por": actor, "at": now, "idc": idc}
              for idc, s in sets_por_cuenta.items()]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(_IMPORT_UPD, params)
        conn.commit()
    return {"actualizadas": len(params), "sin_datos": sin_datos, "sin_clave": sin_clave,
            "n_no_encontradas": len(no_encontradas), "no_encontradas": no_encontradas[:50],
            "n_ambiguas": len(ambiguas), "ambiguas": ambiguas[:50]}


def reconciliar(*, limit: int = 500) -> dict:
    """Conciliador (panel derecho): cuentas de Aunesa que NO están en contrapartes y cuya
    `denominacion` matchea el nombre de una contraparte conocida. Pega Aunesa LIVE."""
    existentes = {str(r["id_cuenta"]) for r in
                  _q("SELECT id_cuenta FROM contrapartes WHERE id_cuenta IS NOT NULL "
                     "AND id_cuenta <> ''")}
    keywords: dict[str, str] = {}
    for r in _q("SELECT DISTINCT contraparte FROM contrapartes WHERE contraparte IS NOT NULL"):
        k = _norm_keyword(r["contraparte"])
        if k:
            keywords[k] = r["contraparte"].strip()
    kw_sorted = sorted(keywords, key=lambda x: (-len(x), x))
    kw_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in kw_sorted) + r")\b") if kw_sorted else None

    resp = aunesa.get("cuentas/listadoCuentas")
    if resp.status_code != 200:
        raise RuntimeError(f"Aunesa cuentas/listadoCuentas devolvió status {resp.status_code}")
    data = resp.json()
    if isinstance(data, dict):
        data = data.get("cuentas") or data.get("data") or []

    candidatos: list[dict] = []
    for c in data:
        if not isinstance(c, dict) or (c.get("estado") or "") != "Activa":
            continue
        tipo_cli = (c.get("disposicionesGenerales") or {}).get("tipoCliente")
        if tipo_cli in _TIPOS_PH:
            continue
        cid = c.get("id")
        if cid is None:
            continue
        cid = str(cid)
        if cid in existentes:
            continue
        den = c.get("denominacion") or ""
        m = kw_re.search(den.upper()) if kw_re else None
        if not m:
            continue
        match = m.group(1)
        candidatos.append({
            "cuenta": cid, "denominacion": den,
            "contraparte_sugerida": keywords[match],
            "segmento_sugerido": inferir_segmento(den, keywords[match]),
            "keyword": keywords[match], "tipo_cliente": tipo_cli,
        })
        if len(candidatos) >= limit:
            break
    candidatos.sort(key=lambda x: x["denominacion"] or "")
    return {"candidatos": candidatos, "n": len(candidatos)}
