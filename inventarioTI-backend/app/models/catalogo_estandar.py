"""
Catálogos estándar (plan de licencias y catálogos, §2 y §4.1).

Todos comparten la misma forma, así el servicio genérico
(app/services/catalogo_generico.py) los administra sin código propio:

    <P>_<Pk>              id
    <P>_Nombre            único sin distinguir mayúsculas ni espacios (app/db/unicidad.py)
    <P>_Codigo            código interno estable; la lógica depende de él, nunca del nombre
    <P>_Descripcion       opcional
    <P>_Orden             orden de presentación
    <P>_Activo            se desactiva en vez de borrarse
    <P>_Fusionado_En      id del registro que lo absorbió al fusionar duplicados
    <P>_Fusionado_Fecha   cuándo se fusionó
    created_at

Las filas con código las siembra el sistema (app/db/catalogos_semilla.py): su
código no cambia y la UI traduce su nombre; las que crea el usuario no llevan
código y se muestran tal cual.
"""
from __future__ import annotations

from sqlalchemy import false, true
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func,
)
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.db.types import PortableJSON


def _columnas(tabla: str, prefijo: str, pk: str, largo_nombre: int = 100) -> dict:
    return {
        "__tablename__": tabla,
        pk: Column(Integer, primary_key=True, index=True, autoincrement=True),
        f"{prefijo}_Nombre": Column(String(largo_nombre), nullable=False),
        f"{prefijo}_Codigo": Column(String(40), nullable=True, unique=True),
        f"{prefijo}_Descripcion": Column(String(255), nullable=True),
        f"{prefijo}_Orden": Column(Integer, nullable=False, default=0, server_default="0"),
        f"{prefijo}_Activo": Column(Boolean, nullable=False, default=True, server_default=true()),
        f"{prefijo}_Fusionado_En": Column(Integer, ForeignKey(f"{tabla}.{pk}", ondelete="SET NULL"), nullable=True),
        f"{prefijo}_Fusionado_Fecha": Column(DateTime, nullable=True),
        "created_at": Column(DateTime, server_default=func.now(), nullable=False),
    }


def catalogo(nombre_clase: str, tabla: str, prefijo: str, pk: str, **extras):
    """Declara un catálogo estándar; `extras` son columnas propias del catálogo."""
    cuerpo = _columnas(tabla, prefijo, pk)
    cuerpo.update(extras)
    cuerpo["__module__"] = __name__
    return type(nombre_clase, (Base,), cuerpo)


# Tipos de movimiento a los que puede aplicar un motivo.
APLICA_A = ("ASIGNACION", "DEVOLUCION", "TRANSFERENCIA", "PRESTAMO")

# ── Movimientos y bajas ──────────────────────────────────────────────────────
MotivoMovimiento = catalogo(
    "MotivoMovimiento", "INV_MOTIVO_MOVIMIENTO", "MMO", "MMO_Motivo",
    # Lista de APLICA_A; vacía = cualquier tipo de movimiento.
    MMO_Aplica_A=Column(PortableJSON(), nullable=True),
    # El motivo exige un detalle escrito (p. ej. «Otro»).
    MMO_Pide_Detalle=Column(Boolean, nullable=False, default=False, server_default=false()),
)
MotivoBaja = catalogo(
    "MotivoBaja", "INV_MOTIVO_BAJA", "MBA", "MBA_Motivo",
    MBA_Pide_Detalle=Column(Boolean, nullable=False, default=False, server_default=false()),
)
DestinoBaja = catalogo(
    "DestinoBaja", "INV_DESTINO_BAJA", "DBA", "DBA_Destino",
    DBA_Pide_Detalle=Column(Boolean, nullable=False, default=False, server_default=false()),
)

# ── Consumibles y unidades ───────────────────────────────────────────────────
UnidadMedida = catalogo(
    "UnidadMedida", "INV_UNIDAD_MEDIDA", "UME", "UME_Unidad",
    UME_Simbolo=Column(String(20), nullable=True),
)
CategoriaConsumible = catalogo("CategoriaConsumible", "INV_CATEGORIA_CONSUMIBLE", "CCO", "CCO_Categoria")

# ── Ubicaciones ──────────────────────────────────────────────────────────────
TipoArea = catalogo("TipoArea", "INV_TIPO_AREA", "TAR", "TAR_Tipo_Area")

# ── Mantenimiento ────────────────────────────────────────────────────────────
TipoFalla = catalogo(
    "TipoFalla", "INV_TIPO_FALLA", "TFA", "TFA_Tipo_Falla",
    TFA_Pide_Detalle=Column(Boolean, nullable=False, default=False, server_default=false()),
)
AccionMantenimiento = catalogo(
    "AccionMantenimiento", "INV_ACCION_MANTENIMIENTO", "AMA", "AMA_Accion",
    AMA_Pide_Detalle=Column(Boolean, nullable=False, default=False, server_default=false()),
)

# ── Documentos ───────────────────────────────────────────────────────────────
TipoDocumento = catalogo("TipoDocumento", "INV_TIPO_DOCUMENTO", "TDO", "TDO_Tipo_Documento")


# ── Licencias (bloque A) ─────────────────────────────────────────────────────
# Campos de una credencial que puede pedir un método de activación.
CAMPOS_CREDENCIAL = ("usuario", "secreto", "serial", "clave", "servidor", "archivo", "dispositivo")

ModeloComercial = catalogo("ModeloComercial", "INV_MODELO_COMERCIAL", "MCO", "MCO_Modelo_Comercial")
MetodoActivacion = catalogo(
    "MetodoActivacion", "INV_METODO_ACTIVACION", "MAC", "MAC_Metodo",
    # Qué pide la credencial y con qué etiqueta (p. ej. «Correo» en vez de «Usuario»).
    **{f"MAC_Pide_{c.capitalize()}": Column(Boolean, nullable=False, default=False, server_default=false())
       for c in CAMPOS_CREDENCIAL},
    **{f"MAC_Etiqueta_{c.capitalize()}": Column(String(60), nullable=True) for c in CAMPOS_CREDENCIAL},
)
# Códigos de los que depende la asignación: EQUIPO, PERSONA, CONCURRENTE, SITIO.
FormaConteo = catalogo("FormaConteo", "INV_FORMA_CONTEO", "FCO", "FCO_Forma")


# ── Especificaciones por tipo de activo ──────────────────────────────────────
class EspecificacionTipoActivo(Base):
    """Qué especificaciones pide cada tipo de activo (p. ej. Laptop → RAM)."""
    __tablename__ = "INV_ESPECIFICACION_TIPO_ACTIVO"

    ETA_Especificacion_Tipo = Column(Integer, primary_key=True, index=True, autoincrement=True)
    TAC_Tipo_Activo = Column(
        Integer, ForeignKey("INV_TIPO_ACTIVO.TAC_Tipo_Activo", ondelete="CASCADE"), nullable=False, index=True,
    )
    TES_Tipo_Especificacion = Column(
        Integer, ForeignKey("INV_TIPO_ESPECIFICACION.TES_Tipo_Especificacion", ondelete="CASCADE"), nullable=False,
    )
    ETA_Obligatoria = Column(Boolean, nullable=False, default=False, server_default=false())
    # Valores permitidos (lista de textos); vacío = libre.
    ETA_Valores = Column(PortableJSON(), nullable=True)
    ETA_Orden = Column(Integer, nullable=False, default=0, server_default="0")

    tipo_especificacion = relationship("app.models.catalogs.TipoEspecificacion")

    __table_args__ = (UniqueConstraint("TAC_Tipo_Activo", "TES_Tipo_Especificacion", name="uq_especificacion_tipo"),)


# ── Kit estándar de puesto ───────────────────────────────────────────────────
class KitEstandar(Base):
    """Equipos y licencias que recibe un cargo o un departamento al ingresar."""
    __tablename__ = "INV_KIT_ESTANDAR"

    KIT_Kit = Column(Integer, primary_key=True, index=True, autoincrement=True)
    KIT_Nombre = Column(String(100), nullable=False)
    CAR_Cargo = Column(Integer, ForeignKey("INV_CARGO.CAR_Cargo", ondelete="CASCADE"), nullable=True, index=True)
    DEP_Departamento = Column(
        Integer, ForeignKey("INV_DEPARTAMENTO.DEP_Departamento", ondelete="CASCADE"), nullable=True, index=True,
    )
    KIT_Activo = Column(Boolean, nullable=False, default=True, server_default=true())
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    items = relationship("KitEstandarItem", cascade="all, delete-orphan", order_by="KitEstandarItem.KIT_Item")

    __table_args__ = (
        # Un kit es por cargo o por departamento (exactamente uno).
        CheckConstraint(
            '("CAR_Cargo" IS NULL AND "DEP_Departamento" IS NOT NULL) OR '
            '("CAR_Cargo" IS NOT NULL AND "DEP_Departamento" IS NULL)', name="ck_kit_cargo_o_departamento",
        ),
    )


class KitEstandarItem(Base):
    __tablename__ = "INV_KIT_ESTANDAR_ITEM"

    KIT_Item = Column(Integer, primary_key=True, index=True, autoincrement=True)
    KIT_Kit = Column(Integer, ForeignKey("INV_KIT_ESTANDAR.KIT_Kit", ondelete="CASCADE"), nullable=False, index=True)
    TAC_Tipo_Activo = Column(
        Integer, ForeignKey("INV_TIPO_ACTIVO.TAC_Tipo_Activo", ondelete="CASCADE"), nullable=True,
    )
    KIT_Cantidad = Column(Integer, nullable=False, default=1, server_default="1")
    LIC_Licencia = Column(Integer, ForeignKey("INV_LICENCIA.LIC_Licencia", ondelete="CASCADE"), nullable=True)

    __table_args__ = (
        CheckConstraint('"KIT_Cantidad" BETWEEN 1 AND 50', name="ck_kit_item_cantidad"),
        CheckConstraint(
            '"TAC_Tipo_Activo" IS NOT NULL OR "LIC_Licencia" IS NOT NULL', name="ck_kit_item_algo",
        ),
    )
