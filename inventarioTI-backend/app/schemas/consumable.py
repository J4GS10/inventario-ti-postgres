from typing import Optional
from datetime import datetime
import uuid

from pydantic import BaseModel, Field, ConfigDict
from app.schemas.common import Esquema
from decimal import Decimal
from app.core.currency import CurrencyCode


# =======================
# CONSUMIBLE
# =======================
class ConsumibleBase(Esquema):
    CON_Nombre: str = Field(..., min_length=2, max_length=100)
    CON_Descripcion: Optional[str] = Field(None, max_length=255)
    CON_Categoria: Optional[str] = Field(None, max_length=50)
    CON_Unidad: str = Field("unidad", min_length=1, max_length=20)
    # Catálogos (si vienen, fijan CON_Categoria y CON_Unidad con su nombre).
    CCO_Categoria: Optional[int] = None
    UME_Unidad: Optional[int] = None
    CON_Stock_Minimo: int = Field(0, ge=0)
    # Bodega (sede) donde se almacena: alcance de datos.
    SED_Sede: Optional[int] = None


class ConsumibleCreate(ConsumibleBase):
    # Stock inicial opcional al dar de alta el consumible (queda en el kardex
    # como saldo inicial, valorizado si se indica su costo unitario).
    CON_Stock_Actual: int = Field(0, ge=0)
    CON_Costo_Unitario: Optional[Decimal] = Field(None, ge=0, max_digits=14, decimal_places=4)
    CON_Moneda: Optional[CurrencyCode] = None


class ConsumibleUpdate(Esquema):
    CON_Nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    CON_Descripcion: Optional[str] = Field(None, max_length=255)
    CON_Categoria: Optional[str] = Field(None, max_length=50)
    CON_Unidad: Optional[str] = Field(None, min_length=1, max_length=20)
    CCO_Categoria: Optional[int] = None
    UME_Unidad: Optional[int] = None
    CON_Stock_Minimo: Optional[int] = Field(None, ge=0)
    CON_Activo: Optional[bool] = None
    SED_Sede: Optional[int] = None


class ConsumibleResponse(ConsumibleBase):
    CON_Consumible: int
    CON_Stock_Actual: int
    CON_Activo: bool
    # Último costo unitario de compra y su moneda (valoriza el stock).
    CON_Costo_Unitario: Optional[Decimal] = None
    CON_Moneda: Optional[str] = None
    # Calculado: stock actual <= mínimo (y mínimo > 0).
    bajo_stock: bool = False
    model_config = ConfigDict(from_attributes=True)


# =======================
# MOVIMIENTO DE STOCK
# =======================
class StockMovimientoCreate(Esquema):
    """Entrada o salida de stock."""
    MOC_Cantidad: int = Field(..., gt=0)
    MOC_Motivo: Optional[str] = Field(None, max_length=255)
    PER_Persona: Optional[uuid.UUID] = None
    # Solo en salidas: equipo en que se instala (tóner en una impresora).
    ACT_Activo: Optional[uuid.UUID] = None
    # Solo en entradas: costo unitario de la compra y su moneda.
    precio_unitario: Optional[Decimal] = Field(None, ge=0, max_digits=14, decimal_places=2)
    moneda: Optional[CurrencyCode] = None


class StockAjusteCreate(Esquema):
    """Ajuste por conteo físico: fija la existencia real."""
    CON_Stock_Real: int = Field(..., ge=0)
    MOC_Motivo: str = Field(..., min_length=5, max_length=255)


class MovimientoConsumibleResponse(Esquema):
    MOC_Movimiento: int
    MOC_Tipo: str
    MOC_Cantidad: int
    MOC_Stock_Resultante: int
    # Diálogo: la salida dejó el stock en o bajo el mínimo → proponer una compra.
    bajo_minimo: bool = False
    CON_Stock_Minimo: Optional[int] = None
    MOC_Motivo: Optional[str] = None
    MOC_Fecha: datetime
    CON_Consumible: int
    PER_Persona: Optional[uuid.UUID] = None
    ACT_Activo: Optional[uuid.UUID] = None
    USU_Usuario: Optional[uuid.UUID] = None
    model_config = ConfigDict(from_attributes=True)


class CompatiblesUpdate(Esquema):
    """Modelos de equipo con los que es compatible el consumible (tóner → impresoras)."""
    modelos: list[int] = Field(default_factory=list, max_length=200)
