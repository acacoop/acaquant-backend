"""jobs/validar_instrumentos.py — vigencia de títulos + marca de validación.

## Qué hace, en dos pasos

**(1) VIGENCIA.** Un título que amortizó deja de existir en el mercado, pero del
catálogo NO se puede borrar: la tenencia histórica lo referencia. Por eso
`portafolio.assets.vigente`, que el job apaga cuando la fecha de vencimiento ya
pasó — de `assets.vencimiento` o de `mercado.curvas.fecha_vencimiento`, la que
esté cargada. Es reversible: si la fecha estaba mal y se corrige, el título
vuelve solo. Y nunca pisa una marca humana — tildar/destildar en Manager sella
`vigencia_motivo='manual'` y el job deja de opinar sobre esa fila.

**(2) VALIDACIÓN.** Cruza cada símbolo de `mercado.especies` contra el catálogo
real de Primary (`manager.pyrofex_instruments`). El que existe queda marcado
`validado` con su fecha; **el que no existe se BORRA**.

Borrar y no marcar-y-dejar es la decisión correcta acá porque una especie es,
por definición, una pata que cotiza. Si Primary no la lista, no es una pata: es
basura que ensucia el catálogo y reaparece en cada reporte. Y no se pierde nada
recuperable — `scripts/sembrar_especies` reconstruye la tabla entera desde el
master y el universo de Primary, así que el día que el símbolo exista de verdad
vuelve solo.

## Dónde se aplica de verdad

El job **no impide** nada — sólo deja el estado escrito y visible. Lo que impide
suscribirse a un símbolo muerto es `core/instrumentos_validos`, aplicado en
`core/websocket.py::agregar_suscripciones`, que es el único punto por el que
pasan TODAS las suscripciones de TODOS los motores.

Las dos cosas leen **la misma fuente** (`manager.pyrofex_instruments`) para que
no puedan contradecirse: lo que la marca dice es exactamente lo que el filtro
hace. Una segunda copia del criterio se queda vieja y nadie sabe cuál manda.

## Ojo con la dependencia

Si `manager.pyrofex_instruments` está vacía el job ABORTA sin marcar nada: con
un catálogo vacío todo parecería inválido, y una tabla llena de falsos negativos
es peor que no tener la marca.

Uso:
    python -m jobs.validar_instrumentos
    python -m jobs.validar_instrumentos --dry     # no escribe nada
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta

from core.instrumentos_validos import validos
from core.job_runs import JobRunLogger
from core.postgres import get_pool

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


# ── Marca de validación ──────────────────────────────────────────────────────

def decidir_validacion(simbolos: list[str], universo: set[str]) -> tuple[list[str], list[str]]:
    """`(a_marcar, a_borrar)`. PURA.

    Los válidos se re-marcan TODOS y no sólo los que cambian: la marca lleva
    `validado_at`, y saber CUÁNDO se confirmó por última vez que un símbolo
    existe vale tanto como el booleano — sin eso, un `true` de hace tres meses no
    se distingue de uno de hoy.
    """
    ok, fuera = [], []
    for s in sorted(set(simbolos)):
        (ok if s in universo else fuera).append(s)
    return ok, fuera


def leer_simbolos() -> list[str]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT simbolo FROM mercado.especies "
                    "WHERE simbolo IS NOT NULL AND trim(simbolo) <> ''")
        return [s.strip() for (s,) in cur.fetchall() if s and s.strip()]


def aplicar_validacion(ok: list[str], borrar: list[str]) -> tuple[int, int]:
    """Marca los válidos y BORRA los que no existen, en UNA transacción."""
    ts = datetime.now(UTC)
    with get_pool().connection() as conn, conn.cursor() as cur:
        if ok:
            cur.execute("UPDATE mercado.especies SET validado = true, validado_at = %s "
                        "WHERE simbolo = ANY(%s)", (ts, ok))
        if borrar:
            cur.execute("DELETE FROM mercado.especies WHERE simbolo = ANY(%s)", (borrar,))
        conn.commit()
    return len(ok), len(borrar)


def main() -> int:
    ap = argparse.ArgumentParser(description="Vigencia de títulos + marca de validación")
    ap.add_argument("--dry", action="store_true", help="no escribir nada")
    args = ap.parse_args()

    with JobRunLogger("validar_instrumentos") as jr:
        # 1) VIGENCIA — qué títulos siguen existiendo.
        cambios = decidir_vigencia(leer_vigencia(), _hoy_art())
        bajas = sum(1 for c in cambios if not c["vigente"])
        if not args.dry:
            aplicar_vigencia(cambios)
        jr.log(f"{'[dry] ' if args.dry else ''}vigencia: {bajas} baja(s) por "
               f"vencimiento, {len(cambios) - bajas} reactivación(es)")
        jr.set_stat("vigencia_bajas", bajas)
        jr.set_stat("vigencia_altas", len(cambios) - bajas)
        no_vig = tickers_no_vigentes()
        jr.set_stat("tickers_no_vigentes", len(no_vig))
        jr.set_stat("tickers_no_vigentes_lista", no_vig[:200])

        # 2) VALIDACIÓN — mismo criterio y misma fuente que el filtro del WS.
        universo = validos(forzar=True)
        if universo is None:
            # Sin catálogo creíble, marcar sería llenar la tabla de falsos
            # negativos. El filtro del WS toma la misma decisión: no opinar.
            jr.log("⚠ catálogo de Primary no disponible o vacío — no se marca "
                   "nada. Correr: python -m scripts.discovery_pyrofex")
            jr.set_stat("abortado_sin_universo", True)
            return 0

        ok, borrar = decidir_validacion(leer_simbolos(), universo)
        if not args.dry:
            aplicar_validacion(ok, borrar)
        jr.log(f"{'[dry] ' if args.dry else ''}validación: {len(ok)} símbolo(s) "
               f"validado(s) · {len(borrar)} BORRADO(s) por no existir en Primary "
               f"(universo: {len(universo)})")
        for sim in borrar:
            jr.log(f"      🗑 {sim}")
        jr.set_stat("simbolos_validados", len(ok))
        jr.set_stat("simbolos_borrados", len(borrar))
        jr.set_stat("simbolos_borrados_lista", sorted(borrar)[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
