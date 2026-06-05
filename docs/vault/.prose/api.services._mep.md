Helper que devuelve el MEP histórico para una fecha dada: el último valor con `timestamp <= fin-del-día(fecha)`, o `None` si no hay docs anteriores. Sirve para pesificar/dolarizar: en `Valuaciones.AuM` la valuación está siempre en ARS, así que para mostrar en USD basta dividir por este MEP.

Conecta con: lee `Valuaciones.Dolar` (poblada por el script de PC oficina); lo usan los services de valuaciones/PnL como fallback cuando un boleto no trae su `mep` snapshot.
