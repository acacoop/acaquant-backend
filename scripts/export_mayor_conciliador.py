"""Genera el .xlsx del MAYOR que se sube al CONCILIADOR (Back Office → INTERBANKING),
a partir de los registros contables de Aunesa.

Hoy ese archivo se exporta a mano desde HYGIRUS. Esto lo arma desde la API, para
UNA cuenta contable y UN día, con las columnas EXACTAS que parsea el backend.

CONTRATO (leído de `api/services/bancos.py`, no inventado):
  · Las columnas se buscan **por nombre** en las primeras 10 filas (`_columna`):
    `Fecha`, `Concepto`, `Debe`, `Haber`, `Saldo`. El orden no importa, el nombre sí.
  · **Una fila es un movimiento si tiene `Fecha`** (`_movimientos_del_mayor`).
    Por eso la fila de saldo inicial va SIN fecha: si la llevara, se contaría como
    un movimiento de más y rompería la suma del día.
  · `importe = Debe − Haber`.
  · `Concepto` alimenta `_grupo_mayor`, que saca el `[Op. NNNN]` y corta en el
    primer ` - `. Por eso va la `referencia` del asiento TAL CUAL: es el mismo
    texto que trae el export de HYGIRUS y el que agrupa el CONSOLIDADO.
  · **`Saldo` es ACUMULADO CORRIDO y solo se lee el ÚLTIMO valor** — ese es el
    cierre del mayor contra el que se compara nuestro saldo.

⚠️ **`--saldo-inicial` NO ES OPCIONAL PARA CONCILIAR DE VERDAD.**
El endpoint devuelve MOVIMIENTOS, no saldos: no hay forma de deducir de él con
cuánto abrió la cuenta. Sin ese dato el acumulado arranca en cero y el "cierre del
mayor" termina siendo la suma del día, que no es un saldo — y el conciliador lo va
a comparar igual, sin saber que le dimos una base falsa. Se pasa el cierre del día
ANTERIOR según HYGIRUS.

Y **no se puede sacar de `bancos.saldos`**: ese es el saldo del BANCO, que es
justamente el otro lado de la comparación. Alimentarlo con eso haría que todo
concilie siempre por construcción — un conciliador que nunca encuentra nada.

Uso (en el Droplet):
    python -m scripts.export_mayor_conciliador \\
        --archivo /tmp/registros_1908.json \\
        --cuenta 101010100002 \\
        --saldo-inicial 12345678.90 \\
        -o /tmp/mayor_patagonia_1908.xlsx

    # pidiéndolo a la API (fechaDesde/fechaHasta = el mismo día, ~90 s)
    python -m scripts.export_mayor_conciliador --fecha 19/08/2026 --cuenta 101010100002

Después, en el Mac:  scp root@<droplet>:/tmp/mayor_patagonia_1908.xlsx ~/Desktop/
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta

from scripts.export_registros_contables import ENDPOINT, _fecha, _num

# Las del export real de HYGIRUS. `Comprobante`, `Auxiliar` y `Asiento` NO las
# parsea el backend: van para que la persona que concilia reconozca la fila.
COLUMNAS = (("Fecha", 12), ("Comprobante", 18), ("Concepto", 72), ("Auxiliar", 12),
            ("Asiento", 12), ("Debe", 16), ("Haber", 16), ("Saldo", 18))

# Un solo formato, sin sección negativa propia: `'#,##0.00;[Red]-#,##0.00'`
# imprimía **doble menos** (`--1.915,78`) porque el visor ya antepone el signo al
# formatear la sección negativa. Con una sola sección el menos lo pone el visor.
FMT_NUM = "#,##0.00"
FMT_FECHA = "DD/MM/YYYY"


def _movimientos_de_la_cuenta(registros: list[dict], aguja: str) -> list[dict]:
    """Los movimientos cuya cuenta matchea, con los datos del asiento al lado."""
    aguja = aguja.casefold()
    out = []
    for asiento in registros:
        for mov in (asiento.get("movimientos") or []):
            if not isinstance(mov, dict):
                continue
            codigo = str(mov.get("codigoCuenta") or "")
            nombre = str(mov.get("nombreCuenta") or "")
            if aguja not in f"{codigo} {nombre}".casefold():
                continue
            out.append({
                "fecha": _fecha(asiento.get("fechaConciliacion")),
                "comprobante": str(mov.get("comprobante") or ""),
                # El texto que agrupa `_grupo_mayor`. La referencia del ASIENTO es
                # la que trae el `[Op. NNNN]`; la del movimiento describe el detalle
                # y se usa de respaldo cuando el asiento no dice nada.
                "concepto": (str(asiento.get("referencia") or "").strip()
                             or str(mov.get("referencia") or "").strip()
                             or "(sin concepto)"),
                "asiento": str(asiento.get("numero") or ""),
                "importe": _num(mov.get("valuacion")) or 0.0,
                "cuenta": codigo, "nombre_cuenta": nombre,
                "alta": _fecha(asiento.get("fechaAlta")),
            })
    # El orden es cosmético: `_saldo_del_mayor` lee el ÚLTIMO valor de la columna
    # y el acumulado llega al mismo número en cualquier orden.
    out.sort(key=lambda m: (str(m["alta"]), m["asiento"]))
    return out


def _grilla(movs: list[dict], saldo_inicial: float, titulo: str) -> list[list]:
    """La grilla exacta que termina en el .xlsx, como la ve el backend.

    Está separada del volcado a Excel A PROPÓSITO: así lo que se testea contra
    `_movimientos_del_mayor` / `_saldo_del_mayor` es LO MISMO que se escribe, y no
    una reconstrucción parecida. Sin esto el test probaría una copia del formato.
    """
    filas: list[list] = [[n for n, _ in COLUMNAS]]
    # Saldo inicial SIN fecha: con fecha, `_movimientos_del_mayor` lo contaría
    # como un movimiento más y el día cerraría con el doble del arranque.
    filas.append([None, None, f"SALDO INICIAL — {titulo}", None, None,
                  None, None, saldo_inicial])
    saldo = saldo_inicial
    for m in movs:
        saldo = round(saldo + m["importe"], 2)
        filas.append([
            m["fecha"], m["comprobante"], m["concepto"], None, m["asiento"],
            m["importe"] if m["importe"] > 0 else None,
            -m["importe"] if m["importe"] < 0 else None,
            saldo,
        ])
    return filas


def _escribir(filas: list[list], salida: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Mayor"
    for col, (nombre, ancho) in enumerate(COLUMNAS, start=1):
        c = ws.cell(row=1, column=col, value=nombre)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        c.alignment = Alignment(horizontal="center")
        ws.column_dimensions[c.column_letter].width = ancho
    for i, valores in enumerate(filas[1:], start=2):
        for col, valor in enumerate(valores, start=1):
            if valor is None:
                continue
            c = ws.cell(row=i, column=col, value=valor)
            if col == 1:
                c.number_format = FMT_FECHA
            elif col in (6, 7, 8):
                c.number_format = FMT_NUM
    ws.cell(row=2, column=3).font = Font(italic=True)
    ws.freeze_panes = "A2"
    wb.save(salida)


def main() -> None:
    ayer = date.today() - timedelta(days=1)
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--archivo", default=None, help="JSON ya guardado (no toca la API)")
    p.add_argument("--fecha", default=None,
                   help="dd/mm/yyyy, fecha de CONCILIACIÓN (default: ayer)")
    p.add_argument("--cuenta", default="101010100002",
                   help="código o nombre de la cuenta contable (default: Patagonia CC)")
    p.add_argument("--saldo-inicial", type=float, default=None,
                   help="cierre del día ANTERIOR según HYGIRUS (ver el aviso del docstring)")
    p.add_argument("-o", "--salida", default=None, help="ruta del .xlsx")
    args = p.parse_args()

    fecha = args.fecha or ayer.strftime("%d/%m/%Y")

    if args.archivo:
        print(f"Leyendo {args.archivo} (no se toca la API)…")
        with open(args.archivo, encoding="utf-8") as f:
            data = json.load(f)
    else:
        from core import aunesa

        print(f"Pidiendo {fecha} a Aunesa (~90 s)…")
        # Mismo día en desde y hasta: el rango filtra por fechaConciliacion.
        resp = aunesa.get(ENDPOINT, {"fechaDesde": fecha, "fechaHasta": fecha,
                                     "inclNC": "true"}, timeout=180)
        if resp.status_code != 200:
            print(f"❌ HTTP {resp.status_code}: {resp.text[:300]}")
            return
        data = resp.json()

    registros = data.get("registros") if isinstance(data, dict) else data
    if not isinstance(registros, list) or not registros:
        print("❌ la respuesta no trae `registros`")
        return
    registros = [r for r in registros if isinstance(r, dict)]

    movs = _movimientos_de_la_cuenta(registros, args.cuenta)
    if not movs:
        print(f"❌ ningún movimiento para la cuenta {args.cuenta!r} en ese archivo.")
        return

    nombre = movs[0]["nombre_cuenta"] or args.cuenta
    codigos = {m["cuenta"] for m in movs}
    if len(codigos) > 1:
        # El conciliador es de UNA cuenta bancaria: mezclar dos daría un saldo que
        # no es el de ninguna, y nada en la pantalla lo delataría.
        print(f"❌ {args.cuenta!r} matchea {len(codigos)} cuentas: {sorted(codigos)}")
        print("   Afiná --cuenta con el código exacto.")
        return

    d = _fecha(fecha)
    sufijo = d.strftime("%Y%m%d") if isinstance(d, date) else fecha.replace("/", "")
    salida = args.salida or f"/tmp/mayor_{next(iter(codigos))}_{sufijo}.xlsx"

    inicial = args.saldo_inicial if args.saldo_inicial is not None else 0.0
    filas = _grilla(movs, inicial, f"{next(iter(codigos))} {nombre}")
    _escribir(filas, salida)
    cierre = filas[-1][7]

    neto = round(sum(m["importe"] for m in movs), 2)
    debe = sum(m["importe"] for m in movs if m["importe"] > 0)
    haber = -sum(m["importe"] for m in movs if m["importe"] < 0)
    print(f"\n✔ {salida}")
    print(f"   cuenta   {next(iter(codigos))} {nombre}")
    print(f"   fecha    {fecha} (conciliación)")
    print(f"   {len(movs)} movimientos · Debe {debe:,.2f} · Haber {haber:,.2f} · neto {neto:,.2f}")
    print(f"   saldo inicial {inicial:,.2f} → CIERRE DEL MAYOR {cierre:,.2f}")
    if args.saldo_inicial is None:
        print("\n   ⚠️  SIN --saldo-inicial: el acumulado arrancó en CERO, así que el")
        print(f"      cierre de arriba ({cierre:,.2f}) es el neto del día, NO un saldo.")
        print("      El conciliador lo va a comparar igual y va a dar una diferencia")
        print("      falsa. Para conciliar de verdad, volvé a correrlo pasando el")
        print("      cierre del día anterior según HYGIRUS en --saldo-inicial.")


if __name__ == "__main__":
    main()
