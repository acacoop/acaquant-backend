"""scripts/diag_gastos_bancarios.py — probar las reglas de gasto ANTES de cargarlas.

READ-ONLY por defecto: solo `SELECT` sobre `bancos.*`. Con `--aplicar` (y solo
con eso) carga las reglas en el catálogo.

## Por qué existe

El back office pasó una lista de descripciones de gasto bancario por banco. Esa
lista es **una transcripción a mano de una pantalla**, y por lo tanto NO es la
fuente de verdad (REGLA #2): está truncada, puede tener acentos rotos y puede
faltarle casos. La fuente de verdad es `bancos.movimientos.descripcion_banco`.

Este script cruza las dos cosas y contesta, con datos reales de un día:

  1. ¿Qué descripciones DISTINTAS hubo, cuánto suman, y cuál regla las agarra?
  2. ¿Qué movimientos NO agarra ninguna regla? (los huérfanos: lo que falta)
  3. ¿Cuánto daría el gasto de cada cuenta?
  4. ¿Se enciende alguna cuenta que el back office dice que NUNCA tuvo gastos?
     Eso es un FALSO POSITIVO y es la prueba más barata que tenemos.
  5. ¿El campo `tipo` (D/C) es confiable? De él depende el signo del gasto.

## Las tres cosas que la lista deja ver

**(a) La descripción viene TRUNCADA** — «N/D - COMISION MANTENIMIE», «COMISION
POR SERVICIO BAN», «Percep. Ingr. Brutos CABA». El campo es de ancho fijo. Por
eso las reglas van con `contiene` y con la RAÍZ de la palabra, nunca con `igual`
sobre el texto completo: `igual` no matchearía casi nada.

**(b) Cada banco abrevia distinto** — «C/MANT CTA» y «COM.TEF DN» (BIND),
«COM.MANT.PQ 18» (BBVA), «IVAPERCEP»/«IIBBPERCEP» (Galicia, BIND). No hay una
sola palabra que cubra a todos: hace falta una regla por familia y por dialecto.

**(c) Hay descripciones AMBIGUAS que NO se pueden resolver por texto** —
«TRANSFERENCIA DATANET» es gasto en Patagonia, pero en los datos reales del
2026-08-18 aparecía como movimiento normal de cientos de millones; y «DEBITOS»
(Galicia) como regla global sería un desastre. Esas van marcadas RIESGO y hay que
mirarlas acá antes de activarlas.

Uso:
    python -m scripts.diag_gastos_bancarios                 # último día hábil
    python -m scripts.diag_gastos_bancarios --fecha 2026-08-14
    python -m scripts.diag_gastos_bancarios --huerfanos     # solo lo que no agarra
    python -m scripts.diag_gastos_bancarios --aplicar       # ⚠️ CARGA las reglas
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date

from api.services import bancos as svc

SEP = "=" * 110

# ─────────────────────────────────────────────────────────────────────────────
# Reglas CANDIDATAS, derivadas de la lista del back office (2026-08-18).
# NO están cargadas: esto es lo que se propone y lo que el script mide.
#
# Todas van por `descripcion_banco` + `contiene`, y con la RAÍZ de la palabra
# para sobrevivir al truncado y a los acentos rotos:
#   · "COMISI"  agarra COMISION, COMISIONES, Comision y también «Comisi.n»
#     (Comafi/Credicoop, donde el acento llegó como punto).
#   · "PERCEP"  agarra PERCEPCION, Percepci.n, IVAPERCEP, IIBBPERCEP, PERCEP AGIP.
#   · "INTERES" agarra INTERESES, Intereses, INTERES COBRAD.
# ─────────────────────────────────────────────────────────────────────────────
CANDIDATAS: list[tuple[str, str, str]] = [
    # (valor, familia, nota)
    ("COMISI",        "comisión",   "COMISION/COMISIONES/Comisi.n — la raíz cubre los 3 dialectos"),
    ("MANTENIMIE",    "comisión",   "MANTENIMIENTO DE CUENTA / N/D - COMISION MANTENIMIE"),
    ("C/MANT",        "comisión",   "BIND abrevia así"),
    ("COM.MANT",      "comisión",   "BBVA: COM.MANT.PQ 18"),
    ("COM.TEF",       "comisión",   "BIND: COM.TEF DN"),
    ("IVA",           "iva",        "IVA / IVA TASA GENERAL / IVA ALICUOTA / IVAPERCEP"),
    ("I.V.A",         "iva",        "Credicoop y BIND lo escriben con puntos"),
    ("PERCEP",        "percepción", "PERCEPCION / Percepci.n / IVAPERCEP / IIBBPERCEP / PERCEP AGIP"),
    ("RETENCION",     "retención",  "RETENCION IIBB SIRCREB / RETENCION IMP.GANANCIAS"),
    ("IIBB",          "percepción", "IIBB PERCEPCION CABA / IIBBPERCEP"),
    ("SIRCREB",       "retención",  "RETENCION IIBB SIRCREB"),
    ("INGR",          "percepción", "Percep. Ingr. Brutos CABA / N/D - PERCEPCION INGRESOS"),
    ("SELLOS",        "impuesto",   "IMPUESTO A LOS SELLOS / Imp Sellos CABA"),
    ("IMPUESTO",      "impuesto",   "IMPUESTO A LOS SELLOS / Impuesto al cr.dito"),
    ("IMP.DB",        "impuesto",   "IMP.DB/CR BANCARIOS P/CRE"),
    ("LEY25413",      "impuesto",   "BIND: LEY25413DB (impuesto al débito)"),
    ("OTROS IMP",     "impuesto",   "Galicia"),
    ("INTERES",       "interés",    "INTERESES P/SOBREGIRO / Intereses por Acuerdo / INTERES COBRAD"),
    ("SOBREGIRO",     "interés",    "INTERESES P/SOBREGIRO"),
    ("ADELANT",       "interés",    "DEBITO DE INTERESES ADELANTOS / N/D - INTERESES ADELANTAD"),
    ("TASA LIQUIDEZ", "interés",    "VALO: N/D - TASA LIQUIDEZ INTRA"),
    ("EMISION ECHEQ", "comisión",   "Patagonia: «es como una comisión que lleva el IVA del 21%»"),
    ("ECHEQ CLEA",    "comisión",   "VALO ACDI: N/D - COMISION ECHEQ CLEA"),
    ("NOTA DB",       "otro",       "Credicoop"),
]

# ⚠️ Estas NO se proponen: el mismo texto es un gasto en un banco y una operación
# real en otro. Se listan aparte para mirarlas con los datos en la mano.
RIESGOSAS: list[tuple[str, str]] = [
    ("TRANSFERENCIA DATANET",
     "Patagonia la marca como gasto, pero en los datos reales aparece como "
     "movimiento normal de cientos de millones. Por texto NO se distingue."),
    ("DEBITOS",
     "Galicia: «aparece así pero si existe la posibilidad de que se detecte como "
     "comisión». Como regla global agarraría medio extracto."),
    ("DATANET",
     "Comafi/Patagonia/BIND lo usan para la COMISION de datanet y también para la "
     "transferencia en sí."),
]

# El back office dijo que estas cuentas NUNCA tuvieron gastos. Si alguna se
# enciende, la regla está de más — es el oráculo más barato que tenemos.
SIN_GASTOS_ESPERADO = (
    "VALO CERA USD", "VALO ACDI CERA USD", "BBVA TERCEROS ARS", "COMAFI BNY",
    "COMAFI TERCEROS USD", "GALICIA ACDI", "GALICIA USD",
)


def _agarra(desc: str, valor: str) -> bool:
    return valor.strip().casefold() in (desc or "").strip().casefold()


def main() -> None:
    ap = argparse.ArgumentParser(description="Probar las reglas de gasto bancario")
    ap.add_argument("--fecha", type=date.fromisoformat, default=None)
    ap.add_argument("--huerfanos", action="store_true",
                    help="solo lo que NO agarra ninguna regla")
    ap.add_argument("--aplicar", action="store_true",
                    help="⚠️ CARGA las candidatas en bancos.gastos_reglas")
    ap.add_argument("--email", default="diag@acaquant.com",
                    help="con quién se firman las reglas si se aplican")
    args = ap.parse_args()

    fecha = args.fecha or svc.fecha_default()
    movs = svc._movs_para_clasificar(fecha)
    print(f"\n{SEP}\nGASTOS BANCARIOS — prueba de reglas al {fecha}\n{SEP}")
    if not movs:
        print("  No hay movimientos guardados de ese día. "
              "Probá con --fecha de un día que sí tenga.\n")
        return

    cuentas = {c["id"]: c for c in svc._q(
        "SELECT id, bank_name, account_type, currency, account_label, account_number "
        "FROM bancos.cuentas")}

    # ── 1. ¿El campo `tipo` es confiable? De él depende el SIGNO del gasto ──
    tipos: dict[str, int] = defaultdict(int)
    for m in movs:
        tipos[str(m.get("tipo"))] += 1
    print(f"\n  {len(movs)} movimientos · tipo: " +
          " · ".join(f"{k}={v}" for k, v in sorted(tipos.items())))
    if set(tipos) - {"C", "D"}:
        print("  ⚠️  Hay tipos distintos de C/D. El signo del gasto depende de esto.")

    # ── 2. Descripciones distintas, y qué regla las agarra ──────────────────
    por_desc: dict[str, dict] = {}
    for m in movs:
        d = (m.get("descripcion_banco") or "").strip()
        e = por_desc.setdefault(d, {
            "n": 0, "importe": 0.0, "cuentas": set(),
            "tipos": defaultdict(int), "imp_por_tipo": defaultdict(list),
        })
        e["n"] += 1
        imp = float(m.get("importe") or 0)
        e["importe"] += imp
        e["cuentas"].add(m["cuenta_id"])
        e["tipos"][str(m.get("tipo"))] += 1
        e["imp_por_tipo"][str(m.get("tipo"))].append(imp)

    filas = []
    for d, e in por_desc.items():
        match = next(((v, f) for v, f, _ in CANDIDATAS if _agarra(d, v)), None)
        riesgo = next((v for v, _ in RIESGOSAS if _agarra(d, v)), None)
        filas.append((d, e, match, riesgo))
    filas.sort(key=lambda x: -x[1]["importe"])

    if args.huerfanos:
        # ⚠️ "43 huérfanas" no es accionable: la mayoría son movimientos normales
        # que ESTÁ BIEN que no sean gasto. Lo que hay que mirar son los DÉBITOS —
        # un gasto bancario siempre lo es. Los créditos son plata entrando y casi
        # con seguridad no son un cobro del banco.
        #
        # Ordenados por CUÁNTAS VECES aparecen y no por importe: un gasto se
        # repite (todos los días o todos los meses) y suele ser chico. Un importe
        # grande que aparece una vez es, casi siempre, una operación del negocio.
        for tipo, titulo, ayuda in (
            ("D", "HUÉRFANOS — DÉBITOS", "acá están los gastos que faltan (y los «?????»)"),
            ("C", "HUÉRFANOS — CRÉDITOS", "plata entrando: casi seguro NO son gasto"),
        ):
            print(f"\n{SEP}\n{titulo} — {ayuda}\n{SEP}")
            print(f"  {'DESCRIPCIÓN (texto REAL de la base)':<42}{'N':>4}{'IMPORTE':>18}"
                  f"{'MENOR':>14}{'MAYOR':>16}  CUENTAS")
            print("  " + "-" * 106)
            hubo = False
            for d, e, match, _ in filas:
                if match or not e["tipos"].get(tipo):
                    continue
                hubo = True
                imps = e["imp_por_tipo"][tipo]
                print(f"  {(d or '(vacía)')[:40]:<42}{len(imps):>4}{sum(imps):>18,.2f}"
                      f"{min(imps):>14,.2f}{max(imps):>16,.2f}  {len(e['cuentas'])}")
            if not hubo:
                print("  (ninguno)")
        print()
        return

    print(f"\n{SEP}\nDESCRIPCIONES DEL DÍA\n{SEP}")
    print(f"  {'DESCRIPCIÓN (texto REAL de la base)':<42}{'N':>4}{'IMPORTE':>18}"
          f"{'CTAS':>6}  REGLA QUE LA AGARRA")
    print("  " + "-" * 106)
    for d, e, match, riesgo in filas:
        if match:
            marca = f"«{match[0]}» ({match[1]})"
        elif riesgo:
            marca = f"⚠️ RIESGO — «{riesgo}» sin activar"
        else:
            marca = "—"
        print(f"  {(d or '(vacía)')[:40]:<42}{e['n']:>4}{e['importe']:>18,.2f}"
              f"{len(e['cuentas']):>6}  {marca}")

    # ── 2b. Las RIESGOSAS, medidas ──────────────────────────────────────────
    #
    # La pregunta era "¿la comisión de datanet se llama igual que la
    # transferencia?". No hace falta preguntarla: si son dos cosas distintas con
    # el mismo nombre, se ven DOS POBLACIONES DE IMPORTE — unos pocos pesos
    # (la comisión) contra millones (la transferencia). Si todos los importes son
    # del mismo orden, entonces son todos lo mismo.
    print(f"\n{SEP}\nLAS RIESGOSAS, MEDIDAS — ¿son una cosa o dos?\n{SEP}")
    for valor, por_que in RIESGOSAS:
        imps = sorted(
            float(m.get("importe") or 0) for m in movs
            if _agarra(m.get("descripcion_banco") or "", valor) and m.get("tipo") == "D"
        )
        if not imps:
            print(f"\n  «{valor}» — sin débitos ese día.")
            continue
        chicos = [i for i in imps if i < 100_000]
        print(f"\n  «{valor}» — {len(imps)} débito(s) · menor {imps[0]:,.2f} · "
              f"mayor {imps[-1]:,.2f}")
        print(f"     {por_que}")
        if imps[-1] > 0 and imps[0] < imps[-1] / 1000:
            print(f"     → DOS POBLACIONES: {len(chicos)} chico(s) y "
                  f"{len(imps) - len(chicos)} grande(s). Los chicos son la comisión;"
                  " por texto no se separan, pero por IMPORTE sí.")
        else:
            print("     → una sola población de importes: o son todos gasto, o ninguno.")

    # ── 3. Cuánto daría por cuenta ──────────────────────────────────────────
    if not args.huerfanos:
        print(f"\n{SEP}\nGASTO QUE DARÍA CADA CUENTA\n{SEP}")
        total: dict[int, float] = defaultdict(float)
        for m in movs:
            d = (m.get("descripcion_banco") or "")
            if not any(_agarra(d, v) for v, _, _ in CANDIDATAS):
                continue
            imp = float(m.get("importe") or 0)
            total[m["cuenta_id"]] += imp if m.get("tipo") == "D" else -imp

        sospechosas = 0
        for cid, monto in sorted(total.items(), key=lambda x: -abs(x[1])):
            c = cuentas.get(cid, {})
            etiqueta = f"{c.get('bank_name','?')} {c.get('account_type','')}/" \
                       f"{c.get('currency','')} …{str(c.get('account_number') or '')[-4:]}" \
                       f" {c.get('account_label') or ''}"
            aviso = ""
            if any(x.casefold() in etiqueta.casefold() for x in SIN_GASTOS_ESPERADO):
                aviso, sospechosas = "   ⚠️ el back office dijo que NUNCA tuvo gastos", sospechosas + 1
            print(f"  {etiqueta[:66]:<68}{monto:>16,.2f}{aviso}")
        print(f"\n  {len(total)} cuenta(s) con gasto · total "
              f"{sum(total.values()):,.2f} (mezcla monedas: es solo para dimensionar)")
        if sospechosas:
            print(f"  ⚠️  {sospechosas} cuenta(s) se encendieron y NO deberían: "
                  "hay una regla de más.")

        huerfanos = sum(1 for d, _, m, _ in filas if not m)
        print(f"\n  {len(filas) - huerfanos}/{len(filas)} descripciones distintas "
              f"quedan clasificadas. Las {huerfanos} restantes: --huerfanos")

    # ── 4. Aplicar (solo si se pide) ────────────────────────────────────────
    if args.aplicar:
        print(f"\n{SEP}\nCARGANDO {len(CANDIDATAS)} REGLAS\n{SEP}")
        for valor, familia, nota in CANDIDATAS:
            try:
                svc.crear_regla(args.email, "descripcion_banco", "contiene", valor,
                                f"{familia} — {nota}")
                print(f"  ✓ {valor}")
            except Exception as e:
                print(f"  ✗ {valor}: {e}")
        print("\n  Las RIESGOSAS NO se cargaron. Se activan a mano desde la vista "
              "si el listado de arriba lo justifica.")
    print()


if __name__ == "__main__":
    main()
