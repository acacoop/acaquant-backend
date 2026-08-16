"""La industria se muda del BONO al EMISOR.

Lo que se congela es la razón por la que se mudó: guardar el mismo dato N veces y
esperar que nadie lo escriba distinto no funciona. Medido en producción: 8 de 51
emisores tienen HOY sectores que se contradicen entre sus propios bonos — Pampa
Energía tiene tres valores repartidos en sus 4 bonos. Ninguna fila está "mal";
cada una suma bien por separado, y por eso agrupar da distinto según de dónde se
lea.
"""
from __future__ import annotations

from api.services.emisores import normalizar


def test_colapsa_los_espacios_que_crean_emisores_fantasma():
    """`'YPF '` y `'YPF'` serían DOS emisores con PK de texto, y cada uno sumaría
    bien por su lado. Es el precio de la clave de texto y esto es lo que lo paga."""
    assert normalizar(" YPF ") == "YPF"
    assert normalizar("Pan  American   Energy") == "Pan American Energy"
    assert normalizar("\tCresud\n") == "Cresud"


def test_NO_toca_mayusculas_ni_acentos():
    """El nombre guardado es el que estandarizó 1816 y se muestra tal cual. Bajar
    a minúsculas rompería la presentación para arreglar algo que ya resuelve el
    índice único de la base, que compara en `upper(btrim(...))`."""
    assert normalizar("Compañía Mega") == "Compañía Mega"
    assert normalizar("BBVA Argentina") == "BBVA Argentina"


def test_vacio_y_None_no_rompen():
    assert normalizar(None) == "" and normalizar("") == "" and normalizar("   ") == ""


def test_normalizar_es_idempotente():
    """Se aplica en la escritura y en la comparación: si no fuera idempotente, el
    segundo guardado crearía una fila nueva."""
    for s in (" YPF ", "Pan  American", "Compañía Mega", ""):
        assert normalizar(normalizar(s)) == normalizar(s)
