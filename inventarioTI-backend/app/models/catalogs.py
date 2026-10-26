from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.models.gestion import gestion


def _clase_por_nombre(contexto) -> str:
    """Sin clase indicada, se deduce del nombre (Laptop → COMPUTO, Impresora → IMPRESION)."""
    from app.core.clases_activo import clase_por_nombre
    return clase_por_nombre(contexto.get_current_parameters().get("TAC_Nombre") or "")


# ==========================================
# 1. TIPO DE ACTIVO
# ==========================================
class TipoActivo(Base):
    __tablename__ = "INV_TIPO_ACTIVO"

    TAC_Tipo_Activo = Column(Integer, primary_key=True, index=True, autoincrement=True)
    TAC_Nombre = Column(String(100), unique=True, nullable=False)
    TAC_Prefijo = Column(String(10), nullable=True, unique=True)
    TAC_Aplica_Depreciacion = Column(Boolean, default=True, nullable=False)
    # Clase (app/core/clases_activo.py): decide si se asigna a una persona o se
    # instala en un área, si admite software y componentes, y si es un insumo.
    TAC_Clase = Column(String(20), nullable=False, default=_clase_por_nombre, server_default="OTRO")

    activos = relationship("app.models.core.Activo", back_populates="tipo_activo")

    __table_args__ = (
        CheckConstraint(
            "\"TAC_Clase\" IN ('COMPUTO', 'MOVIL', 'PERIFERICO', 'IMPRESION', 'RED', 'ENERGIA', 'AUDIOVISUAL', "
            "'COMPONENTE', 'HERRAMIENTA', 'INSUMO', 'OTRO')", name="ck_tipo_activo_clase"),
    )


class TipoComponente(Base):
    """Componentes internos que admite un tipo de equipo (Laptop → RAM, disco, batería)."""
    __tablename__ = "INV_TIPO_COMPONENTE"

    TCP_Tipo_Componente = Column(Integer, primary_key=True, autoincrement=True)
    TAC_Padre = Column(Integer, ForeignKey("INV_TIPO_ACTIVO.TAC_Tipo_Activo", ondelete="CASCADE"), nullable=False,
                       index=True)
    TAC_Componente = Column(Integer, ForeignKey("INV_TIPO_ACTIVO.TAC_Tipo_Activo", ondelete="CASCADE"),
                            nullable=False)

    __table_args__ = (
        UniqueConstraint("TAC_Padre", "TAC_Componente", name="uq_tipo_componente"),
        CheckConstraint('"TAC_Padre" <> "TAC_Componente"', name="ck_tipo_componente_distinto"),
    )


# ==========================================
# 2. MARCA
# ==========================================
class Marca(Base):
    __tablename__ = "INV_MARCA"

    MAR_Marca = Column(Integer, primary_key=True, index=True, autoincrement=True)
    MAR_Nombre = Column(String(100), unique=True, nullable=False)
    MAR_Descripcion = Column(String(255), nullable=True)
    locals().update(gestion("INV_MARCA", "MAR", "MAR_Marca"))

    modelos = relationship("Modelo", back_populates="marca")


# ==========================================
# 3. TIPO DE CONEXIÓN
# ==========================================
class TipoConexion(Base):
    __tablename__ = "INV_TIPO_CONEXION"

    TCN_Tipo_Conexion = Column(Integer, primary_key=True, index=True, autoincrement=True)
    TCN_Nombre = Column(String(50), unique=True, nullable=False)
    TCN_Descripcion = Column(String(200), nullable=True)
    locals().update(gestion("INV_TIPO_CONEXION", "TCN", "TCN_Tipo_Conexion"))

    modelos = relationship("Modelo", back_populates="tipo_conexion")


# ==========================================
# 4. MODELO
# ==========================================
class Modelo(Base):
    __tablename__ = "INV_MODELO"

    MOD_Modelo = Column(Integer, primary_key=True, index=True, autoincrement=True)
    MOD_Nombre = Column(String(150), nullable=False)
    MOD_Anio_Lanzamiento = Column(Integer, nullable=True)

    MAR_Marca = Column(
        Integer,
        ForeignKey("INV_MARCA.MAR_Marca", ondelete="RESTRICT"),
        nullable=False,
    )
    TCN_Tipo_Conexion = Column(
        Integer,
        ForeignKey("INV_TIPO_CONEXION.TCN_Tipo_Conexion", ondelete="SET NULL"),
        nullable=True,
    )
    # Tipo de componente al que pertenece el modelo (Mouse, Laptop…). Nullable
    # para modelos legados; habilita la cascada de selección en la UI.
    TAC_Tipo_Activo = Column(
        Integer,
        ForeignKey("INV_TIPO_ACTIVO.TAC_Tipo_Activo", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    marca = relationship("Marca", back_populates="modelos")
    tipo_conexion = relationship("TipoConexion", back_populates="modelos")
    tipo_activo = relationship("TipoActivo")
    activos = relationship("app.models.core.Activo", back_populates="modelo")

    __table_args__ = (
        CheckConstraint(
            '"MOD_Anio_Lanzamiento" IS NULL OR "MOD_Anio_Lanzamiento" BETWEEN 1970 AND 2100',
            name="ck_modelo_anio_razonable",
        ),
    )


# ==========================================
# 5. ESTADO OPERATIVO
# ==========================================
class EstadoOperativo(Base):
    __tablename__ = "INV_ESTADO_OPERATIVO"

    EOP_Estado_Operativo = Column(Integer, primary_key=True, index=True, autoincrement=True)
    EOP_Nombre = Column(String(50), unique=True, nullable=False)
    EOP_Descripcion = Column(String(200), nullable=True)

    activos = relationship("app.models.core.Activo", back_populates="estado_operativo")


# ==========================================
# 6. TIPO DE ESPECIFICACIÓN
# ==========================================
class TipoEspecificacion(Base):
    __tablename__ = "INV_TIPO_ESPECIFICACION"

    TES_Tipo_Especificacion = Column(Integer, primary_key=True, index=True, autoincrement=True)
    TES_Nombre = Column(String(100), unique=True, nullable=False)
    TES_Unidad_Medida = Column(String(20), nullable=True)
    # Unidad del catálogo de unidades; TES_Unidad_Medida queda como su símbolo legible.
    UME_Unidad = Column(Integer, ForeignKey("INV_UNIDAD_MEDIDA.UME_Unidad", ondelete="SET NULL"), nullable=True)
    TES_Descripcion = Column(String(255), nullable=True)
    locals().update(gestion("INV_TIPO_ESPECIFICACION", "TES", "TES_Tipo_Especificacion"))

    especificaciones = relationship(
        "app.models.core.Especificacion", back_populates="tipo_especificacion"
    )
