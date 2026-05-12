"""Helpers de acceso a las bases Mongo (sin dependencia de FastAPI).

Extraído de `api/deps.py` para que la capa de servicios (`api/services/*`)
no importe fastapi. `api/deps.py` sigue reexportándolos para compat.
"""
from core.mongo import get_mongo_client_read


def get_db_cuentas():
    return get_mongo_client_read()["CuentasAPI"]


def get_db_operaciones():
    return get_mongo_client_read()["OperacionesAPI"]


def get_db_portfolio():
    return get_mongo_client_read()["PortfolioAPI"]


def get_db_titulos():
    return get_mongo_client_read()["TitulosAPI"]


def get_db_opciones():
    return get_mongo_client_read()["Opciones"]


def get_db_trading():
    return get_mongo_client_read()["Trading"]


def get_db_valuaciones():
    return get_mongo_client_read()["Valuaciones"]


def get_db_cashflow():
    return get_mongo_client_read()["CashFlow"]


def get_db_smart_read():
    """DB del módulo Renta Variable (smart money): 13F + Form 4 + catalog."""
    return get_mongo_client_read()["Smart"]
