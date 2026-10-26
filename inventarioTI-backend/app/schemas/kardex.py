"""Esquemas del kardex, la toma de inventario y los reportes de trazabilidad."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Esquema


class KardexEvento(BaseModel):
    KDX_Evento: int
    KDX_Fecha: datetime
    KDX_Tipo: str
    KDX_Objeto: str
    ACT_Activo: Optional[uuid.UUID] = None
    CON_Consumible: Optional[int] = None
    LIC_Licencia: Optional[int] = None
    LCL_Licencia_Clave: Optional[int] = None
    KDX_Referencia: Optional[str] = None
    KDX_Origen_Persona: Optional[uuid.UUID] = None
    KDX_Origen_Area: Optional[int] = None
    KDX_Origen_Sede: Optional[int] = None
    KDX_Origen_Activo: Optional[uuid.UUID] = None
    KDX_Destino_Persona: Optional[uuid.UUID] = None
    KDX_Destino_Area: Optional[int] = None
    KDX_Destino_Sede: Optional[int] = None
    KDX_Destino_Activo: Optional[uuid.UUID] = None
    KDX_Cantidad: Optional[int] = None
    KDX_Saldo: Optional[int] = None
    KDX_Costo: Optional[Decimal] = None
    KDX_Moneda: Optional[str] = None
    KDX_Saldo_Valor: Optional[Decimal] = None
    KDX_Documento_Tipo: Optional[str] = None
    KDX_Documento_Ref: Optional[str] = None
    KDX_Motivo: Optional[str] = None
    KDX_Detalle: Optional[dict[str, Any]] = None
    USU_Usuario: Optional[uuid.UUID] = None
    SED_Sede: Optional[int] = None
    KDX_Migrado: bool = False
    KDX_Secuencia: Optional[int] = None
    KDX_Hash: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class KardexEventoLegible(KardexEvento):
    """Evento con los nombres resueltos para mostrar (persona, área, sede, usuario)."""
    origen_persona: Optional[str] = None
    origen_area: Optional[str] = None
    origen_sede: Optional[str] = None
    origen_activo: Optional[str] = None
    destino_persona: Optional[str] = None
    destino_area: Optional[str] = None
    destino_sede: Optional[str] = None
    destino_activo: Optional[str] = None
    usuario: Optional[str] = None


class KardexPagina(BaseModel):
    total: int
    items: List[KardexEventoLegible]


# =======================
# TOMA DE INVENTARIO
# =======================
class TomaCreate(Esquema):
    SED_Sede: int
    ARE_Area: Optional[int] = None
    TOM_Observacion: Optional[str] = Field(None, max_length=500)


class LecturaCreate(Esquema):
    codigo: str = Field(..., min_length=1, max_length=100)
    # Dónde se encontró (si se recorre una sede, el área en que se está).
    ARE_Area: Optional[int] = None
    observacion: Optional[str] = Field(None, max_length=255)


class LecturaResponse(BaseModel):
    TLE_Lectura: int
    TLE_Codigo_Leido: str
    ACT_Activo: Optional[uuid.UUID] = None
    TLE_Resultado: str
    ARE_Area: Optional[int] = None
    TLE_Fecha: datetime
    TLE_Observacion: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class LecturaResultado(LecturaResponse):
    """Respuesta a una lectura: qué es y qué significa (diálogo con quien escanea)."""
    activo_codigo: Optional[str] = None
    activo_descripcion: Optional[str] = None
    mensaje: str
    esperados: int
    leidos: int


class TomaResponse(BaseModel):
    TOM_Toma: int
    SED_Sede: int
    ARE_Area: Optional[int] = None
    TOM_Estado: str
    TOM_Fecha_Inicio: datetime
    TOM_Fecha_Cierre: Optional[datetime] = None
    TOM_Observacion: Optional[str] = None
    USU_Usuario: Optional[uuid.UUID] = None
    TOM_Resumen: Optional[dict[str, Any]] = None
    model_config = ConfigDict(from_attributes=True)


class TomaDetalle(TomaResponse):
    resumen: dict[str, Any]
    lecturas: List[LecturaResponse] = Field(default_factory=list)


# =======================
# REPORTES
# =======================
class CustodiaItem(BaseModel):
    ACT_Activo: uuid.UUID
    activo_codigo: str
    activo_serie: str
    activo_tipo: Optional[str] = None
    PER_Persona: uuid.UUID
    persona: str
    ARE_Area: int
    area: Optional[str] = None
    SED_Sede: Optional[int] = None
    desde: datetime
    tipo: Optional[str] = None
    devolucion_prevista: Optional[date] = None


# ================= FALTANTES DE LA TOMA =================
class FaltanteNota(Esquema):
    nota: str = Field(..., min_length=3, max_length=500)


class FaltanteLocalizado(Esquema):
    nota: str = Field(..., min_length=3, max_length=500)
    # Dónde apareció (corrige la ubicación del equipo).
    ARE_Area: Optional[int] = None
