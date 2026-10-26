"""
Toma de inventario físico (conciliación): se recorre una sede o un área
leyendo el QR/código de cada equipo y el sistema lo compara con lo registrado.

Resultado de cada lectura:
  ENCONTRADO       el activo está donde el sistema dice.
  OTRA_UBICACION   es de la sede, pero el sistema lo tiene en otra área.
  OTRA_SEDE        el sistema lo tiene en otra sede.
  DE_BAJA          figura dado de baja y apareció físicamente.
  NO_REGISTRADO    el código no existe en el inventario (sobrante).
  REPETIDO         ya se leyó en esta toma.
Al cerrar, lo esperado que no se leyó queda como FALTANTE.
"""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.db.types import PortableJSON


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


RESULTADOS_LECTURA = ("ENCONTRADO", "OTRA_UBICACION", "OTRA_SEDE", "DE_BAJA", "NO_REGISTRADO", "REPETIDO")


class TomaInventario(Base):
    __tablename__ = "INV_TOMA_INVENTARIO"

    TOM_Toma = Column(Integer, primary_key=True, autoincrement=True)
    SED_Sede = Column(Integer, ForeignKey("INV_SEDE.SED_Sede", ondelete="RESTRICT"), nullable=False, index=True)
    ARE_Area = Column(Integer, ForeignKey("INV_AREA.ARE_Area", ondelete="RESTRICT"), nullable=True)
    TOM_Estado = Column(String(10), nullable=False, default="ABIERTA")
    TOM_Fecha_Inicio = Column(DateTime, nullable=False, default=_utcnow_naive)
    TOM_Fecha_Cierre = Column(DateTime, nullable=True)
    TOM_Observacion = Column(String(500), nullable=True)
    USU_Usuario = Column(Uuid, ForeignKey("INV_USUARIO.USU_Usuario", ondelete="SET NULL"), nullable=True)
    # Resumen congelado al cerrar (esperados, encontrados, faltantes…).
    TOM_Resumen = Column(PortableJSON(), nullable=True)

    lecturas = relationship("LecturaToma", back_populates="toma", cascade="all, delete-orphan",
                            order_by="LecturaToma.TLE_Lectura")

    __table_args__ = (
        CheckConstraint("\"TOM_Estado\" IN ('ABIERTA', 'CERRADA', 'ANULADA')", name="ck_toma_estado"),
    )


class LecturaToma(Base):
    __tablename__ = "INV_TOMA_LECTURA"

    TLE_Lectura = Column(Integer, primary_key=True, autoincrement=True)
    TOM_Toma = Column(Integer, ForeignKey("INV_TOMA_INVENTARIO.TOM_Toma", ondelete="CASCADE"),
                      nullable=False, index=True)
    TLE_Codigo_Leido = Column(String(100), nullable=False)
    ACT_Activo = Column(Uuid, nullable=True)
    TLE_Resultado = Column(String(20), nullable=False)
    ARE_Area = Column(Integer, nullable=True)  # dónde se encontró
    TLE_Fecha = Column(DateTime, nullable=False, default=_utcnow_naive)
    TLE_Observacion = Column(String(255), nullable=True)
    USU_Usuario = Column(Uuid, nullable=True)

    toma = relationship("TomaInventario", back_populates="lecturas")

    __table_args__ = (
        CheckConstraint(
            "\"TLE_Resultado\" IN ('ENCONTRADO', 'OTRA_UBICACION', 'OTRA_SEDE', 'DE_BAJA', "
            "'NO_REGISTRADO', 'REPETIDO')",
            name="ck_toma_lectura_resultado",
        ),
    )


ESTADOS_FALTANTE = ("PENDIENTE", "EN_INVESTIGACION", "LOCALIZADO", "BAJA_SOLICITADA", "DADO_DE_BAJA")


class Faltante(Base):
    """
    Equipo que no apareció en una toma. Se investiga y se resuelve:
    LOCALIZADO (apareció; también al leerlo en otra toma) o baja por pérdida o
    robo (BAJA_SOLICITADA → DADO_DE_BAJA al aprobarse; si se rechaza, vuelve a
    investigación). El seguimiento guarda cada nota con su autor y fecha.
    """
    __tablename__ = "INV_FALTANTE"

    FAL_Faltante = Column(Integer, primary_key=True, autoincrement=True)
    TOM_Toma = Column(Integer, ForeignKey("INV_TOMA_INVENTARIO.TOM_Toma", ondelete="SET NULL"), nullable=True,
                      index=True)
    ACT_Activo = Column(Uuid, ForeignKey("INV_ACTIVO.ACT_Activo", ondelete="CASCADE"), nullable=False, index=True)
    SED_Sede = Column(Integer, ForeignKey("INV_SEDE.SED_Sede", ondelete="RESTRICT"), nullable=True, index=True)
    FAL_Estado = Column(String(20), nullable=False, default="PENDIENTE")
    FAL_Fecha = Column(DateTime, nullable=False, default=_utcnow_naive)
    FAL_Seguimiento = Column(PortableJSON(), nullable=True)
    FAL_Resolucion = Column(String(500), nullable=True)
    FAL_Fecha_Resolucion = Column(DateTime, nullable=True)
    APR_Aprobacion = Column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "\"FAL_Estado\" IN ('PENDIENTE', 'EN_INVESTIGACION', 'LOCALIZADO', 'BAJA_SOLICITADA', 'DADO_DE_BAJA')",
            name="ck_faltante_estado"),
    )
