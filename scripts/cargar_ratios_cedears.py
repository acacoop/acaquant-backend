"""cargar_ratios_cedears.py — carga MASIVA de ratios de CEDEARs (one-shot).

Fuente: listado oficial de programas CEDEAR de BYMA (Excel 08-07-2026, columnas
"Identificación Mercado" y "Ratio Cedear/Acción — ADR"), parseado y embebido acá
como dict para no depender del archivo en el Droplet. Ratio = CEDEARs por acción
("4:1" → 4; "1:3" → 0.3333). Alimenta el CCL implícito del tab REUTERS.

ORCL/DECK/ASTS venían "3.1"/"25.1"/"15.1" en el archivo (punto en vez de ":");
se interpretaron como N:1 — verificarlos en Manager tras la carga.

    python -m scripts.cargar_ratios_cedears            # dry-run: muestra qué haría
    python -m scripts.cargar_ratios_cedears --apply    # escribe mercado.cedears.ratio

Matchea por ticker_corto y si no por underlying. Solo actualiza cuando el valor
CAMBIA; lo cargado a mano idéntico no se toca. Se borra al cerrar (REGLA #5).
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool

RATIOS: dict[str, float] = {
    'AABA': 3, 'AAL': 2, 'AAP': 14, 'AAPL': 20, 'ABBV': 10, 'ABEV': 0.3333,
    'ABT': 4, 'ACN': 75, 'ACWI': 26, 'ADBE': 44, 'ADI': 15, 'ADP': 6,
    'ADS': 22, 'AEG': 1, 'AEM': 6, 'AGRO': 1, 'AI': 5, 'AIG': 5,
    'AKO.B': 1, 'ALAB': 44, 'AMAT': 5, 'AMD': 10, 'AMGN': 30, 'AMX': 1,
    'AMZN': 144, 'ANET': 29, 'ANF': 1, 'ARCO': 0.5, 'ARM': 27, 'ASML': 146,
    'ASR': 20, 'ASTS': 15, 'ATAD': 4, 'AVGO': 39, 'AVY': 18, 'AXIA': 0.25,
    'AXP': 15, 'AZN': 2, 'B': 2, 'BA': 24, 'BABA': 9, 'BAC': 4,
    'BAK': 2, 'BAS GR': 2, 'BAYN GR': 3, 'BB': 3, 'BBD': 1, 'BBVA': 1,
    'BCS': 1, 'BG': 5, 'BHP': 2, 'BIDU': 11, 'BIIB': 13, 'BIOX': 1,
    'BKNG': 700, 'BKR': 7, 'BMNR': 8, 'BMY': 3, 'BNY': 2, 'BP': 5,
    'BRK/B': 22, 'BSBR': 1, 'BSN GR': 20, 'BX': 30, 'C': 3, 'CAAP': 0.25,
    'CAH': 3, 'CAR': 26, 'CAT': 20, 'CBD': 1, 'CCJ': 23, 'CCL': 3,
    'CDE': 1, 'CEG': 45, 'CL': 3, 'CLS': 20, 'COIN': 27, 'COP': 25,
    'COPX': 14, 'COST': 48, 'CRM': 18, 'CRWD': 79, 'CRWV': 27, 'CSCO': 5,
    'CVS': 15, 'CVX': 16, 'CX': 1, 'DAL': 8, 'DD': 5, 'DE': 40,
    'DECK': 25, 'DEO': 6, 'DHR': 54, 'DIS': 12, 'DJNJ3-XD004': 1, 'DOCU': 22,
    'DOW': 6, 'DTEA GR': 3, 'E': 4, 'EA': 14, 'EBAY': 2, 'ECL': 56,
    'EFA': 18, 'EFX': 16, 'ELPC': 0.3333, 'EOAN GR': 6, 'EQNR': 6, 'ERIC': 2,
    'ERJ': 1, 'ESGU': 30, 'ETSY': 16, 'EWJ': 14, 'FCX': 3, 'FDX': 10,
    'FISV': 11, 'FMCC': 1, 'FMX': 6, 'FNMA': 1, 'FSLR': 18, 'FXI': 5,
    'GDX': 10, 'GE': 8, 'GFI': 1, 'GGB': 0.25, 'GILD': 4, 'GLNG': 10,
    'GLOB': 18, 'GLW': 4, 'GM': 6, 'GOOGL': 58, 'GPRK': 1, 'GRMN': 3,
    'GS': 13, 'GSK': 4, 'GT': 2, 'HAL': 2, 'HD': 32, 'HDB': 2,
    'HHPD LI': 2, 'HIMS': 4, 'HL': 1, 'HMC': 1, 'HMY': 1, 'HOG': 3,
    'HON': 8, 'HOOD': 29, 'HPQ': 1, 'HSBC': 2, 'HSY': 21, 'HWM': 1,
    'IBB': 27, 'IBIT': 10, 'IBM': 15, 'IBN': 1, 'IEMG': 12, 'IEUR': 11,
    'IFF': 12, 'IJH': 12, 'ILF': 6, 'INFY': 1, 'ING': 3, 'INTC': 5,
    'IP': 4, 'IREN': 12, 'ISRG': 90, 'ITUB': 1, 'IVE': 40, 'IVV': 692,
    'IVW': 20, 'IWDA': 24, 'JCI': 2, 'JD': 4, 'JNJ': 15, 'JOYY': 5,
    'JPM': 15, 'KB': 2, 'KEP': 1, 'KGC': 1, 'KMB': 6, 'KO': 5,
    'KOF': 2, 'LAC': 1, 'LAR': 1, 'LKOD': 4, 'LLY': 56, 'LMT': 20,
    'LND': 1, 'LRCX': 56, 'LVS': 2, 'LYG': 2, 'MA': 33, 'MBG GR': 4,
    'MBT': 2, 'MCD': 24, 'MDLZ': 15, 'MDT': 4, 'MELI': 120, 'META': 24,
    'MFG': 1, 'MMM': 10, 'MO': 4, 'MP': 10, 'MRK': 5, 'MRNA': 19,
    'MRSH': 16, 'MRVL': 14, 'MSFT': 30, 'MSI': 20, 'MUFG': 1, 'MUX': 2,
    'NBIS': 27, 'NEC1 GR': 0.3333, 'NEE': 19, 'NEM': 3, 'NFLX': 48, 'NG': 0.25,
    'NGG': 2, 'NIO': 4, 'NKE': 12, 'NMR': 1, 'NOK': 1, 'NOW': 172,
    'NSANY': 1, 'NTES': 14, 'NU': 2, 'NUE': 16, 'NVDA': 24, 'NVO': 7,
    'NVS': 4, 'NXE': 1, 'O': 13, 'OGZD': 2, 'OKLO': 28, 'ONDS': 2,
    'ORANY': 1, 'ORCL': 3, 'ORLY': 222, 'PAAS': 3, 'PAC': 16, 'PAGS': 3,
    'PATH': 2, 'PBI': 1, 'PBR': 1, 'PCAR': 3, 'PDD': 25, 'PEP': 18,
    'PFE': 4, 'PG': 15, 'PHG': 5, 'PINS': 7, 'PKX': 3, 'PLTR': 3,
    'PM': 18, 'PSO': 1, 'PSQ': 8, 'PSX': 6, 'PYPL': 8, 'QCOM': 11,
    'RACE': 83, 'RGTI': 2, 'RIO': 8, 'RIOT': 3, 'RKLB': 12, 'ROKU': 13,
    'ROST': 4, 'RTX': 5, 'SAN': 0.25, 'SAP': 6, 'SBS': 0.5, 'SBUX': 12,
    'SCCO': 2, 'SCHW': 13, 'SDA': 2, 'SE': 32, 'SHEL': 2, 'SHOP': 107,
    'SHPW': 0.5, 'SID': 0.125, 'SIEGY': 3, 'SLB': 3, 'SLV': 6, 'SMSN LI': 14,
    'SNA': 6, 'SNAP': 1, 'SNDK': 170, 'SNOW': 30, 'SONY': 8, 'SPCE': 0.5,
    'SPCX': 50, 'SPGI': 45, 'SPHQ': 14, 'SPOT': 28, 'STLA': 5, 'STNE': 3,
    'SUZ': 1, 'SWKS': 21, 'SYY': 8, 'T': 3, 'TCOM': 2, 'TEAM': 47,
    'TELFY': 8, 'TEM': 12, 'TGT': 24, 'TIIAY': 1, 'TIMB': 1, 'TJX': 22,
    'TM': 15, 'TMO': 22, 'TMUS': 33, 'TRIP': 2, 'TRV': 6, 'TS': 1,
    'TSLA': 15, 'TSM': 9, 'TTE': 3, 'TV': 3, 'TWLO': 36, 'TX': 4,
    'TXN': 5, 'UGP': 1, 'UL': 3, 'UNH': 33, 'UNP': 20, 'URBN': 2,
    'USB': 5, 'USO': 15, 'V': 18, 'VALE': 2, 'VEA': 10, 'VIG': 39,
    'VIST': 3, 'VIV': 1, 'VOD': 1, 'VRSN': 6, 'VRTX': 101, 'VST': 26,
    'VZ': 4, 'WB': 6, 'WFC': 5, 'WMT': 18, 'XLB': 18, 'XLC': 19,
    'XLI': 28, 'XLK': 46, 'XLP': 16, 'XLRE': 9, 'XLV': 29, 'XLY': 43,
    'XOM': 10, 'XP': 4, 'XPEV': 4, 'XRX': 1, 'XYZ': 20, 'YELP': 2,
    'YZCAY': 2, 'ZM': 47,
}

REVISAR = ("ORCL", "DECK", "ASTS")   # ratio inferido de un formato dudoso del archivo


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribe (default: dry-run)")
    a = ap.parse_args()

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, ticker_corto, underlying, ratio FROM mercado.cedears "
                    "ORDER BY ticker_corto")
        filas = cur.fetchall()

        cambios: list[tuple[str, str, float | None, float]] = []
        sin_match: list[str] = []
        for ticker, corto, under, actual in filas:
            clave = (corto or "").upper()
            nuevo = RATIOS.get(clave)
            if nuevo is None and under:
                nuevo = RATIOS.get(under.upper())
            if nuevo is None:
                sin_match.append(corto or ticker)
                continue
            act = float(actual) if actual is not None else None
            if act != float(nuevo):
                cambios.append((ticker, corto, act, float(nuevo)))

        print(f"{len(filas)} CEDEARs en el master · {len(cambios)} para actualizar · "
              f"{len(sin_match)} sin ratio en el listado BYMA")
        for _, corto, act, nuevo in cambios:
            marca = "  ⚠ REVISAR" if corto in REVISAR else ""
            print(f"  {corto:<8} {act if act is not None else '—':>10} → {nuevo}{marca}")
        if sin_match:
            print(f"\nsin match ({len(sin_match)}): {', '.join(sin_match)}")

        if not a.apply:
            print("\nDRY-RUN — no se escribió nada. Para aplicar: --apply")
            return
        for ticker, _, _, nuevo in cambios:
            cur.execute("UPDATE mercado.cedears SET ratio = %s WHERE ticker = %s",
                        (nuevo, ticker))
        print(f"\n✓ {len(cambios)} ratios escritos.")


if __name__ == "__main__":
    main()
