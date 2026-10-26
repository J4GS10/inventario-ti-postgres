from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.common import Esquema

# =======================
# 1. PAIS
# =======================
class PaisBase(Esquema):
    PAI_Nombre: str = Field(..., min_length=2, max_length=100)
    PAI_Codigo_ISO: str = Field(..., min_length=2, max_length=5)

class PaisCreate(PaisBase):
    pass

class PaisUpdate(Esquema):
    PAI_Nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    PAI_Codigo_ISO: Optional[str] = Field(None, min_length=2, max_length=5)

class PaisResponse(PaisBase):
    PAI_Pais: int
    model_config = ConfigDict(from_attributes=True)

# =======================
# 2. ESTADO
# =======================
class EstadoBase(Esquema):
    EST_Nombre: str = Field(..., min_length=2, max_length=100)
    PAI_Pais: int # FK

class EstadoCreate(EstadoBase):
    pass

class EstadoUpdate(Esquema):
    EST_Nombre: Optional[str] = Field(None, min_length=2, max_length=100)

class EstadoResponse(EstadoBase):
    EST_Estado: int
    model_config = ConfigDict(from_attributes=True)

# =======================
# 3. MUNICIPIO
# =======================
class MunicipioBase(Esquema):
    MUN_Nombre: str = Field(..., min_length=2, max_length=100)
    EST_Estado: int # FK

class MunicipioCreate(MunicipioBase):
    pass

class MunicipioUpdate(Esquema):
    MUN_Nombre: Optional[str] = Field(None, min_length=2, max_length=100)

class MunicipioResponse(MunicipioBase):
    MUN_Municipio: int
    model_config = ConfigDict(from_attributes=True)

# =======================
# 4. SEDE
# =======================
class SedeBase(Esquema):
    SED_Nombre: str = Field(..., min_length=3, max_length=150)
    SED_Direccion_Calle: Optional[str] = Field(None, max_length=200)
    SED_Direccion_Numero: Optional[str] = Field(None, max_length=50)
    MUN_Municipio: int # FK

class SedeCreate(SedeBase):
    pass

class SedeUpdate(Esquema):
    SED_Nombre: Optional[str] = Field(None, min_length=3, max_length=150)
    SED_Direccion_Calle: Optional[str] = Field(None, max_length=200)
    SED_Direccion_Numero: Optional[str] = Field(None, max_length=50)

class SedeResponse(SedeBase):
    SED_Sede: int
    model_config = ConfigDict(from_attributes=True)

# =======================
# 5. EDIFICIO
# =======================
class EdificioBase(Esquema):
    EDI_Nombre: str = Field(..., min_length=1, max_length=100)
    SED_Sede: int # FK

class EdificioCreate(EdificioBase):
    pass

class EdificioUpdate(Esquema):
    EDI_Nombre: Optional[str] = Field(None, min_length=1, max_length=100)

class EdificioResponse(EdificioBase):
    EDI_Edificio: int
    model_config = ConfigDict(from_attributes=True)

# =======================
# 6. NIVEL (Piso)
# =======================
class NivelBase(Esquema):
    NIV_Numero_Piso: str = Field(..., min_length=1, max_length=50)
    NIV_Alias: Optional[str] = Field(None, max_length=100)
    EDI_Edificio: int # FK

class NivelCreate(NivelBase):
    pass

class NivelUpdate(Esquema):
    NIV_Numero_Piso: Optional[str] = Field(None, min_length=1, max_length=50)
    NIV_Alias: Optional[str] = Field(None, max_length=100)

class NivelResponse(NivelBase):
    NIV_Nivel: int
    model_config = ConfigDict(from_attributes=True)

# =======================
# 7. AREA (Oficina Final)
# =======================
class AreaBase(Esquema):
    ARE_Nombre: str = Field(..., min_length=2, max_length=150)
    ARE_Tipo_Acceso: str = "General"
    ARE_Descripcion: Optional[str] = Field(None, max_length=255)
    NIV_Nivel: int # FK
    # Tipo de área del catálogo (BODEGA, OFICINA, REMOTO…).
    TAR_Tipo_Area: Optional[int] = None

class AreaCreate(AreaBase):
    pass

class AreaUpdate(Esquema):
    ARE_Nombre: Optional[str] = Field(None, min_length=2, max_length=150)
    ARE_Tipo_Acceso: Optional[str] = None
    ARE_Descripcion: Optional[str] = Field(None, max_length=255)
    TAR_Tipo_Area: Optional[int] = None

class AreaResponse(AreaBase):
    ARE_Area: int
    model_config = ConfigDict(from_attributes=True)