"""scripts/backfill_flujos_porcentual.py — AGREGA el formato porcentual a los flujos.

Paso 1 de la migración del motor a los EJES. Verificado por
`scripts/diag_convertir_flujos`: estos bonos conservan su TEA **exacta** (Δ = 0 bps)
cuando sus flujos se leen en formato porcentual.

**PATRÓN: EXPANDIR → MIGRAR → CONTRAER.** Este script hace SOLO el "expandir".

    1. EXPANDIR  (este script) — agrega `amortizacion_pct` / `cupon_sobre_residual`
                                 / `residual_previo_pct` SIN sacar `amortizacion`
                                 ni `interes`.
    2. MIGRAR    (después)     — el motor pasa a elegir la rama por los EJES.
    3. CONTRAER  (al final)    — se limpian las claves viejas.

**Por qué NO se borran las viejas acá, que es lo importante.** Hoy el motor manda
estos bonos a la rama `on` por su `curva='on_*'`, y esa rama lee `amortizacion` +
`interes`. Si el backfill las sacara, el bono se quedaría sin TEA **en el momento
de correr esto**, no cuando se migre el motor. Con las dos puestas, las DOS ramas
leen bien y el orden de los pasos deja de importar: se puede correr hoy y migrar
el motor la semana que viene, o al revés.

REGLA #4 — cómo cumple:
  · **Scopeado**: solo los tickers de `TICKERS` (7 filas), por PK. Cero scans.
  · **Idempotente**: si un flujo ya tiene las claves nuevas, no se toca. Correrlo
    dos veces no cambia nada.
  · **Verificado antes**: la conversión se despeja de las fórmulas del motor y
    tiene autochequeo numérico (ver `diag_convertir_flujos`).
  · **Liviano**: 7 UPDATEs por PK. No hace falta esperar a que cierre el mercado.
  · **`--dry` por DEFAULT**: sin `--aplicar` no escribe nada, solo muestra el diff.

Uso:
    python -m scripts.backfill_flujos_porcentual              # dry-run (default)
    python -m scripts.backfill_flujos_porcentual --aplicar    # escribe
"""
from __future__ import annotations

import json
import sys

from core import curvas_sql
from core.postgres import get_pool

_SEP = "=" * 96

# Los 7 que `diag_convertir_flujos` midió en IDÉNTICA (Δ = +0 bps).
# NO se amplía esta lista sin volver a correr ese diag: el que no está medido, no entra.
TICKERS = {
    # BCRA — los BOPREAL. Prioridad de la mesa.
    "BPOA7": "soberanos", "BPOB7": "soberanos",
    "BPOB8": "soberanos", "BPOD7": "soberanos",
    # Provinciales en dólares que estaban en el cajón `on_otros`.
    "CO32": "soberanos", "NDT25": "soberanos", "SFD34": "soberanos",
}


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _residuales(flujos: list[dict]) -> list[float]:
    """El nominal VIVO antes de cada pago. Se deriva acumulando amortizaciones —
    el formato absoluto no lo trae y la paridad lo necesita."""
    out, vivo = [], 100.0
    for f in flujos:
        out.append(vivo)
        vivo -= _f(f.get("amortizacion", f.get("amortizacion_pct", 0)))
    return out


def _expandir(flujos: list[dict], vn: float) -> tuple[list[dict], int]:
    """Agrega las claves porcentuales conservando las absolutas. (nuevos, cuántos).

    Despejado de `monto_flujo_soberano(f, vn) = amortizacion_pct/100·vn +
    cupon_sobre_residual/100·vn` para que el monto dé IGUAL que
    `monto_flujo(f) = amortizacion + interes`.
    """
    res = _residuales(flujos)
    out, tocados = [], 0
    for f, residual in zip(flujos, res, strict=False):
        nuevo = dict(f)
        if "amortizacion_pct" in f and "cupon_sobre_residual" in f:
            out.append(nuevo)          # ya expandido → idempotente
            continue
        nuevo["amortizacion_pct"] = _f(f.get("amortizacion")) * 100 / vn if vn > 0 else 0.0
        nuevo["cupon_sobre_residual"] = _f(f.get("interes")) * 100 / vn if vn > 0 else 0.0
        nuevo.setdefault("residual_previo_pct", round(residual, 6))
        out.append(nuevo)
        tocados += 1
    return out, tocados


def main() -> None:
    aplicar = "--aplicar" in sys.argv
    print(_SEP)
    print(f"EXPANDIR flujos a formato porcentual — {'APLICANDO' if aplicar else 'DRY-RUN'}")
    print(_SEP)
    if not aplicar:
        print("  (sin --aplicar no se escribe nada; esto solo muestra qué haría)")

    # Autochequeo: la conversión tiene que reproducir el monto EXACTO. Si no, aborta.
    from engines.curvas import monto_flujo, monto_flujo_soberano
    prueba = [{"fecha": "2027-01-01", "amortizacion": 50.0, "interes": 2.0},
              {"fecha": "2028-01-01", "amortizacion": 50.0, "interes": 1.0}]
    conv, _ = _expandir(prueba, 100.0)
    for a, b in zip(prueba, conv, strict=False):
        if abs(monto_flujo(a) - monto_flujo_soberano(b, 100)) > 1e-9:
            print(f"\n  ❌ AUTOCHEQUEO FALLIDO ({monto_flujo(a)} ≠ "
                  f"{monto_flujo_soberano(b, 100)}) — ABORTA sin escribir.")
            sys.exit(1)
    print("  ✅ autochequeo: la conversión reproduce el monto exacto\n")

    docs = {d.get("ticker_corto"): d for d in curvas_sql.cargar_todos()}
    plan = []
    for tk in sorted(TICKERS):
        d = docs.get(tk)
        if not d:
            print(f"  ⚠️  {tk}: no está en mercado.curvas — se saltea")
            continue
        flujos = d.get("flujos") or []
        if not flujos:
            print(f"  ⚠️  {tk}: sin flujos — se saltea")
            continue
        vn = _f(d.get("valor_nominal"), 100.0) or 100.0
        nuevos, tocados = _expandir(flujos, vn)
        plan.append((tk, flujos, nuevos, tocados, vn))

    print(f"  {'TICKER':<9}{'FLUJOS':>7}{'A TOCAR':>9}{'VN':>7}   PRIMER FLUJO (antes → después)")
    print("  " + "-" * 92)
    for tk, viejos, nuevos, tocados, vn in plan:
        a, b = viejos[0], nuevos[0]
        antes = f"am={a.get('amortizacion')} int={a.get('interes')}"
        desp = (f"am_pct={round(b.get('amortizacion_pct', 0), 4)} "
                f"cup={round(b.get('cupon_sobre_residual', 0), 4)} "
                f"res={b.get('residual_previo_pct')}")
        print(f"  {tk:<9}{len(viejos):>7}{tocados:>9}{vn:>7.0f}   {antes}  →  {desp}")

    total = sum(t for _, _, _, t, _ in plan)
    print(f"\n  {len(plan)} bonos · {total} flujos a expandir")
    if total == 0:
        print("  Nada que hacer: ya estaban todos expandidos (idempotente).")
        return

    if not aplicar:
        print("\n  DRY-RUN. Para escribir: python -m scripts.backfill_flujos_porcentual --aplicar")
        print(f"\n{_SEP}")
        return

    escritos = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for tk, _, nuevos, tocados, _ in plan:
            if not tocados:
                continue
            # Solo se toca la clave `flujos` DENTRO del blob — el resto del doc
            # queda intacto (jsonb_set sobre `data`).
            cur.execute(
                "UPDATE mercado.curvas SET data = jsonb_set(data, '{flujos}', %s::jsonb) "
                "WHERE ticker = %s",
                (json.dumps(nuevos, default=str), tk))
            escritos += cur.rowcount or 0
        conn.commit()
    curvas_sql.invalidar()
    print(f"\n  ✅ {escritos} bonos actualizados. Cache del master invalidado.")
    print("     Las claves VIEJAS siguen ahí: el motor calcula exactamente igual que")
    print("     antes. Este paso no cambia ningún número — habilita el siguiente.")
    print(f"\n{_SEP}")


if __name__ == "__main__":
    main()
