"""
Solicitudes que requieren la aprobación del Jefe de TI (separación de funciones).

Una operación sensible (dar de baja un activo, aceptar un sobrante en una
recepción de compra) no se ejecuta al pedirla: queda PENDIENTE con los datos
exactos de la solicitud. Al aprobarla, otra persona con rol JEFE_TI (o
SUPER_ADMIN) la ejecuta tal como se pidió, revalidando las reglas en ese
momento; quien solicita nunca puede aprobar su propia solicitud.
"""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, Uuid

from app.db.base import Base
from app.db.types import PortableJSON


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


TIPOS_APROBACION = ("BAJA", "RECEPCION_SOBRANTE")
ESTADOS_APROBACION = ("PENDIENTE", "APROBADA", "RECHAZADA", "ANULADA")


# BAJA y RECEPCION_SOBRANTE (sesión 13); el resto completa la separación de
# funciones en toda salida de inventario (sesión 16).
TIPOS_APROBACION = ("BAJA", "RECEPCION_SOBRANTE", "AJUSTE_CONSUMIBLE", "CIERRE_FALTANTE", "CANCELACION_ORDEN",
                    "RECLASIFICACION")


class Aprobacion(Base):
    __tablename__ = "INV_APROBACION"

    APR_Aprobacion = Column(Integer, primary_key=True, autoincrement=True)
    APR_Tipo = Column(String(30), nullable=False)
    APR_Estado = Column(String(12), nullable=False, default="PENDIENTE")
    # Datos exactos de la operación solicitada (se ejecutan tal cual al aprobar).
    APR_Payload = Column(PortableJSON(), nullable=False)
    # Descripción legible para la bandeja del aprobador.
    APR_Resumen = Column(String(500), nullable=False)
    APR_Motivo = Column(String(500), nullable=True)

    ACT_Activo = Column(Uuid, nullable=True, index=True)
    OCO_Orden = Column(Integer, nullable=True, index=True)
    CON_Consumible = Column(Integer, nullable=True, index=True)
    SED_Sede = Column(Integer, nullable=True, index=True)

    APR_Solicitante = Column(Uuid, nullable=False)
    APR_Fecha_Solicitud = Column(DateTime, nullable=False, default=_utcnow_naive)
    APR_Aprobador = Column(Uuid, nullable=True)
    APR_Fecha_Resolucion = Column(DateTime, nullable=True)
    APR_Comentario = Column(String(500), nullable=True)

    __table_args__ = (
        CheckConstraint(f'"APR_Tipo" IN ({", ".join(repr(t) for t in TIPOS_APROBACION)})', name="ck_aprobacion_tipo"),
        CheckConstraint(
            "\"APR_Estado\" IN ('PENDIENTE', 'APROBADA', 'RECHAZADA', 'ANULADA')", name="ck_aprobacion_estado"),
        # Separación de funciones también en la base: nadie se aprueba a sí mismo.
        CheckConstraint(
            "\"APR_Aprobador\" IS NULL OR \"APR_Aprobador\" <> \"APR_Solicitante\"", name="ck_aprobacion_otra_persona"),
        Index("ix_aprobacion_estado_fecha", "APR_Estado", "APR_Fecha_Solicitud"),
    )
