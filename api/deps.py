"""Dependencias compartidas de la API."""
from core.mongo import get_mongo_client_read


def get_db_cuentas():
    return get_mongo_client_read()["CuentasAPI"]


def get_db_operaciones():
    return get_mongo_client_read()["OperacionesAPI"]


def get_db_portfolio():
    return get_mongo_client_read()["PortfolioAPI"]


def get_db_titulos():
    return get_mongo_client_read()["TitulosAPI"]


def get_db_trading():
    return get_mongo_client_read()["Trading"]
