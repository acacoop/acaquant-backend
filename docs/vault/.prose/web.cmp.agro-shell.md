Contenedor con tabs del módulo Agro: "Mercado" (futuros + cadena de opciones + pizarra, vía `DerivadosAgroView`), "Mejoras Precio Dispo" y "Datos" (Cámara de Cereales). Recibe el snapshot inicial del mercado por SSR y arma la navegación entre sub-vistas. La pizarra abre a los 3 roles (`canEdit` siempre true).

Conecta con: monta `agro-view`/`derivados-agro-view`, `agro-mejoras-dispo` y `agro-datos`; recibe `agroInitial` (snapshot del backend) como prop.
