"""
Columnas de gestión del catálogo estándar (orden, activo y fusión) para los
catálogos que ya existían antes de app/models/catalogo_estandar.py.

Módulo sin dependencias de los modelos: lo importan catalogs.py y
traceability.py mientras app.db.base todavía registra las tablas.
"""
from sqlalchemy import true
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer


def gestion(tabla: str, prefijo: str, pk: str) -> dict:
    """Se usa dentro del cuerpo de la clase: ``locals().update(gestion(...))``."""
    return {
        f"{prefijo}_Orden": Column(Integer, nullable=False, default=0, server_default="0"),
        f"{prefijo}_Activo": Column(Boolean, nullable=False, default=True, server_default=true()),
        f"{prefijo}_Fusionado_En": Column(Integer, ForeignKey(f"{tabla}.{pk}", ondelete="SET NULL"), nullable=True),
        f"{prefijo}_Fusionado_Fecha": Column(DateTime, nullable=True),
    }
