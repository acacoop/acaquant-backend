"""
build_contrapartes.py — Escanea CashFlow.Operaciones y genera CashFlow.Contrapartes.

Por cada Denominación única que contenga alguna de las palabras clave,
inserta/actualiza un doc en Contrapartes con:
  - denominacion: valor real del campo Denominación
  - contraparte:  etiqueta normalizada (la palabra clave que matcheó)

También migra los registros existentes con contraparte="ST" → "ONE618".

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/Excel/build_contrapartes.py
"""

import sys
import os
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mongo_manager import get_mongo_client

# Palabras clave → etiqueta. Orden importa: las más específicas primero.
# Multi-palabra y nombres largos van arriba para no ser opacados por keywords cortas.
PALABRAS_CLAVE = [
    ("BULL MARKET",        "BULL MARKET"),
    ("BM",                 "BULL MARKET"),
    ("CONO SUR",           "CONO SUR"),
    ("CONOSUR",            "CONO SUR"),
    ("INDUSTRIAL VALORES", "INDUSTRIAL VALORES"),
    ("PP INVERSIONES",     "PP INVERSIONES"),
    ("DA VALORES",         "DA VALORES"),
    ("S & C",              "S & C"),
    ("ARGENFUNDS",         "ARGENFUNDS"),
    ("CONSULTATIO",        "CONSULTATIO"),
    ("CENTAURUS",          "CENTAURUS"),
    ("SCHRODER",           "SCHRODER"),
    ("TORONTO",            "TORONTO"),
    ("ALLARIA",            "ALLARIA"),
    ("BALANZ",             "BALANZ"),
    ("COMPASS",            "COMPASS"),
    ("COCOS",              "COCOS"),
    ("LOMBARD",            "LOMBARD"),
    ("ADCAP",              "ADCAP"),
    ("ONE618",             "ONE618"),
    ("DALMHORE",           "DALMHORE"),
    ("PHAROS",             "PHAROS"),
    ("PETRINI",            "PETRINI"),
    ("BAVSA",              "BAVSA"),
    ("BBVA",               "BBVA"),
    ("FIRST",              "FIRST"),
    ("SBS",                "SBS"),
    ("IAM",                "IAM"),
    ("GMC",                "GMC"),
    ("IEB",                "IEB"),
    ("MAF",                "MAF"),
    ("MAX",                "MAX"),
    # ST va al final y ahora etiqueta ONE618
    ("ST",                 "ONE618"),
]


def normalizar(s):
    """Quita acentos y pasa a mayúsculas para comparación robusta."""
    return unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode("utf-8").upper()


def detectar_contraparte(denominacion):
    """Devuelve la etiqueta de la primera palabra clave que matchea, o None."""
    norm = normalizar(denominacion)
    for palabra, etiqueta in PALABRAS_CLAVE:
        if normalizar(palabra) in norm:
            return etiqueta
    return None


if __name__ == "__main__":
    client = get_mongo_client()
    col_ops = client["CashFlow"]["Operaciones"]
    col_cp  = client["CashFlow"]["Contrapartes"]

    # Índice único por denominacion para upserts idempotentes
    col_cp.create_index("denominacion", unique=True, background=True)

    # ── Migración: ST → ONE618 en registros existentes ───────────────────────
    migrados = col_cp.update_many(
        {"contraparte": "ST"},
        {"$set": {"contraparte": "ONE618"}},
    ).modified_count
    if migrados:
        print(f"🔄 Migrados ST → ONE618: {migrados} registros existentes")

    # ── Escanear todas las denominaciones únicas ──────────────────────────────
    denominaciones = col_ops.distinct("Denominación")
    print(f"Denominaciones únicas en Operaciones: {len(denominaciones)}\n")

    encontradas = 0
    sin_match   = []

    for den in sorted(denominaciones):
        contraparte = detectar_contraparte(den)
        if contraparte:
            col_cp.update_one(
                {"denominacion": den},
                {"$set": {"denominacion": den, "contraparte": contraparte}},
                upsert=True,
            )
            print(f"  ✓  {contraparte:<22}  ←  {den}")
            encontradas += 1
        else:
            sin_match.append(den)

    print(f"\n✅ Contrapartes insertadas/actualizadas: {encontradas}")

    if sin_match:
        print(f"\n⚠️  Sin match ({len(sin_match)}) — revisá si falta alguna palabra clave:")
        for d in sin_match:
            print(f"     {d}")

    client.close()
