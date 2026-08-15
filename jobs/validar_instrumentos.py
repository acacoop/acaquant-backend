"""jobs/validar_instrumentos.py — ningún símbolo guardado puede no existir en Primary.

## Por qué existe

Un símbolo de mercado (`MERV - XMEV - AL30D - 24hs`) es lo ÚNICO que conecta un
papel del catálogo con su precio. Si está mal escrito, o es de una especie que ya
no cotiza, no pasa nada ruidoso: el motor simplemente no lo suscribe y el papel se
queda **sin precio, en silencio**. Esa es la peor forma de romperse — la vista
sigue abriendo, la fila sigue ahí, y el número está viejo o vacío.

El 2026-08-15 se descubrieron 13 símbolos así en `mercado.curvas`. El primer
reflejo fue culpar al discovery ("el catálogo de Primary quedó atrasado"), se lo
refrescó, y los 13 seguían: **no existen**. Nadie lo había notado nunca porque
NINGUNA pantalla se hacía la pregunta.

## Qué hace

Cruza TODOS los símbolos guardados contra el universo real de Primary
(`manager.pyrofex_instruments`, que refresca `scripts/discovery_pyrofex`) y, para
cada huérfano, **propone a qué se parece**. La sugerencia es la mitad útil del
trabajo: saber que `MERV - XMEV - VSCWO - 24hs` no existe no dice nada; saber que
existe `MERV - XMEV - VSCWD - 24hs` convierte el hallazgo en un arreglo de 10
segundos.

Las tres fuentes que se validan son las tres que alimentan market data:

    mercado.curvas.instrumento        → el símbolo con el que se dibuja la curva
    portafolio.assets.instrumento     → lo que el motor de portfolio SUSCRIBE
    portafolio.assets.instrumento_usd → la pata en dólares del catálogo
    mercado.especies.simbolo          → el catálogo de patas

## La vigencia: por qué sin ella el chequeo no sirve

Un título que **amortizó** deja de existir en Primary — y eso no es un error, es
que se terminó. Pero del catálogo **no se puede borrar**: la tenencia histórica lo
referencia, y perder el papel sería perder el pasado de las carteras.

Sin distinguir los dos casos, cada bono vencido sería un huérfano más, todos los
días, para siempre. El reporte tendría cientos de líneas correctas y las dos que
importan quedarían enterradas — que es la forma más común de que una alerta deje
de leerse.

Por eso el job hace PRIMERO la vigencia y después valida **sólo lo vigente**. La
marca se apoya en un HECHO, no en una heurística: la fecha de vencimiento ya pasó.
Sale de `portafolio.assets.vencimiento` y, para los bonos, de
`mercado.curvas.fecha_vencimiento` — la que esté cargada.

El job es dueño únicamente del motivo `vencido`. Si la mesa marcó algo a mano
(un papel sin fecha cargada, un rescate anticipado), ese valor no se toca: el job
no le puede ganar a alguien que sabe algo que la fecha no dice. Y es reversible en
las dos direcciones — si una fecha estaba mal cargada y se corrige a futuro, el
título vuelve a vigente solo.

## Lo que NO hace

**No corrige los símbolos.** Es deliberado: elegir por su cuenta entre `VSCWD` y
`VSCWO` es elegir entre un precio en dólares y uno en pesos, y equivocarse ahí es
peor que no tener precio. Reporta, propone, y la mesa decide en Manager.

Tampoco falla el run cuando encuentra huérfanos — el exit code queda en 0 y el
hallazgo va al log y a las stats de `manager.job_runs`. Un job que se pone en rojo
todos los días deja de leerse.

## Ojo con la dependencia

Si `manager.pyrofex_instruments` está vacía (discovery nunca corrió, o falló), el
universo es vacío y TODO parecería huérfano. Ese caso se detecta y se aborta sin
reportar nada: un falso positivo masivo destruye la confianza en el chequeo mucho
más rápido de lo que un hallazgo real la construye.

Uso:
    python -m jobs.validar_instrumentos
    python -m jobs.validar_instrumentos --dry     # no escribe la vigencia
    python -m jobs.validar_instrumentos --todos   # sin capar el detalle por fuente
"""
from __future__ import annotations

import argparse
import re
from datetime import UTC, date, datetime, timedelta
from difflib import get_close_matches

from core.job_runs import JobRunLogger
from core.postgres import get_pool

# `MERV - XMEV - AL30D - 24hs` → núcleo `AL30D`, plazo `24hs`.
_RE_MERV = re.compile(r"^MERV\s*-\s*XMEV\s*-\s*(?P<core>.+?)\s*-\s*(?P<plazo>\S+)$")
# Primary publica además una forma corta: `CICAOD/24hs`.
_RE_CORTA = re.compile(r"^(?P<core>[^/]+)/(?P<plazo>\S+)$")

_PLAZOS = ("24hs", "CI")
# Sufijo de especie: '' = pesos · D = MEP · C = cable. Es la variación que más
# veces está detrás de un huérfano, porque es la letra que se tipea mal.
_SUFIJOS = ("", "D", "C")

# De dónde salen los símbolos guardados. El 4º campo dice cómo se descarta lo NO
# vigente en esa tabla: `None` = es el propio catálogo (tiene la columna
# `vigente`), un nombre = la columna de ticker con la que se cruza contra los
# tickers dados de baja.
FUENTES: tuple[tuple[str, str, str, str | None], ...] = (
    ("mercado.curvas", "instrumento", "master de curvas", "ticker"),
    ("portafolio.assets", "instrumento", "catálogo (pata ARS)", None),
    ("portafolio.assets", "instrumento_usd", "catálogo (pata USD)", None),
    ("mercado.especies", "simbolo", "catálogo de patas", "ticker"),
)

# Piso de parecido para proponer un símbolo por semejanza de texto. 0.8 deja
# pasar una letra cambiada en un ticker de 5 y frena las coincidencias de
# casualidad, que son ruido y hacen que el reporte no se lea.
_CUTOFF = 0.8


def partir(simbolo: str) -> tuple[str, str] | None:
    """`símbolo → (núcleo, plazo)`, o None si no tiene forma conocida."""
    s = (simbolo or "").strip()
    for rx in (_RE_MERV, _RE_CORTA):
        if m := rx.match(s):
            return m["core"].strip().upper(), m["plazo"].strip()
    return None


def _armar(simbolo: str, core: str, plazo: str) -> str:
    """Reconstruye respetando la FORMA del original (larga o corta)."""
    if _RE_MERV.match((simbolo or "").strip()):
        return f"MERV - XMEV - {core} - {plazo}"
    return f"{core}/{plazo}"


def sugerencias(simbolo: str, universo: set[str], *, tope: int = 3) -> list[str]:
    """A qué símbolos REALES se parece uno que no existe. PURA.

    El orden es por confianza, no alfabético — la primera es la que hay que mirar:

      1. **El mismo papel en el otro plazo.** Es el caso más benigno: el símbolo
         está bien escrito y sólo cotiza en CI (o sólo en 24hs).
      2. **La misma base con otra especie.** `…VSCWO…` no existe pero `…VSCWD…` sí
         → es la pata que falta. Acá es donde aparecen los huérfanos de verdad.
      3. **Parecido de texto** sobre el núcleo, para el error de tipeo que no cae
         en ninguna de las dos anteriores.
    """
    partes = partir(simbolo)
    if not partes:
        return []
    core, plazo = partes
    vistos: list[str] = []

    def _sumar(cand: str) -> None:
        if cand in universo and cand != simbolo and cand not in vistos:
            vistos.append(cand)

    for p in (pz for pz in _PLAZOS if pz != plazo):
        _sumar(_armar(simbolo, core, p))

    # La base es el núcleo sin su letra de especie — pero sólo si esa letra ES
    # una especie. `AL30` no termina en D/C y su base es él mismo.
    base = core[:-1] if core[-1:] in ("D", "C") else core
    for suf in _SUFIJOS:
        for p in (plazo, *(pz for pz in _PLAZOS if pz != plazo)):
            _sumar(_armar(simbolo, base + suf, p))

    if len(vistos) < tope:
        cores = {c for u in universo if (pp := partir(u)) and (c := pp[0])}
        for c in get_close_matches(core, sorted(cores), n=tope, cutoff=_CUTOFF):
            for p in (plazo, *(pz for pz in _PLAZOS if pz != plazo)):
                _sumar(_armar(simbolo, c, p))

    return vistos[:tope]


# ── Vigencia ─────────────────────────────────────────────────────────────────

# El único motivo del que el job es DUEÑO. Lo que la mesa marcó a mano lleva otro
# motivo y no se toca: alguien que sabe que el papel se rescató anticipadamente
# sabe algo que la fecha de vencimiento no dice.
MOTIVO_JOB = "vencido"

ART_OFFSET = timedelta(hours=-3)


def _hoy_art() -> date:
    return (datetime.now(UTC) + ART_OFFSET).date()


def _a_fecha(v) -> date | None:
    """`vencimiento` es text en el catálogo y date en el master. Lo que no parsea
    NO es una fecha vencida: es un dato sin cargar, y no habilita a apagar nada."""
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except (ValueError, TypeError):
        return None


def decidir_vigencia(rows: list[dict], hoy: date) -> list[dict]:
    """`[{unidad, vigente, motivo}]` con SÓLO los que hay que cambiar. PURA.

    Dos direcciones, porque una fecha mal cargada se corrige y el título tiene que
    poder volver:

      · vencida y hoy figura vigente        → apagar con motivo `vencido`
      · apagado por el job y la fecha ya no está vencida → volver a encender

    Nunca toca una fila cuyo `vigencia_motivo` no sea el del job.
    """
    cambios = []
    for r in rows:
        motivo = (r.get("vigencia_motivo") or "").strip()
        if motivo and motivo != MOTIVO_JOB:
            continue                                    # marca humana: no se pisa
        vto = _a_fecha(r.get("vencimiento")) or _a_fecha(r.get("fecha_vencimiento"))
        vigente_ahora = r.get("vigente") is not False
        if vto and vto < hoy:
            if vigente_ahora:
                cambios.append({"unidad": r["unidad"], "vigente": False,
                                "motivo": MOTIVO_JOB})
        elif not vigente_ahora and motivo == MOTIVO_JOB:
            cambios.append({"unidad": r["unidad"], "vigente": True, "motivo": None})
    return cambios


def leer_vigencia() -> list[dict]:
    """Catálogo + la fecha de vencimiento del master, que para los bonos suele
    estar cargada aunque la del catálogo no."""
    cols = ("unidad", "vencimiento", "vigente", "vigencia_motivo", "fecha_vencimiento")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT a.unidad, a.vencimiento, a.vigente, a.vigencia_motivo, "
                    "       c.fecha_vencimiento "
                    "FROM portafolio.assets a "
                    "LEFT JOIN mercado.curvas c ON c.ticker = a.ticker")
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def aplicar_vigencia(cambios: list[dict]) -> int:
    if not cambios:
        return 0
    ts = datetime.now(UTC)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE portafolio.assets SET vigente = %(vigente)s, "
            "vigencia_motivo = %(motivo)s, vigencia_at = %(ts)s "
            "WHERE unidad = %(unidad)s",
            [{**c, "ts": ts} for c in cambios])
        conn.commit()
    return len(cambios)


def tickers_no_vigentes() -> list[str]:
    """Tickers cuyos assets están TODOS dados de baja.

    El `HAVING` no es un detalle: tras el rebautizo de Aunesa un mismo ticker
    puede tener varias `unidad`, y alcanza con que UNA siga vigente para que el
    papel lo esté. Bajarlo porque la unidad vieja venció escondería el bono vivo.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM portafolio.assets "
                    "WHERE ticker IS NOT NULL AND trim(ticker) <> '' "
                    "GROUP BY ticker HAVING bool_and(vigente IS false)")
        return [t for (t,) in cur.fetchall()]


# ── Validación de símbolos ───────────────────────────────────────────────────

def universo_primary() -> set[str]:
    """Todos los tickers que Primary publica hoy. Sin filtrar por formato: la
    forma corta (`CICAOD/24hs`) también es un instrumento real y descartarla
    haría aparecer huérfanos que no lo son."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT trim(i->>'ticker') "
                    "FROM manager.pyrofex_instruments p, "
                    "     jsonb_array_elements(p.instruments) AS i "
                    "WHERE i->>'ticker' IS NOT NULL")
        return {t for (t,) in cur.fetchall() if t}


def guardados(tabla: str, columna: str, col_ticker: str | None,
              bajas: list[str]) -> list[str]:
    """Símbolos VIGENTES de esa fuente. Los nombres se interpolan pero salen de
    `FUENTES`, que es una constante del módulo — no llegan de afuera."""
    if col_ticker is None:
        filtro, params = "AND vigente IS NOT false", ()
    else:
        filtro, params = f"AND NOT ({col_ticker} = ANY(%s))", (bajas,)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT {columna} FROM {tabla} "
                    f"WHERE {columna} IS NOT NULL AND trim({columna}) <> '' {filtro}",
                    params)
        return [v.strip() for (v,) in cur.fetchall() if v and v.strip()]


def revisar(simbolos: list[str], universo: set[str]) -> list[dict]:
    """Los que NO existen, cada uno con a qué se parece. PURA."""
    return [{"simbolo": s, "sugerencias": sugerencias(s, universo)}
            for s in sorted(set(simbolos)) if s not in universo]


def main() -> int:
    ap = argparse.ArgumentParser(description="Símbolos guardados que no existen en Primary")
    ap.add_argument("--dry", action="store_true", help="no escribir la vigencia")
    ap.add_argument("--todos", action="store_true", help="no capar el detalle por fuente")
    args = ap.parse_args()

    with JobRunLogger("validar_instrumentos") as jr:
        # 1) VIGENCIA primero: sin esto, cada bono amortizado sería un huérfano
        #    todos los días y el reporte dejaría de leerse.
        cambios = decidir_vigencia(leer_vigencia(), _hoy_art())
        apagados = sum(1 for c in cambios if not c["vigente"])
        if args.dry:
            jr.log(f"[dry] vigencia: {apagados} a dar de baja por vencimiento, "
                   f"{len(cambios) - apagados} a reactivar — no se escribió")
        else:
            aplicar_vigencia(cambios)
            jr.log(f"vigencia: {apagados} título(s) dados de baja por vencimiento, "
                   f"{len(cambios) - apagados} reactivado(s)")
        jr.set_stat("vigencia_bajas", apagados)
        jr.set_stat("vigencia_altas", len(cambios) - apagados)

        bajas = tickers_no_vigentes()
        jr.set_stat("tickers_no_vigentes", len(bajas))

        # 2) VALIDACIÓN, sólo sobre lo que sigue vivo.
        universo = universo_primary()
        jr.set_stat("universo_primary", len(universo))
        if not universo:
            # Sin universo TODO parece huérfano. Un falso positivo masivo rompe la
            # confianza en el chequeo más rápido de lo que un hallazgo real la crea.
            jr.log("⚠ manager.pyrofex_instruments VACÍA — no se valida nada. "
                   "Correr: python -m scripts.discovery_pyrofex")
            jr.set_stat("abortado_sin_universo", True)
            return 0

        jr.log(f"universo de Primary: {len(universo)} símbolos · "
               f"{len(bajas)} ticker(s) fuera de vigencia (no se validan)")
        total = 0
        for tabla, columna, etiqueta, col_ticker in FUENTES:
            try:
                valores = guardados(tabla, columna, col_ticker, bajas)
            except Exception as e:                       # columna que todavía no existe
                jr.log(f"  · {tabla}.{columna}: no se pudo leer ({e})")
                continue
            huerfanos = revisar(valores, universo)
            total += len(huerfanos)
            jr.log(f"  · {etiqueta} ({tabla}.{columna}): {len(valores)} símbolo(s), "
                   f"{len(huerfanos)} inexistente(s)")
            tope = len(huerfanos) if args.todos else 15
            for h in huerfanos[:tope]:
                cerca = " · ".join(h["sugerencias"]) or "sin parecidos — revisar a mano"
                jr.log(f"      ⚠ {h['simbolo']}  →  {cerca}")
            if len(huerfanos) > tope:
                jr.log(f"      … +{len(huerfanos) - tope} más (verlos: --todos)")
            jr.set_stat(f"{tabla}.{columna}", {"guardados": len(valores),
                                               "inexistentes": len(huerfanos)})

        jr.set_stat("inexistentes_total", total)
        if total:
            jr.log(f"⚠ {total} símbolo(s) guardado(s) NO existen en Primary. No se "
                   f"corrigen solos: la sugerencia se aplica en Manager → TÍTULOS.")
        else:
            jr.log("✅ todos los símbolos guardados existen en Primary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
