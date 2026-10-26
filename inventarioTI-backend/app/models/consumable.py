"""
Modelo de Consumibles: inventario NO serializado, controlado por cantidad
(tóner, cables, periféricos a granel). Convive con INV_ACTIVO (serializado).

Cada consumible tiene un stock actual y un mínimo; cuando el actual cae por
debajo del mínimo se considera "bajo stock" (alerta). Los movimientos de stock
(entrada/salida) quedan registrados en INV_MOVIMIENTO_CONSUMIBLE para trazabilidad.
"""
import uuid
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Numeric, Uuid, func,
    CheckConstraint, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


# ==========================================
# 1. CONSUMIBLE
# ==========================================
class Consumible(Base):
    __tablename__ = "INV_CONSUMIBLE"

    CON_Consumible = Column(Integer, primary_key=True, index=True, autoincrement=True)

    CON_Nombre = Column(String(100), nullable=False, unique=True)
    CON_Descripcion = Column(String(255), nullable=True)
    CON_Categoria = Column(String(50), nullable=True)
    # Unidad de medida: unidad, caja, metro, litro, etc.
    CON_Unidad = Column(String(20), nullable=False, default="unidad")
    # Catálogos; CON_Categoria y CON_Unidad conservan el nombre legible.
    CCO_Categoria = Column(
        Integer, ForeignKey("INV_CATEGORIA_CONSUMIBLE.CCO_Categoria", ondelete="RESTRICT"), nullable=True, index=True,
    )
    UME_Unidad = Column(
        Integer, ForeignKey("INV_UNIDAD_MEDIDA.UME_Unidad", ondelete="RESTRICT"), nullable=True, index=True,
    )

    CON_Stock_Actual = Column(Integer, nullable=False, default=0)
    CON_Stock_Minimo = Column(Integer, nullable=False, default=0)
    # Último costo unitario de compra (valoriza el stock) y su moneda.
    CON_Costo_Unitario = Column(Numeric(16, 4), nullable=True)  # costo promedio ponderado
    CON_Moneda = Column(String(3), nullable=True)

    CON_Activo = Column(Boolean, nullable=False, default=True)

    # Bodega (sede) donde se almacena el consumible. Base del RLS por sede.
    SED_Sede = Column(
        Integer,
        ForeignKey("INV_SEDE.SED_Sede", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, onupdate=func.now())

    movimientos = relationship(
        "MovimientoConsumible", back_populates="consumible",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint('"CON_Stock_Actual" >= 0', name="ck_consumible_stock_no_negativo"),
        CheckConstraint('"CON_Stock_Minimo" >= 0', name="ck_consumible_minimo_no_negativo"),
    )


# ==========================================
# 2. MOVIMIENTO DE STOCK (entrada / salida / ajuste)
# ==========================================
class MovimientoConsumible(Base):
    __tablename__ = "INV_MOVIMIENTO_CONSUMIBLE"

    MOC_Movimiento = Column(Integer, primary_key=True, index=True, autoincrement=True)

    # ENTRADA suma stock; SALIDA resta; AJUSTE fija un valor absoluto.
    MOC_Tipo = Column(String(10), nullable=False)
    MOC_Cantidad = Column(Integer, nullable=False)
    # Stock resultante DESPUÉS de aplicar el movimiento (snapshot para auditoría).
    MOC_Stock_Resultante = Column(Integer, nullable=False)
    MOC_Motivo = Column(String(255), nullable=True)
    MOC_Fecha = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    CON_Consumible = Column(
        Integer,
        ForeignKey("INV_CONSUMIBLE.CON_Consumible", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Persona que recibe (opcional, típico en SALIDA).
    PER_Persona = Column(
        Uuid,
        ForeignKey("INV_PERSONA.PER_Persona", ondelete="SET NULL"),
        nullable=True,
    )
    # Equipo en que se instaló (tóner en una impresora): la salida queda en su historial.
    ACT_Activo = Column(Uuid, ForeignKey("INV_ACTIVO.ACT_Activo", ondelete="SET NULL"), nullable=True, index=True)
    # Usuario que ejecutó el movimiento (operador).
    USU_Usuario = Column(
        Uuid,
        ForeignKey("INV_USUARIO.USU_Usuario", ondelete="SET NULL"),
        nullable=True,
    )

    consumible = relationship("Consumible", back_populates="movimientos")
    persona = relationship("app.models.organization.Persona")

    __table_args__ = (
        CheckConstraint('"MOC_Cantidad" > 0', name="ck_moc_cantidad_positiva"),
        CheckConstraint(
            "\"MOC_Tipo\" IN ('ENTRADA', 'SALIDA', 'AJUSTE')",
            name="ck_moc_tipo_valido",
        ),
    )


# ==========================================
# 3. COMPATIBILIDAD (tóner HP 26A → modelos de impresora)
# ==========================================
class ConsumibleCompatible(Base):
    __tablename__ = "INV_CONSUMIBLE_COMPATIBLE"

    CCM_Compatible = Column(Integer, primary_key=True, autoincrement=True)
    CON_Consumible = Column(Integer, ForeignKey("INV_CONSUMIBLE.CON_Consumible", ondelete="CASCADE"), nullable=False,
                            index=True)
    MOD_Modelo = Column(Integer, ForeignKey("INV_MODELO.MOD_Modelo", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (UniqueConstraint("CON_Consumible", "MOD_Modelo", name="uq_consumible_compatible"),)
