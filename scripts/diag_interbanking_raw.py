"""scripts/diag_interbanking_raw.py — el JSON crudo de las 5 APIs + análisis de forma.

READ-ONLY. Todas las llamadas son GET.

Hace DOS cosas con cada endpoint:

  1. **Guarda el JSON completo** en un archivo, para abrirlo y mirarlo con calma.
  2. **Analiza la forma**: recorre la respuesta campo por campo y reporta qué
     porcentaje de los registros trae cada uno con valor. Eso es lo que decide el
     modelo de datos — un campo que el YAML declara pero que llega siempre vacío
     no sirve para nada, y no hay forma de saberlo sin mirar datos reales
     (REGLA #2).

⚠️ **Los archivos tienen datos reales del banco** (CUIT, CBU, importes, nombres de
comitentes). Se escriben en `interbanking_muestras/`, que está en `.gitignore`.
NUNCA los commitees.

Por eso mismo, el reporte que sale por CONSOLA va **enmascarado** por default:
los identificadores salen como `<oculto>` y los importes como `<número>`, así se
puede pegar en un chat o un ticket sin filtrar nada. Con `--valores` se ven los
valores reales en pantalla (los archivos siempre los tienen).

Uso (desde la raíz del repo):
    python -m scripts.diag_interbanking_raw                  # cuenta #0, las 5 APIs
    python -m scripts.diag_interbanking_raw --cuenta 7       # otra cuenta
    python -m scripts.diag_interbanking_raw --dias 30        # ventana de históricos
    python -m scripts.diag_interbanking_raw --valores        # sin enmascarar la consola
    python -m scripts.diag_interbanking_raw --salida C:\\tmp\\ib   # otra carpeta

Costo: ~10 llamadas. El plan admite 100 por minuto.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from core import interbanking as ib

SEP = "=" * 78

# Campos cuyo VALOR no se muestra en consola salvo --valores.
_SENSIBLES = re.compile(
    r"cuit|cbu|account_number|account_label|denomination|depositor|"
    r"balance|amount|importe|total_credits|total_debits",
    re.I,
)


def _hojas(obj: Any, prefijo: str = "") -> list[tuple[str, Any]]:
    """Aplana un JSON a pares (ruta, valor). Las listas se marcan con `[]`."""
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_hojas(v, f"{prefijo}.{k}" if prefijo else k))
    elif isinstance(obj, list):
        if not obj:
            out.append((f"{prefijo}[]", None))
        for item in obj:
            out.extend(_hojas(item, f"{prefijo}[]"))
    else:
        out.append((prefijo, obj))
    return out


def _mostrar(ruta: str, valor: Any, con_valores: bool) -> str:
    if valor is None:
        return "—"
    if con_valores or not _SENSIBLES.search(ruta):
        s = str(valor)
        return s if len(s) <= 40 else s[:37] + "…"
    return "<número>" if isinstance(valor, (int, float)) else "<oculto>"


def analizar(nombre: str, payload: Any, con_valores: bool) -> None:
    """Tabla de cobertura: qué campos vienen con valor y cuáles llegan siempre vacíos."""
    vistos: dict[str, list[Any]] = defaultdict(list)
    for ruta, valor in _hojas(payload):
        vistos[ruta].append(valor)

    print(f"\n  Campos de {nombre} ({len(vistos)} rutas):")
    print(f"     {'CAMPO':<46} {'CON VALOR':>12}   EJEMPLO")
    print(f"     {'-' * 46} {'-' * 12}   {'-' * 24}")

    vacios: list[str] = []
    for ruta in sorted(vistos):
        vals = vistos[ruta]
        llenos = [v for v in vals if v not in (None, "", [])]
        pct = 100 * len(llenos) / len(vals) if vals else 0
        if not llenos:
            vacios.append(ruta)
            continue
        ejemplo = _mostrar(ruta, llenos[0], con_valores)
        print(f"     {ruta:<46} {len(llenos):>4}/{len(vals):<4} {pct:>3.0f}%   {ejemplo}")

    if vacios:
        print(f"\n     SIEMPRE VACÍOS ({len(vacios)}) — declarados pero sin dato:")
        for r in vacios:
            print(f"       · {r}")


def guardar(salida: Path, nombre: str, payload: Any) -> None:
    salida.mkdir(parents=True, exist_ok=True)
    f = salida / f"{nombre}.json"
    f.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  → {f}")


def bloque(nombre: str, titulo: str, fn, salida: Path, con_valores: bool) -> Any:
    """Llama al endpoint, guarda el crudo y analiza la forma. Nunca corta el script."""
    print(f"\n{SEP}\n{titulo}\n{SEP}")
    try:
        payload = fn()
    except ib.InterbankingError as e:
        print(f"  ✗ {e}")
        return None
    guardar(salida, nombre, payload)
    analizar(nombre, payload, con_valores)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="JSON crudo + forma de las APIs de Interbanking")
    ap.add_argument("--cuenta", type=int, default=0, help="índice de la cuenta a explorar")
    ap.add_argument("--dias", type=int, default=15, help="ventana de históricos (máx 60)")
    ap.add_argument("--salida", default="interbanking_muestras", help="carpeta de salida")
    ap.add_argument("--valores", action="store_true",
                    help="mostrar valores reales en consola (por default van enmascarados)")
    args = ap.parse_args()

    salida = Path(args.salida)
    hasta = date.today()
    desde = hasta - timedelta(days=min(args.dias, 60))
    d1, d2 = desde.isoformat(), hasta.isoformat()

    if args.valores:
        print("\n⚠️  Modo --valores: la consola muestra datos reales del banco. No la pegues en público.")

    # ---- Cuentas -----------------------------------------------------------
    cc = bloque("cuentas_CC", "1. CUENTAS — tipo CC (cuenta corriente)",
                lambda: ib.cuentas(account_type="CC"), salida, args.valores)
    ca = bloque("cuentas_CA", "2. CUENTAS — tipo CA (caja de ahorro)",
                lambda: ib.cuentas(account_type="CA"), salida, args.valores)

    todas = ((cc or {}).get("accounts") or []) + ((ca or {}).get("accounts") or [])
    if not todas:
        print("\n✗ Sin cuentas: no hay con qué seguir.")
        return

    print(f"\n{SEP}\nCUENTAS DISPONIBLES ({len(todas)})\n{SEP}")
    for i, c in enumerate(todas):
        etiqueta = c.get("account_label") if args.valores else "<oculto>"
        print(f"  #{i:<3} banco={c.get('bank_number')} ({c.get('bank_name')})"
              f" | {c.get('account_type')} {c.get('currency')} | {etiqueta}")

    if args.cuenta >= len(todas):
        print(f"\n✗ No existe la cuenta #{args.cuenta}.")
        return
    c = todas[args.cuenta]
    nro, banco = c["account_number"], c["bank_number"]
    tipo = c.get("account_type") or "CC"
    mon = c.get("currency") or "ARS"
    print(f"\n>>> Explorando la cuenta #{args.cuenta}: {c.get('bank_name')} {tipo} {mon}")

    # ---- El resto, sobre esa cuenta ---------------------------------------
    bloque("saldos_actual", "3. SALDOS — actual",
           lambda: ib.saldos(nro, banco, account_type=tipo, currency=mon),
           salida, args.valores)

    bloque("saldos_historico", f"4. SALDOS — histórico {d1} → {d2}",
           lambda: ib.saldos(nro, banco, account_type=tipo, currency=mon,
                             date_since=d1, date_until=d2),
           salida, args.valores)

    bloque("extractos", f"5. EXTRACTOS {d1} → {d2}",
           lambda: ib.extractos(nro, banco, d1, d2, account_type=tipo, currency=mon),
           salida, args.valores)

    for tipo_mov in ("dia", "anteriores", "diferidos", "zughus"):
        bloque(f"movimientos_v2_{tipo_mov}", f"6. MOVIMIENTOS v2 — {tipo_mov}",
               lambda t=tipo_mov: ib.movimientos(
                   nro, banco, t, account_type=tipo, currency=mon,
                   date_since=d1, date_until=d2),
               salida, args.valores)

    bloque("movimientos_v1_dia", "7. MOVIMIENTOS v1 — dia (¿trae el campo `id`?)",
           lambda: ib.movimientos(nro, banco, "dia", version="v1",
                                  account_type=tipo, currency=mon),
           salida, args.valores)

    bloque("transferencias_detalle", f"8. TRANSFERENCIAS — detalle {d1} → {d2}",
           lambda: ib.transferencias(d1, d2), salida, args.valores)

    bloque("transferencias_comprobantes", f"9. TRANSFERENCIAS — comprobantes {d1} → {d2}",
           lambda: ib.transferencias(d1, d2, comprobantes=True), salida, args.valores)

    print(f"\n{SEP}")
    print(f"LISTO. Los JSON completos quedaron en: {salida.resolve()}")
    print("Esa carpeta está en .gitignore — tiene datos reales del banco, NO la commitees.")
    print(SEP)


if __name__ == "__main__":
    main()
