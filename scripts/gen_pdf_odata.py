"""scripts/gen_pdf_odata.py — genera el PDF prolijo del documento OData para el
proveedor (docs/PARTNER_API_ODATA_PROVEEDOR.pdf), con el logo de ACA Valores en el
margen superior de cada hoja. Fuente del contenido: este mismo archivo.

Uso:
    python -m scripts.gen_pdf_odata
"""
from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT = "docs/PARTNER_API_ODATA_PROVEEDOR.pdf"
LOGO = "images/logo-header.png"

NAVY = colors.HexColor("#2B2D7C")
GRAY = colors.HexColor("#6E6E6E")
LIGHT = colors.HexColor("#F2F3F7")
BORDER = colors.HexColor("#D7D9E3")
INK = colors.HexColor("#22242E")

PW, PH = A4
LOGO_W = 40 * mm
LOGO_H = LOGO_W * 80 / 401  # ratio real del logo (401x80)

# ── estilos ──────────────────────────────────────────────────────────────────
title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=18, textColor=NAVY, leading=21, spaceAfter=2)
subtitle = ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5, textColor=GRAY, leading=12, spaceAfter=10)
h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11.5, textColor=NAVY, leading=14, spaceBefore=12, spaceAfter=5)
body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, textColor=INK, leading=13, alignment=TA_LEFT)
cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=9, textColor=INK, leading=12)
cellb = ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=9, textColor=INK, leading=12)
mono = ParagraphStyle("mono", fontName="Courier", fontSize=8.3, textColor=NAVY, leading=11)
cellhdr = ParagraphStyle("cellhdr", fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.white, leading=11)


def P(t, s=cell):
    return Paragraph(t, s)


def kv_table(rows, widths):
    """Tabla simple param/valor sin header."""
    data = [[P(a, cellb), P(b, cell)] for a, b in rows]
    t = Table(data, colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def hdr_table(header, rows, widths):
    """Tabla con header navy."""
    data = [[P(h, cellhdr) for h in header]]
    data += [[P(c, cell) for c in r] for r in rows]
    t = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def bullets(items):
    return ListFlowable(
        [ListItem(P(t, body), leftIndent=10, value="•") for t in items],
        bulletType="bullet", bulletColor=NAVY, leftIndent=12, bulletFontSize=8,
    )


def _page(canvas, doc):
    canvas.saveState()
    y = PH - 17 * mm
    canvas.drawImage(LOGO, doc.leftMargin, y, width=LOGO_W, height=LOGO_H,
                     mask="auto", preserveAspectRatio=True)
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.6)
    canvas.line(doc.leftMargin, y - 3 * mm, PW - doc.rightMargin, y - 3 * mm)
    # footer
    canvas.setStrokeColor(BORDER)
    canvas.line(doc.leftMargin, 15 * mm, PW - doc.rightMargin, 15 * mm)
    canvas.setFont("Helvetica", 7.3)
    canvas.setFillColor(GRAY)
    canvas.drawString(doc.leftMargin, 11.5 * mm,
                      "ACA Valores — Mercado de Capitales · Documento técnico para proveedor · Confidencial")
    canvas.drawRightString(PW - doc.rightMargin, 11.5 * mm, f"Página {doc.page}")
    canvas.restoreState()


def build():
    doc = SimpleDocTemplate(
        OUT, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=28 * mm, bottomMargin=20 * mm,
        title="Acceso OData — Portfolio Acaquant", author="ACA Valores",
    )
    full = PW - 40 * mm
    e = []
    e.append(Paragraph("Acceso OData — Portfolio Acaquant", title))
    e.append(Paragraph("Documento funcional para la integración del proveedor (ej. SAP Datasphere).", subtitle))

    e.append(Paragraph("1. Conexión", h2))
    e.append(kv_table([
        ("URL del servicio", "<font face='Courier'>https://data.acaquant.com/odata/</font>"),
        ("Protocolo", "<b>OData V2</b>"),
        ("Tipo de conexión (Datasphere)", "<b>Generic OData</b>"),
        ("Autenticación", "<b>HTTP Basic</b> (usuario y contraseña provistos por ACA Valores)"),
        ("Transporte", "HTTPS únicamente"),
    ], [58 * mm, full - 58 * mm]))

    e.append(Paragraph("2. Prueba rápida (navegador)", h2))
    e.append(Paragraph("1. <b>Esquema</b> (sin credenciales) — abrir "
                       "<font face='Courier'>https://data.acaquant.com/odata/$metadata</font>. "
                       "Debe devolver un XML con la entidad <font face='Courier'>Portfolio</font>.", body))
    e.append(Spacer(1, 3))
    e.append(Paragraph("2. <b>Datos</b> (con credenciales) — abrir "
                       "<font face='Courier'>https://data.acaquant.com/odata/Portfolio</font>. "
                       "El navegador pide usuario y contraseña y devuelve los datos en JSON.", body))
    e.append(Spacer(1, 3))
    e.append(Paragraph("Si ambos responden, la conexión está operativa.", body))

    e.append(Paragraph("3. Entidad <font face='Courier'>Portfolio</font> (una fila por posición)", h2))
    e.append(hdr_table(["Campo", "Tipo", "Descripción"], [
        ["ID", "texto", "Clave única (fecha | id_cuenta | unidad)"],
        ["fecha", "texto", "Fecha del dato (YYYY-MM-DD)"],
        ["id_cuenta", "texto", "Identificador de la cuenta"],
        ["cuenta", "texto", "Nombre de la cuenta"],
        ["unidad", "texto", "Instrumento"],
        ["cantidad", "número", "Nominales"],
        ["precio", "número", "Precio"],
        ["valuacion", "número", "Valuación"],
    ], [34 * mm, 22 * mm, full - 56 * mm]))

    e.append(Paragraph("4. Consultas (opcionales)", h2))
    e.append(hdr_table(["Operación", "Ejemplo"], [
        ["Filtrar por fecha", "<font face='Courier' size=8>/odata/Portfolio?$filter=fecha eq '2026-05-16'</font>"],
        ["Filtrar por cuenta", "<font face='Courier' size=8>/odata/Portfolio?$filter=id_cuenta eq '101'</font>"],
        ["Combinar (and)", "<font face='Courier' size=8>?$filter=fecha eq '2026-05-16' and id_cuenta eq '101'</font>"],
        ["Paginar", "<font face='Courier' size=8>$top</font> , <font face='Courier' size=8>$skip</font>"],
        ["Seleccionar columnas", "<font face='Courier' size=8>$select=fecha,id_cuenta,valuacion</font>"],
        ["Conteo de filas", "<font face='Courier' size=8>/odata/Portfolio/$count</font>"],
    ], [42 * mm, full - 42 * mm]))

    e.append(Paragraph("5. Actualización de los datos", h2))
    e.append(bullets([
        "Se actualizan <b>una vez por día hábil</b> (lunes a viernes), <b>durante la noche</b>.",
        "El dato de cada jornada queda disponible <b>a partir de las 02:00 UTC</b> (≈ 23:00 hora Argentina).",
        "Se mantiene <b>histórico por fecha</b> (las fechas anteriores quedan disponibles).",
    ]))

    e.append(Paragraph("6. Notas técnicas", h2))
    e.append(bullets([
        "Servicio de <b>solo lectura</b> (únicamente GET) — no requiere tokens de escritura (CSRF).",
        "Respuesta en <b>JSON</b>. El esquema (<font face='Courier'>$metadata</font>) es de acceso abierto; "
        "los datos (<font face='Courier'>/Portfolio</font>) requieren autenticación.",
        "Los datos son los mismos que entrega la API REST existente; OData es un acceso <b>adicional</b>.",
    ]))

    e.append(Paragraph("7. Soporte", h2))
    e.append(Paragraph(
        "Ante cualquier inconveniente de conexión, indicar la <b>versión de OData</b> que requiere la "
        "herramienta (V2 / V4) y el <b>método de autenticación</b> que utiliza, junto con el mensaje de "
        "error exacto.", body))

    doc.build(e, onFirstPage=_page, onLaterPages=_page)
    print(f"OK -> {OUT}")


if __name__ == "__main__":
    build()
