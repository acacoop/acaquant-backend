"""scripts/diag_fase_b.py — ¿qué universo cambia si forwards/fair value pasan a los ejes? READ-ONLY.

Fase B del plan: forwards, breakevens, fair value e histórico hoy parten por
`mercado.curvas.curva`; tienen que pasar a los EJES. El riesgo NO es que se
rompan — es que **sigan dando números y sean otros**. Un fit de curva al que le
entra un bono de más se corre unos puntos básicos y no hay nada que se vea mal.

Por eso, antes de tocar una línea, hay que poder contestar tres cosas:

  1) CUÁNTA HISTORIA hay escrita con la clave vieja. Son 5 tablas particionadas
     por `curva` y ninguna se limpia nunca (no hay un solo `DELETE FROM` sobre
     ellas en el repo). Sin este número no se puede decidir entre backfillear,
     doble-escribir o truncar — y esa decisión es irreversible.

  2) QUIÉN ENTRA Y QUIÉN SALE de cada universo, POR NOMBRE. No alcanza con
     "cambia el conteo": hay que ver los tickers, porque el que sale de un fit
     deja de tener z-score y el que entra corre el ajuste para todos los demás.

  3) `mercado.snapshots_cierre`, que es el caso que puede MOVER PLATA. Es el
     fallback de precio del motor de PnL, se puebla barriendo `CURVAS_V1` y su
     fila **nunca se borra** (`ON CONFLICT ... WHERE EXCLUDED.fecha >= fecha`).
     Un ticker que salga del barrido no da error: se queda con el último precio
     que tuvo, congelado para siempre, y el PnL lo sigue usando como si fuera de
     hoy. SALUD tampoco lo ve — su contrato mira `max(fecha)` de la tabla, que
     sigue fresco mientras cualquier otro ticker actualice.

READ-ONLY. No escribe, no borra, no toca los motores.

Uso:
    python -m scripts.diag_fase_b
"""
from __future__ import annotations

from core.postgres import get_pool

_SEP = "=" * 96

# Las 5 tablas cuya PK incluye la clave de curva. `mercado_hist` guarda los
# forwards con la clave adentro de `k`, por eso va aparte.
_PARTICIONADAS = (
    ("mercado.fit_params", "curva", "ts_cierre", None),
    ("mercado.fair_value_residuos", "curva", "ts_cierre", "ticker"),
    ("mercado.snapshots_cierre_hist", "curva", "fecha", "ticker"),
    ("mercado.forwards_zscore", "curva", None, None),
)

# La traducción de cada `curva` de hoy al predicado EQUIVALENTE sobre los ejes.
# Es la propuesta a validar: el diag existe para ver si equivale de verdad.
# OJO con `cer` y `tamar`: llevan `OR ajuste_alt` porque un dual entra a las dos.
# DOS predicados por curva, porque son DOS preguntas distintas y medirlas juntas
# fue el error de la primera pasada:
#
#   · VISTA — qué se MUESTRA en esa tabla. Junta emisores a propósito: querés ver
#     el corporativo al lado del soberano.
#   · FIT   — qué entra al AJUSTE de la curva (fair value, forwards, z-score). Acá
#     juntar emisores está MAL: un corporativo tiene spread de crédito y meterlo
#     adentro corre la curva para todos los demás, sin que nada se vea raro.
#
# Medido: con el predicado de VISTA, `soberanos` pasa de 21 a 129 bonos. Para la
# tabla HARD DOLAR eso es lo correcto; para un fit sería un desastre mudo.
_EQUIV = {
    "tasa_fija":    ("ajuste = 'fija' AND moneda_eje = 'ARS'",
                     "ajuste = 'fija' AND moneda_eje = 'ARS' AND emisor_tipo = 'soberano'"),
    "cer":          ("(ajuste = 'cer' OR ajuste_alt = 'cer')",
                     "(ajuste = 'cer' OR ajuste_alt = 'cer') AND emisor_tipo = 'soberano'"),
    "soberanos":    ("ajuste = 'fija' AND moneda_eje IN ('USD','EUR')",
                     "ajuste = 'fija' AND moneda_eje IN ('USD','EUR') "
                     "AND emisor_tipo = 'soberano'"),
    "dolar_linked": ("(ajuste = 'dolar_linked' OR ajuste_alt = 'dolar_linked')",
                     "(ajuste = 'dolar_linked' OR ajuste_alt = 'dolar_linked') "
                     "AND emisor_tipo = 'soberano'"),
    "tamar":        ("(ajuste = 'tamar' OR ajuste_alt = 'tamar')",
                     "(ajuste = 'tamar' OR ajuste_alt = 'tamar') AND emisor_tipo = 'soberano'"),
}


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)   # None = no parsear % (si no, un LIKE revienta)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _existe(tabla: str) -> bool:
    esquema, nombre = tabla.split(".")
    return bool(_q("SELECT 1 FROM information_schema.tables "
                   "WHERE table_schema = %s AND table_name = %s", (esquema, nombre)))


def historia() -> None:
    print(f"\n{_SEP}\n1) CUÁNTA HISTORIA hay con la clave vieja\n{_SEP}")
    print("  Ninguna de estas tablas se limpia nunca: no hay un `DELETE FROM`")
    print("  sobre ellas en todo el repo. Lo que esté mal escrito, queda.\n")
    for tabla, clave, fecha, tk in _PARTICIONADAS:
        if not _existe(tabla):
            print(f"  · {tabla:<36} (no existe en esta base)")
            continue
        extra = (f", min({fecha})::text AS desde, max({fecha})::text AS hasta"
                 if fecha else ", NULL AS desde, NULL AS hasta")
        r = _q(f"SELECT count(*) AS filas, count(DISTINCT {clave}) AS claves{extra} "
               f"FROM {tabla}")[0]
        print(f"  · {tabla}")
        print(f"      {r['filas']} filas · {r['claves']} clave(s) distinta(s)"
              + (f" · {r['desde']} → {r['hasta']}" if r["desde"] else ""))

        # Claves que ya no existen en el master: el cementerio.
        huerf = _q(f"SELECT DISTINCT {clave} AS k FROM {tabla} WHERE {clave} NOT IN "
                   f"(SELECT DISTINCT curva FROM mercado.curvas WHERE curva IS NOT NULL)")
        if huerf:
            print(f"      ⚠ {len(huerf)} clave(s) que ya no existen en el master: "
                  + ", ".join(str(h["k"]) for h in huerf[:8]))
        if tk:
            n = _q(f"SELECT count(DISTINCT {tk}) AS n FROM {tabla} WHERE {tk} NOT IN "
                   f"(SELECT ticker FROM mercado.curvas)")[0]["n"]
            if n:
                print(f"      ⚠ {n} ticker(s) que ya no están en el master — los borró "
                      "`cleanup_curvas` y sus residuos quedan inalcanzables para siempre")

        por_clave = _q(f"SELECT {clave} AS k, count(*) AS n FROM {tabla} "
                       f"GROUP BY 1 ORDER BY 2 DESC LIMIT 12")
        print("      " + " · ".join(f"{p['k']}({p['n']})" for p in por_clave))


def universos() -> None:
    print(f"\n{_SEP}\n2) QUIÉN ENTRA Y QUIÉN SALE de cada universo, POR NOMBRE\n{_SEP}")
    print("  Izquierda: los que hoy entran por `curva`. Derecha: los que entrarían")
    print("  por el predicado de ejes. Lo que importa son las DIFERENCIAS: el que")
    print("  sale de un fit pierde su z-score, y el que entra corre el ajuste para")
    print("  TODOS los demás — sin que nada se vea mal.\n")

    for curva, (p_vista, p_fit) in _EQUIV.items():
        hoy = {r["ticker"] for r in _q(
            "SELECT ticker FROM mercado.curvas WHERE curva = %s", (curva,))}
        print(f"\n  ── {curva} (hoy: {len(hoy)} bonos) " + "─" * (60 - len(curva)))
        for etiqueta, pred in (("VISTA (la tabla)", p_vista), ("FIT (el ajuste)", p_fit)):
            nuevo = {r["ticker"] for r in _q(
                f"SELECT ticker FROM mercado.curvas WHERE {pred}")}
            entran, salen = sorted(nuevo - hoy), sorted(hoy - nuevo)
            marca = "✅" if not entran and not salen else "⚠ "
            print(f"     {marca} {etiqueta:<18} {len(nuevo):>3} bonos · "
                  f"entran {len(entran)} · salen {len(salen)}")
            if entran:
                print(f"          ENTRAN: {', '.join(entran[:14])}"
                      + (f" … +{len(entran) - 14}" if len(entran) > 14 else ""))
            if salen:
                print(f"          SALEN : {', '.join(salen[:14])}"
                      + (f" … +{len(salen) - 14}" if len(salen) > 14 else ""))
    print("\n  Las ONs (curva `on_*`) pasan a ser `emisor_tipo='corporativo'`:")
    hoy_on = {r["ticker"] for r in _q(
        "SELECT ticker FROM mercado.curvas WHERE left(curva, 3) = 'on_'")}
    nuevo_on = {r["ticker"] for r in _q(
        "SELECT ticker FROM mercado.curvas WHERE emisor_tipo = 'corporativo'")}
    print(f"      hoy={len(hoy_on)}  ejes={len(nuevo_on)}  "
          f"entran={len(nuevo_on - hoy_on)}  salen={len(hoy_on - nuevo_on)}")
    if nuevo_on - hoy_on:
        print(f"      ENTRAN: {', '.join(sorted(nuevo_on - hoy_on)[:20])}")
    if hoy_on - nuevo_on:
        print(f"      SALEN : {', '.join(sorted(hoy_on - nuevo_on)[:20])}")


def cierre() -> None:
    print(f"\n{_SEP}\n3) `snapshots_cierre` — el ÚNICO que puede mover un número de PnL\n{_SEP}")
    print("  Es el fallback de precio del motor de PnL. Se puebla barriendo las 3")
    print("  curvas de `CURVAS_V1` y su fila NUNCA se borra: un ticker que salga")
    print("  del barrido se queda con su último precio congelado, y el PnL lo sigue")
    print("  usando como si fuera de hoy. SALUD tampoco lo ve — mira `max(fecha)`")
    print("  de la tabla, que sigue fresco mientras otro ticker actualice.\n")

    # Importado, NO copiado: una tupla hardcodeada acá seguiría diciendo "barre 3
    # familias" el día que alguien sume una, y el diag pasaría a mentir justo
    # sobre lo que vino a medir.
    from jobs.snapshot_cierre import CURVAS_V1 as v1
    barre = {r["ticker"] for r in _q(
        "SELECT ticker FROM mercado.curvas WHERE curva = ANY(%s)", (list(v1),))}
    todos = {r["ticker"] for r in _q("SELECT ticker FROM mercado.curvas")}
    print(f"  el master tiene {len(todos)} bonos · `CURVAS_V1` barre {len(barre)}")
    print(f"  → {len(todos - barre)} bono(s) NO tienen cierre persistido hoy")
    fuera = _q("""
        SELECT COALESCE(curva,'∅') AS curva, count(*) AS n
        FROM mercado.curvas WHERE curva IS NULL OR curva <> ALL(%s)
        GROUP BY 1 ORDER BY 2 DESC
    """, (list(v1),))
    print("      " + " · ".join(f"{f['curva']}({f['n']})" for f in fuera))
    print("\n  (esto es de HOY, no lo causa la migración — pero define el piso: el")
    print("   barrido nuevo tiene que cubrir AL MENOS los mismos tickers)")

    if _existe("mercado.snapshots_cierre"):
        r = _q("SELECT count(*) AS n, min(fecha)::text AS desde, "
               "max(fecha)::text AS hasta FROM mercado.snapshots_cierre")[0]
        print(f"\n  filas en la tabla: {r['n']} · fechas {r['desde']} → {r['hasta']}")
        viejos = _q("""
            SELECT ticker, last_price, fecha::text AS fecha
            FROM mercado.snapshots_cierre
            WHERE fecha < (SELECT max(fecha) FROM mercado.snapshots_cierre)
            ORDER BY fecha LIMIT 15
        """)
        if viejos:
            print(f"\n  ⚠ {len(viejos)}+ ticker(s) con precio ATRASADO respecto del último")
            print("    cierre. Son los que ya están congelados hoy:")
            for v in viejos:
                print(f"      {v['ticker'][:44]:<46} {v['fecha']}  last={v['last_price']}")


def main() -> None:
    print(_SEP)
    print("FASE B — medir antes de mover forwards / fair value / histórico a los ejes")
    print(_SEP)
    historia()
    universos()
    cierre()
    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
