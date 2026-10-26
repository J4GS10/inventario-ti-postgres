"""Esquemas del servicio genérico de catálogos."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from app.schemas.common import Esquema


class CatalogoItemCreate(Esquema):
    nombre: str = Field(..., min_length=1, max_length=150)
    descripcion: Optional[str] = Field(None, max_length=255)
    orden: Optional[int] = Field(None, ge=0, le=100000)
    extras: Dict[str, Any] = Field(default_factory=dict)


class CatalogoItemUpdate(Esquema):
    nombre: Optional[str] = Field(None, min_length=1, max_length=150)
    descripcion: Optional[str] = Field(None, max_length=255)
    orden: Optional[int] = Field(None, ge=0, le=100000)
    activo: Optional[bool] = None
    extras: Dict[str, Any] = Field(default_factory=dict)


class CatalogoReorden(Esquema):
    ids: List[int] = Field(..., min_length=1, max_length=500)


class EspecificacionTipoItem(Esquema):
    TES_Tipo_Especificacion: int
    obligatoria: bool = False
    valores: List[str] = Field(default_factory=list, max_length=50)


class EspecificacionesTipo(Esquema):
    items: List[EspecificacionTipoItem] = Field(default_factory=list, max_length=40)


class KitItem(Esquema):
    TAC_Tipo_Activo: Optional[int] = None
    KIT_Cantidad: int = Field(1, ge=1, le=50)
    LIC_Licencia: Optional[int] = None


class KitEstandarIn(Esquema):
    KIT_Nombre: str = Field(..., min_length=2, max_length=100)
    CAR_Cargo: Optional[int] = None
    DEP_Departamento: Optional[int] = None
    KIT_Activo: bool = True
    items: List[KitItem] = Field(..., min_length=1, max_length=30)
