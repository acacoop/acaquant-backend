"""READ-ONLY. ¿En qué MONEDA está la valuación de un fondo en USD en `portafolio.tenencia`?

Herramienta: diag — solo SELECT, cero escrituras.

EL PUNTO
========

COMISIONES FCI devenga `valuación × fee ÷ 2 ÷ 365` sobre la foto de tenencia, y
etiqueta cada fondo con `tenencia.moneda`. Un fondo en dólares PAGA su honorario
en dólares. Para que la pantalla lo muestre en USD hay que saber una sola cosa,
que hoy nadie midió: **la `valuacion` que Aunesa escribe para ese fondo, ¿está
en USD o en pesos?**

  · Si está en USD → alcanza con etiquetar bien (la moneda del fondo, no la de
    la fila) y el número ya es el dólar que se cobra.
  · Si está en ARS → hay que dividir por el tipo de cambio DEL DÍA de cada foto
    (el honorario es diario, no se puede dolarizar el mes al MEP de hoy).

Las dos hipótesis son coherentes consigo mismas y ninguna falla si está mal:
mostrarían un número seguro y equivocado. Por eso se mide, no se asume.

CÓMO LO RESUELVE
================

Para cada fondo con tenencia en la última foto compara el **precio por cuotaparte
de la tenencia** contra el **VCP oficial de Primary** del mismo día
(`mercado.fci_vcp`, fuente `primary`), que viene en la moneda del fondo:

  ratio = precio_tenencia / vcp_primary
     ≈ 1     → la tenencia valúa en la MONEDA DEL FONDO (USD si es USD)
     ≈ MEP   → la tenencia valúa en PESOS (precio = VCP × dólar)
     otro    → no se puede afirmar (se lista, no se adivina)

Además cruza las TRES copias de "qué moneda es este fondo" (`mercado.fci.moneda`,
`assets.clase_activo` y `tenencia.moneda`) y lista dónde no coinciden —
REGLA #9: dos copias sin árbitro no fallan, contestan distinto.

SEGUNDA PREGUNTA (columna AuM de la vista)
==========================================

La tabla de COMISIONES FCI suma TODA la tenencia FCI, sin el filtro `aum='si'`
que usa el resto de la app para llamar AuM a un número. Este diag cuenta cuánta
valuación de la foto de corte tiene `aum='no'` (cuentas propias, contrapartes) y
en qué cuentas, para decidir si esa plata devenga comisión o no.

Uso:
    python -m scripts.diag_fci_moneda
    python -m scripts.diag_fci_moneda --fecha 2026-08-31
"""
from __future__ import annotations

import argparse
from datetime import date

from core.cartera import FCI
from core.dolar_sql import mep_para_fecha
from core.postgres import get_job_pool

_FCI = [x.upper() for x in FCI]
_W_FCI = "upper(btrim(coalesce(t.cartera, ''))) = ANY(%(fci)s)"

# Clase de activo → moneda. COPIA de la tabla de `jobs/fci_universo` (importarla
# arrastra pyRofex, que un diag no necesita). Es un diag descartable: si se
# reusa en código productivo, la tabla se mueve a `core/` y se importa.
_MONEDA_POR_CLASE = {"MM ARS": "ARS", "ARS T1": "ARS", "RENTA VARIABLE": "ARS",
                     "MM USD": "USD", "HD T1": "USD"}


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _f(x) -> float | None:
    return float(x) if x is not None else None


def _corte(fecha: str | None) -> date | None:
    r = _q(f"SELECT max(t.fecha) AS f FROM portafolio.tenencia t WHERE {_W_FCI}"
           + (" AND t.fecha <= %(f)s" if fecha else ""),
           {"fci": _FCI, "f": fecha})
    return r[0]["f"] if r and r[0]["f"] else None


_SQL_FONDOS = f"""
SELECT t.unidad,
       a.clase_activo,
       a.emisor,
       a.fee_admin,
       f.moneda                                   AS moneda_fci,
       string_agg(DISTINCT upper(t.moneda), '/')  AS moneda_ten,
       max(t.precio)                              AS precio_ten,
       sum(t.valuacion)                           AS valuacion,
       count(DISTINCT t.id_cuenta)                AS ctas,
       sum(t.valuacion) FILTER (WHERE coalesce(t.aum, 'si') <> 'si') AS val_no_aum,
       count(*)         FILTER (WHERE coalesce(t.aum, 'si') <> 'si') AS n_no_aum,
       (SELECT v.vcp FROM mercado.fci_vcp v
         WHERE v.fci_id = f.fci_id AND v.fuente = 'primary'
           -- hasta 5 días atrás: cubre un fin de semana largo sin arrastrar un
           -- VCP de la semana anterior; si no hay, sale "SIN VCP", no un número viejo
           AND v.fecha <= %(corte)s AND v.fecha >= %(corte)s - 5
         ORDER BY v.fecha DESC LIMIT 1)           AS vcp_primary
FROM portafolio.tenencia t
LEFT JOIN portafolio.assets a ON a.unidad = t.unidad
LEFT JOIN mercado.fci f       ON f.unidad = t.unidad
WHERE t.fecha = %(corte)s AND {_W_FCI}
GROUP BY t.unidad, a.clase_activo, a.emisor, a.fee_admin, f.moneda, f.fci_id
ORDER BY sum(t.valuacion) DESC NULLS LAST
"""

_SQL_NO_AUM = f"""
SELECT t.id_cuenta, max(t.cuenta) AS cuenta, count(*) AS filas, sum(t.valuacion) AS val
FROM portafolio.tenencia t
WHERE t.fecha = %(corte)s AND {_W_FCI} AND coalesce(t.aum, 'si') <> 'si'
GROUP BY t.id_cuenta ORDER BY sum(t.valuacion) DESC NULLS LAST LIMIT 15
"""


def _veredicto(precio: float | None, vcp: float | None, mep: float | None) -> str:
    if not precio or not vcp:
        return "SIN VCP"
    r = precio / vcp
    if 0.95 <= r <= 1.05:
        return "MONEDA FONDO"
    if mep and 0.90 <= r / mep <= 1.10:
        return "PESOS (×MEP)"
    return f"? ratio {r:,.2f}"


def _n(x, d=2) -> str:
    return "—" if x is None else f"{x:,.{d}f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fecha", help="YYYY-MM-DD: usar la última foto <= esta fecha")
    a = ap.parse_args()

    corte = _corte(a.fecha)
    if corte is None:
        print("no hay foto de tenencia FCI")
        return 1
    mep = mep_para_fecha(corte.isoformat())
    fondos = _q(_SQL_FONDOS, {"corte": corte, "fci": _FCI})

    print("=" * 110)
    print(f"MONEDA DE LA VALUACIÓN FCI — foto {corte} · MEP del día {_n(mep)} · {len(fondos)} fondos")
    print("=" * 110)

    for r in fondos:
        r["moneda_clase"] = _MONEDA_POR_CLASE.get((r["clase_activo"] or "").upper())
        # La moneda "declarada" del fondo: Primary manda; si no está, la clase.
        r["moneda_decl"] = (r["moneda_fci"] or r["moneda_clase"] or "").upper() or None
        r["veredicto"] = _veredicto(_f(r["precio_ten"]), _f(r["vcp_primary"]), mep)

    usd = [r for r in fondos if r["moneda_decl"] == "USD"]
    print(f"\n  fondos declarados USD (Primary o clase): {len(usd)} de {len(fondos)}")
    por_v: dict[str, int] = {}
    for r in usd:
        k = r["veredicto"] if not r["veredicto"].startswith("?") else "?"
        por_v[k] = por_v.get(k, 0) + 1
    for k, n in sorted(por_v.items()):
        print(f"    {k:<14}: {n}")

    print("\n" + "=" * 110)
    print("FONDOS USD — precio de la tenencia vs VCP oficial de Primary el mismo día")
    print("=" * 110)
    print(f"  {'FONDO':<44}{'CLASE':<9}{'TEN':<8}{'PRECIO TEN':>13}{'VCP PRIM':>13}"
          f"{'VEREDICTO':>15}{'VALUACIÓN':>16}{'CTAS':>6}")
    for r in usd:
        print(f"  {r['unidad'][:43]:<44}{(r['clase_activo'] or '')[:8]:<9}"
              f"{(r['moneda_ten'] or '—')[:7]:<8}{_n(_f(r['precio_ten']), 4):>13}"
              f"{_n(_f(r['vcp_primary']), 4):>13}{r['veredicto']:>15}"
              f"{_n(_f(r['valuacion']), 0):>16}{r['ctas']:>6}")

    # Las tres copias de la moneda, donde no dicen lo mismo.
    desac = [r for r in fondos if len({m for m in (
        r["moneda_fci"], r["moneda_clase"],
        (r["moneda_ten"] if r["moneda_ten"] and "/" not in r["moneda_ten"] else None),
    ) if m}) > 1 or (r["moneda_ten"] and "/" in r["moneda_ten"])]
    print("\n" + "=" * 110)
    print(f"DESACUERDO DE MONEDA entre mercado.fci · clase_activo · tenencia ({len(desac)})")
    print("=" * 110)
    if not desac:
        print("  ninguno: las tres copias coinciden en todos los fondos con foto")
    else:
        print(f"  {'FONDO':<52}{'FCI':<7}{'CLASE→':<8}{'TENENCIA':<10}{'VALUACIÓN':>16}")
        for r in desac:
            print(f"  {r['unidad'][:51]:<52}{(r['moneda_fci'] or '—'):<7}"
                  f"{(r['moneda_clase'] or '—'):<8}{(r['moneda_ten'] or '—'):<10}"
                  f"{_n(_f(r['valuacion']), 0):>16}")

    sin_decl = [r for r in fondos if not r["moneda_decl"]]
    if sin_decl:
        print(f"\n  ⚠ {len(sin_decl)} fondo(s) sin moneda declarada en ningún lado "
              f"(ni mercado.fci ni clase): {', '.join(r['unidad'][:30] for r in sin_decl[:8])}"
              + (" …" if len(sin_decl) > 8 else ""))

    # ── AuM: lo que la vista suma y el resto de la app no llama AuM ──────────
    total = sum(_f(r["valuacion"]) or 0 for r in fondos)
    no_aum = sum(_f(r["val_no_aum"]) or 0 for r in fondos)
    n_no = sum(int(r["n_no_aum"] or 0) for r in fondos)
    print("\n" + "=" * 110)
    print("AuM — filas de la foto con aum='no' (cuentas propias / contrapartes)")
    print("=" * 110)
    print(f"  valuación FCI total de la foto : {_n(total, 0)}")
    print(f"  de eso con aum='no'            : {_n(no_aum, 0)}  "
          f"({(no_aum / total * 100) if total else 0:.1f} %) en {n_no} filas")
    for r in _q(_SQL_NO_AUM, {"corte": corte, "fci": _FCI}):
        print(f"    {r['id_cuenta']:<8} {(r['cuenta'] or '')[:40]:<41}"
              f"{r['filas']:>5} filas {_n(_f(r['val']), 0):>16}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
