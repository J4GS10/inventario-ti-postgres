"""Motor de estados para el ciclo de vida de los activos."""
import unicodedata
from typing import Dict, Set
from app.models.enums import EstadoOperativoEnum
from app.core.errors import InvalidStateTransitionError


def _normalizar(valor: str) -> str:
    base = unicodedata.normalize("NFKD", valor or "")
    return "".join(ch for ch in base if not unicodedata.combining(ch)).strip().lower()


def estado_enum_de_nombre(nombre: str | None) -> EstadoOperativoEnum | None:
    """Nombre del catálogo (con o sin tildes) → estado canónico, o None si es propio."""
    if not nombre:
        return None
    buscado = _normalizar(nombre)
    for estado in EstadoOperativoEnum:
        if _normalizar(estado.value) == buscado:
            return estado
    return None


# Estados que se pueden elegir al dar de alta un activo o al editarlo a mano.
# Los demás (Asignado, En Reparación, En Tránsito, Baja) solo se alcanzan con
# su operación: asignar, abrir un ticket, trasladar o dar de baja.
ESTADOS_DE_ALMACEN = frozenset({EstadoOperativoEnum.DISPONIBLE, EstadoOperativoEnum.BODEGA})

class AssetStateMachine:
    """
    Define las transiciones permitidas entre estados operativos.
    Proporciona validación centralizada para evitar inconsistencias.
    """

    # Matriz de transiciones legales: { EstadoOrigen: {EstadosDestinoPermitidos} }
    TRANSITIONS: Dict[EstadoOperativoEnum, Set[EstadoOperativoEnum]] = {
        EstadoOperativoEnum.DISPONIBLE: {
            EstadoOperativoEnum.ASIGNADO,     # Asignación/Préstamo
            EstadoOperativoEnum.INSTALADO,    # Equipo compartido instalado en un área
            EstadoOperativoEnum.BODEGA,       # Movimiento interno
            EstadoOperativoEnum.REPARACION,   # Envío a taller
            EstadoOperativoEnum.BAJA,         # Retiro
            EstadoOperativoEnum.TRANSITO,     # Envío entre sedes
        },
        EstadoOperativoEnum.BODEGA: {
            EstadoOperativoEnum.ASIGNADO,
            EstadoOperativoEnum.INSTALADO,
            EstadoOperativoEnum.DISPONIBLE,
            EstadoOperativoEnum.REPARACION,
            EstadoOperativoEnum.BAJA,
            EstadoOperativoEnum.TRANSITO,
        },
        EstadoOperativoEnum.ASIGNADO: {
            EstadoOperativoEnum.BODEGA,       # Devolución
            EstadoOperativoEnum.DISPONIBLE,
            EstadoOperativoEnum.ASIGNADO,     # Transferencia directa entre personas
            EstadoOperativoEnum.TRANSITO,
            # Un equipo asignado puede ir a reparación sin cerrar su custodia:
            # al cerrar el ticket vuelve a "Asignado" con la misma persona.
            EstadoOperativoEnum.REPARACION,
        },
        EstadoOperativoEnum.INSTALADO: {
            EstadoOperativoEnum.INSTALADO,    # Reubicación a otra área
            EstadoOperativoEnum.BODEGA,       # Retiro del área
            EstadoOperativoEnum.DISPONIBLE,
            EstadoOperativoEnum.REPARACION,   # Al cerrar el ticket vuelve a «Instalado»
            EstadoOperativoEnum.TRANSITO,
        },
        EstadoOperativoEnum.REPARACION: {
            EstadoOperativoEnum.INSTALADO,
            EstadoOperativoEnum.BODEGA,       # Retorno de taller
            EstadoOperativoEnum.DISPONIBLE,
            EstadoOperativoEnum.BAJA,         # Si no tiene reparación
        },
        EstadoOperativoEnum.TRANSITO: {
            EstadoOperativoEnum.BODEGA,       # Llegada a destino
            EstadoOperativoEnum.DISPONIBLE,
        },
        EstadoOperativoEnum.BAJA: {
            # Se permite reversar la baja (por error humano) regresando a un estado de almacén.
            # Se recomienda que esta acción esté protegida por permisos de Admin en el endpoint.
            EstadoOperativoEnum.BODEGA,
            EstadoOperativoEnum.DISPONIBLE,
        }
    }

    @classmethod
    def is_transition_allowed(
        cls, current: EstadoOperativoEnum, target: EstadoOperativoEnum
    ) -> bool:
        """
        Verifica si la transición de un estado a otro es legal.
        """
        if current == target:
            return True
            
        allowed = cls.TRANSITIONS.get(current, set())
        return target in allowed

    @classmethod
    def validate_transition(
        cls, current: EstadoOperativoEnum, target: EstadoOperativoEnum
    ) -> None:
        """
        Valida la transición y lanza InvalidStateTransitionError si es ilegal.
        La capa de servicio traduce esta excepción de dominio a HTTPException 400.
        """
        if not cls.is_transition_allowed(current, target):
            raise InvalidStateTransitionError(current.value, target.value)
