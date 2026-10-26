"""
Motivo de un movimiento desde el catálogo (INV_MOTIVO_MOVIMIENTO).

Un movimiento lleva UN motivo del catálogo (MMO_Motivo, obligatorio) y UNA
nota opcional (MOV_Motivo_Detalle; obligatoria si el motivo la pide, p. ej.
«Otro»). La observación anterior (MOV_Observacion) se toma como la nota si no
viene otra, así no hay dos textos distintos para lo mismo. Aquí se valida que
el motivo esté activo y aplique al tipo de movimiento, y se compone el texto
legible que guardan MOV_Motivo y el kardex («Renovación: teclado dañado»).
Sin MMO_Motivo se acepta el texto libre de una integración; sin ninguno de
los dos, el movimiento se rechaza (MOVIMIENTO_EXIGE_MOTIVO).
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalogo_estandar import MotivoMovimiento


async def con_motivo(db: AsyncSession, schema, aplica_a: str):
    """Devuelve una copia del esquema con MOV_Motivo compuesto desde el catálogo."""
    from app.core.config import settings
    motivo_id = getattr(schema, "MMO_Motivo", None)
    detalle = ((getattr(schema, "MOV_Motivo_Detalle", None) or getattr(schema, "MOV_Observacion", None) or "")
               .strip()[:255] or None)
    if motivo_id is None:
        if settings.MOVIMIENTO_EXIGE_MOTIVO and not (getattr(schema, "MOV_Motivo", None) or "").strip():
            raise HTTPException(422, detail="MOVEMENT_REASON_REQUIRED")
        return schema.model_copy(update={"MOV_Motivo_Detalle": None})
    motivo = await db.get(MotivoMovimiento, motivo_id)
    if motivo is None or not motivo.MMO_Activo:
        raise HTTPException(422, detail="MOVEMENT_REASON_NOT_FOUND")
    if motivo.MMO_Aplica_A and aplica_a not in motivo.MMO_Aplica_A:
        raise HTTPException(422, detail="MOVEMENT_REASON_NOT_APPLICABLE")
    if motivo.MMO_Pide_Detalle and not detalle:
        raise HTTPException(422, detail="MOVEMENT_REASON_DETAIL_REQUIRED")
    texto = f"{motivo.MMO_Nombre}: {detalle}" if detalle else motivo.MMO_Nombre
    return schema.model_copy(update={"MOV_Motivo": texto[:255], "MOV_Motivo_Detalle": detalle})


async def aplica(db: AsyncSession, motivo_id: int | None, aplica_a: str) -> bool:
    """¿El motivo del catálogo aplica a este tipo de movimiento?"""
    if motivo_id is None:
        return False
    motivo = await db.get(MotivoMovimiento, motivo_id)
    return motivo is not None and (not motivo.MMO_Aplica_A or aplica_a in motivo.MMO_Aplica_A)
