"""El deploy tiene que dejar corriendo el código nuevo — TODO el código nuevo.

⚠️⚠️ **EL DAEMON DEL AGENTE NO LO REINICIABA NADIE** (medido 2026-09-07, doc
`AGENT.md` §0.ea). `agente.service` corre `jobs.agente`, o sea TODOS los
detectores, y no entraba por ningún lado: `deploy.sh` reiniciaba sólo
`api.service`, y `restart_all.sh` itera `deploy/systemd/motor_*.service` — un
glob que `agente.service` no matchea.

Resultado: después de CUALQUIER deploy la API servía el código nuevo y **el
agente seguía detectando con el viejo, indefinidamente**. Y el síntoma es cruel
porque cada mitad es coherente consigo misma: los botones nuevos aparecen (los
sirve la API) y las filas siguen saliendo con el texto viejo (las escribe el
daemon). No se lee como «falta un restart»: se lee como «el cambio no funcionó».
Es la REGLA #9 aplicada al deploy.

Este test hace que la clasificación sea OBLIGATORIA: cada unit de
`deploy/systemd/` que no sea un motor tiene que estar en uno de los dos lados, y
sumar una unit nueva sin decidir de qué lado va rompe acá.
"""
from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
UNITS = RAIZ / "deploy" / "systemd"
DEPLOY_SH = RAIZ / "deploy" / "deploy.sh"

# Las que el deploy NO reinicia, con el motivo. No es una lista de excepciones:
# es la mitad declarada de una decisión que antes no existía.
#
# `motor_*` no está acá porque su regla ya es estructural (el glob de
# `restart_all.sh` + el aviso de `deploy.sh`): reiniciar un motor EN RUEDA corta
# el feed de precios de la mesa, y eso lo decide la mesa.
NO_SE_REINICIAN = {
    "control_saldos.service":
        "feed vivo como un motor (saldo liquidado del día): reiniciarlo en "
        "rueda corta la serie, y esa es una decisión de la mesa",
    "tenencia_live.service":
        "feed vivo como un motor (posición T0/T1 durante la rueda): mismo "
        "criterio que los motores",
}


def _units() -> list[str]:
    return sorted(p.name for p in UNITS.glob("*.service"))


def test_toda_unit_que_no_es_motor_esta_clasificada():
    """O la reinicia el deploy, o está declarada como que no — con su motivo.

    Sin esto, una unit nueva queda corriendo código viejo para siempre y **nada
    falla**: el proceso está vivo, el deploy dice OK y el commit está en `main`.
    """
    sh = DEPLOY_SH.read_text(encoding="utf-8")
    sueltas = []
    for u in _units():
        if u.startswith("motor_"):
            continue
        if u in NO_SE_REINICIAN:
            continue
        if u not in sh:
            sueltas.append(u)
    assert not sueltas, (
        f"units que el deploy no nombra y nadie declaró: {sueltas}. "
        "O las reinicia `deploy/deploy.sh`, o van a NO_SE_REINICIAN con el "
        "motivo en una línea. Una unit sin clasificar corre código viejo "
        "para siempre sin que nada falle.")


def test_el_agente_se_reinicia_en_el_deploy():
    """El caso que originó el test, congelado aparte: es el que más duele
    porque el agente ESCRIBE lo que después se lee como verdad."""
    sh = DEPLOY_SH.read_text(encoding="utf-8")
    assert "agente.service" in sh, (
        "`deploy.sh` dejó de reiniciar el daemon del agente: sus detectores "
        "van a seguir corriendo el código viejo después de cada deploy")
    assert "reiniciar_agente" in sh
    # Y las DOS ramas del script (con y sin `--con-motores`) tienen que llamarla:
    # que sólo una lo hiciera dejaría el bug vivo para la mitad de los deploys.
    assert sh.count("    reiniciar_agente") >= 2, (
        "las dos ramas de `deploy.sh` (con y sin --con-motores) tienen que "
        "reiniciar el agente")


def test_el_motivo_de_no_reiniciar_esta_escrito():
    """Escribir el motivo ES el filtro: si no se puede escribir, no hay motivo."""
    for u, motivo in NO_SE_REINICIAN.items():
        assert (UNITS / u).exists(), f"{u} está declarada y ya no existe"
        assert len(motivo) > 40, f"el motivo de {u} no dice nada"


def test_el_barrido_mira_algo():
    """Los de arriba pasan en verde con la carpeta vacía o el path mal."""
    us = _units()
    assert len(us) > 10, f"solo {len(us)} units: ¿cambió deploy/systemd/?"
    assert "api.service" in us and "agente.service" in us
    assert DEPLOY_SH.exists()
