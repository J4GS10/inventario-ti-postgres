"""Reglas por clase de activo aplicadas a cada operación (ver app/core/clases_activo.py)."""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clases_activo import COMPARTIDO, COMPONENTE, INSUMO, reglas
from app.models.catalogs import TipoActivo, TipoComponente
from app.models.core import Activo


async def reglas_de_tipo(db: AsyncSession, tipo_id: int | None) -> dict:
    clase = (await db.execute(select(TipoActivo.TAC_Clase).where(TipoActivo.TAC_Tipo_Activo == tipo_id))).scalar()
    return reglas(clase)


async def reglas_de_activo(db: AsyncSession, activo: Activo | uuid.UUID) -> dict:
    if not isinstance(activo, Activo):
        tipo = (await db.execute(select(Activo.TAC_Tipo_Activo).where(Activo.ACT_Activo == activo))).scalar()
    else:
        tipo = activo.TAC_Tipo_Activo
    return await reglas_de_tipo(db, tipo)


async def exigir_no_insumo_tipo(db: AsyncSession, tipo_id: int) -> None:
    """Un insumo (aire comprimido, pasta térmica…) se registra como consumible, no como activo."""
    if (await reglas_de_tipo(db, tipo_id))["uso"] == INSUMO:
        raise HTTPException(422, detail="ASSET_TYPE_IS_SUPPLY")


async def exigir_asignable_a_persona(db: AsyncSession, activo: Activo | uuid.UUID) -> None:
    """Asignar, prestar o transferir a una persona."""
    uso = (await reglas_de_activo(db, activo))["uso"]
    if uso == COMPARTIDO:
        raise HTTPException(422, detail="ASSET_IS_SHARED_INSTALL_IN_AREA")
    if uso == INSUMO:
        raise HTTPException(422, detail="ASSET_TYPE_IS_SUPPLY")


async def exigir_instalable_en_area(db: AsyncSession, activo: Activo) -> None:
    uso = (await reglas_de_activo(db, activo))["uso"]
    if uso == INSUMO:
        raise HTTPException(422, detail="ASSET_TYPE_IS_SUPPLY")
    if uso == COMPONENTE:
        raise HTTPException(422, detail="COMPONENT_INSTALLS_IN_EQUIPMENT")


async def exigir_software(db: AsyncSession, activo_id: uuid.UUID) -> None:
    """Instalaciones y licencias por equipo solo en equipos que llevan software."""
    if not (await reglas_de_activo(db, activo_id))["software"]:
        raise HTTPException(422, detail="ASSET_CLASS_NO_SOFTWARE")


async def tipos_componente_permitidos(db: AsyncSession, tipo_padre: int) -> list[int]:
    return list((await db.execute(select(TipoComponente.TAC_Componente).where(
        TipoComponente.TAC_Padre == tipo_padre))).scalars())


async def exigir_componente_valido(db: AsyncSession, equipo: Activo, componente: Activo) -> None:
    """El equipo admite componentes y el componente es de un tipo que admite."""
    if not (await reglas_de_activo(db, equipo))["componentes"]:
        raise HTTPException(422, detail="ASSET_CLASS_NO_COMPONENTS")
    permitidos = await tipos_componente_permitidos(db, equipo.TAC_Tipo_Activo)
    if permitidos:
        if componente.TAC_Tipo_Activo not in permitidos:
            raise HTTPException(422, detail="COMPONENT_TYPE_NOT_ALLOWED")
    elif (await reglas_de_activo(db, componente))["uso"] != COMPONENTE:
        raise HTTPException(422, detail="COMPONENT_TYPE_NOT_ALLOWED")
