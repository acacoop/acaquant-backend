"""Dependencias compartidas de la API."""
from core.mongo import get_mongo_client_read


def get_db_cuentas():
    return get_mongo_client_read()["CuentasAPI"]


def get_db_operaciones():
    return get_mongo_client_read()["OperacionesAPI"]
