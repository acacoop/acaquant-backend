"""
Corrige valuaciones mal calculadas en Valuaciones.AuM.
Obligaciones Negociables, Fideicomisos y CPD se guardaron con P×Q en vez de P×Q/100.
Este script divide la valuacion por 100 para esos tipos.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mongo_manager import get_mongo_client

TIPOS_A_CORREGIR = [
    "Obligaciones Negociables",
    "Fideicomisos Financieros",
    "Cheques de Pago Diferido",
    "Letras del Tesoro Ajustables por CER en Pesos",
    "Títulos de Deuda",
]

client = get_mongo_client()
col = client["Valuaciones"]["AuM"]

r = col.update_many(
    {"tipoTitulo": {"$in": TIPOS_A_CORREGIR}},
    [{"$set": {"valuacion": {"$divide": ["$valuacion", 100]}}}]
)
print(f"Documentos modificados: {r.modified_count}")
client.close()
