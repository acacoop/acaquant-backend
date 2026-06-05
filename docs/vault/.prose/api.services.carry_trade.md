Calcula la serie de carry trade en USD de cada bono ARS de una curva (tasa_fija o cer): el retorno acumulado en pesos descontando la variación del MEP (o CCL) en el mismo período, con fórmula exacta `(1+ret_ars)/(1+var_dolar)−1`. Sirve para ver si el carry en pesos le gana o no a la devaluación implícita. Output día por día, listo para line chart.

Conecta con: lee precios diarios de `Trading` (cierre + live-fallback) y la serie de MEP de `Valuaciones.Dolar`; lo consume el endpoint de carry trade.
