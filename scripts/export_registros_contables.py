"""Exporta a .xlsx los registros contables de Aunesa (`contabilidad/registrosContables`).

El JSON del custodio son ~9,6 MB anidados (asiento → movimientos): ilegible para
cualquiera, y menos que menos para pasárselo a Contaduría. Esto lo APLANA a una
fila por movimiento y lo escribe como un Excel que se puede filtrar y sumar.

Tres hojas:
  · **Movimientos** — una fila por movimiento, con los datos del asiento repetidos
    al lado (para que cada fila se explique sola al filtrar).
  · **Por cuenta**  — resumen: movimientos, debe, haber y saldo por cuenta contable.
  · **Asientos**    — uno por asiento, con la suma de sus movimientos y si CUADRA.

⚠️ **DEBE/HABER está DERIVADO DEL SIGNO de `valuacion`** (positivo → Debe), que es
la misma convención que ya usamos para el mayor en INTERBANKING (`importe = Debe −
Haber`). El endpoint NO manda las dos columnas por separado. Por eso la hoja
**Asientos** existe: si la partida doble es correcta, cada asiento tiene que sumar
CERO, y eso confirma la convención en vez de asumirla. El resumen de la corrida
dice cuántos cuadran — **si no cuadran, Debe/Haber está al revés o incompleto y no
hay que mandar el archivo sin revisarlo**. La columna `Valuación` va siempre con el
signo original, así que el dato crudo nunca se pierde.

Los números van como NÚMERO (no texto) con formato es-AR, así que Excel los suma y
filtra nativamente. Las fechas, como fecha.

Uso (en el Droplet):
    # desde un JSON ya bajado (no toca la API)
    python -m scripts.export_registros_contables --archivo /tmp/registros_1908.json

    # pidiéndolo de nuevo (tarda ~93 s por día)
    python -m scripts.export_registros_contables --desde 19/08/2026

    # solo una cuenta, para mandar algo corto
    python -m scripts.export_registros_contables --archivo /tmp/registros_1908.json \
        --cuenta 101010100002 -o /tmp/patagonia_1908.xlsx

Después, para bajarlo al Mac:
    scp root@<droplet>:/tmp/registros_1908.xlsx ~/Desktop/
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta

ENDPOINT = "contabilidad/registrosContables"

# (encabezado, ancho). El orden es el que se lee de izquierda a derecha en Excel:
# primero CUÁNDO y QUÉ asiento, después la cuenta, y los importes al final.
COLUMNAS = (
    ("Fecha alta", 12), ("Fecha concil.", 13), ("Asiento", 12), ("AsientoID", 11),
    ("Referencia del asiento", 60),
    ("Cuenta", 14), ("Nombre de cuenta", 42), ("Moneda", 8),
    ("Debe", 16), ("Haber", 16), ("Valuación", 16),
    ("Cantidad", 14), ("Factor", 8),
    ("Comprobante", 18), ("Nro operación", 14),
    ("MovimientoID", 13), ("CuentaID", 10), ("Cód. exp.", 10),
    ("Referencia del movimiento", 70),
)

# Una sola sección, sin sección negativa propia: `'#,##0.00;[Red]-#,##0.00'`
# imprimía **doble menos** (`--1.915,78`), porque el visor ya antepone el signo al
# formatear la sección negativa y el formato lo agregaba de nuevo.
FMT_NUM = "#,##0.00"
FMT_FECHA = "DD/MM/YYYY"


def _fecha(v) -> date | str | None:
    """`"20/08/2026"` → `date`. Si no parsea, devuelve el texto crudo: perder el
    dato por no poder convertirlo sería peor que mostrarlo como está."""
    s = str(v or "").strip()[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return s


def _num(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _filas(registros: list[dict], aguja: str | None) -> list[list]:
    filas = []
    for asiento in registros:
        alta = _fecha(asiento.get("fechaAlta"))
        conc = _fecha(asiento.get("fechaConciliacion"))
        for mov in (asiento.get("movimientos") or []):
            if not isinstance(mov, dict):
                continue
            codigo = str(mov.get("codigoCuenta") or "")
            nombre = str(mov.get("nombreCuenta") or "")
            if aguja and aguja.casefold() not in f"{codigo} {nombre}".casefold():
                continue
            val = _num(mov.get("valuacion"))
            filas.append([
                alta, conc, str(asiento.get("numero") or ""),
                str(asiento.get("asientoID") or ""),
                str(asiento.get("referencia") or ""),
                codigo, nombre, str(mov.get("codigoUnidad") or ""),
                val if (val or 0) > 0 else None,       # Debe
                -val if (val or 0) < 0 else None,      # Haber
                val,
                _num(mov.get("cantidad")), str(mov.get("factor") or ""),
                str(mov.get("comprobante") or ""), str(mov.get("numeroOperacion") or ""),
                str(mov.get("movimientoID") or ""), str(mov.get("cuentaID") or ""),
                str(mov.get("codigoExp") or ""),
                str(mov.get("referencia") or ""),
            ])
    return filas


def _hoja_movimientos(wb, filas: list[list]) -> None:
    from openpyxl.styles import Font, PatternFill

    ws = wb.active
    ws.title = "Movimientos"
    for col, (titulo, ancho) in enumerate(COLUMNAS, start=1):
        c = ws.cell(row=1, column=col, value=titulo)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        ws.column_dimensions[c.column_letter].width = ancho
    for i, valores in enumerate(filas, start=2):
        for col, valor in enumerate(valores, start=1):
            c = ws.cell(row=i, column=col, value=valor)
            if col in (1, 2):
                c.number_format = FMT_FECHA
            elif col in (9, 10, 11, 12):
                c.number_format = FMT_NUM
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{ws.cell(row=1, column=len(COLUMNAS)).column_letter}{len(filas) + 1}"


def _hoja_por_cuenta(wb, filas: list[list]) -> None:
    from openpyxl.styles import Font, PatternFill

    ws = wb.create_sheet("Por cuenta")
    resumen: dict[tuple[str, str], list[float]] = {}
    for f in filas:
        clave = (f[5], f[6])
        acc = resumen.setdefault(clave, [0.0, 0.0, 0.0, 0.0])
        acc[0] += 1
        acc[1] += f[8] or 0.0
        acc[2] += f[9] or 0.0
        acc[3] += f[10] or 0.0
    cabecera = (("Cuenta", 14), ("Nombre de cuenta", 48), ("Movimientos", 12),
                ("Debe", 18), ("Haber", 18), ("Saldo", 18))
    for col, (titulo, ancho) in enumerate(cabecera, start=1):
        c = ws.cell(row=1, column=col, value=titulo)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        ws.column_dimensions[c.column_letter].width = ancho
    for i, ((codigo, nombre), acc) in enumerate(
            sorted(resumen.items(), key=lambda kv: kv[0][0]), start=2):
        ws.cell(row=i, column=1, value=codigo)
        ws.cell(row=i, column=2, value=nombre)
        ws.cell(row=i, column=3, value=int(acc[0]))
        for col, v in ((4, acc[1]), (5, acc[2]), (6, acc[3])):
            ws.cell(row=i, column=col, value=round(v, 2)).number_format = FMT_NUM
    ws.freeze_panes = "A2"


def _hoja_asientos(wb, registros: list[dict]) -> int:
    """Un asiento por fila con la suma de sus movimientos. Devuelve cuántos NO
    cuadran: es la verificación de que Debe/Haber se dedujo bien del signo."""
    from openpyxl.styles import Font, PatternFill

    ws = wb.create_sheet("Asientos")
    cabecera = (("Fecha alta", 12), ("Fecha concil.", 13), ("Asiento", 12),
                ("Movs.", 8), ("Suma", 18), ("¿Cuadra?", 10), ("Referencia", 70))
    for col, (titulo, ancho) in enumerate(cabecera, start=1):
        c = ws.cell(row=1, column=col, value=titulo)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        ws.column_dimensions[c.column_letter].width = ancho
    descuadrados = 0
    for i, asiento in enumerate(registros, start=2):
        movs = asiento.get("movimientos") or []
        suma = round(sum(_num(m.get("valuacion")) or 0.0 for m in movs), 2)
        cuadra = abs(suma) < 0.01
        descuadrados += 0 if cuadra else 1
        ws.cell(row=i, column=1, value=_fecha(asiento.get("fechaAlta"))).number_format = FMT_FECHA
        ws.cell(row=i, column=2, value=_fecha(asiento.get("fechaConciliacion"))).number_format = FMT_FECHA
        ws.cell(row=i, column=3, value=str(asiento.get("numero") or ""))
        ws.cell(row=i, column=4, value=len(movs))
        ws.cell(row=i, column=5, value=suma).number_format = FMT_NUM
        ws.cell(row=i, column=6, value="sí" if cuadra else "NO")
        ws.cell(row=i, column=7, value=str(asiento.get("referencia") or ""))
    ws.freeze_panes = "A2"
    return descuadrados


def main() -> None:
    ayer = date.today() - timedelta(days=1)
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--archivo", default=None, help="JSON ya guardado (no toca la API)")
    p.add_argument("--desde", default=None, help="dd/mm/yyyy (default: ayer)")
    p.add_argument("--hasta", default=None, help="dd/mm/yyyy (default: igual a --desde)")
    p.add_argument("--sin-nc", action="store_true", help="inclNC=false (default: true)")
    p.add_argument("--cuenta", default=None,
                   help="exportar SOLO las cuentas que contengan este texto")
    p.add_argument("-o", "--salida", default=None, help="ruta del .xlsx")
    args = p.parse_args()

    desde = args.desde or ayer.strftime("%d/%m/%Y")
    hasta = args.hasta or desde

    if args.archivo:
        print(f"Leyendo {args.archivo} (no se toca la API)…")
        with open(args.archivo, encoding="utf-8") as f:
            data = json.load(f)
    else:
        # Import acá y no arriba: con `--archivo` esto no toca la red, y así
        # tampoco arrastra `config` ni `requests` (se puede correr en cualquier lado).
        from core import aunesa

        print(f"Pidiendo {desde} → {hasta} a Aunesa (puede tardar ~90 s por día)…")
        resp = aunesa.get(ENDPOINT, {"fechaDesde": desde, "fechaHasta": hasta,
                                     "inclNC": "false" if args.sin_nc else "true"},
                          timeout=180)
        if resp.status_code != 200:
            print(f"❌ HTTP {resp.status_code}: {resp.text[:300]}")
            return
        data = resp.json()

    registros = data.get("registros") if isinstance(data, dict) else data
    if not isinstance(registros, list) or not registros:
        print("❌ la respuesta no trae `registros` (o vino vacía)")
        return
    registros = [r for r in registros if isinstance(r, dict)]

    filas = _filas(registros, args.cuenta)
    if not filas:
        print(f"❌ ningún movimiento matchea --cuenta {args.cuenta!r}")
        return

    from openpyxl import Workbook

    wb = Workbook()
    _hoja_movimientos(wb, filas)
    _hoja_por_cuenta(wb, filas)
    # La hoja Asientos va SIEMPRE sobre el total, aunque se filtre por cuenta: un
    # asiento cuadra con todos sus movimientos, no con los que elegimos mirar.
    descuadrados = _hoja_asientos(wb, registros)

    d = _fecha(desde)
    sufijo = d.strftime("%Y%m%d") if isinstance(d, date) else desde.replace("/", "")
    salida = args.salida or f"/tmp/registros_{sufijo}.xlsx"
    wb.save(salida)

    total_debe = sum(f[8] or 0.0 for f in filas)
    total_haber = sum(f[9] or 0.0 for f in filas)
    print(f"\n✔ {salida}")
    print(f"   {len(filas)} movimientos · {len(registros)} asientos")
    print(f"   Debe {total_debe:,.2f} · Haber {total_haber:,.2f} · "
          f"Saldo {total_debe - total_haber:,.2f}")
    if descuadrados:
        print(f"\n   ⚠️  {descuadrados}/{len(registros)} asientos NO suman cero.")
        print("   Debe/Haber se dedujo del SIGNO de `valuacion`; si la partida doble")
        print("   no cierra, esa deducción es incorrecta o faltan movimientos.")
        print("   REVISAR la hoja «Asientos» (filtrar ¿Cuadra? = NO) ANTES de mandarlo.")
    else:
        print(f"   ✔ los {len(registros)} asientos suman cero → la partida doble cierra")
        print("     y Debe/Haber quedó bien deducido del signo.")


if __name__ == "__main__":
    main()
