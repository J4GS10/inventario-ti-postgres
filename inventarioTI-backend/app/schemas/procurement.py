from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal
import uuid

from pydantic import BaseModel, Field, ConfigDict, EmailStr, computed_field, model_validator
from app.schemas.common import Esquema

from app.core.currency import DEFAULT_CURRENCY, CurrencyCode
from app.schemas.software import LicenciaClaveCreate


# =======================
# PROVEEDOR
# =======================
class ProveedorBase(Esquema):
    PRV_Nombre: str = Field(..., min_length=2, max_length=150)
    PRV_Identificacion_Fiscal: Optional[str] = Field(None, max_length=50)
    PRV_Contacto: Optional[str] = Field(None, max_length=100)
    PRV_Email: Optional[EmailStr] = None
    PRV_Telefono: Optional[str] = Field(None, max_length=30)
    PRV_Direccion: Optional[str] = Field(None, max_length=255)


class ProveedorCreate(ProveedorBase):
    pass


class ProveedorUpdate(Esquema):
    PRV_Nombre: Optional[str] = Field(None, min_length=2, max_length=150)
    PRV_Identificacion_Fiscal: Optional[str] = Field(None, max_length=50)
    PRV_Contacto: Optional[str] = Field(None, max_length=100)
    PRV_Email: Optional[EmailStr] = None
    PRV_Telefono: Optional[str] = Field(None, max_length=30)
    PRV_Direccion: Optional[str] = Field(None, max_length=255)
    PRV_Activo: Optional[bool] = None


class ProveedorResponse(ProveedorBase):
    PRV_Proveedor: int
    PRV_Activo: bool
    model_config = ConfigDict(from_attributes=True)


# =======================
# LÍNEA DE ORDEN
# =======================
TIPOS_LINEA = ("ACTIVO", "CONSUMIBLE", "LICENCIA", "OTRO")


class LineaCreate(Esquema):
    OCL_Descripcion: str = Field(..., min_length=1, max_length=255)
    OCL_Cantidad: int = Field(1, gt=0)
    OCL_Precio_Unitario: Decimal = Field(0, ge=0, max_digits=14, decimal_places=2)
    # Qué se compra: la recepción de la línea solo aceptará eso.
    OCL_Tipo: Optional[str] = Field(None, pattern="^(ACTIVO|CONSUMIBLE|LICENCIA|OTRO)$")
    ACT_Activo: Optional[uuid.UUID] = None
    CON_Consumible: Optional[int] = None
    LIC_Licencia: Optional[int] = None


class LineaResponse(Esquema):
    OCL_Linea: int
    OCL_Descripcion: str
    OCL_Cantidad: int
    OCL_Precio_Unitario: Decimal
    OCL_Subtotal: Decimal
    OCL_Tipo: Optional[str] = None
    OCL_Cantidad_Recibida: int = 0
    ACT_Activo: Optional[uuid.UUID] = None
    CON_Consumible: Optional[int] = None
    LIC_Licencia: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def OCL_Pendiente(self) -> int:
        return max(self.OCL_Cantidad - (self.OCL_Cantidad_Recibida or 0), 0)


# =======================
# ORDEN DE COMPRA
# =======================
class OrdenCreate(Esquema):
    OCO_Numero: str = Field(..., min_length=1, max_length=50)
    OCO_Fecha: date
    OCO_Moneda: CurrencyCode = DEFAULT_CURRENCY
    OCO_Notas: Optional[str] = Field(None, max_length=500)
    PRV_Proveedor: int
    # Sede que recibe la compra (alcance de datos). Los activos recibidos
    # nacen en esta sede.
    SED_Sede: Optional[int] = None
    lineas: List[LineaCreate] = Field(default_factory=list)


class OrdenEstadoUpdate(Esquema):
    OCO_Estado: str = Field(..., pattern="^(BORRADOR|PARCIAL|RECIBIDA|CERRADA_CON_FALTANTE|CANCELADA)$")
    # Motivo de la cancelación (se guarda en la orden).
    motivo: Optional[str] = Field(None, max_length=500)


class OrdenCierre(Esquema):
    """Cerrar una orden PARCIAL aceptando que lo pendiente no llegará."""
    motivo: str = Field(..., min_length=5, max_length=500)


# =======================
# RECEPCIÓN DE ORDEN (lazo cerrado: stock de consumibles + alta de activos)
# =======================
class RecepcionConsumible(Esquema):
    """Una línea que reabastece un consumible: suma `cantidad` a su stock."""
    OCL_Linea: int
    CON_Consumible: int
    cantidad: int = Field(..., gt=0)
    # Precio unitario real facturado (si se omite, el de la orden).
    precio_unitario: Optional[Decimal] = Field(None, ge=0, max_digits=14, decimal_places=2)


class RecepcionActivo(Esquema):
    """Una línea que se convierte en un activo nuevo (alta + enlace a la orden)."""
    OCL_Linea: int
    ACT_Codigo_Interno: Optional[str] = Field(None, min_length=3, max_length=50)
    ACT_Serie_Fabricante: str = Field(..., min_length=3, max_length=100)
    ACT_Hostname: Optional[str] = Field(None, max_length=100)
    MOD_Modelo: int
    TAC_Tipo_Activo: int
    ACT_Fecha_Compra: date
    ACT_Fin_Garantia: Optional[date] = None
    ACT_Costo: Optional[Decimal] = Field(None, ge=0, max_digits=12, decimal_places=2)


class RecepcionLicencia(Esquema):
    """Una linea que registra software/licencias, con claves individuales opcionales."""
    OCL_Linea: int
    SOF_Software: int
    TLI_Tipo_Licencia: int
    cantidad: Optional[int] = Field(None, gt=0)
    LIC_Fecha_Vencimiento: Optional[date] = None
    LIC_Clave_Activacion: Optional[str] = Field(None, max_length=255)
    claves: List[LicenciaClaveCreate] = Field(default_factory=list)
    # Precio unitario real facturado (si se omite, el de la orden).
    precio_unitario: Optional[Decimal] = Field(None, ge=0, max_digits=14, decimal_places=2)

    @model_validator(mode="after")
    def _cantidad_vs_claves(self):
        if self.claves:
            if self.cantidad is not None and self.cantidad != len(self.claves):
                raise ValueError("LICENSE_KEY_COUNT_MISMATCH")
            self.cantidad = len(self.claves)
        if not self.cantidad:
            raise ValueError("LICENSE_QUANTITY_REQUIRED")
        return self


class RecepcionOtro(Esquema):
    """Conformidad de una línea OTRO (flete, instalación, servicio): no genera inventario."""
    OCL_Linea: int
    cantidad: int = Field(..., gt=0)
    precio_unitario: Optional[Decimal] = Field(None, ge=0, max_digits=14, decimal_places=2)


class DiferenciaRecepcion(Esquema):
    """Justificación de una diferencia en una línea (el sistema la exige)."""
    OCL_Linea: int
    motivo: str = Field(..., min_length=5, max_length=500)
    # Recibir más de lo pendiente (solo con motivo).
    aceptar_sobrante: bool = False


class RecepcionOrden(Esquema):
    consumibles: List[RecepcionConsumible] = Field(default_factory=list)
    activos: List[RecepcionActivo] = Field(default_factory=list)
    licencias: List[RecepcionLicencia] = Field(default_factory=list)
    otros: List[RecepcionOtro] = Field(default_factory=list)
    # Guía de remisión o factura con que llegó la entrega.
    documento: Optional[str] = Field(None, max_length=100)
    observacion: Optional[str] = Field(None, max_length=500)
    diferencias: List[DiferenciaRecepcion] = Field(default_factory=list)


class PendienteLinea(Esquema):
    OCL_Linea: int
    descripcion: str
    tipo: Optional[str] = None
    pedido: int
    recibido: int
    pendiente: int


class DiferenciaRegistrada(Esquema):
    OCL_Linea: int
    tipo: str  # SOBRANTE | PRECIO
    detalle: str
    motivo: Optional[str] = None


class RecepcionResultado(Esquema):
    OCO_Orden: int
    OCO_Estado: str
    REC_Recepcion: Optional[int] = None
    consumibles_reabastecidos: int
    activos_creados: int
    licencias_creadas: int = 0
    licencias_claves_creadas: int = 0
    activos_codigos: List[str] = Field(default_factory=list)
    # Lo que falta por recibir tras esta entrega (vacío si quedó completa).
    pendientes: List[PendienteLinea] = Field(default_factory=list)
    diferencias: List[DiferenciaRegistrada] = Field(default_factory=list)
    # La entrega trae un sobrante: quedó pendiente de aprobación del Jefe de TI
    # (no se registró nada todavía).
    aprobacion_pendiente: Optional[int] = None


class RecepcionLineaResponse(Esquema):
    OCL_Linea: int
    RCL_Cantidad: int
    RCL_Precio_Real: Optional[Decimal] = None
    RCL_Diferencia: Optional[str] = None
    RCL_Motivo: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class RecepcionResponse(Esquema):
    REC_Recepcion: int
    OCO_Orden: int
    REC_Fecha: datetime
    REC_Documento: Optional[str] = None
    REC_Observacion: Optional[str] = None
    REC_Resultado: str
    USU_Usuario: Optional[uuid.UUID] = None
    lineas: List[RecepcionLineaResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class _ProveedorSummary(Esquema):
    PRV_Proveedor: int
    PRV_Nombre: str
    model_config = ConfigDict(from_attributes=True)


class OrdenResponse(Esquema):
    OCO_Orden: int
    OCO_Numero: str
    OCO_Fecha: date
    OCO_Estado: str
    OCO_Moneda: str
    OCO_Total: Decimal
    OCO_Notas: Optional[str] = None
    OCO_Motivo_Cierre: Optional[str] = None
    PRV_Proveedor: int
    SED_Sede: Optional[int] = None
    proveedor: Optional[_ProveedorSummary] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OrdenDetalleResponse(OrdenResponse):
    lineas: List[LineaResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


# =======================
# GARANTÍAS
# =======================
class GarantiaItem(Esquema):
    ACT_Activo: uuid.UUID
    ACT_Codigo_Interno: str
    ACT_Serie_Fabricante: str
    ACT_Fecha_Compra: date
    ACT_Fin_Garantia: Optional[date] = None
    dias_restantes: Optional[int] = None
    estado_garantia: str  # vigente | por_vencer | vencida | sin_garantia
    proveedor: Optional[str] = None
