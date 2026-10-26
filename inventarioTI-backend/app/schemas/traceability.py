from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.common import Esquema
from datetime import date, datetime
import uuid

# =======================
# TIPOS DE MOVIMIENTO
# =======================
class TipoMovimientoBase(Esquema):
    TMO_Nombre: str = Field(..., min_length=3, max_length=50)

class TipoMovimientoCreate(TipoMovimientoBase):
    pass

class TipoMovimientoUpdate(Esquema):
    TMO_Nombre: Optional[str] = Field(None, min_length=3, max_length=50)

class TipoMovimientoResponse(TipoMovimientoBase):
    TMO_Tipo_Movimiento: int
    model_config = ConfigDict(from_attributes=True)

# =======================
# SCHEMAS ANIDADOS (Para mostrar nombres, no solo IDs)
# =======================
class ActivoSummary(Esquema):
    ACT_Codigo_Interno: str
    ACT_Hostname: Optional[str] = None

class PersonaSummary(Esquema):
    PER_Primer_Nombre: str
    PER_Primer_Apellido: str
    PER_Email_Corporativo: str

class AreaSummary(Esquema):
    ARE_Nombre: str

# =======================
# MOVIMIENTOS (ASIGNACIONES)
# =======================
class MovimientoBase(Esquema):
    ACT_Activo: uuid.UUID
    PER_Persona: uuid.UUID
    ARE_Area: int
    TMO_Tipo_Movimiento: int 
    MOV_Observacion: Optional[str] = None

class MovimientoCreate(MovimientoBase):
    # Override con cota de longitud SOLO en la entrada (la base queda laxa para
    # que los Response no fallen ante observaciones legadas largas en BD).
    MOV_Observacion: Optional[str] = Field(None, max_length=1000)
    # Opcional: al asignar un equipo a un nuevo usuario, permite fijar/cambiar
    # el hostname del activo. Si es None, el activo conserva su hostname actual.
    ACT_Hostname: Optional[str] = Field(None, max_length=100)
    # Préstamos: fecha comprometida de devolución (obligatoria en un préstamo).
    MOV_Fecha_Prevista_Devolucion: Optional[date] = None
    MOV_Motivo: Optional[str] = Field(None, max_length=255)
    # Motivo del catálogo (INV_MOTIVO_MOVIMIENTO) y su detalle; MOV_Motivo
    # (texto libre) se acepta sin catálogo por compatibilidad.
    MMO_Motivo: Optional[int] = None
    MOV_Motivo_Detalle: Optional[str] = Field(None, max_length=255)

    from pydantic import field_validator

    @field_validator("MOV_Fecha_Prevista_Devolucion")
    @classmethod
    def fecha_prevista_no_puede_ser_pasada(cls, value: Optional[date]) -> Optional[date]:
        if value is not None and value < date.today():
            raise ValueError("La fecha prevista de devolución no puede estar en el pasado")
        return value

class MovimientoResponse(MovimientoBase):
    MOV_Movimiento: uuid.UUID
    MOV_Fecha_Asignacion: datetime
    MOV_Fecha_Devolucion: Optional[datetime] = None
    MOV_Motivo: Optional[str] = None
    MMO_Motivo: Optional[int] = None
    MOV_Motivo_Detalle: Optional[str] = None
    MOV_Fecha_Prevista_Devolucion: Optional[date] = None
    MOV_Usuario_Registra: Optional[uuid.UUID] = None
    
    # --- AQUÍ ESTÁ LA MAGIA QUE FALTABA ---
    # Estos campos coinciden con los nombres de las relaciones en el Modelo SQLAlchemy
    activo: Optional[ActivoSummary] = None
    persona: Optional[PersonaSummary] = None
    tipo_movimiento: Optional[TipoMovimientoResponse] = None
    area: Optional[AreaSummary] = None
    
    model_config = ConfigDict(from_attributes=True)

# =======================
# DEVOLUCIÓN
# =======================
class DevolucionCreate(Esquema):
    ACT_Activo: uuid.UUID
    # Por qué se devuelve (renuncia, cambio de equipo, fin de préstamo…).
    MOV_Motivo: Optional[str] = Field(None, max_length=255)
    # Motivo del catálogo (INV_MOTIVO_MOVIMIENTO) y su detalle; MOV_Motivo
    # (texto libre) se acepta sin catálogo por compatibilidad.
    MMO_Motivo: Optional[int] = None
    MOV_Motivo_Detalle: Optional[str] = Field(None, max_length=255)
    # Bodega o área donde queda el equipo (su ubicación física).
    ARE_Area_Destino: Optional[int] = None
    # Estado en que llega: CON_DANO propone abrir un ticket de mantenimiento.
    condicion: Optional[str] = Field(None, pattern="^(BUENO|CON_DANO)$")

# =======================
# TRANSFERENCIA
# =======================
class TransferenciaCreate(Esquema):
    ACT_Activo: uuid.UUID
    PER_Persona_Destino: uuid.UUID
    ARE_Area_Destino: int
    MOV_Observacion: Optional[str] = Field(None, max_length=1000)
    MOV_Motivo: Optional[str] = Field(None, max_length=255)
    # Motivo del catálogo (INV_MOTIVO_MOVIMIENTO) y su detalle; MOV_Motivo
    # (texto libre) se acepta sin catálogo por compatibilidad.
    MMO_Motivo: Optional[int] = None
    MOV_Motivo_Detalle: Optional[str] = Field(None, max_length=255)

# =======================
# EQUIPOS COMPARTIDOS (instalar en un área / retirar)
# =======================
class InstalacionAreaCreate(Esquema):
    ACT_Activo: uuid.UUID
    ARE_Area: int
    # Responsable opcional (reporta fallas, recibe el tóner); no es custodia.
    PER_Responsable: Optional[uuid.UUID] = None
    MMO_Motivo: Optional[int] = None
    MOV_Motivo: Optional[str] = Field(None, max_length=255)
    MOV_Motivo_Detalle: Optional[str] = Field(None, max_length=255)


class RetiroAreaCreate(Esquema):
    ACT_Activo: uuid.UUID
    # Bodega o área donde queda (si no, conserva el área).
    ARE_Area_Destino: Optional[int] = None
    MMO_Motivo: Optional[int] = None
    MOV_Motivo: Optional[str] = Field(None, max_length=255)
    MOV_Motivo_Detalle: Optional[str] = Field(None, max_length=255)
    condicion: Optional[str] = Field(None, pattern="^(BUENO|CON_DANO)$")

# Request para descarga por lote
class ActaLoteRequest(Esquema):
    movimientos_ids: List[uuid.UUID]
