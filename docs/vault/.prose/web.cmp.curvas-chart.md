Gráfico principal de curvas de renta fija: dibuja los bonos (scatter de TEA/TEM/paridad/duration por ticker) más la línea de la curva ajustada y las tasas forward asociadas, con histórico por ticker. Embebe la vista de Fair Value relativo intra-curva.

Conecta con: consume los endpoints de renta fija / forwards / fair value (services `renta_fija.py`, `derivados.py`, `fair_value.py`), que leen `Trading.MarketSnapshot`, `Trading.Curvas` y los snapshots de forwards. Monta `fair-value-view`.
