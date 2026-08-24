"""core/postrade_catalogo.py — QUÉ métodos existen en Postrade, declarados UNA vez.

Existe por lo mismo que `core/duplicados.py` o `av_agent_acciones.DESTINOS`: si
cada caller arma su propio path, el día que uno se equivoca en una letra el
error aparece como "no habilitado" y se reclama al proveedor algo que es
nuestro. Acá el path se escribe una sola vez y se usa desde todos lados.

Y sobre todo: acá vive la marca de **LECTURA vs. ESCRITURA**. Esta API no es
como Interbanking —que era 100% GET y no podía mover plata ni por error—:
Postrade tiene métodos que **registran órdenes de suscripción y rescate de FCI,
cancelan órdenes, dan de alta cuentas y cambian la contraseña del usuario**. La
diferencia entre leer y escribir tiene que ser un dato del programa, no algo que
uno recuerde al escribir el caller.

Fuente: manual "API Postrade" del proveedor, versión 1.64 (27/4/2026).

⚠️ Que un método esté ACÁ no significa que nuestro usuario lo tenga habilitado:
esto es lo que la API ofrece, no lo que nos dieron. Qué contesta de verdad con
nuestras credenciales lo mide `scripts/diag_postrade_metodos.py` (REGLA #2).
"""
from __future__ import annotations

from dataclasses import dataclass, field

LECTURA = "lectura"
ESCRITURA = "escritura"


@dataclass(frozen=True)
class Metodo:
    """Un método de la API. `path` va sin barra inicial."""

    nombre: str
    path: str
    verbo: str          # LECTURA (GET) | ESCRITURA (POST)
    que_trae: str
    obligatorios: tuple[str, ...] = ()
    # Params con los que el relevamiento puede probarlo sin efectos. Si está
    # vacío y hay obligatorios, el diag no lo prueba (no inventa valores).
    prueba: dict = field(default_factory=dict)


def _l(nombre: str, path: str, que_trae: str, obligatorios: tuple[str, ...] = (), **prueba) -> Metodo:
    return Metodo(nombre, path, LECTURA, que_trae, obligatorios, prueba)


def _e(nombre: str, path: str, que_trae: str) -> Metodo:
    return Metodo(nombre, path, ESCRITURA, que_trae)


# --------------------------------------------------------------------------- #
# LECTURA — todo GET. Nada de esto modifica nada del lado del proveedor.
# --------------------------------------------------------------------------- #
LECTURAS: tuple[Metodo, ...] = (
    # --- Referenciales: no dependen de fecha ni de que tengamos posiciones ---
    _l("ClosingProcesses", "PosTrade/ClosingProcesses",
       "cuándo terminaron los procesos de la cámara", ("EntryDate",)),
    _l("CurrencyList", "PreTrade/CurrencyList", "monedas que maneja la cámara"),
    _l("PartyDetails", "PreTrade/PartyDetails", "el ALyC y sus cuentas de compensación"),
    _l("SecurityList", "PreTrade/SecurityList", "instrumentos negociables"),
    _l("DerivativeSecurityList", "PreTrade/DerivativeSecurityList",
       "instrumentos derivados y FCI, con su rango horario"),
    _l("AccountDetails", "PreTrade/AccountDetails", "detalle de las cuentas"),
    _l("AccountList", "PreTrade/AccountList", "cuentas de neteo"),
    _l("DepositaryAccountList", "PosTrade/DepositaryAccountList", "cuentas depositarias"),
    _l("CollateralList", "PosTrade/CollateralList", "activos aceptados en garantía"),
    _l("Fee", "PosTrade/Fee", "tarifas"),

    # --- Nuestra posición y nuestra plata ---
    _l("AccountBalance", "PosTrade/AccountBalance", "balance de saldos"),
    _l("PositionReport", "PosTrade/PositionReport",
       "reporte de posición", ("clearingBusinessDate",)),
    _l("MT940", "PosTrade/MT940", "mayor contable"),
    _l("Invoices", "PosTrade/Invoices", "comprobantes"),
    _l("AccruedFees", "PosTrade/AccruedFees", "tarifas devengadas"),
    _l("CorporateActionCredits", "PosTrade/CorporateActionCredits", "acreencias"),

    # --- Operaciones ---
    _l("TradeCaptureReport", "PosTrade/TradeCaptureReport", "operaciones"),
    _l("TradeCaptureReportFCI", "PosTrade/TradeCaptureReportFCI",
       "operaciones del mercado de FCI"),
    _l("SettlementStatusReport", "PosTrade/SettlementStatusReport", "operaciones OTC"),
    _l("ExecutionReport", "PosTrade/ExecutionReport", "libro de órdenes"),
    _l("PositionMaintenance", "PosTrade/PositionMaintenance", "operaciones canceladas"),
    _l("MarketData", "PosTrade/MarketData",
       "cotizaciones y precios de ajuste", ("mdEntryType",)),

    # --- Garantías y márgenes ---
    _l("MT506", "PosTrade/MT506", "garantías"),
    _l("MT536", "PosTrade/MT536", "movimientos de garantías"),
    _l("MarginRequirementReport", "PosTrade/MarginRequirementReport", "márgenes requeridos"),
    _l("DeliveryMarginRequirementReport", "PosTrade/DeliveryMarginRequirementReport",
       "márgenes por entrega de mercadería"),
    _l("MarginBalance", "Risk/MarginBalance", "saldos por finalidad"),
    _l("SecurityDefinition", "PosTrade/SecurityDefinition", "simulación de escenarios"),
    _l("NewCollateralReport", "PosTrade/NewCollateralReport", "instrucciones de garantía"),
    _l("BalanceTransfer", "PosTrade/BalanceTransfer", "transferencias de saldos"),
    _l("CollateralAssignment", "PosTrade/CollateralAssignment", "distribución de activos"),

    # --- Entregas ---
    _l("DeliveryQuotaRegistration", "PosTrade/DeliveryQuotaRegistration", "cupos para entrega"),
    _l("AllocationInstruction", "PosTrade/AllocationInstruction", "ofertas de entrega"),
    _l("AllocationReport", "PosTrade/AllocationReport", "carátulas agrupadas"),

    # --- Custodia / billeteras ---
    _l("CustodyRegistration", "PreTrade/CustodyRegistration", "activos en custodia"),
    _l("DigitalInstrument", "PreTrade/DigitalInstrument", "instrumentos digitales"),
    _l("DigitalWalletStock", "PosTrade/DigitalWalletStock", "stock de billetera digital"),
    _l("DigitalWalletDetail", "PosTrade/DigitalWalletDetail",
       "movimientos de billetera contribuidos no procesados"),

    # --- Órdenes FCI (consulta) ---
    _l("ActiveOrders", "Trade/ActiveOrders", "órdenes activas de FCI"),
    _l("FilledOrders", "Trade/FilledOrders", "órdenes ejecutadas de FCI"),
    _l("OrderById", "Trade/OrderById", "una orden de FCI por id", ("OrderId",)),
)

# --------------------------------------------------------------------------- #
# ESCRITURA — ⚠️ ESTOS MÉTODOS TIENEN EFECTO REAL DEL OTRO LADO.
#
# No se llaman nunca por accidente: `core/postrade.post()` los rechaza salvo que
# POSTRADE_ESCRITURA esté prendido en el .env Y el caller lo pida explícito. El
# relevamiento NO los toca.
# --------------------------------------------------------------------------- #
ESCRITURAS: tuple[Metodo, ...] = (
    _e("NewOrderSingle", "Trade/NewOrderSingle",
       "⚠️ SUSCRIBE O RESCATA un FCI — mueve plata de verdad"),
    _e("NewOrderList", "Trade/NewOrderList", "⚠️ suscripciones/rescates MASIVOS de FCI"),
    _e("ReplaceOrder", "Trade/ReplaceOrder", "⚠️ modifica una suscripción o rescate"),
    _e("CancelOrder", "Trade/CancelOrder", "⚠️ cancela una orden"),
    _e("TradeMatchReport", "Trade/TradeMatchReport", "⚠️ registra una operación OTC"),
    _e("AccountRegistration", "PreTrade/AccountRegistration", "⚠️ da de alta cuentas de neteo"),
    _e("AccountUpdate", "PreTrade/AccountUpdate", "⚠️ modifica cuentas de registro"),
    _e("AccountStatus", "PreTrade/AccountStatus", "⚠️ INACTIVA una cuenta"),
    _e("RegistrationInstructions", "PreTrade/RegistrationInstructions",
       "⚠️ registra warrants y certificados de depósito"),
    _e("CustodyGlobalStock", "PosTrade/CustodyGlobalStock", "⚠️ almacena info de stock"),
    _e("ChangePassword", "Security/ChangePassword",
       "⚠️ CAMBIA LA CONTRASEÑA del usuario de la API — nos deja afuera"),
)

TODOS: tuple[Metodo, ...] = LECTURAS + ESCRITURAS
POR_NOMBRE: dict[str, Metodo] = {m.nombre: m for m in TODOS}


def metodo(nombre: str) -> Metodo:
    """El método por nombre. Falla fuerte si no existe — un typo no puede
    convertirse en un path inventado que la API rechace como 'no habilitado'."""
    try:
        return POR_NOMBRE[nombre]
    except KeyError:
        raise KeyError(
            f"Postrade: no existe el método {nombre!r}. "
            f"Los declarados están en core/postrade_catalogo.py"
        ) from None


def es_escritura(nombre: str) -> bool:
    return metodo(nombre).verbo == ESCRITURA
