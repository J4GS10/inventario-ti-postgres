"""
Clase de un tipo de activo: decide cómo se registra y opera cada equipo.

Una laptop y una impresora no se manejan igual: la laptop se asigna a una
persona, lleva software y componentes internos (RAM, disco, batería); la
impresora se instala en un área, no lleva software y consume tóner. Cada tipo
de activo (INV_TIPO_ACTIVO.TAC_Clase) pertenece a una clase, y las reglas de
la clase se aplican en el servidor:

- uso PERSONAL     se asigna o presta a una persona (también puede instalarse
                   en un área como equipo de uso común).
- uso COMPARTIDO   se instala en un área, con un responsable opcional; no se
                   asigna a una persona ni cuenta como su custodia.
- uso COMPONENTE   va instalado dentro de otro equipo (o en bodega como
                   repuesto); también puede entregarse como accesorio.
- uso INSUMO       no es un activo: se convierte a consumible (herramienta de
                   reclasificación con aprobación) y no se crean activos nuevos.

`software`     admite instalaciones y licencias por equipo.
`componentes`  admite componentes internos (los permitidos por tipo en
               INV_TIPO_COMPONENTE; sin lista, cualquier tipo de clase COMPONENTE).
`red`          tiene identidad de red (hostname, IP, MAC).
`consumibles`  usa consumibles compatibles (tóner, tinta) que se le registran.
"""
from __future__ import annotations

import unicodedata

PERSONAL, COMPARTIDO, COMPONENTE, INSUMO = "PERSONAL", "COMPARTIDO", "COMPONENTE", "INSUMO"

CLASES: dict[str, dict] = {
    "COMPUTO": {"uso": PERSONAL, "software": True, "componentes": True, "red": True, "consumibles": False},
    "MOVIL": {"uso": PERSONAL, "software": True, "componentes": False, "red": True, "consumibles": False},
    "PERIFERICO": {"uso": PERSONAL, "software": False, "componentes": False, "red": False, "consumibles": False},
    "IMPRESION": {"uso": COMPARTIDO, "software": False, "componentes": True, "red": True, "consumibles": True},
    "RED": {"uso": COMPARTIDO, "software": False, "componentes": True, "red": True, "consumibles": False},
    "ENERGIA": {"uso": COMPARTIDO, "software": False, "componentes": True, "red": False, "consumibles": False},
    "AUDIOVISUAL": {"uso": COMPARTIDO, "software": False, "componentes": False, "red": True, "consumibles": True},
    "COMPONENTE": {"uso": COMPONENTE, "software": False, "componentes": False, "red": False, "consumibles": False},
    "HERRAMIENTA": {"uso": PERSONAL, "software": False, "componentes": False, "red": False, "consumibles": False},
    "INSUMO": {"uso": INSUMO, "software": False, "componentes": False, "red": False, "consumibles": False},
    "OTRO": {"uso": PERSONAL, "software": False, "componentes": False, "red": False, "consumibles": False},
}
CODIGOS = tuple(CLASES)
DEFECTO = "OTRO"


def reglas(clase: str | None) -> dict:
    return {"clase": clase or DEFECTO, **CLASES.get(clase or DEFECTO, CLASES[DEFECTO])}


def _normalizar(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in base if not unicodedata.combining(c)).strip().lower()


# Clasificación inicial de los tipos existentes por su nombre (migración y
# tipos nuevos sin clase). El administrador la puede cambiar en «Tipos».
_POR_NOMBRE: dict[str, tuple[str, ...]] = {
    "COMPUTO": ("laptop", "desktop", "servidor", "all in one", "all-in-one", "workstation", "computadora", "pc"),
    "MOVIL": ("tablet", "telefono", "celular", "smartphone", "movil"),
    "PERIFERICO": ("monitor", "mouse", "teclado", "docking", "headset", "camara", "bocina", "adaptador",
                   "cargador de docking", "webcam", "audifonos", "diadema"),
    "IMPRESION": ("impresora", "multifuncional", "escaner", "plotter"),
    "RED": ("switch", "router", "access point", "firewall", "modem"),
    "ENERGIA": ("ups", "regulador", "no break"),
    "AUDIOVISUAL": ("proyector", "televisor", "pantalla", "smart home"),
    "COMPONENTE": ("disco", "memoria ram", "ram", "bateria", "cargador de laptop", "fuente de poder",
                   "tarjeta de video"),
    "INSUMO": ("tinta", "toner", "cable", "aire comprimido", "limpia contactos", "espuma limpiadora",
               "pasta termica", "limpia pantalla", "desengrasante", "kit de limpieza", "brochas"),
    "HERRAMIENTA": ("herramientas", "aspiradora", "herramienta"),
}


def clase_por_nombre(nombre: str) -> str:
    buscado = _normalizar(nombre)
    for clase, nombres in _POR_NOMBRE.items():
        if buscado in nombres:
            return clase
    return DEFECTO


# Componentes internos que admite cada tipo (por nombre), sembrados en la migración.
COMPONENTES_POR_TIPO: dict[str, tuple[str, ...]] = {
    "laptop": ("memoria ram", "disco", "bateria", "cargador de laptop"),
    "desktop": ("memoria ram", "disco"),
    "ups": ("bateria",),
}
