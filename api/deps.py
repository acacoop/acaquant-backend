"""Dependencias compartidas de la API."""
from fastapi import Header, HTTPException

from config import API_KEY
from core.mongo import get_mongo_client_read


def verify_api_key(authorization: str = Header(...)) -> None:
    """Valida el header Authorization: Bearer <API_KEY>.

    Si API_KEY no está configurada en .env, deja pasar todo (modo dev).
    """
    if not API_KEY:
        return
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="API key inválida")


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
