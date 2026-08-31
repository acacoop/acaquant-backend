"""jobs/fred_research.py — sincroniza series de FRED a Postgres (tab Datos
Internacionales). Doc vivo: docs/RESEARCH.md.

Feed SEPARADO de jobs/bcra.py / jobs/argentina_datos.py (motores) — no se tocan.
Alimenta `research.fred_observations` SOLO para el universo curado
(`research.fred_watch`). Arranque: bloque Tasas USA (10 series, IDs verificados
contra fred.stlouisfed.org 2026-07-19). Eficiencia primero: incremental por
watermark con COLCHÓN por frecuencia (FRED revisa datos hacia atrás), nada de
re-bajar historia que ya está.

Modos:
    --dry-run    universo + watermarks + qué haría (0 requests de series)
    --backfill   historia de cada serie del watch (una vez; throttled). Por
                 defecto desde 2020-01-01 (decisión del user); --desde la cambia.
    (default)    incremental: desde = max(fecha) − colchón por serie (D:−10d,
                 otras:−95d, para capturar revisiones). Refresca metadata del
                 watch 1×/corrida.

Cron: 4×/día L-V (deploy/crontab.txt) — FRED publica en días hábiles USA; la
vista sirve SIEMPRE de la DB (24/7 real). Necesita env FRED_API_KEY.
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from core import fred_api
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Seed inicial — bloque TASAS USA. IDs VERIFICADOS contra el catálogo vivo de FRED
# (2026-07-19). (bloque, series_id, etiqueta, unidad, freq, pais, orden).
# Editable después en research.fred_watch. DFEDTARU quedó AFUERA (no verificado).
_SEED: list[tuple[str, str, str, str, str, str, int]] = [
    ("tasas_usa", "DGS2",     "UST 2Y",              "%", "D", "EEUU", 1),
    ("tasas_usa", "DGS5",     "UST 5Y",              "%", "D", "EEUU", 2),
    ("tasas_usa", "DGS10",    "UST 10Y",             "%", "D", "EEUU", 3),
    ("tasas_usa", "DGS30",    "UST 30Y",             "%", "D", "EEUU", 4),
    ("tasas_usa", "DFII10",   "UST 10Y real (TIPS)", "%", "D", "EEUU", 5),
    ("tasas_usa", "T10Y2Y",   "Spread 10Y−2Y",       "%", "D", "EEUU", 6),
    ("tasas_usa", "T10Y3M",   "Spread 10Y−3M",       "%", "D", "EEUU", 7),
    ("tasas_usa", "SOFR",     "SOFR",                "%", "D", "EEUU", 8),
    ("tasas_usa", "FEDFUNDS", "Fed Funds (efectiva)", "%", "M", "EEUU", 9),
    ("tasas_usa", "DFF",      "Fed Funds (diaria)",  "%", "D", "EEUU", 10),
    # ── Bloque COMMODITIES / AGRO (IDs verificados 2026-07-19). OJO: los granos y
    # el cobre son MENSUALES (precio global del FMI, con rezago); WTI/Brent/oro/gas
    # son DIARIOS. Unidades MIXTAS → el default de la vista muestra el complejo soja
    # (misma escala USD/t); comparar series de escalas distintas es para el índice
    # base 100 (transform on-the-fly, pendiente).
    ("commodities", "PSOYBUSDM",        "Soja",                    "USD/t",     "M", "Global", 1),
    ("commodities", "PSMEAUSDM",        "Harina de soja",          "USD/t",     "M", "Global", 2),
    ("commodities", "PSOILUSDM",        "Aceite de soja",          "USD/t",     "M", "Global", 3),
    ("commodities", "PMAIZMTUSDM",      "Maíz",                    "USD/t",     "M", "Global", 4),
    ("commodities", "PWHEAMTUSDM",      "Trigo",                   "USD/t",     "M", "Global", 5),
    ("commodities", "PCOPPUSDM",        "Cobre",                   "USD/t",     "M", "Global", 6),
    ("commodities", "DCOILWTICO",       "Petróleo WTI",            "USD/bbl",   "D", "Global", 7),
    ("commodities", "DCOILBRENTEU",     "Petróleo Brent",          "USD/bbl",   "D", "Global", 8),
    ("commodities", "DHHNGSP",          "Gas natural (Henry Hub)", "USD/MMBtu", "D", "Global", 9),
    # ── Bloque EEUU MACRO (vista 3). IDs canónicos verificados. Unidades MUY
    # mixtas (índice/miles/%/claims) → se leen con las transformaciones de la vista
    # (Base 100 / Var %). Default Nivel muestra los 3 índices de precios (misma escala).
    ("eeuu_macro", "CPIAUCSL", "CPI (nivel)",                  "índice", "M", "EEUU", 1),
    ("eeuu_macro", "CPILFESL", "CPI núcleo",                   "índice", "M", "EEUU", 2),
    ("eeuu_macro", "PCEPILFE", "PCE núcleo (target Fed)",      "índice", "M", "EEUU", 3),
    ("eeuu_macro", "PAYEMS",   "Nóminas no agrícolas",         "miles",  "M", "EEUU", 4),
    ("eeuu_macro", "UNRATE",   "Desempleo",                    "%",      "M", "EEUU", 5),
    ("eeuu_macro", "ICSA",     "Pedidos seguro desempleo",     "claims", "W", "EEUU", 6),
    ("eeuu_macro", "GDPC1",    "PBI real",                     "MM USD", "Q", "EEUU", 7),
    ("eeuu_macro", "INDPRO",   "Producción industrial",        "índice", "M", "EEUU", 8),
    ("eeuu_macro", "UMCSENT",  "Confianza consumidor (UMich)", "índice", "M", "EEUU", 9),
    ("eeuu_macro", "T10YIE",   "Breakeven inflación 10Y",      "%",      "D", "EEUU", 10),
    # ── Bloque ÍNDICES (bolsa) — SP500/Nasdaq/Dow. Diarios. Sirven de sub-tab Y de
    # referencia para superponer (2º eje Y) en cualquier otro bloque de la vista.
    # OJO: SP500 y DJIA tienen tope de 10 años en FRED (licencia S&P DJ); como el
    # backfill arranca en 2020, no nos afecta. Nasdaq trae historia completa.
    ("indices", "SP500",     "S&P 500",          "índice", "D", "EEUU", 1),
    ("indices", "NASDAQCOM", "Nasdaq Composite", "índice", "D", "EEUU", 2),
    ("indices", "DJIA",      "Dow Jones",        "índice", "D", "EEUU", 3),
    # ── Bloque VOLATILIDAD (familia VIX, CBOE) — todas en la MISMA escala (~10-80
    # pts) → combinan en una sola vista. Historia completa, sin límite de licencia.
    ("volatilidad", "VIXCLS", "VIX (S&P 500)",      "pts", "D", "EEUU",   1),
    ("volatilidad", "VXNCLS", "VXN (Nasdaq 100)",   "pts", "D", "EEUU",   2),
    ("volatilidad", "VXDCLS", "VXD (Dow)",          "pts", "D", "EEUU",   3),
    ("volatilidad", "RVXCLS", "RVX (Russell 2000)", "pts", "D", "EEUU",   4),
    ("volatilidad", "OVXCLS", "OVX (petróleo)",     "pts", "D", "Global", 5),
    ("volatilidad", "GVZCLS", "GVZ (oro)",          "pts", "D", "Global", 6),
    # ── Bloque DÓLAR / FX global — escalas MUY distintas (índice ~120 vs BRL ~5 vs
    # JPY ~150) → la vista arranca en Base 100 (frontend BLOQUE_MODO_DEFAULT).
    ("dolar_fx", "DTWEXBGS", "Dólar amplio (índice)", "índice", "D", "EEUU",     1),
    ("dolar_fx", "DEXBZUS",  "Real (BRL/USD)",        "BRL",    "D", "Brasil",   2),
    ("dolar_fx", "DEXCHUS",  "Yuan (CNY/USD)",        "CNY",    "D", "China",    3),
    ("dolar_fx", "DEXMXUS",  "Peso mex. (MXN/USD)",   "MXN",    "D", "Mexico",   4),
    ("dolar_fx", "DEXJPUS",  "Yen (JPY/USD)",         "JPY",    "D", "Japon",    5),
    ("dolar_fx", "DEXUSEU",  "Euro (USD/EUR)",        "USD",    "D", "Eurozona", 6),
    # ── Bloque RIESGO / CRÉDITO — CUADRANTES (crédito USA · EM · condiciones). OJO:
    # los spreads ICE BofA solo tienen ~3 años de historia en FRED (recorte 2026).
    ("riesgo_credito", "BAMLH0A0HYM2",      "High Yield USA",       "pp",     "D", "EEUU", 1),
    ("riesgo_credito", "BAMLC0A0CM",        "Investment Grade USA", "pp",     "D", "EEUU", 2),
    ("riesgo_credito", "BAMLEMCBPIOAS",     "EM Corporativo",       "pp",     "D", "EM",   3),
    ("riesgo_credito", "BAMLEMHBHYCRPIOAS", "EM High Yield",        "pp",     "D", "EM",   4),
    ("riesgo_credito", "NFCI",              "Cond. fin. (Chicago Fed)", "índice", "W", "EEUU", 5),
    ("riesgo_credito", "STLFSI4",           "Estrés fin. (St. Louis Fed)", "índice", "W", "EEUU", 6),
    # ── Bloque MACRO GLOBAL — CUADRANTES (China · Brasil · Liquidez Fed). China vía
    # OECD (CPI con algo de rezago); liquidez Fed en millones (WALCL/WRESBAL) y M2 en
    # miles de millones → distinta escala, se separan por grupo.
    ("macro_global", "CHNCPIALLMINMEI", "CPI China",             "índice", "M", "China",  1),
    ("macro_global", "XTEXVA01CNM667S", "Exportaciones China",   "USD",    "M", "China",  2),
    ("macro_global", "TRESEGCNM052N",   "Reservas China",        "USD",    "M", "China",  3),
    ("macro_global", "IRSTCB01BRM156N", "Selic (Brasil)",        "%",      "M", "Brasil", 4),
    ("macro_global", "BRACPIALLMINMEI", "IPCA (CPI Brasil)",     "índice", "M", "Brasil", 5),
    ("macro_global", "WALCL",           "Balance de la Fed",     "M USD",  "W", "EEUU",   6),
    ("macro_global", "WRESBAL",         "Reservas bancarias Fed", "M USD", "W", "EEUU",   7),
    ("macro_global", "M2SL",            "M2 (EEUU)",             "MM USD", "M", "EEUU",   8),
]

# Series RETIRADAS: se fuerzan activo=false SIEMPRE (aunque ya estén sembradas en
# prod). GOLDPMGBD228NLBM = LBMA Gold PM Fix, que FRED ELIMINÓ el 2022-01-31 por
# licencia (ICE Benchmark Administration) → la API responde "series does not exist".
# No hay un spot diario de oro bueno en FRED post-2022; si se quiere oro, se sourcea
# de otro lado. (El verificador del workflow lo dio por bueno por error.)
_RETIRADAS: tuple[str, ...] = ("GOLDPMGBD228NLBM",)

_BACKFILL_DESDE_DEFAULT = "2020-01-01"   # decisión del user: de 2020 a hoy


def _colchon_dias(freq: str | None) -> int:
    """Cuánto re-leemos hacia atrás en el incremental. FRED revisa retroactivo:
    las diarias poco (−10d); las mensuales/trimestrales bastante (GDP/payrolls se
    revisan 2-3 publicaciones después → −95d ≈ un trimestre)."""
    return 10 if (freq or "D").upper().startswith("D") else 95


def _seed_watch(cur) -> None:
    """Siembra/actualiza el watch desde _SEED (idempotente por series_id; lo
    editado a mano se preserva vía ON CONFLICT DO NOTHING). Agregar un bloque
    nuevo = sumar filas a _SEED: el próximo run inserta SOLO las nuevas sin tocar
    el resto, y como no tienen watermark el incremental las backfillea desde 2020
    solo (no hace falta correr --backfill de nuevo)."""
    nuevas = 0
    for bloque, sid, etiqueta, unidad, freq, pais, orden in _SEED:
        cur.execute(
            "INSERT INTO research.fred_watch (series_id, bloque, etiqueta, unidad,"
            " freq, pais, orden) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (series_id) DO NOTHING",
            (sid, bloque, etiqueta, unidad, freq, pais, orden),
        )
        nuevas += cur.rowcount
    if nuevas:
        logger.info("fred_research: watch +%s series nuevas (seed total %s)", nuevas, len(_SEED))
    if _RETIRADAS:
        cur.execute("UPDATE research.fred_watch SET activo = false "
                    "WHERE series_id = ANY(%s) AND activo", (list(_RETIRADAS),))


def _watch(cur) -> list[dict]:
    cur.execute("SELECT series_id, bloque, etiqueta, freq FROM research.fred_watch "
                "WHERE activo ORDER BY bloque, orden")
    return [{"id": r[0], "bloque": r[1], "etiqueta": r[2], "freq": r[3]} for r in cur.fetchall()]


def _watermarks(cur) -> dict[str, str]:
    cur.execute("SELECT series_id, max(fecha) FROM research.fred_observations GROUP BY series_id")
    return {r[0]: r[1].isoformat() for r in cur.fetchall() if r[1]}


def _upsert_puntos(cur, series_id: str, puntos: list[dict]) -> int:
    if not puntos:
        return 0
    cur.executemany(
        "INSERT INTO research.fred_observations (series_id, fecha, valor) VALUES "
        "(%(sid)s, %(fecha)s, %(valor)s) "
        "ON CONFLICT (series_id, fecha) DO UPDATE SET valor = EXCLUDED.valor, "
        "ingestado_en = now()",
        [{"sid": series_id, **p} for p in puntos],
    )
    return len(puntos)


def _refrescar_metadata(cur, series: list[dict]) -> int:
    """Metadata del watch (1 request /series por id). Barato y útil para la UI
    (frecuencia, unidad de origen, SA/NSA, rango, last_updated)."""
    n = 0
    for s in series:
        try:
            m = fred_api.metadata(s["id"])
        except Exception as e:
            logger.warning("fred_research: metadata %s falló (%s)", s["id"], e)
            continue
        if not m:
            continue
        cur.execute(
            "INSERT INTO research.fred_series (series_id, title, frequency, units,"
            " seasonal_adj, observation_start, observation_end, last_updated, notes)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (series_id) DO UPDATE SET title=EXCLUDED.title,"
            " frequency=EXCLUDED.frequency, units=EXCLUDED.units,"
            " seasonal_adj=EXCLUDED.seasonal_adj, observation_end=EXCLUDED.observation_end,"
            " last_updated=EXCLUDED.last_updated, actualizado_en=now()",
            (s["id"], m.get("title"), m.get("frequency"), m.get("units"),
             m.get("seasonal_adjustment_short"), m.get("observation_start") or None,
             m.get("observation_end") or None, m.get("last_updated") or None,
             m.get("notes")),
        )
        n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help=f"historia del watch (una vez; desde {_BACKFILL_DESDE_DEFAULT} salvo --desde)")
    ap.add_argument("--desde", help="con --backfill: fecha de inicio YYYY-MM-DD")
    ap.add_argument("--purgar-antes", metavar="YYYY-MM-DD",
                    help="borra observaciones ANTERIORES a esta fecha (limpieza one-off)")
    ap.add_argument("--dry-run", action="store_true", help="muestra qué haría; no pega ni escribe")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    hoy = datetime.now(UTC).date()

    if args.purgar_antes:
        with get_pool().connection() as conn, conn.cursor() as cur:
            if args.dry_run:
                cur.execute("SELECT count(*) FROM research.fred_observations WHERE fecha < %s",
                            (args.purgar_antes,))
                print(f"(DRY-RUN) purgaría {cur.fetchone()[0]} puntos anteriores a {args.purgar_antes}")
                return
            cur.execute("DELETE FROM research.fred_observations WHERE fecha < %s", (args.purgar_antes,))
            print(f"✅ purgados {cur.rowcount} puntos anteriores a {args.purgar_antes}")
        return

    with get_pool().connection() as conn, conn.cursor() as cur:
        _seed_watch(cur)
        series = _watch(cur)
        marcas = _watermarks(cur)

    print(f"Watch: {len(series)} series · con historia: {len(marcas)}")
    if args.dry_run:
        for s in series:
            wm = marcas.get(s["id"])
            if args.backfill or not wm:
                desde = args.desde or _BACKFILL_DESDE_DEFAULT
            else:
                desde = (datetime.fromisoformat(wm).date()
                         - timedelta(days=_colchon_dias(s["freq"]))).isoformat()
            print(f"  [{s['bloque']:10}] {s['id']:9} {s['etiqueta']:22} desde={desde}")
        print("(DRY-RUN — no se pegó a la API ni se escribió)")
        return

    from core.job_runs import JobRunLogger
    with JobRunLogger("fred_research") as jr:
        n_total = n_series_ok = 0
        with get_pool().connection() as conn, conn.cursor() as cur:
            try:
                n_meta = _refrescar_metadata(cur, series)
                jr.log(f"metadata refrescada: {n_meta} series")
            except Exception as e:
                logger.warning("fred_research: metadata falló (%s) — sigo con series", e)
                jr.log(f"metadata ERROR: {e}")
            for s in series:
                wm = marcas.get(s["id"])
                if args.backfill or not wm:
                    desde = args.desde or _BACKFILL_DESDE_DEFAULT
                else:
                    desde = (datetime.fromisoformat(wm).date()
                             - timedelta(days=_colchon_dias(s["freq"]))).isoformat()
                try:
                    puntos = fred_api.observaciones(s["id"], desde=desde, hasta=hoy.isoformat())
                    n = _upsert_puntos(cur, s["id"], puntos)
                except Exception as e:
                    logger.warning("fred_research: serie %s (%s) falló (%s)",
                                   s["id"], s["etiqueta"], e)
                    jr.log(f"serie {s['id']} {s['etiqueta']}: ERROR {e}")
                    continue
                n_total += n
                n_series_ok += 1
        jr.set_stat("series_ok", n_series_ok)
        jr.set_stat("puntos", n_total)
        jr.set_stat("modo", "backfill" if args.backfill else "incremental")
    print(f"✅ {n_series_ok}/{len(series)} series · {n_total} puntos upserteados "
          f"({'backfill' if args.backfill else 'incremental'})")


if __name__ == "__main__":
    main()
