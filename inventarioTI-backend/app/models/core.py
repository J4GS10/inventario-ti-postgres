import uuid
from sqlalchemy import false
from sqlalchemy import (
    Boolean, Column, Integer, String, ForeignKey, Date, Numeric, Uuid,
    CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.core.currency import CURRENCY_SQL_LIST, DEFAULT_CURRENCY


# ==========================================
# 1. ACTIVO
# ==========================================
class Activo(Base):
    __tablename__ = "INV_ACTIVO"

    ACT_Activo = Column(Uuid, primary_key=True, default=uuid.uuid4, index=True)

    ACT_Codigo_Interno = Column(String(50), unique=True, nullable=False, index=True)
    ACT_Serie_Fabricante = Column(String(100), nullable=False)
    ACT_Hostname = Column(String(100), nullable=True)

    ACT_Fecha_Compra = Column(Date, nullable=False)
    ACT_Fin_Garantia = Column(Date, nullable=True)
    ACT_Costo = Column(Numeric(12, 2), nullable=True)
    # Origen de la compra (editable en lote): proveedor y número de factura.
    PRV_Proveedor = Column(Integer, ForeignKey("INV_PROVEEDOR.PRV_Proveedor", ondelete="SET NULL"), nullable=True)
    ACT_Factura = Column(String(60), nullable=True)
    # Moneda en que se pagó el costo (no se convierte).
    ACT_Moneda = Column(
        String(3), nullable=False, default=DEFAULT_CURRENCY, server_default=DEFAULT_CURRENCY,
    )

    MOD_Modelo = Column(
        Integer,
        ForeignKey("INV_MODELO.MOD_Modelo", ondelete="RESTRICT"),
        nullable=False,
    )
    TAC_Tipo_Activo = Column(
        Integer,
        ForeignKey("INV_TIPO_ACTIVO.TAC_Tipo_Activo", ondelete="RESTRICT"),
        nullable=False,
    )
    EOP_Estado_Operativo = Column(
        Integer,
        ForeignKey("INV_ESTADO_OPERATIVO.EOP_Estado_Operativo", ondelete="RESTRICT"),
        nullable=False,
    )

    # Componente instalado dentro de otro equipo (disco, memoria, monitor…).
    ACT_Activo_Padre = Column(
        Uuid,
        ForeignKey("INV_ACTIVO.ACT_Activo", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Ubicación física actual (área): la fija la asignación/transferencia o la
    # devolución a una bodega; vacía si no se conoce.
    ARE_Area_Ubicacion = Column(
        Integer, ForeignKey("INV_AREA.ARE_Area", ondelete="SET NULL"), nullable=True,
    )
    # Equipo compartido instalado en un área (impresora, switch, UPS…): no es
    # custodia de nadie; el responsable es opcional. Al volver de un
    # mantenimiento regresa a «Instalado».
    ACT_Instalado = Column(Boolean, nullable=False, default=False, server_default=false())
    ACT_Responsable = Column(
        Uuid, ForeignKey("INV_PERSONA.PER_Persona", ondelete="SET NULL"), nullable=True, index=True,
    )
    # Baja formal: motivo, destino y respaldo (el estado es "Baja").
    ACT_Baja_Motivo = Column(String(30), nullable=True)
    ACT_Baja_Destino = Column(String(30), nullable=True)
    ACT_Baja_Fecha = Column(Date, nullable=True)
    ACT_Baja_Documento = Column(String(120), nullable=True)
    ACT_Baja_Observacion = Column(String(500), nullable=True)
    # Sede a la que pertenece el activo (también en bodega). Se actualiza al
    # asignarlo o transferirlo a un área de otra sede. Base del RLS por sede.
    SED_Sede = Column(
        Integer,
        ForeignKey("INV_SEDE.SED_Sede", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    modelo = relationship("app.models.catalogs.Modelo", back_populates="activos")
    tipo_activo = relationship("app.models.catalogs.TipoActivo", back_populates="activos")
    estado_operativo = relationship("app.models.catalogs.EstadoOperativo", back_populates="activos")
    sede = relationship("app.models.location.Sede")

    hijos = relationship("Activo", backref="padre", remote_side=[ACT_Activo])
    especificaciones = relationship(
        "Especificacion", back_populates="activo", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("ACT_Serie_Fabricante", name="uq_activo_serie_fabricante"),
        CheckConstraint(
            '"ACT_Fin_Garantia" IS NULL OR "ACT_Fin_Garantia" >= "ACT_Fecha_Compra"',
            name="ck_activo_garantia_posterior_compra",
        ),
        CheckConstraint(
            '"ACT_Activo_Padre" IS NULL OR "ACT_Activo_Padre" <> "ACT_Activo"',
            name="ck_activo_padre_distinto_self",
        ),
        CheckConstraint(
            '"ACT_Costo" IS NULL OR "ACT_Costo" >= 0',
            name="ck_activo_costo_no_negativo",
        ),
        CheckConstraint(
            f'"ACT_Moneda" IN ({CURRENCY_SQL_LIST})',
            name="ck_activo_moneda_valida",
        ),
        Index("ix_activo_hostname", "ACT_Hostname"),
    )


# ==========================================
# 2. ESPECIFICACIONES TÉCNICAS
# ==========================================
class Especificacion(Base):
    __tablename__ = "INV_ESPECIFICACION"

    ESP_Especificacion = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ESP_Valor = Column(String(255), nullable=False)

    ACT_Activo = Column(
        Uuid,
        ForeignKey("INV_ACTIVO.ACT_Activo", ondelete="CASCADE"),
        nullable=False,
    )
    TES_Tipo_Especificacion = Column(
        Integer,
        ForeignKey("INV_TIPO_ESPECIFICACION.TES_Tipo_Especificacion", ondelete="RESTRICT"),
        nullable=False,
    )

    activo = relationship("Activo", back_populates="especificaciones")
    tipo_especificacion = relationship(
        "app.models.catalogs.TipoEspecificacion", back_populates="especificaciones"
    )

    __table_args__ = (
        UniqueConstraint(
            "ACT_Activo", "TES_Tipo_Especificacion", name="uq_espec_activo_tipo"
        ),
    )
