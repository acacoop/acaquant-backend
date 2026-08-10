"""Diag READ-ONLY — TEST: ¿los depósitos que informa Aunesa alcanzan para llenar
solos la tab CHEQUES → RECIBIDOS de Tesorería, y así sacarle el tipeo al back office?

LA REGLA DE NEGOCIO (la puso el user, 2026-08-10): un depósito de e-cheq entra a
Aunesa con la fecha del DÍA en que se carga, pero la plata llega al banco el DÍA
HÁBIL SIGUIENTE → `fecha_pago` = próximo hábil de la fecha del movimiento.

QUÉ CAMBIÓ RESPECTO DE LA PRIMERA VERSIÓN. Filtraba por "depósito Y menciona cheq"
y encontró 5 del 07/08, cuando en la app había 6. Un depósito de e-cheq que Aunesa
escriba solo "Depósito" quedaba afuera — y ESE filtro es la regla del futuro espejo:
si se equivoca, el espejo carga de menos. Así que ahora lista **TODOS los depósitos
del día** y marca cuáles mencionan cheq, para que el sexto aparezca solo y podamos
fijar la regla mirando datos y no adivinando.

TAMBIÉN RESUELVE EL CUIT, con la misma cadena que usa el autocomplete del form:
`cuenta` "[805] NOMBRE" → id_cuenta → `clientes.comitentes.nro_doc` (tipo_doc CUIT).
Sirve para confirmar que el espejo puede completarlo sin que nadie lo tipee.

EL BANCO (cuenta operativa) NO viene en este feed y ESTÁ BIEN: el back office lo
carga después, cuando el movimiento ya está a la vista. El espejo nace sin banco.

100% read-only: no crea ni edita un cheque. 1 llamada a Aunesa.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_echeq_tesoreria --fecha 2026-08-07
    python -m scripts.diag_echeq_tesoreria
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta

from api.services._sql import _q
from jobs.cashflow import autenticar as _auth
from jobs.cashflow import fetch_dia

_LINEA = "─" * 78
_RE_ID = re.compile(r"^\[(\d+)\]")


def _norm(s) -> str:
    return (unicodedata.normalize("NFD", str(s or ""))
            .encode("ascii", "ignore").decode("utf-8").lower())


def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _titulo(n: str, txt: str) -> None:
    print(f"\n{_LINEA}\n{n}. {txt}\n{_LINEA}")


def _es_deposito(info) -> bool:
    """CUALQUIER depósito. A propósito NO se pide que diga 'cheq': el sexto del
    07/08 se perdía justo por eso."""
    return "deposito" in _norm(info)


def _cuits(ids: list[str]) -> dict[str, str]:
    """id_cuenta → CUIT. Misma cadena que `tesoreria.buscar_comitentes_cheque`."""
    if not ids:
        return {}
    return {
        r["id_cuenta"]: r["nro_doc"]
        for r in _q("SELECT id_cuenta, nro_doc FROM clientes.comitentes "
                    " WHERE id_cuenta = ANY(%(ids)s) "
                    "   AND upper(COALESCE(tipo_doc, '')) LIKE '%%CUIT%%'", {"ids": ids})
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: hoy ART)")
    args = ap.parse_args()

    dia = (datetime.strptime(args.fecha, "%Y-%m-%d").date() if args.fecha else _hoy_art())
    from core.calendario import proximo_habil
    pago = proximo_habil(dia)

    print("\nTEST Aunesa → TESORERÍA / CHEQUES RECIBIDOS")
    print(f"Día del movimiento: {dia}   →   fecha de pago esperada: {pago} (próximo hábil)")

    # ── 1. TODOS los depósitos del día ───────────────────────────────────────
    _titulo("1", "AUNESA — TODOS los depósitos del día (no solo los que dicen 'cheq')")
    headers = _auth()
    try:
        data, _ = fetch_dia(dia.strftime("%d/%m/%Y"), headers)
    except Exception as e:
        print(f"❌ Aunesa: {str(e)[:150]}")
        return 1
    data = data or []
    dep = [r for r in data if _es_deposito(r.get("informacion"))]

    ids = []
    for r in dep:
        m = _RE_ID.match(str(r.get("cuenta") or ""))
        r["_id_cuenta"] = m.group(1) if m else None
        if r["_id_cuenta"]:
            ids.append(r["_id_cuenta"])
    cuit_map = _cuits(sorted(set(ids)))

    con_cheq = [r for r in dep if "cheq" in _norm(r.get("informacion"))]
    print(f"Aunesa devolvió {len(data):,} filas → {len(dep)} depósito(s), "
          f"de los cuales {len(con_cheq)} mencionan 'cheq'.\n")

    print(f"{'cheq?':>5}  {'comprobante':>15}  {'mon':>4}  {'importe':>19}  "
          f"{'CUIT':>13}  informacion / cuenta")
    for r in sorted(dep, key=lambda x: -_f(x.get("total")) * -1):
        marca = " CHQ " if "cheq" in _norm(r.get("informacion")) else "  ·  "
        cuit = cuit_map.get(r.get("_id_cuenta") or "", "") or "— sin cuit —"
        print(f"{marca:>5}  {r.get('comprobante') or ''!s:>15}  "
              f"{r.get('unidad') or ''!s:>4}  {-_f(r.get('total')):>19,.2f}  "
              f"{cuit:>13}  {str(r.get('informacion') or '')[:34]}")
        print(f"{'':>5}  {'':>15}  {'':>4}  {'':>19}  {'':>13}  "
              f"↳ {str(r.get('cuenta') or '')[:60]}")

    sin_cuit = [r for r in dep if not cuit_map.get(r.get("_id_cuenta") or "")]
    print(f"\nCUIT resuelto desde el padrón: {len(dep) - len(sin_cuit)}/{len(dep)}"
          + ("  → el espejo lo completa solo" if not sin_cuit else "  ⚠ faltan algunos"))

    # ── 2. Shape crudo ───────────────────────────────────────────────────────
    if dep:
        _titulo("2", "SHAPE CRUDO de un depósito — qué campos hay para armar el cheque")
        muestra = con_cheq[0] if con_cheq else dep[0]
        for k, v in sorted(muestra.items()):
            if v not in (None, "", {}, []):
                print(f"   {k:<24} {str(v)[:76]}")

    # ── 3. La carga manual del día ───────────────────────────────────────────
    _titulo("3", "LA CARGA MANUAL — tesoreria_cheques (lado='recibido') del día")
    manual = _q(
        "SELECT id, tipo, comitente, comitente_denominacion, cuit, banco, unidad, "
        "       importe, estado, fecha_pago, origen, creado_por "
        "  FROM tesoreria_cheques "
        " WHERE lado = 'recibido' AND (creado_at - interval '3 hours')::date = %(d)s "
        " ORDER BY importe DESC", {"d": dia})
    print(f"Cargados a mano ese día: {len(manual)}\n")
    for r in manual:
        print(f"   #{r['id']:<6} {r['tipo'] or ''!s:>7}  {str(r['banco'])[:20]:<20} "
              f"{r['unidad']!s:>4} {_f(r['importe']):>18,.2f}  {r['estado']!s:<11} "
              f"pago={r['fecha_pago']}  {str(r['comitente_denominacion'] or '')[:22]}")

    # ── 4. Match ─────────────────────────────────────────────────────────────
    _titulo("4", "MATCH — ¿Aunesa muestra lo mismo que se cargó a mano?")
    if not dep and not manual:
        print("Nada de un lado ni del otro: el test no concluye.")
        return 0

    pend = list(manual)
    ok, solo_aunesa = [], []
    for r in dep:
        imp = round(-_f(r.get("total")), 2)
        mon = str(r.get("unidad") or "ARS").upper()
        m = next((x for x in pend
                  if round(_f(x["importe"]), 2) == imp
                  and str(x["unidad"] or "").upper() == mon), None)
        if m:
            pend.remove(m)
            ok.append((r, m))
        else:
            solo_aunesa.append(r)

    print(f"COINCIDEN por (importe, moneda): {len(ok)} de {len(manual)} cargados a mano")
    for r, m in ok:
        cheq = "CHQ" if "cheq" in _norm(r.get("informacion")) else " · "
        print(f"   ✔ [{cheq}] {round(-_f(r.get('total')), 2):>17,.2f} "
              f"{r.get('unidad') or ''!s:>4}  {r.get('comprobante') or ''!s:<15} "
              f"↔ #{m['id']} ({str(m['banco'])[:18]}, pago={m['fecha_pago']})")
        print(f"          Aunesa dice: {str(r.get('informacion') or '')[:60]}")

    print(f"\nSOLO EN AUNESA (no se cargaron a mano): {len(solo_aunesa)}")
    for r in solo_aunesa:
        print(f"   → {-_f(r.get('total')):>17,.2f} {r.get('unidad') or ''!s:>4}  "
              f"{str(r.get('informacion') or '')[:30]:<30} {str(r.get('cuenta') or '')[:34]}")

    print(f"\nSOLO MANUAL (Aunesa NO los tiene → el tipeo no se puede eliminar): {len(pend)}")
    for m in pend:
        print(f"   → {_f(m['importe']):>17,.2f} {m['unidad']!s:>4}  "
              f"{m['tipo'] or ''!s:>7}  {str(m['banco'])[:20]:<20} "
              f"{str(m['comitente_denominacion'] or '')[:26]}")

    # Regla T+1 contra lo que el equipo cargó.
    con_pago = [m for m in manual if m["fecha_pago"]]
    if con_pago:
        coinc = sum(1 for m in con_pago if m["fecha_pago"] == pago)
        print(f"\nRegla T+1: {coinc}/{len(con_pago)} de los manuales tienen fecha_pago = {pago}.")
        otras = sorted({str(m["fecha_pago"]) for m in con_pago if m["fecha_pago"] != pago})
        if otras:
            print(f"   otras fechas de pago cargadas: {', '.join(otras)}")
    else:
        print("\nRegla T+1: ningún cheque manual del día tiene fecha_pago → no se contrasta.")

    print("\nLO QUE DEFINE EL ESPEJO: mirá la columna 'cheq?' de los que COINCIDEN. Si")
    print("alguno matcheó sin decir 'cheq', la regla del espejo NO puede ser el texto —")
    print("hay que tomar todos los depósitos, o encontrar otro campo que los distinga.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
