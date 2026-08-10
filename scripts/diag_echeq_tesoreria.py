"""Diag READ-ONLY — TEST: ¿los depósitos de e-cheq de Aunesa sirven para llenar
solos la tab CHEQUES → RECIBIDOS de Tesorería?

LA REGLA DE NEGOCIO (la puso el user, 2026-08-10): un depósito de e-cheq entra a
Aunesa con la fecha del DÍA en que se carga, pero la plata llega al banco el DÍA
HÁBIL SIGUIENTE. O sea `fecha_pago` = próximo hábil de la fecha del movimiento.

EL TEST, tal cual se pidió: se toman los depósitos de e-cheq que Aunesa tiene HOY
y se comparan uno a uno contra los que el back office cargó HOY a mano en
`operaciones.tesoreria_cheques` (lado='recibido'). Si coinciden, el espejo
automático es viable y reemplaza el tipeo; si no coinciden, dice exactamente en
qué difieren antes de escribir una línea de código de producción.

DE DÓNDE SALEN. Del feed del COMITENTE (`operaciones/consolidadosGenerales`), NO
del bancario. Medido en el paso 1: el feed bancario
(`cuentas/consultaMovDocsSolicitados`, el que ya usa Tesorería) trajo 0 depósitos
e-cheq en 3 días contra 20 extracciones — por eso los RECIBIDOS son 100% carga
manual mientras los EMITIDOS ya tienen espejo (`sincronizar_echeq_emitidos`).

LO QUE HAY QUE MIRAR EN LA SALIDA, porque decide si esto se puede construir:
  · ¿Aunesa los tiene HOY, o recién aparecen mañana? (bloque 1)
  · ¿Coinciden importes y cantidad con la carga manual? (bloque 3)
  · ¿El movimiento trae con qué llenar el BANCO? La tab pide cuenta operativa y
    este feed da la cuenta COMITENTE — si no viene, hay que resolverlo por otro
    lado antes de automatizar nada. (bloque 2, shape crudo)

100% read-only: no crea ni edita un cheque. 1 llamada a Aunesa.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_echeq_tesoreria
    python -m scripts.diag_echeq_tesoreria --fecha 2026-08-07
"""
from __future__ import annotations

import argparse
import unicodedata
from datetime import UTC, date, datetime, timedelta

from api.services._sql import _q
from jobs.cashflow import autenticar as _auth
from jobs.cashflow import fetch_dia

_LINEA = "─" * 78


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


def _es_deposito_echeq(info) -> bool:
    """"Depósito de e-cheque" y variantes. Se pide que sea DEPÓSITO y que mencione
    cheq — así no entran las "Extracción - ECHEQ ID: …" ni las "Recepción ECHEQ"."""
    n = _norm(info)
    return "deposito" in n and "cheq" in n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: hoy ART)")
    args = ap.parse_args()

    dia = (datetime.strptime(args.fecha, "%Y-%m-%d").date() if args.fecha else _hoy_art())
    from core.calendario import proximo_habil
    pago = proximo_habil(dia)

    print("\nTEST e-cheq → TESORERÍA / CHEQUES RECIBIDOS")
    print(f"Día del movimiento: {dia}   →   fecha de pago esperada: {pago} (próximo hábil)")

    # ── 1. ¿Los tiene Aunesa HOY? ────────────────────────────────────────────
    _titulo("1", "AUNESA — depósitos de e-cheq con fecha del día")
    headers = _auth()
    try:
        data, _ = fetch_dia(dia.strftime("%d/%m/%Y"), headers)
    except Exception as e:
        print(f"❌ Aunesa: {str(e)[:150]}")
        return 1
    data = data or []
    dep = [r for r in data if _es_deposito_echeq(r.get("informacion"))]
    print(f"Aunesa devolvió {len(data):,} filas para el día → {len(dep)} depósito(s) de e-cheq.\n")

    if not dep:
        print("❌ NINGUNO. O no hubo hoy, o Aunesa todavía no los publicó a esta hora.")
        print("   Si el back office SÍ cargó cheques hoy (ver bloque 3), entonces el dato")
        print("   llega tarde y un espejo automático los mostraría un día después —")
        print("   justo cuando ya no sirven para el tablero del día.")
    else:
        print(f"{'comprobante':>16}  {'mon':>4}  {'importe':>20}  cuenta comitente")
        for r in dep:
            print(f"{r.get('comprobante') or ''!s:>16}  {r.get('unidad') or ''!s:>4}  "
                  f"{-_f(r.get('total')):>20,.2f}  {str(r.get('cuenta') or '')[:40]}")
        print(f"\nTotal: {sum(-_f(r.get('total')) for r in dep):,.2f} "
              "(suma cruda; puede mezclar monedas)")

        # ── 2. ¿Con qué se puede llenar la fila de la tab? ────────────────────
        _titulo("2", "SHAPE CRUDO del 1er depósito — ¿alcanza para armar el cheque?")
        print("La tab RECIBIDOS necesita: comitente · CUIT · BANCO (cuenta operativa) ·")
        print("importe · moneda · fecha de pago. Veamos qué de eso viene en el movimiento:\n")
        for k, v in sorted(dep[0].items()):
            if v not in (None, "", {}, []):
                print(f"   {k:<24} {str(v)[:76]}")
        falta = [c for c in ("banco", "cuentaOperativa", "cuit") if c not in dep[0]]
        if falta:
            print(f"\n   ⚠ NO viene: {', '.join(falta)} → habría que resolverlo por otro lado")
            print("     (el CUIT sale del padrón por id_cuenta; el BANCO es el problema real).")

    # ── 3. Contra la carga manual ────────────────────────────────────────────
    _titulo("3", "LA CARGA MANUAL — tesoreria_cheques (lado='recibido') del día")
    manual = _q(
        "SELECT id, tipo, comitente, comitente_denominacion, banco, unidad, importe, "
        "       estado, fecha_pago, origen, creado_por "
        "  FROM tesoreria_cheques "
        " WHERE lado = 'recibido' AND (creado_at - interval '3 hours')::date = %(d)s "
        " ORDER BY importe DESC", {"d": dia})
    print(f"Cargados a mano ese día: {len(manual)}\n")
    for r in manual:
        print(f"   #{r['id']:<6} {r['tipo'] or ''!s:>7}  {str(r['banco'])[:22]:<22} "
              f"{r['unidad']!s:>4} {_f(r['importe']):>18,.2f}  {r['estado']!s:<11} "
              f"pago={r['fecha_pago']}  {r['origen']}")

    # ── 4. Match uno a uno por importe ───────────────────────────────────────
    _titulo("4", "MATCH — ¿muestra lo mismo que hay cargado a mano?")
    if not dep and not manual:
        print("Nada de un lado ni del otro: el test no concluye. Repetir un día con carga.")
        return 0

    pend = list(manual)
    ok, solo_aunesa = [], []
    for r in dep:
        imp = round(-_f(r.get("total")), 2)
        mon = (str(r.get("unidad") or "ARS")).upper()
        m = next((x for x in pend
                  if round(_f(x["importe"]), 2) == imp
                  and str(x["unidad"] or "").upper() == mon), None)
        if m:
            pend.remove(m)
            ok.append((r, m))
        else:
            solo_aunesa.append(r)

    print(f"Coinciden por (importe, moneda): {len(ok)}")
    for r, m in ok:
        print(f"   ✔ {round(-_f(r.get('total')), 2):>18,.2f} {r.get('unidad') or ''!s:>4}  "
              f"Aunesa {r.get('comprobante') or ''!s:<16} ↔ manual #{m['id']} "
              f"({str(m['banco'])[:20]}, pago={m['fecha_pago']})")

    print(f"\nSolo en AUNESA (el equipo no los cargó): {len(solo_aunesa)}")
    for r in solo_aunesa:
        print(f"   → {-_f(r.get('total')):>18,.2f} {r.get('unidad') or ''!s:>4}  "
              f"{str(r.get('cuenta') or '')[:44]}")

    print(f"\nSolo MANUAL (Aunesa no los tiene): {len(pend)}")
    for m in pend:
        print(f"   → {_f(m['importe']):>18,.2f} {m['unidad']!s:>4}  "
              f"{str(m['banco'])[:22]}  {str(m['comitente_denominacion'] or '')[:26]}")

    # Verificación de la regla T+1 contra lo que el equipo puso a mano.
    con_pago = [m for m in manual if m["fecha_pago"]]
    if con_pago:
        coinciden = sum(1 for m in con_pago if m["fecha_pago"] == pago)
        print(f"\nRegla T+1: {coinciden}/{len(con_pago)} de los cargados a mano tienen "
              f"fecha_pago = {pago} (el próximo hábil).")
        distintos = {str(m["fecha_pago"]) for m in con_pago if m["fecha_pago"] != pago}
        if distintos:
            print(f"   otras fechas de pago vistas: {', '.join(sorted(distintos))}")
    else:
        print("\nRegla T+1: ningún cheque manual del día tiene fecha_pago cargada "
              "→ no se puede contrastar la regla con este día.")

    print("\nLECTURA: si 'Coinciden' cubre casi todo y 'Solo MANUAL' es 0, el espejo")
    print("automático reemplaza el tipeo. Si hay 'Solo MANUAL', esos cheques NO vienen")
    print("por este feed y la carga a mano tiene que seguir existiendo igual.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
