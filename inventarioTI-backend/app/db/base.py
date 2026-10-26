from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """Clase Base pura. Cada modelo define su propio PK."""

#IMPORTANTE:
# Importamos todos los modelos aquí para asegurar que SQLAlchemy
# los registre antes de resolver las relaciones cruzadas.
# Esto soluciona el error "failed to locate name".

# 1. Organización y Ubicación (Independientes)
import app.models.organization  
import app.models.location      

# 2. Catálogos y Core (Interdependientes)
# Al importarlos aquí, el Registry de SQLAlchemy se llena correctamente.
import app.models.catalogs      
import app.models.catalogo_estandar
import app.models.core         

# 3. Resto de módulos
import app.models.software      
import app.models.traceability  
import app.models.governance
import app.models.kardex
import app.models.inventario_fisico
import app.models.aprobacion    

# 4. Unicidad sin distinguir mayúsculas ni espacios (índices funcionales):
#    se registran al crear las tablas (evita la importación circular).
from sqlalchemy import event as _event  # noqa: E402


def _registrar_unicidad(*_args, **_kwargs) -> None:
    from app.db.unicidad import registrar_indices
    registrar_indices()


_event.listen(Base.metadata, "before_create", _registrar_unicidad)
