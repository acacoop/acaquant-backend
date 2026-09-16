"""READ-ONLY. Cruce del FEE ADMIN: lo que dice el contable vs lo que tenemos cargado.

Herramienta: diag ? Solo SELECT, cero escrituras.

EL PUNTO
========

El export del sistema contable trae, por cada fondo, el `Fee% AAPIC` con el que
devenga la comisi?n. Nosotros tenemos ese mismo dato cargado a mano en
**Manager ? T?TULOS** (`portafolio.assets.fee_admin`). Son DOS COPIAS del mismo
hecho, y hasta hoy nadie las compar?.

Es el patr?n de la REGLA #9: si difieren **no falla nada**. Nuestra pantalla
muestra un honorario y el contable factura otro, cada uno es internamente
coherente, y la diferencia solo aparece cuando alguien la busca. Este diag la
busca, fondo por fondo.

DE D?NDE SALEN LOS N?MEROS
==========================

`FEE_CONTABLE` est? **hardcodeado a prop?sito**: es la foto del export de AGOSTO
2026 (`export - 2026-09-01`), 147 fondos, y se guarda tal cual vino para que el
cruce sea reproducible y auditable sin depender de un archivo en la m?quina de
nadie. Verificado al extraerlo: **ning?n fondo aparece con dos fees distintos**,
as? que un fondo = un fee, sin ambig?edad.

El segundo valor de cada par es en cu?ntas filas del export aparece ese fondo.
No entra al cruce: sirve para saber si una diferencia afecta a 500 posiciones o
a una sola.

C?MO LEER EL RESULTADO
======================

? COINCIDE            ? el fee es el mismo. Nada que hacer.
? DIFIERE             ? los dos tienen fee y NO es el mismo. **Es el hallazgo**:
                        alguien est? calculando con el n?mero equivocado.
? SIN FEE CARGADO     ? lo tenemos en el cat?logo pero `fee_admin` est? vac?o.
                        Nuestro lado no puede calcular nada para ese fondo.
? NO EST? EN ASSETS   ? el contable lo factura y nosotros ni lo tenemos. Toda
                        tenencia de ese fondo es invisible para nuestro c?lculo.
? SOLO NUESTRO        ? lo tenemos con fee y el contable no lo factur? en agosto.
                        Puede ser normal (nadie lo tuvo en el mes); se lista
                        aparte para no confundirlo con un error.

Uso:
    python -m scripts.diag_fee_admin
    python -m scripts.diag_fee_admin --solo-problemas
"""
from __future__ import annotations

import argparse

from core.cartera import FCI
from core.postgres import get_job_pool

# Foto del export contable de AGOSTO 2026. {unidad: (fee, filas_en_el_export)}
FEE_CONTABLE: dict[str, tuple[float, int]] = {
    '[1024] CAFCI643-1024 - SBS Pesos Plus - Clase A':
        (0.0125, 31),
    '[1114] CAFCI684-1114 - FCI Balanz Capital Ahorro - Clase A':
        (0.01275, 497),
    '[1135] CAFCI561-1135 - Toronto Trust Multimercado - Clase A':
        (0.0155925, 31),
    '[1138] CAFCI562-1138 - FCI Toronto Trust Renta Fija - Clase A':
        (0.01404, 62),
    '[1141] CAFCI701-1141 - Toronto Trust Renta Fija Plus - Clase A':
        (0.0150925, 31),
    '[1142] CAFCI563-1142 - Toronto Trust Renta Fija Plus - Clase B':
        (0.0120925, 31),
    '[1154] CAFCI62-1154 - Toronto Trust - Clase B':
        (0.01179, 31),
    '[1159] CAFCI711-1159 - Argenfunds Abierto Pymes - Clase B':
        (0.018991, 6),
    '[1170] CAFCI720-1170 - Toronto Trust Abierto Pymes - Clase A':
        (0.0058425, 58),
    '[1171] CAFCI720-1171 - Toronto Trust Abierto Pymes - Clase B':
        (0.017685, 27),
    '[1194] CAFCI688-1194 - FCI Adcap Pesos Plus - Clase A':
        (0.0139, 31),
    '[1201] CAFCI737-1201 - FCI MegaQM Pesos - Clase B':
        (0.007, 1),
    '[1256] CAFCI687-1256 - FCI Adcap Acciones - Clase B':
        (0.0126, 31),
    '[1289] CAFCI634-1289 - FCI IAM Renta Crecimiento - Clase B':
        (0.0104, 26),
    '[1292] CAFCI636-1292 - FCI IAM Ahorro Pesos - Clase A':
        (0.0087, 179),
    '[1302] CAFCI787-1302 - IAM FCI Abierto Pymes - Clase B':
        (0.0105, 31),
    '[1386] CAFCI809-1386 - IAM Renta Capital - Clase B':
        (0.0146, 31),
    '[14017] CAFCI643-1025 - SBS PESOS PLUS FCI Clase B':
        (0.0075, 31),
    '[14176] CAFCI584-837 - Allaria Ahorro - Clase A':
        (0.0125, 107),
    '[14177] CAFCI584-838 - Allaria Ahorro - Clase B':
        (0.01125, 19),
    '[14178] CAFCI584-1903 - Allaria Ahorro - Clase C':
        (0.0075, 44),
    '[14269] CAFCI613-914 - GAINVEST ABIERTO PYMES CLASE B':
        (0.0125, 62),
    '[142] CAFCI153-142 - Schroder Renta Variable - Clase B':
        (0.01, 31),
    '[14370] CAFCI153-1726 - Schroder Renta Variable - Clase A':
        (0.015, 62),
    '[14652] CAFCI636-1293 - FCI IAM Ahorro Pesos - Clase B':
        (0.0087, 164),
    '[14936] CAFCI707-1132 - FCI Toronto Trust Ahorro - Clase A':
        (0.01004, 5600),
    '[14937] CAFCI707-1133 - Toronto Trust Ahorro - Clase B':
        (0.00704, 4505),
    '[14962] CAFCI717-1231 - Schroder Renta Plus - Clase B':
        (0.0113, 88),
    '[15114] CAFCI1015-2212 - FCI Abierto Pymes Zofingen Factoring PYME FUND - Clase B':
        (None, 186),
    '[15186] CAFCI876-2096 - FCI Balanz Ahorro en Dolares - Clase A':
        (0.006, 216),
    '[15228] CAFCI1028-2363 - Schroder Income - Clase A':
        (0.0113, 309),
    '[15278] CAFCI1052-2408 - Bull Market Acciones Argentinas - Clase A':
        (0.015, 31),
    '[1531] CAFCI869-1531 - FCI Adcap Ahorro Dolares - Clase D':
        (0.0009, 248),
    '[1532] CAFCI712-1532 - FCI Adcap Ahorro Dolares - Clase E':
        (0.0006, 2),
    '[15373] CAFCI1039-2390 - FCI IAM Performance Americas - Clase B':
        (0.0005, 62),
    '[15519] CAFCI883-2565 - FCI Adcap Ahorro Pesos Fondo de Dinero - Clase A':
        (0.0144, 307),
    '[15520] CAFCI1106-2566 - ADCAP ahorro pesos fondo de Dinero - Clase B':
        (0.0096, 212),
    '[15568] CAFCI1105-2561 - FCI Argenfunds Liquidez Clase B':
        (0.0087, 19),
    '[15607] CAFCI831-1436 - Schroder Retorno Total - Clase A':
        (0.0138, 372),
    '[15608] CAFCI682-1437 - Schroder Retorno Total - Clase B':
        (0.0025, 93),
    '[15868] CAFCI1107-3260 - Schroder Renta Performance - Clase A':
        (0.0125, 23),
    '[15873] CAFCI1195-3265 - Schroder Renta Performance - Clase B':
        (0.0093, 62),
    '[1589] CAFCI914-1589 - IAM Renta Dólares - Clase A':
        (0.0125, 86),
    '[1590] CAFCI914-1590 - FCI IAM Renta Dolares-Clase B':
        (0.004, 93),
    '[15946] CAFCI1222-3386 - FCI ST Zero - Clase B':
        (0.011, 31),
    '[1611] CAFCI931-1611 - Allaria Dólar Latam - Clase A':
        (0.0095, 31),
    '[1624] CAFCI742-1624 - Allaria Diversificado - Clase C':
        (0.015, 31),
    '[1625] CAFCI1625-916 - FCI First Renta Dolares - Clase A':
        (0.0043, 31),
    '[1626] CAFCI916-1626 - First Renta Dolares - Clase B':
        (0.0023, 31),
    '[1629] CAFCI915-1629 - FCI First Renta Pesos - Clase A':
        (0.01, 110),
    '[1676] CAFCI717-1676 - Schroder Renta Plus - Clase A':
        (0.015, 169),
    '[1695] CAFCI895-1695 - FCI Megaqm Liquidez Dolar - Clase A':
        (0.0008, 62),
    '[1744] CAFCI775-1744 - FCI Toronto Trust Crecimiento Clase A':
        (0.0075, 82),
    '[1748] CAFCI776-1748 - FCI Toronto Trust Retorno Total - Clase B':
        (0.01129, 62),
    '[2020] CAFCI518-2020 - Argenfunds Ahorro Pesos - Clase A':
        (0.015, 124),
    '[2035] CAFCI820-2035 - Argenfunds Renta Balanceada - Clase A':
        (0.015, 31),
    '[2103] CAFCI807-2103 - FCI Compass Best Ideas - Clase A':
        (0.0148, 31),
    '[216] CAFCI271-1208 - Consultatio Acciones Argentina - Clase B':
        (0.01, 31),
    '[2364] CAFCI1028-2364 - Schroder Income - Clase B':
        (0.006, 93),
    '[23747] CAFCI1239-3443 - BM Smart Money Market - Clase B':
        (0.0075, 3),
    '[2420] CAFCI1043-2420 - Balanz Long Pesos - Clase A':
        (0.0095, 31),
    '[24342] CAFCI121-75 - FCI LOMBARD RENTA EN PESOS Clase B':
        (0.0068, 176),
    '[24399] CAFCI1197-3561 - Consultatio Multimercado III - Clase A':
        (0.008, 205),
    '[2482] CAFCI1077-2482 - Galileo Multimercado II - Clase A':
        (0.0125, 31),
    '[25487] CAFCI1350-3853 - Max Money Market - Clase B':
        (0.007, 145),
    '[25741] CAFCI1248-3830 - Schroder Liquidez - Clase A':
        (0.0145, 18),
    '[25742] CAFCI1248-3831 - SCHRODER LIQUIDEZ F.C.I. Clase B':
        (0.0068, 77),
    '[26855] CAFCI1479-4377 - BAVSA Ahorro - Clase B':
        (0.008, 55),
    '[27279] CAFCI1571-4788 - BAVSA Ahorro Dólar - Clase B':
        (0.001, 38),
    '[27552] CAFCI1578-5054 - Toronto Trust Money Market Dólar - Clase A':
        (0.00125, 1560),
    '[27553] CAFCI1578-5055 - Toronto Trust Money Market Dólar - Clase B':
        (0.00075, 392),
    '[27555] CAFCI1578-5058 - Toronto Trust Money Market Dólar - Clase Ley N° 27.743':
        (0.00125, 31),
    '[27565] CAFCI1530-5118 - Schroder Crecimiento Tres - Clase A':
        (0.0125, 90),
    '[27566] CAFCI1530-5119 - Schroder Crecimiento Tres - Clase B':
        (0.0085, 155),
    '[27689] CAFCI1597-5225 - IAM Dinámico FCI Abierto Pymes - Clase B':
        (0.01, 124),
    '[27892] CAFCI394-3445 - 1810 Ahorros':
        (0.5, 4),
    '[27898] CAFCI1380-4087 - Schroder Retorno Total Cuatro - Clase A':
        (0.004, 902),
    '[27899] CAFCI1380-4088 - Schroder Retorno Total Cuatro - Clase B':
        (0.0025, 93),
    '[28016] CAFCI1671-5567 - ConoSur Liquidez - Clase B':
        (0.011, 67),
    '[28030] CAFCI1674-5591 - ConoSur Renta Fija Corporativa - Clase A':
        (0.0073, 236),
    '[28031] CAFCI1674-5592 - ConoSur Renta Fija Corporativa - Clase B':
        (0.0136, 31),
    '[28034] CAFCI1672-5596 - ConoSur Ahorro - Clase B':
        (0.0236, 12),
    '[28199] CAFCI1442-5771 - Lombard Ahorro Plus II - Clase A':
        (0.0032, 62),
    '[28275] CAFCI577-840 - SBS AHORRO PESOS Clase B':
        (0.01, 68),
    '[28294] CAFCI750-1260 - SBS Latam - Clase B':
        (0.0075, 31),
    '[28439] CAFCI1781-6039 - BAVSA Deuda Privada Argentina USD - Clase B':
        (0.015, 29),
    '[28642] CAFCI1341-4109 - FCI ACA Valores Retorno Total - Clase A':
        (0.03, 476),
    '[28643] CAFCI1341-4110 - FCI ACA Valores Retorno Total - Clase B':
        (0.00325, 159),
    '[28644] CAFCI1341-4111 - FCI ACA Valores Retorno Total - Clase C':
        (0.01, 558),
    '[28902] CAFCI1910-6461 - DXA Multicobertura - Clase B':
        (0.0126, 62),
    '[3296] CAFCI577-3296 - SBS Ahorro Pesos - Clase D':
        (0.0063, 62),
    '[3319] CAFCI1205-3319 - FCI IAM Renta Balanceada - Clase B':
        (0.0131, 31),
    '[3352] CAFCI1212-3352 - FCI Compass Liquidez - Clase A':
        (0.0113, 62),
    '[3378] CAFCI1220-3378 - FCI IEB Ahorro - Clase B':
        (0.01, 7),
    '[3410] CAFCI1155-3410 - Balanz Infraestructura - Clase A':
        (0.0115, 31),
    '[341] CAFCI421-341 - FCI Compass Crecimiento - Clase A':
        (0.0195, 93),
    '[342] CAFCI421-342 - Compass Crecimiento - Clase B':
        (0.012, 31),
    '[3467] CAFCI1184-3467 - SMR Fondo Comun de Inversion - Clase A':
        (0.011, 31),
    '[3468] CAFCI1250-3468 - FCI SMR Fondo Comun de Inversion - Clase B':
        (0.011, 50),
    '[3521] CAFCI1205-3521 - FCI Allaria Patrimonio III - Clase B':
        (0.00125, 31),
    '[3562] CAFCI1197-3562 - FCI Consultatio Multimercado III - Clase B':
        (0.0075, 31),
    '[3580] CAFCI1199-3580 - Consultatio Multimercado V - Clase A':
        (0.007, 31),
    '[3671] CAFCI1120-3671 - Balanz Lecaps - Clase A':
        (0.01225, 112),
    '[3852] CAFCI1350-3852 - Max Money Market - Clase A':
        (0.01, 35),
    '[3854] CAFCI1351-3854 - Max Ahorro Pesos - Clase A':
        (0.0115, 92),
    '[3855] CAFCI1351-3855 - Max Ahorro Pesos - Clase B':
        (0.009, 51),
    '[3858] CAFCI1289-3858 - Max Cobertura - Clase A':
        (0.0123, 31),
    '[3932] CAFCI1258-3932 - FCI Balanz Equity Selection - Clase A':
        (0.0138, 31),
    '[4021] CAFCI1338-4021 - FCI Adcap Ahorro Dinamico - Clase A':
        (0.0144, 13),
    '[4022] CAFCI1391-4022 - Adcap Ahorro Dinámico Fondo de Dinero - Clase B':
        (0.0096, 36),
    '[4094] CAFCI1039-4094 - IAM Performance Americas - Clase F':
        (0.0056, 62),
    '[4101] CAFCI1212-4101 - FCI Balanz Capital Estrategia I USD - Clase A':
        (0.0025, 1134),
    '[4129] CAFCI1297-4129 - Allaria Dinámico III - Clase A':
        (0.0125, 74),
    '[4131] CAFCI1297-4131 - Allaria Dinámico III - Clase C':
        (0.0101, 31),
    '[4135] CAFCI1389-4135 - Toronto Trust Balanceado - Clase B':
        (0.01104, 31),
    '[4136] CAFCI1389-4136 - Toronto Trust Balanceado - Clase A':
        (0.01254, 31),
    '[4206] CAFCI1435-4206 - Allaria Dólar Dinámico - Clase A':
        (0.00625, 93),
    '[4208] CAFCI1411-4208 - Allaria Dólar Dinámico - Clase C':
        (0.0045, 16),
    '[4279] CAFCI1430-4279 - FCI Allaria Agro - Clase A':
        (0.0125, 62),
    '[4280] CAFCI1430-4280 - Allaria Agro - Clase B':
        (0.01125, 19),
    '[4307] CAFCI1457-4307 - Adcap Balanceado XVI - Clase B':
        (0.0101, 180),
    '[4318] CAFCI1394-4318 - Max Dinamico II - Clase A':
        (0.0104, 257),
    '[4319] CAFCI1350-4319 - Max Dinamico II - Clase B':
        (0.0084, 334),
    '[4393] CAFCI1463-4393 - Toronto Trust Renta Dólar - Clase A':
        (0.00275, 186),
    '[4395] CAFCI1463-4395 - Toronto Trust Renta Dólar - Clase C':
        (0.007, 13),
    '[4525] CAFCI1523-4525 - Allaria Dolar Ahorro Plus  - Clase A':
        (0.00625, 62),
    '[4526] CAFCI1523-4526 - Allaria Dolar Ahorro Plus  - Clase B':
        (0.01, 8),
    '[4538] CAFCI1527-4538 - IEB Estratégico - Clase A':
        (0.0035, 62),
    '[4557] CAFCI1482-4557 - IAM Liquidez en Dólares - Clase A':
        (0.0015, 1758),
    '[4558] CAFCI1482-4558 - IAM Liquidez en Dólares - Clase B':
        (0.0015, 487),
    '[4619] CAFCI1548-4619 - BAVSA Renta Dólares - Clase B':
        (0.0075, 31),
    '[4629] CAFCI1549-4629 - Balanz Money Market USD - Clase A':
        (0.00125, 11),
    '[4650] CAFCI1522-4650 - Allaria Dólar Ahorro - Clase A':
        (0.0025, 31),
    '[4885] CAFCI1028-4885 - Schroder Income - Clase F Ley N° 27.743':
        (0.0113, 31),
    '[4886] CAFCI831-4886 - Schroder Retorno Total - Clase D Ley N° 27.743':
        (0.0138, 31),
    '[5192] CAFCI1566-5192 - SBS Liquidez USD - Clase A':
        (0.001, 93),
    '[5272] CAFCI1603-5272 - Allaria Equity Selection - Clase A':
        (0.0191, 31),
    '[52] CAFCI62-52 - Toronto Trust - Clase A':
        (0.01479, 29),
    '[5310] CAFCI1645-5310 - Max Money Market Dólares - Clase B':
        (0.0013, 39),
    '[5772] CAFCI1736-5772 - IEB Estratégico II - Clase A':
        (0.0025, 31),
    '[6232] CAFCI1462-6232 - Toronto Trust Ahorro Dólar - Clase A':
        (0.0055, 17),
    '[6233] CAFCI1462-6233 - Toronto Trust Ahorro Dólar - Clase B':
        (0.0045, 8),
    '[6460] CAFCI1910-6460 - DXA Multicobertura - Clase A':
        (0.0252, 29),
    '[728] CAFCI515-728 - MEGAQM Ahorro - Clase B':
        (0.0109, 31),
    '[739] CAFCI551-739 - Gainvest Renta Fija Dolares - Clase A':
        (0.01, 62),
    '[804] CAFCI568-804 - IAM Renta Variable - Clase B':
        (0.0129, 31),
    '[835] CAFCI582-835 - Allaria Acciones - Clase A':
        (0.0191, 31),
}

# Tolerancia del cruce. Los dos lados son `numeric`/float de la misma fracci?n
# (0.006 = 0,6 %), as? que no hay conversi?n de por medio: lo ?nico que se
# perdona es ruido de punto flotante, NO una diferencia real de tarifa.
TOL = 1e-9


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _pct(f) -> str:
    return "--" if f is None else f"{float(f) * 100:.6f}".rstrip("0").rstrip(".") + " %"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--solo-problemas", action="store_true",
                    help="esconde los que coinciden")
    a = ap.parse_args()

    nuestros = {r["unidad"]: r for r in _q(
        "SELECT unidad, fee_admin, emisor, cartera, vigente FROM portafolio.assets")}

    coincide, difiere, sin_fee, falta = [], [], [], []
    for unidad, (fee_c, n) in sorted(FEE_CONTABLE.items()):
        mio = nuestros.get(unidad)
        if mio is None:
            falta.append((unidad, fee_c, n))
        elif mio["fee_admin"] is None:
            sin_fee.append((unidad, fee_c, n, mio))
        elif abs(float(mio["fee_admin"]) - float(fee_c)) <= TOL:
            coincide.append((unidad, fee_c, n))
        else:
            difiere.append((unidad, fee_c, float(mio["fee_admin"]), n))

    # Nuestros FCI con fee que el contable no factur? en el mes.
    fci = {x.upper() for x in FCI}
    solo_nuestros = sorted(
        u for u, r in nuestros.items()
        if u not in FEE_CONTABLE and r["fee_admin"] is not None
        and (r["cartera"] or "").strip().upper() in fci)

    print("=" * 96)
    print("CRUCE FEE ADMIN ? export contable AGOSTO 2026  vs  portafolio.assets")
    print("=" * 96)
    print(f"  fondos en el export        : {len(FEE_CONTABLE)}")
    print(f"  ? COINCIDE                : {len(coincide)}")
    print(f"  ? DIFIERE                 : {len(difiere)}")
    print(f"  ??  SIN FEE CARGADO         : {len(sin_fee)}")
    print(f"  ??  NO EST? EN ASSETS       : {len(falta)}")
    print(f"  ??  SOLO NUESTRO (con fee)  : {len(solo_nuestros)}")

    if difiere:
        print("\n" + "=" * 96)
        print("? DIFIERE ? los dos tienen fee y no es el mismo. AC? EST? EL PROBLEMA.")
        print("=" * 96)
        print(f"  {'FONDO':<58}{'CONTABLE':>13}{'NUESTRO':>13}{'FILAS':>7}")
        for u, fc, fn, n in sorted(difiere, key=lambda x: -x[3]):
            print(f"  {u[:57]:<58}{_pct(fc):>13}{_pct(fn):>13}{n:>7}")

    if sin_fee:
        print("\n" + "=" * 96)
        print("??  SIN FEE CARGADO ? est?n en el cat?logo pero con `fee_admin` vac?o")
        print("=" * 96)
        print(f"  {'FONDO':<58}{'CONTABLE':>13}{'FILAS':>7}  EMISOR")
        for u, fc, n, mio in sorted(sin_fee, key=lambda x: -x[2]):
            print(f"  {u[:57]:<58}{_pct(fc):>13}{n:>7}  {(mio['emisor'] or '')[:22]}")

    if falta:
        print("\n" + "=" * 96)
        print("??  NO EST? EN ASSETS ? el contable los factura y no los tenemos")
        print("=" * 96)
        print(f"  {'FONDO':<58}{'CONTABLE':>13}{'FILAS':>7}")
        for u, fc, n in sorted(falta, key=lambda x: -x[2]):
            print(f"  {u[:57]:<58}{_pct(fc):>13}{n:>7}")

    if solo_nuestros:
        print("\n" + "=" * 96)
        print("??  SOLO NUESTRO ? con fee cargado, sin filas en el export de agosto")
        print("   (puede ser normal: nadie lo tuvo en el mes)")
        print("=" * 96)
        for u in solo_nuestros:
            r = nuestros[u]
            vig = "" if r["vigente"] in (None, True) else "  [NO VIGENTE]"
            print(f"  {u[:57]:<58}{_pct(r['fee_admin']):>13}{vig}")

    if not a.solo_problemas and coincide:
        print("\n" + "=" * 96)
        print(f"? COINCIDE ({len(coincide)})")
        print("=" * 96)
        print(f"  {'FONDO':<58}{'FEE':>13}{'FILAS':>7}")
        for u, fc, n in sorted(coincide, key=lambda x: -x[2]):
            print(f"  {u[:57]:<58}{_pct(fc):>13}{n:>7}")

    print("\n" + "=" * 96)
    print("READ-ONLY: este script no escribi? nada.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
