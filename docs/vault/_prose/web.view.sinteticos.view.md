Vista `/sinteticos` — sintéticos LECAP/DLK + futuro DLR (promovida desde Derivados a módulo top-level). Wrapper `force-dynamic` sin SSR fetch: la data se polleea client-side. Renderiza `DerivadosSinteticosView`.

Conecta con: componente `DerivadosSinteticosView` → backend `/api/derivados/sinteticos` (service `sinteticos`). Vive bajo el layout raíz.
