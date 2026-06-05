Sub-router admin del Manager para auditar valuaciones a mano. Expone `GET /valuaciones/debug` (desglose mes por mes del cálculo de XIRR/TEA y la base-100 cumulada por cuenta, con el cashflow exacto pegable en Excel TIR.NO.PER) y `GET /aum` (docs crudos de una cuenta+fecha con `valuacion_esperada` y `desvio` para cazar precios mal traídos). No cachea: siempre muestra datos vigentes.

Conecta con: invoca `api.services.valuaciones` (`valuacion_mensual_debug`, `aum_raw`); lee `Valuaciones.AuM` y `CashFlow.NegocioMovimientos`; protegido por `require_module('manager')` del router padre.
