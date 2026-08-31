"""snapshot_cierre.py — materializa el cierre diario por bono SQL-NATIVE.

Lee directamente de Trading.MarketSnapshot al cierre. Como el motor de
mercado para a 17:05 ART y este cron corre 17:25 ART (20:25 UTC),
MarketSnapshot ya no recibe más writes del día — su estado representa
el cierre real (last_price, métricas analíticas, total_nominals).

Esto desacopla el cierre de TimeSales: ya no agregamos trades del día,
leemos directo el último estado del MarketSnapshot. Los analíticos
(tea, tem, duration, paridad, etc.) los escribió curvas.py durante la
rueda; los de precio (last_price, total_nominals) los escribió valores.py.

SQL-NATIVE (cutover 2026-06-24): `Trading.SnapshotsCierre` (Mongo) fue migrada
→ dropeada. Este job escribe DOS tablas Postgres (write_native, incondicional):
  • `mercado.snapshots_cierre_hist` — HISTÓRICO por (fecha, curva, ticker).
  • `mercado.snapshots_cierre`      — ÚLTIMO precio por ticker (ticker,
    last_price, fecha). Es el FALLBACK DE PRECIO del PnL (api/services/pnl_sql).
    El job corre por el día actual → upsertear (ticker, hoy) deja siempre el
    cierre más reciente como "último". Antes esta tabla la derivaba el sync
    desde Mongo (sync_snapshots_cierre, eliminado en el cutover).

GUARD contra feriados / días sin trades: si metrics.total_nominals == 0
o metrics.last_price == 0, skip — no persistir snapshot stale (los
analíticos pueden quedar congelados de cierres anteriores).

IDEMPOTENTE: re-correr el mismo día produce las mismas filas. Upsert por
(fecha, curva, ticker) en hist y por (ticker) en el último-precio.

Uso:
    python -m jobs.snapshot_cierre              # cierre del día UTC actual
    python -m jobs.snapshot_cierre --fecha 2026-04-25
    python -m jobs.snapshot_cierre --dry        # no persiste, imprime resumen
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime

from core.pg_mirror import write_native

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("SnapshotCierre")

# Curvas cuyo cierre persistimos cada día leyendo MarketSnapshot. Soberanos
# (Hard Dólar) entró acá para alimentar la serie de Retorno Total: sin esto su
# histórico en SnapshotsCierre se cortaba y la vista quedaba con <2 puntos. No
# necesita el split globales/bonares por jurisdicción — `tipo` ya viene por ticker.
#
# ⚠️ `dolar_linked` y `tamar` se sumaron el 2026-08-16, y no es cosmético.
# `mercado.snapshots_cierre` es el FALLBACK DE PRECIO del motor de PnL, y su
# upsert **solo avanza** (`WHERE EXCLUDED.fecha >= fecha`): nunca borra. Entonces
# un bono que alguna vez entró al barrido y después dejó de entrar no da error —
# se queda con el último precio que tuvo, PARA SIEMPRE, y la valuación lo sigue
# usando como si fuera de hoy. SALUD tampoco lo ve: su contrato de frescura mira
# `max(fecha)` de la tabla, que sigue fresco mientras cualquier otro actualice.
#
# Medido en prod (2026-08): 5 bonos VIVOS y EN CARTERA quedaron
# con el precio del 30-abr, desviados entre 5,8% y 9,6% del valor real —
# TTS26 (17 cuentas, 5.153 M de nominales), TTD26 (6 cuentas, 1.784 M), D30S6
# (21 cuentas), TZV27 y TZV28. Los cinco viven en estas dos familias.
#
# Las ONs (`on_*`, 155 bonos) siguen AFUERA: es una decisión aparte por volumen
# —serían ~155 filas más por rueda en `snapshots_cierre_hist`— y hay que tomarla
# con el dato de cuántas están en cartera, no por analogía con este fix.
CURVAS_V1 = ("tasa_fija", "cer", "soberanos", "dolar_linked", "tamar")


def _meta_curvas(curva: str, *, fit: bool = False) -> dict[str, dict]:
    """Los bonos de esa curva → `símbolo de mercado → ficha`.

    **La pertenencia la deciden los EJES, no la columna `curva`** (2026-08-16).

    Antes esto era `curvas_sql.por_curva(curva)`, que filtra por el campo `curva`
    del blob — una palabra escrita A MANO en cada fila. Medido: había 6
    corporativos y 1 BOPREAL adentro de `soberanos` porque alguien tipeó eso.
    Ahora el criterio sale de `core.curvas_ejes.sql_universo`, que es el MISMO que
    usa la vista: un bono no puede estar en una tabla y en otra a la vez.

    **La CLAVE que se escribe sigue siendo el mismo string** (`'cer'`,
    `'tasa_fija'`…). Es lo que hace que este cambio no toque una sola fila de las
    4.896 que ya hay en `snapshots_cierre_hist`, y que `fair_value`, los forwards
    y el z-score sigan leyendo por clave sin enterarse de nada.

    `fit=True` agrega `emisor_tipo='soberano'`. Es la diferencia entre lo que se
    MUESTRA y lo que entra al CÁLCULO: un corporativo tiene spread de crédito y
    meterlo al ajuste de la curva soberana la corre para todos los demás, sin que
    nada se vea raro. Verificado contra producción: con `fit` el universo de hoy
    se reproduce BONO POR BONO en las dos curvas que tienen fit persistido
    (tasa_fija 11=11, cer 22=22) — o sea que el día uno no se mueve un número.

    Con esto `mercado.curvas.curva` deja de tener lectores acá, que era el último
    candado para poder borrarla.
    """
    from core.curvas_ejes import sql_universo
    from core.postgres import get_pool

    donde = sql_universo(curva, fit=fit)
    if donde is None:                     # curva no mapeada: no se adivina
        logger.warning("curva %r sin universo en curvas_ejes — 0 bonos", curva)
        return {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT instrumento, ticker AS ticker_corto, tipo, fecha_vencimiento, "
            "       fecha_emision, cupon_anual "
            f"FROM mercado.curvas WHERE ({donde}) "
            "  AND instrumento IS NOT NULL AND btrim(instrumento) <> ''")
        cols = [d[0] for d in cur.description]
        filas = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
    return {f["instrumento"]: f for f in filas}


def _market_snapshots(tickers: list[str]) -> dict[str, dict]:
    """Estado del cierre — SQL-only (mercado.market_snapshot). ticker →
    {ticker, metrics: {last_price, total_nominals, TEA, TEM, duration,
    mod_duration, convexity, paridad}, book, updated_at}."""
    from core.market_snapshot import snapshot_docs
    return snapshot_docs(tickers)


def _norm_fecha(v) -> str | None:
    """Normaliza fecha (datetime o str) a 'YYYY-MM-DD' o None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = str(v)
    return s[:10] if len(s) >= 10 else None


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _write_ultimo_precio(rows: list[dict]) -> int:
    """Upsert GUARDADO de `mercado.snapshots_cierre` (último precio por ticker).

    Sólo avanza la fila si la `fecha` nueva es >= la persistida (`WHERE EXCLUDED.fecha
    >= snapshots_cierre.fecha`). Replica la semántica del viejo sync (último por MAX
    ts_cierre): así un backfill `--fecha` de un día VIEJO no pisa el cierre más reciente
    que ya alimenta el fallback de precio del PnL. Best-effort: nunca levanta."""
    if not rows:
        return 0
    try:
        from core.postgres import get_pool
        vals = [(r["ticker"], r["last_price"], r["fecha"]) for r in rows]
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO mercado.snapshots_cierre (ticker, last_price, fecha) "
                "VALUES (%s, %s, %s) ON CONFLICT (ticker) DO UPDATE SET "
                "last_price = EXCLUDED.last_price, fecha = EXCLUDED.fecha "
                "WHERE mercado.snapshots_cierre.fecha IS NULL "
                "OR EXCLUDED.fecha >= mercado.snapshots_cierre.fecha",
                vals,
            )
        return len(vals)
    except Exception as e:
        logger.error("snapshots_cierre (último) falló: %s", str(e).splitlines()[0][:200])
        return 0


def _pg_date(v: str | None) -> date | None:
    """'YYYY-MM-DD' (output de _norm_fecha) → date para el espejo SQL."""
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def procesar_curva(
    curva: str, fecha_str: str, dry: bool,
) -> tuple[int, list[dict], list[dict]]:
    """Procesa una curva leyendo MarketSnapshot.
    Skipea tickers sin trades del día (last_price == 0 o total_nominals == 0).
    Devuelve (n_ok, filas_hist, filas_last) — las filas para las dos tablas SQL:
    `mercado.snapshots_cierre_hist` (histórico) y `mercado.snapshots_cierre`
    (último precio por ticker, fallback del PnL). El caller las escribe juntas."""
    # DOS universos, porque las dos tablas se preguntan cosas distintas:
    #
    #   · `snapshots_cierre` (precio del PnL) → el AMPLIO. Cuantos más tickers
    #     tengan cierre, mejor: la tabla NUNCA borra, así que un bono que se cae
    #     del barrido queda con el precio congelado para siempre. Acá el error
    #     caro es de MENOS, nunca de más.
    #   · `snapshots_cierre_hist` (lo que lee el fit de fair value) → el ESTRICTO.
    #     Acá el error caro es al revés: un corporativo de más corre la curva
    #     soberana para todos los demás, y no se ve.
    #
    # Con un solo universo hay que elegir cuál de los dos se rompe. Medido: con el
    # amplio entran 3 corporativos al fit de CER; con el estricto, 7 bonos de
    # `soberanos` pierden su precio — que es EXACTAMENTE el bug de los precios
    # congelados que se acaba de arreglar.
    metas = _meta_curvas(curva)                              # amplio  → precios
    del_fit = set(_meta_curvas(curva, fit=True))             # estricto → el fit
    if not metas:
        logger.warning("[%s] sin tickers en mercado.curvas — saltando", curva)
        return 0, [], []

    tickers = list(metas.keys())
    snaps = _market_snapshots(tickers)
    if not snaps:
        logger.warning("[%s %s] sin docs en MarketSnapshot — saltando", curva, fecha_str)
        return 0, [], []

    n_ok = 0
    n_skip = 0
    hist_rows: list[dict] = []
    last_rows: list[dict] = []
    fecha_d = date.fromisoformat(fecha_str)
    for ticker, meta in metas.items():
        snap = snaps.get(ticker)
        if not snap:
            n_skip += 1
            continue
        metrics = snap.get("metrics") or {}
        last_price = _to_float(metrics.get("last_price"))
        total_nominals = _to_float(metrics.get("total_nominals"))

        # GUARD: feriados / días sin trades — last_price == 0 o
        # total_nominals == 0 indica que el motor arrancó pero no recibió
        # ningún trade. Las analíticas (TEA/duration/etc) que están en el
        # doc son del cierre anterior — NO persistir como cierre del día
        # actual o ensuciamos la serie histórica.
        if not last_price or not total_nominals:
            n_skip += 1
            continue

        cupon = meta.get("cupon_anual")
        is_zero_coupon = (cupon is None) or (_to_float(cupon) == 0.0)

        n_ok += 1
        if dry:
            continue
        # Fila del HISTÓRICO (mercado.snapshots_cierre_hist; ts_cierre → fecha date,
        # strings de fecha → date). Ver sql/schema.sql §CAPA MERCADO.
        # El precio va SIEMPRE (abajo); al histórico del fit, solo el estricto.
        if ticker not in del_fit:
            last_rows.append({"ticker": ticker, "last_price": last_price,
                              "fecha": fecha_d})
            continue
        hist_rows.append({
            "fecha": fecha_d, "curva": curva, "ticker": ticker,
            "ticker_corto": meta.get("ticker_corto"), "tipo": meta.get("tipo"),
            "fecha_vencimiento": _pg_date(_norm_fecha(meta.get("fecha_vencimiento"))),
            "fecha_emision": _pg_date(_norm_fecha(meta.get("fecha_emision"))),
            "ultimo_precio": last_price,
            "tea": _to_float(metrics.get("TEA")), "tem": _to_float(metrics.get("TEM")),
            "paridad": _to_float(metrics.get("paridad")),
            "duration": _to_float(metrics.get("duration")),
            "mod_duration": _to_float(metrics.get("mod_duration")),
            "convexity": _to_float(metrics.get("convexity")),
            "total_nominals_dia": total_nominals,
            "is_zero_coupon": is_zero_coupon if curva == "cer" else None,
        })
        # Fila del ÚLTIMO precio por ticker (mercado.snapshots_cierre, fallback del PnL).
        # El job corre por el día actual → upsertear (ticker, hoy) deja el cierre más
        # reciente como "último". Antes lo derivaba sync_snapshots_cierre desde Mongo.
        last_rows.append({"ticker": ticker, "last_price": last_price, "fecha": fecha_d})

    logger.info("[%s %s] %d bonos persistidos (%d skipped)",
                curva, fecha_str, n_ok, n_skip)
    return n_ok, hist_rows, last_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha", help="YYYY-MM-DD (default: hoy UTC)")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    if args.fecha:
        try:
            fecha_d = date.fromisoformat(args.fecha)
        except ValueError:
            raise SystemExit(f"--fecha inválida: {args.fecha}") from None
    else:
        fecha_d = datetime.now(UTC).date()
    fecha_str = fecha_d.isoformat()

    # Lectura 100% SQL (mercado.market_snapshot + mercado.curvas). El cierre se
    # escribe SQL-native — ya no se toca Mongo.
    from core.job_runs import JobRunLogger
    with JobRunLogger("snapshot_cierre") as jr:
        total = 0
        hist_rows: list[dict] = []
        last_rows: list[dict] = []
        for curva in CURVAS_V1:
            n, h_rows, l_rows = procesar_curva(curva, fecha_str, args.dry)
            total += n
            hist_rows.extend(h_rows)
            last_rows.extend(l_rows)
        if not args.dry:
            # Histórico: upsert incondicional por (fecha, curva, ticker).
            write_native("mercado.snapshots_cierre_hist", ["fecha", "curva", "ticker"],
                         hist_rows)
            # Último-por-ticker: upsert guardado (sólo avanza fecha) — fallback del PnL.
            _write_ultimo_precio(last_rows)
        jr.set_stat("docs", total)
        jr.set_stat("fecha", fecha_str)
        jr.set_stat("dry", args.dry)
        logger.info("Total: %d docs en %s", total, fecha_str)
    if args.dry:
        logger.info("(--dry: no se escribió en SQL)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
