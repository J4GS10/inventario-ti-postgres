from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, model_validator
from app.schemas.common import Esquema
from decimal import Decimal
from app.core.currency import CurrencyCode
from datetime import date, datetime
import uuid

# --- TIPO LICENCIA ---
class TipoLicenciaBase(Esquema):
    TLI_Nombre: str = Field(..., min_length=2, max_length=50)
    TLI_Descripcion: Optional[str] = Field(None, max_length=200)

class TipoLicenciaCreate(TipoLicenciaBase):
    pass

class TipoLicenciaUpdate(Esquema):
    TLI_Nombre: Optional[str] = Field(None, min_length=2, max_length=50)
    TLI_Descripcion: Optional[str] = Field(None, max_length=200)

class TipoLicenciaResponse(TipoLicenciaBase):
    TLI_Tipo_Licencia: int
    model_config = ConfigDict(from_attributes=True)

# --- SOFTWARE ---
class SoftwareBase(Esquema):
    SOF_Nombre: str = Field(..., min_length=2, max_length=100)
    SOF_Version: Optional[str] = Field(None, max_length=50)
    SOF_Fabricante: str = Field(..., min_length=2, max_length=100)
    # Fabricante como marca del catálogo (fija SOF_Fabricante con su nombre).
    MAR_Marca: Optional[int] = None

class SoftwareCreate(SoftwareBase):
    # Con MAR_Marca el texto del fabricante es opcional.
    SOF_Fabricante: str = Field("", max_length=100)

class SoftwareUpdate(Esquema):
    SOF_Nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    SOF_Version: Optional[str] = Field(None, max_length=50)
    SOF_Fabricante: Optional[str] = Field(None, min_length=2, max_length=100)
    MAR_Marca: Optional[int] = None

class SoftwareResponse(SoftwareBase):
    SOF_Software: int
    model_config = ConfigDict(from_attributes=True)

# --- LICENCIA (Inventario) ---
class LicenciaClaveCreate(Esquema):
    LCL_Clave_Activacion: str = Field(..., min_length=1, max_length=255)
    LCL_Referencia: Optional[str] = Field(None, max_length=120)


class LicenciaClaveResponse(Esquema):
    LCL_Licencia_Clave: int
    LIC_Licencia: int
    LCL_Clave_Activacion: str
    LCL_Referencia: Optional[str] = None
    LCL_Estado: str
    LCL_Creado_En: Optional[datetime] = None
    LCL_Asignada_En: Optional[date] = None
    LCL_Destino_Tipo: Optional[str] = None
    LCL_Destino_Id: Optional[str] = None
    LCL_Destino_Nombre: Optional[str] = None
    LCL_Destino_Detalle: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# --- CREDENCIAL / CUPO (activación variable) ---
class CredencialIn(Esquema):
    """Una cuenta, serial, key… Los campos que lleva los decide el método de activación."""
    usuario: Optional[str] = Field(None, max_length=255)
    secreto: Optional[str] = Field(None, max_length=500)
    serial: Optional[str] = Field(None, max_length=255)
    clave: Optional[str] = Field(None, max_length=255)
    servidor: Optional[str] = Field(None, max_length=255)
    archivo: Optional[uuid.UUID] = None
    dispositivo: Optional[str] = Field(None, max_length=120)
    referencia: Optional[str] = Field(None, max_length=120)
    vence: Optional[date] = None


class CredencialesLote(Esquema):
    credenciales: List[CredencialIn] = Field(..., min_length=1, max_length=500)


class CredencialUpdate(Esquema):
    """Datos visibles de la credencial (el secreto y la key se cambian con «rotar»)."""
    usuario: Optional[str] = Field(None, max_length=255)
    serial: Optional[str] = Field(None, max_length=255)
    servidor: Optional[str] = Field(None, max_length=255)
    dispositivo: Optional[str] = Field(None, max_length=120)
    referencia: Optional[str] = Field(None, max_length=120)
    vence: Optional[date] = None
    estado: Optional[str] = Field(None, pattern="^(DISPONIBLE|SUSPENDIDA|BAJA)$")


class CredencialRotar(Esquema):
    secreto: Optional[str] = Field(None, min_length=1, max_length=500)
    clave: Optional[str] = Field(None, min_length=1, max_length=255)


class LicenciaBase(Esquema):
    LIC_Clave_Activacion: Optional[str] = Field(None, description="Serial Key")
    LIC_Fecha_Vencimiento: Optional[date] = None
    LIC_Cantidad_Total: int = Field(..., gt=0, description="Total de asientos comprados")
    # Costo de la licencia (o de su renovación) en la moneda en que se paga.
    LIC_Costo: Optional[Decimal] = Field(None, ge=0, max_digits=14, decimal_places=2)
    LIC_Moneda: Optional[CurrencyCode] = None
    
    # FKs
    SOF_Software: int
    # Tipo anterior (alias del modelo comercial): basta uno de los dos.
    TLI_Tipo_Licencia: Optional[int] = None
    MCO_Modelo_Comercial: Optional[int] = None
    MAC_Metodo_Activacion: Optional[int] = None
    FCO_Forma_Conteo: Optional[int] = None
    LIC_Proveedor: Optional[int] = None
    LIC_Fecha_Inicio: Optional[date] = None
    LIC_Renovacion_Automatica: bool = False
    LIC_Notas: Optional[str] = Field(None, max_length=1000)

class LicenciaCreate(LicenciaBase):
    # Cota SOLO en la entrada: el texto plano cifrado debe caber en la columna
    # (500, ciphertext). La base/respuesta quedan laxas para no romper la
    # lectura de claves legadas descifradas más largas.
    LIC_Clave_Activacion: Optional[str] = Field(None, max_length=255, description="Serial Key")
    LIC_Claves: List[LicenciaClaveCreate] = Field(default_factory=list)
    # Credenciales según el método de activación (cuentas, seriales, servidor…).
    LIC_Credenciales: List[CredencialIn] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _cantidad_coherente_con_claves(self):
        if self.LIC_Claves and self.LIC_Cantidad_Total != len(self.LIC_Claves):
            raise ValueError("LICENSE_KEY_COUNT_MISMATCH")
        if self.TLI_Tipo_Licencia is None and self.MCO_Modelo_Comercial is None:
            raise ValueError("LICENSE_COMMERCIAL_MODEL_REQUIRED")
        return self

class LicenciaUpdate(Esquema):
    LIC_Clave_Activacion: Optional[str] = Field(None, max_length=255)
    LIC_Fecha_Vencimiento: Optional[date] = None
    LIC_Cantidad_Total: Optional[int] = Field(None, gt=0)
    LIC_Costo: Optional[Decimal] = Field(None, ge=0, max_digits=14, decimal_places=2)
    LIC_Moneda: Optional[CurrencyCode] = None
    MCO_Modelo_Comercial: Optional[int] = None
    MAC_Metodo_Activacion: Optional[int] = None
    FCO_Forma_Conteo: Optional[int] = None
    LIC_Proveedor: Optional[int] = None
    LIC_Fecha_Inicio: Optional[date] = None
    LIC_Renovacion_Automatica: Optional[bool] = None
    LIC_Notas: Optional[str] = Field(None, max_length=1000)

class LicenciaResponse(LicenciaBase):
    LIC_Licencia: int
    LIC_Cantidad_Usada: int # Campo calculado/gestionado por el sistema
    LIC_Claves_Total: int = 0
    LIC_Claves_Disponibles: int = 0
    LIC_Claves_Asignadas: int = 0
    model_config = ConfigDict(from_attributes=True)

# --- INSTALACIÓN (Asignación) ---
class InstalacionBase(Esquema):
    ACT_Activo: Optional[uuid.UUID] = None
    PER_Persona: Optional[uuid.UUID] = None
    LIC_Licencia: int
    LCL_Licencia_Clave: Optional[int] = None
    INS_Fecha_Instalacion: date = Field(default_factory=date.today)

    @model_validator(mode="after")
    def _destino_xor(self):
        if (self.ACT_Activo is None) == (self.PER_Persona is None):
            raise ValueError("INSTALLATION_TARGET_XOR_REQUIRED")
        return self

class InstalacionCreate(InstalacionBase):
    # Solo al desinstalar: por qué se retira (queda en el historial).
    motivo: Optional[str] = Field(None, max_length=255)

class InstalacionResponse(InstalacionBase):
    INS_Instalacion: int
    INS_Estado: bool
    INS_Fecha_Retiro: Optional[datetime] = None
    INS_Motivo_Retiro: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class _SoftwareSummary(Esquema):
    SOF_Software: int
    SOF_Nombre: str
    SOF_Version: Optional[str] = None
    SOF_Fabricante: str
    model_config = ConfigDict(from_attributes=True)


class _TipoLicenciaSummary(Esquema):
    TLI_Tipo_Licencia: int
    TLI_Nombre: str
    model_config = ConfigDict(from_attributes=True)


class _LicenciaWithSoftware(Esquema):
    LIC_Licencia: int
    LIC_Fecha_Vencimiento: Optional[date] = None
    LIC_Cantidad_Total: int
    LIC_Cantidad_Usada: int
    software: Optional[_SoftwareSummary] = None
    tipo_licencia: Optional[_TipoLicenciaSummary] = None
    model_config = ConfigDict(from_attributes=True)


class InstalacionDetalleResponse(InstalacionResponse):
    """Instalación con la licencia y software anidados (para mostrar en UI)."""
    licencia: Optional[_LicenciaWithSoftware] = None
    model_config = ConfigDict(from_attributes=True)
