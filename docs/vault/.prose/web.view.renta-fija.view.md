Vista `/renta-fija` — terminal de renta fija. SSR inicial en paralelo de 9 datasets (renta fija, forwards, flujos, breakevens, históricos de breakevens/forwards, forwards-zscore, fair value tasa_fija y CER) desde `/api/cotizaciones/*` y `/api/titulos/flujos`. Carga rápida con el último snapshot; luego `RentaFijaLiveView` mantiene los datasets live con polling client-side.

Conecta con: backend `/api/cotizaciones/{renta-fija,forwards,breakevens,fair-value,...}`, `/api/titulos/flujos`; componente `RentaFijaLiveView`.
