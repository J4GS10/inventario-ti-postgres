"""
Kardex unificado: registro de SOLO INSERCIÓN de todo evento de inventario.

Responde, para cualquier activo, componente, consumible o licencia: qué pasó,
cuándo, quién lo hizo, desde dónde hacia dónde, por qué y con qué documento.
Cada servicio escribe su evento EN LA MISMA TRANSACCIÓN que la operación, así
el kardex nunca queda desfasado. Los registros previos a su existencia se
reconstruyen con la migración (KDX_Migrado = true).

Personas, áreas y equipos se guardan como identificadores sin FK (un borrado
no debe alterar la historia) y con una referencia de texto legible
(KDX_Referencia) tomada en el momento del evento.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DateTime, Index, Integer, Numeric, String, UniqueConstraint, Uuid,
)

from app.db.base import Base
from app.db.types import PortableJSON


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Tipos de evento por objeto.
EVENTOS_ACTIVO = (
    "ALTA",                     # entrada manual al inventario
    "RECEPCION",                # entrada por una orden de compra
    "ASIGNACION", "PRESTAMO", "DEVOLUCION", "TRANSFERENCIA",
    "TRASLADO_SEDE",
    "MANTENIMIENTO_ENTRADA", "MANTENIMIENTO_SALIDA",
    "COMPONENTE_INSTALADO", "COMPONENTE_RETIRADO",
    "ESPECIFICACION",
    "CAMBIO_ESTADO",            # edición de almacén o corrección de datos
    "BAJA",
    "INVENTARIO_FISICO",        # hallazgo de una toma de inventario
    "INSTALACION_AREA",         # equipo compartido instalado o reubicado en un área
    "RETIRO_AREA",              # equipo compartido retirado de su área (a bodega)
    "RECLASIFICACION",          # un insumo registrado como activo pasa a consumible
)
EVENTOS_CONSUMIBLE = ("CONSUMIBLE_ENTRADA", "CONSUMIBLE_SALIDA", "CONSUMIBLE_AJUSTE", "CONSUMIBLE_TRASLADO")
EVENTOS_LICENCIA = (
    "LICENCIA_ALTA", "LICENCIA_ASIGNADA", "LICENCIA_RETIRADA", "LICENCIA_AJUSTE", "LICENCIA_BAJA",
    "LICENCIA_CREDENCIAL_ALTA",     # nueva cuenta, serial o key
    "LICENCIA_SECRETO_CAMBIADO",    # rotación de la clave de una cuenta
    "LICENCIA_SECRETO_VISTO",       # alguien reveló el secreto (también en la bitácora)
)
TIPOS_EVENTO = EVENTOS_ACTIVO + EVENTOS_CONSUMIBLE + EVENTOS_LICENCIA

# Clasificación para la bitácora de entradas y salidas.
EVENTOS_ENTRADA = ("ALTA", "RECEPCION", "CONSUMIBLE_ENTRADA", "LICENCIA_ALTA")
EVENTOS_SALIDA = ("BAJA", "RECLASIFICACION", "CONSUMIBLE_SALIDA", "LICENCIA_BAJA")
EVENTOS_REASIGNACION = (
    "ASIGNACION", "PRESTAMO", "DEVOLUCION", "TRANSFERENCIA", "INSTALACION_AREA", "RETIRO_AREA",
    "COMPONENTE_INSTALADO", "COMPONENTE_RETIRADO", "LICENCIA_ASIGNADA", "LICENCIA_RETIRADA",
)


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


class Kardex(Base):
    __tablename__ = "INV_KARDEX"

    KDX_Evento = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    KDX_Fecha = Column(DateTime, nullable=False, default=_utcnow_naive, index=True)
    KDX_Tipo = Column(String(30), nullable=False, index=True)
    KDX_Objeto = Column(String(12), nullable=False)  # ACTIVO | CONSUMIBLE | LICENCIA

    ACT_Activo = Column(Uuid, nullable=True, index=True)
    CON_Consumible = Column(Integer, nullable=True, index=True)
    LIC_Licencia = Column(Integer, nullable=True, index=True)
    LCL_Licencia_Clave = Column(Integer, nullable=True)
    # Referencia legible del objeto en el momento del evento (código, nombre).
    KDX_Referencia = Column(String(200), nullable=True)

    # Origen → destino (persona, área, sede o equipo contenedor).
    KDX_Origen_Persona = Column(Uuid, nullable=True, index=True)
    KDX_Origen_Area = Column(Integer, nullable=True)
    KDX_Origen_Sede = Column(Integer, nullable=True)
    KDX_Origen_Activo = Column(Uuid, nullable=True)
    KDX_Destino_Persona = Column(Uuid, nullable=True, index=True)
    KDX_Destino_Area = Column(Integer, nullable=True)
    KDX_Destino_Sede = Column(Integer, nullable=True)
    KDX_Destino_Activo = Column(Uuid, nullable=True)

    KDX_Cantidad = Column(Integer, nullable=True)
    KDX_Saldo = Column(Integer, nullable=True)  # stock o cupo usado resultante
    # Costo unitario del movimiento (consumibles: entradas a su precio,
    # salidas y ajustes al costo promedio ponderado vigente).
    KDX_Costo = Column(Numeric(16, 4), nullable=True)
    KDX_Moneda = Column(String(3), nullable=True)
    # Valor del saldo tras el evento (saldo × costo promedio ponderado).
    KDX_Saldo_Valor = Column(Numeric(16, 2), nullable=True)

    KDX_Documento_Tipo = Column(String(30), nullable=True)  # ORDEN_COMPRA, RECEPCION, ACTA, TICKET…
    KDX_Documento_Ref = Column(String(120), nullable=True)
    KDX_Motivo = Column(String(500), nullable=True)
    KDX_Detalle = Column(PortableJSON(), nullable=True)

    USU_Usuario = Column(Uuid, nullable=True)
    # Sede del evento (visibilidad por alcance): la de destino o, si no hay, la de origen.
    SED_Sede = Column(Integer, nullable=True, index=True)
    KDX_Migrado = Column(Boolean, nullable=False, default=False)

    # Sello encadenado (los asigna el disparador de la base, ver
    # app/db/kardex_integridad.py): orden sin huecos y hash SHA-256 del evento
    # junto con el hash del anterior. Alterar o quitar un evento rompe la cadena.
    KDX_Secuencia = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    KDX_Hash_Anterior = Column(String(64), nullable=True)
    KDX_Hash = Column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint(f'"KDX_Tipo" IN ({_lista(TIPOS_EVENTO)})', name="ck_kardex_tipo"),
        CheckConstraint("\"KDX_Objeto\" IN ('ACTIVO', 'CONSUMIBLE', 'LICENCIA')", name="ck_kardex_objeto"),
        Index("ix_kardex_activo_fecha", "ACT_Activo", "KDX_Fecha"),
        Index("ix_kardex_tipo_fecha", "KDX_Tipo", "KDX_Fecha"),
        UniqueConstraint("KDX_Secuencia", name="uq_kardex_secuencia"),
    )


def nuevo_id() -> uuid.UUID:
    return uuid.uuid4()
