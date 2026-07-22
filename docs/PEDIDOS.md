# PEDIDOS — buzón de la mesa

> **AUTO-GENERADO** por `python -m scripts.gen_pedidos` desde
> `manager.pedidos`. NO editar a mano: se regenera entero. Para triar
> un pedido: `python -m scripts.gen_pedidos --marcar <id> aceptado`
> (estados: nuevo · aceptado · descartado · hecho; `--nota "…"` agrega
> el porqué).

Los carga la gente hablándole al copiloto mientras trabaja (tool
`registrar_pedido`, ver `api/services/copiloto/pedidos.py`): el que
tiene la idea la dice donde le surgió, sin abrir un ticket. Después
`jobs/pedidos_triage.py` los tría con IA (duplicados, impacto,
esfuerzo, propuesta técnica) y avisa por Telegram con botones para
aprobar; `jobs/pedidos_inbox.py` aplica esa decisión.

**1 pedidos** · nuevo: 0 · aceptado: 1 · descartado: 0 · hecho: 0

## 🛠 COLA DE TRABAJO (1)

Aprobados y sin hacer, **ordenados por lo que más rinde** (impacto alto /
esfuerzo chico primero). Al terminar uno:
`python -m scripts.gen_pedidos --marcar <id> hecho`.

### #1 · Agregar filtro de segmentación nivel 2
<sub>impacto **bajo** · esfuerzo **chico** · pedido desde `negocio` · aprobó telegram:5745588571</sub>

**Propuesta:** En la vista Manager - Clients, dentro de la sección 'Segmentación' de sección, agregar un filtro desplegable para el nivel 2, junto al existente de nivel 1.

> _Lo que pidieron:_ Agregar el nivel 2 como filtro en Manager - Clients, dentro de la sección Segmentación. Actualmente solo está disponible el filtro por nivel 1.

---

## ACEPTADO (1)

### Mejoras pedidas

- **#1 · Agregar filtro de segmentación nivel 2**  
  <sub>2026-07-22 13:13 · nicolas.mollo · desde `negocio` · impacto bajo / esfuerzo chico</sub>  
  Agregar el nivel 2 como filtro en Manager - Clients, dentro de la sección Segmentación. Actualmente solo está disponible el filtro por nivel 1.
  <sub>💡 En la vista Manager - Clients, dentro de la sección 'Segmentación' de sección, agregar un filtro desplegable para el nivel 2, junto al existente de nivel 1.</sub>
  <sub>contexto: quiero pedir agregar el nivel 2 como filtro en manager - clients. En la parte de segmentacion, ahora soplo hay nivel 1.</sub>
