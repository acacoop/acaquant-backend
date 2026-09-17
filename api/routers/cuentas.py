"""Router Cuentas: accionistas y contrapartes (ambos SQL — fuente única).
Lógica en `api/services/cuentas_sql.py`; acá solo el cache HTTP."""
from fastapi import APIRouter

from api.cache import cached
from api.services import cuentas_sql as svc

router = APIRouter(prefix="/api/cuentas", tags=["Cuentas"])


@router.get("/accionistas")
@cached(ttl=3600)
def listar_accionistas():
    """Accionistas desde `clientes.accionistas`: `{cuenta, id_cuenta, nombre, grupo}`."""
    return svc.listar_accionistas()


@router.get("/contrapartes")
@cached(ttl=3600)
def listar_contrapartes():
    """Contrapartes desde `clientes.contrapartes`, mismo shape que accionistas."""
    return svc.listar_contrapartes()
