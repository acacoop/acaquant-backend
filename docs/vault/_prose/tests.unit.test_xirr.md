Valida `quant/xirr.py` contra TIR.NO.PER de Excel (caso canónico de Microsoft) y la convención del proyecto para valuación de portfolio (valor inicio +, cierre −, flujos con signo natural). Cubre invariantes (orden no importa, swap global de signos no cambia la tasa), edge cases (menos de dos flujos, todos mismo signo → None) y regresiones de TEA extrema argentina (+12676% y −99% en un mes) que antes no convergían.

Conecta con: importa `quant.xirr`; red de seguridad del XIRR que calcula la performance mensual por cuenta en `api/services/valuaciones.py`.
