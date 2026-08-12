"""Diag READ-ONLY: DEPÓSITOS de cheque en Aunesa vs. lo que hay cargado en la app.

QUÉ RESPONDE. Para un día, pone lado a lado las dos verdades del tablero
Tesorería → CHEQUES → RECIBIDOS:

  · lo que Aunesa informa   (feed del COMITENTE, `operaciones/consolidadosGenerales`)
  · lo que hay en la app    (`operaciones.tesoreria_cheques`, lado 'recibido')

y dice, depósito por depósito, si está ESPEJADO, si FALTA, o si probablemente ya
lo cargó el back office A MANO (mismo importe y moneda, `origen='manual'`).

POR QUÉ HACE FALTA. El espejo lo corre un cron con ventana horaria y un lookback
de 2 días hábiles: un depósito que se carga en Aunesa fuera de esa ventana puede
no llegar a espejarse nunca. Este diag es el chequeo antes de forzar el job con
`--fecha`, para no crear un duplicado de algo que ya se cargó a mano — que es
exactamente el motivo por el que el lookback se bajó de 3 a 2 (2026-08-11).

NO ESCRIBE NADA. Si al final aparecen faltantes, imprime el comando exacto que
los crea (`jobs.tesoreria_echeq_recibidos --fecha …`, idempotente por `mov_id`).

Usa las MISMAS funciones que el espejo (`_es_deposito_cheque`,
`_filas_espejo_depositos`) → lo que este diag dice que falta es literalmente lo
que el job crearía. No puede divergir.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_echeq_recibidos_vs_aunesa                 # ayer hábil
    python -m scripts.diag_echeq_recibidos_vs_aunesa --fecha 2026-08-11
    python -m scripts.diag_echeq_recibidos_vs_aunesa --dias 5        # 5 días hacia atrás
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime

sys.path.insert(0, ".")

from api.services._sql import _q
from api.services.tesoreria import (
    _DIA_RECIBIDO,
    _ENDPOINT_COMITENTE,
    _TABLA_CHEQUES,
    ORIGEN_AUNESA,
    _es_deposito_cheque,
    _filas_espejo_depositos,
    _hoy_art,
    _num,
    _tipo_cheque_recibido,
)
from core import aunesa
from core.calendario import proximo_habil, ultimos_habiles


def _plata(x: float) -> str:
    return f"{x:>16,.2f}"


def _traer_aunesa(dia: date) -> list[dict]:
    """Boletos del día del feed del COMITENTE. Mismo endpoint/params que el espejo."""
    ddmmyyyy = dia.strftime("%d/%m/%Y")
    resp = aunesa.get(_ENDPOINT_COMITENTE, {
        "tiposCuenta": "Comitente",
        "concertacionDesde": ddmmyyyy,
        "concertacionHasta": ddmmyyyy,
    })
    if resp.status_code == 204:
        return []
    if resp.status_code != 200:
        raise RuntimeError(f"Aunesa [{resp.status_code}]: {resp.text[:200]}")
    body = resp.json()
    return body if isinstance(body, list) else []


def _filas_app(movs: list[str], dias: list[date]) -> list[dict]:
    """Recibidos de la app que pueden corresponder a este día: los que espejan uno
    de estos comprobantes, más TODO lo que la vista muestra ese día y el siguiente
    hábil (que es donde caería una carga manual del mismo depósito)."""
    return _q(
        f"SELECT id, tipo, comitente, comitente_denominacion, banco, unidad, importe, "
        f"       estado, fecha_pago, origen, mov_id, creado_por, creado_at, "
        f"       {_DIA_RECIBIDO} AS dia "
        f"  FROM {_TABLA_CHEQUES} "
        f" WHERE lado = 'recibido' "
        f"   AND (mov_id = ANY(%(movs)s) OR {_DIA_RECIBIDO} = ANY(%(dias)s)) "
        f" ORDER BY id",
        {"movs": movs, "dias": dias},
    )


def _comparar(dia: date) -> dict:
    crudas = _traer_aunesa(dia)
    depositos = [r for r in crudas if _es_deposito_cheque(r.get("informacion"))]
    # Lo que el espejo REALMENTE crearía (solo e-cheq, con comprobante e importe).
    espejables = _filas_espejo_depositos(dia, crudas)
    esperados = {f["mov"]: f for f in espejables}

    # Depósitos que el espejo deja afuera a propósito — se listan para que el
    # chequeo sea contra TODO lo que informa Aunesa, no solo contra lo espejable.
    fuera = []
    for r in depositos:
        mov = f"dep:{str(r.get('comprobante') or '').strip()}"
        if mov in esperados:
            continue
        tipo = _tipo_cheque_recibido(r.get("informacion"))
        motivo = ("cheque de PAPEL (el espejo solo toma e-cheq)" if tipo != "echeq"
                  else "sin comprobante" if not str(r.get("comprobante") or "").strip()
                  else "importe 0" if _num(r.get("total")) == 0 else "descartado")
        fuera.append({"mov": mov, "tipo": tipo, "motivo": motivo,
                      "denom": str(r.get("cuenta") or "").strip(),
                      "importe": abs(_num(r.get("total"))),
                      "unidad": str(r.get("unidad") or "ARS").upper(),
                      "info": str(r.get("informacion") or "")[:60]})

    filas = _filas_app(sorted(esperados), sorted({dia, proximo_habil(dia)}))
    por_mov = {f["mov_id"]: f for f in filas if f["mov_id"]}
    manuales = [f for f in filas if f["origen"] != ORIGEN_AUNESA]

    ok, faltan, dudosos = [], [], []
    usados: set[int] = set()   # un manual no puede ser el gemelo de dos depósitos
    for mov, esp in sorted(esperados.items()):
        if mov in por_mov:
            ok.append((esp, por_mov[mov]))
            continue
        # ¿lo cargó el back office a mano? mismo importe + misma moneda.
        gemelo = next((m for m in manuales
                       if m["id"] not in usados
                       and round(float(m["importe"] or 0), 2) == round(esp["importe"], 2)
                       and (m["unidad"] or "").upper() == esp["unidad"]), None)
        if gemelo:
            usados.add(gemelo["id"])
        (dudosos if gemelo else faltan).append((esp, gemelo))

    # Espejos en la app sin respaldo en el feed de hoy (Aunesa cambió/borró la fila).
    huerfanos = [f for f in filas
                 if f["origen"] == ORIGEN_AUNESA and f["mov_id"] not in esperados]
    return {"dia": dia, "depositos": len(depositos), "espejables": espejables,
            "fuera": fuera, "ok": ok, "faltan": faltan, "dudosos": dudosos,
            "huerfanos": huerfanos, "manuales": manuales}


def _imprimir(res: dict) -> None:
    d = res["dia"]
    print(f"\n{'═' * 78}\n  {d}  ·  {res['depositos']} depósito(s) de cheque en Aunesa"
          f"  ·  {len(res['espejables'])} espejable(s)\n{'═' * 78}")

    if res["ok"]:
        print(f"\n  ✅ ESPEJADOS OK ({len(res['ok'])})")
        for esp, fila in res["ok"]:
            print(f"     {esp['mov']:<22} {esp['unidad']} {_plata(esp['importe'])}  "
                  f"id={fila['id']:<6} estado={fila['estado']:<10} "
                  f"banco={(fila['banco'] or '— sin banco —')[:28]}")

    if res["dudosos"]:
        print(f"\n  ⚠️  FALTA EL ESPEJO, PERO YA HAY UNO MANUAL IGUAL ({len(res['dudosos'])})"
              "\n     → NO correr el job para estos: duplicaría el ingreso.")
        for esp, gemelo in res["dudosos"]:
            print(f"     {esp['mov']:<22} {esp['unidad']} {_plata(esp['importe'])}  "
                  f"{(esp['denom'] or '')[:34]}")
            print(f"     {'':<22} ↳ manual id={gemelo['id']} estado={gemelo['estado']} "
                  f"banco={(gemelo['banco'] or '—')[:24]} cargó={gemelo['creado_por'] or '?'}")

    if res["faltan"]:
        print(f"\n  ❌ FALTAN EN LA APP ({len(res['faltan'])})  → esto es lo que hay que crear")
        for esp, _ in res["faltan"]:
            print(f"     {esp['mov']:<22} {esp['unidad']} {_plata(esp['importe'])}  "
                  f"pago={esp['fp']}  {(esp['denom'] or '')[:34]}")

    if res["fuera"]:
        print(f"\n  ℹ️  DEPÓSITOS QUE EL ESPEJO NO TOMA ({len(res['fuera'])})")
        for f in res["fuera"]:
            print(f"     {f['mov']:<22} {f['unidad']} {_plata(f['importe'])}  "
                  f"[{f['motivo']}]  {f['info']}")

    if res["huerfanos"]:
        print(f"\n  🔎 ESPEJOS EN LA APP SIN FILA EN EL FEED DE HOY ({len(res['huerfanos'])})")
        for f in res["huerfanos"]:
            print(f"     {f['mov_id']:<22} {f['unidad']} {_plata(float(f['importe'] or 0))}  "
                  f"id={f['id']} estado={f['estado']}")

    solo_manual = [m for m in res["manuales"]
                   if m["id"] not in {g["id"] for _, g in res["dudosos"] if g}]
    if solo_manual:
        print(f"\n  ✍️  CARGA MANUAL DE ESOS DÍAS, SIN DEPÓSITO EQUIVALENTE ({len(solo_manual)})")
        for m in solo_manual:
            print(f"     id={m['id']:<6} {m['unidad']} {_plata(float(m['importe'] or 0))}  "
                  f"{m['estado']:<10} {(m['comitente_denominacion'] or '')[:34]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: ayer hábil)")
    ap.add_argument("--dias", type=int, default=1,
                    help="días hábiles hacia atrás desde --fecha, esa incluida (default 1)")
    args = ap.parse_args()

    hoy = _hoy_art().date()
    if args.fecha:
        try:
            hasta = datetime.strptime(args.fecha, "%Y-%m-%d").date()
        except ValueError:
            print(f"--fecha mal formada: {args.fecha}")
            return 1
    else:
        hasta = ultimos_habiles(hoy, 1)[0]   # ayer hábil

    dias = ultimos_habiles(hasta, max(0, args.dias - 1))
    print(f"Comparando Aunesa vs. app · {len(dias)} día(s): "
          f"{', '.join(d.isoformat() for d in dias)}  (hoy es {hoy})")

    faltan_total: list[date] = []
    for i, d in enumerate(dias):
        try:
            res = _comparar(d)
        except Exception as e:
            print(f"\n  {d}: ERROR — {str(e).splitlines()[0][:200]}")
            continue
        _imprimir(res)
        if res["faltan"]:
            faltan_total.append(d)
        if i < len(dias) - 1:
            time.sleep(2)   # throttle entre días (REGLA #4)

    print(f"\n{'─' * 78}")
    if faltan_total:
        print("  Para crear los que faltan (idempotente, no pisa lo editado):")
        for d in faltan_total:
            print(f"      python -m jobs.tesoreria_echeq_recibidos --fecha {d} --dry")
            print(f"      python -m jobs.tesoreria_echeq_recibidos --fecha {d}")
        print("  Revisá antes el bloque ⚠️: si el importe ya está cargado a mano,\n"
              "  el job igual lo crea (el espejo no puede reconocer una carga manual).")
    else:
        print("  Nada que crear: todo lo espejable ya está en la app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
