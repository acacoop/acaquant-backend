"""scripts/diag_interbanking_columnas.py — TODAS las columnas de un movimiento.

READ-ONLY: solo GET contra Interbanking + un SELECT al catálogo de la base.

## Para qué

La tabla MOVIMIENTOS de la vista se quedó corta y hay que decidir qué columnas
sumarle. Esa decisión no se puede tomar de memoria ni leyendo el YAML del
proveedor (que ya se comprobó que miente). Este script contesta, con datos
REALES de producción y por cada campo:

  · ¿en qué API viene?   Extractos, Movimientos v2, o las dos
  · ¿viene con dato?     % de movimientos que lo traen lleno
  · ¿sirve para algo?    cuántos valores DISTINTOS toma + ejemplos reales
  · ¿lo guardamos?       columna de `bancos.movimientos`, o solo dentro de `raw`
  · ¿se ve en la vista?  clave que publica `bancos._movimiento_publico`

Y los agrupa en las cuatro decisiones posibles:

  1. YA SE VE                  — nada que hacer
  2. SE GUARDA Y NO SE VE      — **gratis**: el dato ya está en la base, es solo
                                 sumar la columna al front
  3. VIENE Y NO SE GUARDA      — está en `raw`; hay que sumar columna + backfill
  4. SOLO EN LA API MOVIMIENTOS — no lo trae Extractos: para tenerlo hay que
                                 pedirle a OTRA API, una llamada más por cuenta

## Por qué compara DOS APIs

Extractos es hoy la fuente única, pero **no traen los mismos campos**. Medido en
la exploración del 2026-08-18: v2 tiene `account_cbu`, `associated_voucher`,
`grouping_code_standard` y `operation_code_standard`, que Extractos NO trae; y
Extractos tiene `statement_number`, que v2 NO trae. Si la columna que falta vive
solo en una, el costo de la decisión cambia — por eso se piden las dos sobre el
MISMO rango y la MISMA cuenta.

## Privacidad

`customer_cuit`, `account_cbu` y `depositor_description` son datos de terceros:
salen ENMASCARADOS. Con `--sin-mascara` se ven completos (para mirarlo una vez y
decidir, no para pegarlo en ningún lado).

Uso:
    python -m scripts.diag_interbanking_columnas
    python -m scripts.diag_interbanking_columnas --cuentas 5 --dias 3
    python -m scripts.diag_interbanking_columnas --sin-mascara

Costo: 4 llamadas del maestro + 2 por cuenta analizada (default 3) = ~10.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta

from core import interbanking as ib
from core.calendario import restar_habiles
from core.postgres import get_pool

SEP = "=" * 100

# Campos con datos de TERCEROS: se enmascaran salvo --sin-mascara.
SENSIBLES = {"customer_cuit", "depositor_description", "account_cbu", "depositor_code"}

# Campo de la API → columna de `bancos.movimientos`.
# ⚠️ ESPEJA `jobs/interbanking_sync._persistir`. Si allá se agrega un campo y acá
# no, el script lo canta al final (`_chequear_mapa`) en vez de mentir en silencio.
API_A_COLUMNA = {
    "movement_date": "fecha_movimiento",
    "value_date": "fecha_valor",
    "process_date": "fecha_proceso",
    "amount": "importe",
    "debit_credit_type": "tipo",
    "code_description_bank": "descripcion_banco",
    "code_description_ib": "descripcion_ib",
    "operation_code_ib": "codigo_operacion_ib",
    "operation_code_bank": "codigo_operacion_banco",
    "statement_number": "numero_extracto",
    "correlative_number": "correlativo",
    "voucher_number": "comprobante",
    "branch_office_activity": "sucursal",
    "customer_cuit": "cuit_contraparte",
    "depositor_description": "denominacion_contraparte",
}

# Columnas de `bancos.movimientos` que NO salen de un campo del movimiento.
COLUMNAS_PROPIAS = {"mov_hash", "cuenta_id", "fecha", "raw", "sincronizado_at"}

# Columna de la base → clave que publica la API al front.
# ⚠️ ESPEJA `api/services/bancos._movimiento_publico`.
COLUMNA_A_VISTA = {
    "fecha_proceso": "hora",
    "importe": "importe",
    "tipo": "tipo",
    "descripcion_banco": "descripcion",
    "descripcion_ib": "concepto",
    "codigo_operacion_ib": "codigo",
    "numero_extracto": "extracto",
    "correlativo": "correlativo",
    "comprobante": "comprobante",
    "denominacion_contraparte": "contraparte",
    "cuit_contraparte": "contraparte_cuit (enmascarado)",
}


def _mascara(campo: str, v: object, sin_mascara: bool) -> str:
    s = str(v)
    if sin_mascara or campo not in SENSIBLES or len(s) < 4:
        return s if len(s) <= 38 else s[:35] + "…"
    return f"{s[:2]}…{s[-2:]}"


def _movimientos_extracto(c: dict, d1: str, d2: str) -> list[dict]:
    r = ib.extractos(c["account_number"], c["bank_number"], d1, d2,
                     account_type=c.get("account_type") or "CC",
                     currency=c.get("currency") or "ARS", limit=100)
    return [m for d in (r.get("statements") or []) for m in (d.get("movement_detail") or [])]


def _movimientos_api(c: dict, d1: str, d2: str) -> list[dict]:
    r = ib.movimientos(c["account_number"], c["bank_number"], "anteriores",
                       account_type=c.get("account_type") or "CC",
                       currency=c.get("currency") or "ARS",
                       date_since=d1, date_until=d2, limit=100)
    return r.get("movements_detail") or []


def _chequear_mapa() -> None:
    """El mapa de arriba no puede quedar viejo sin que nadie se entere."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='bancos' AND table_name='movimientos'")
            reales = {r[0] for r in cur.fetchall()}
    except Exception as e:
        print(f"\n  (no pude leer el catálogo de la base: {e})")
        return
    if not reales:
        print("\n  (la tabla bancos.movimientos no existe todavía)")
        return
    mapeadas = set(API_A_COLUMNA.values()) | COLUMNAS_PROPIAS
    if huerfanas := reales - mapeadas:
        print(f"\n  ⚠️  Columnas en la base que este script no sabe de dónde salen: "
              f"{sorted(huerfanas)}\n      → actualizá API_A_COLUMNA en este archivo.")
    if fantasma := mapeadas - reales - COLUMNAS_PROPIAS:
        print(f"\n  ⚠️  El mapa nombra columnas que NO existen en la base: {sorted(fantasma)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Todas las columnas de un movimiento")
    ap.add_argument("--cuentas", type=int, default=3,
                    help="cuántas cuentas CON movimientos analizar (default 3)")
    ap.add_argument("--dias", type=int, default=1, help="días HÁBILES hacia atrás")
    ap.add_argument("--sin-mascara", action="store_true",
                    help="mostrar CUIT/CBU/denominación completos")
    args = ap.parse_args()

    hasta = date.today()
    desde = max(restar_habiles(hasta, args.dias), hasta - timedelta(days=60))
    d1, d2 = desde.isoformat(), hasta.isoformat()

    print(f"\n{SEP}\nCOLUMNAS DE UN MOVIMIENTO — rango {d1}..{d2}\n{SEP}")

    cuentas = ib.todas_las_cuentas()
    print(f"  {len(cuentas)} cuentas en el maestro. Busco las primeras "
          f"{args.cuentas} con movimientos en el rango.\n")

    # valores[campo][fuente] = lista de valores no vacíos
    valores: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    totales: dict[str, int] = defaultdict(int)
    analizadas = 0

    for c in cuentas:
        if analizadas >= args.cuentas:
            break
        try:
            ext = _movimientos_extracto(c, d1, d2)
        except Exception as e:
            print(f"  · {c.get('bank_name')}: extracto falló ({e})")
            continue
        if not ext:
            continue
        try:
            mov = _movimientos_api(c, d1, d2)
        except Exception as e:
            print(f"  · {c.get('bank_name')}: movimientos falló ({e}) — sigo con extracto")
            mov = []

        analizadas += 1
        print(f"  · {c.get('bank_name')} {c.get('account_type')}/{c.get('currency')}: "
              f"{len(ext)} mov. por extracto, {len(mov)} por la API de movimientos")

        for fuente, filas in (("extracto", ext), ("movimientos", mov)):
            totales[fuente] += len(filas)
            for m in filas:
                for k, v in m.items():
                    if v not in (None, "", []):
                        valores[k][fuente].append(v)

    if not analizadas:
        print("\n  Ninguna cuenta tuvo movimientos en el rango. Probá con --dias 5.\n")
        return

    # ── Clasificación ────────────────────────────────────────────────────────
    grupos: dict[str, list] = defaultdict(list)
    for campo in sorted(valores):
        en_ext = bool(valores[campo].get("extracto"))
        en_mov = bool(valores[campo].get("movimientos"))
        col = API_A_COLUMNA.get(campo)
        vista = COLUMNA_A_VISTA.get(col or "")

        vals = valores[campo].get("extracto") or valores[campo].get("movimientos") or []
        base = totales["extracto"] if en_ext else totales["movimientos"]
        pct = round(100 * len(vals) / base) if base else 0
        distintos = len({str(v) for v in vals})
        ejemplos = []
        for v in vals:
            e = _mascara(campo, v, args.sin_mascara)
            if e not in ejemplos:
                ejemplos.append(e)
            if len(ejemplos) == 3:
                break

        fuente = "ambas" if en_ext and en_mov else ("extracto" if en_ext else "MOVIMIENTOS")
        fila = (campo, fuente, pct, distintos, " · ".join(ejemplos))

        if vista:
            grupos["1. YA SE VE en la tabla"].append(fila + (vista,))
        elif col:
            grupos["2. SE GUARDA Y NO SE VE — sumar la columna al front y listo"].append(
                fila + (f"columna `{col}`",))
        elif not en_ext:
            grupos["4. SOLO EN LA API DE MOVIMIENTOS — hay que traer esa API"].append(
                fila + ("no se pide",))
        else:
            grupos["3. VIENE Y NO SE GUARDA — está en `raw`; sumar columna + backfill"].append(
                fila + ("solo en `raw`",))

    for titulo in sorted(grupos):
        print(f"\n{SEP}\n{titulo}\n{SEP}")
        print(f"  {'CAMPO':<30}{'FUENTE':<13}{'LLENO':>6}{'DIST':>6}  {'EJEMPLOS':<44}ESTADO")
        print("  " + "-" * 96)
        for campo, fuente, pct, dist, ej, estado in grupos[titulo]:
            print(f"  {campo:<30}{fuente:<13}{pct:>5}%{dist:>6}  {ej[:42]:<44}{estado}")

    print(f"\n{SEP}\nRESUMEN\n{SEP}")
    print(f"  {analizadas} cuenta(s) · {totales['extracto']} movimientos por extracto · "
          f"{totales['movimientos']} por la API de movimientos")
    print(f"  {len(valores)} campos distintos vistos en total.")
    solo_mov = [c for c in valores if not valores[c].get("extracto")]
    if solo_mov:
        print(f"  ⚠️  {len(solo_mov)} campo(s) NO los trae Extractos: {sorted(solo_mov)}")
    solo_ext = [c for c in valores
                if valores[c].get("extracto") and not valores[c].get("movimientos")
                and totales["movimientos"]]
    if solo_ext:
        print(f"  ⚠️  {len(solo_ext)} campo(s) NO los trae Movimientos: {sorted(solo_ext)}")
    _chequear_mapa()
    print()


if __name__ == "__main__":
    main()
