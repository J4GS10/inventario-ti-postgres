"""Objetos de dominio independientes de FastAPI y del motor SQL."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class AssetStatus(StrEnum):
    AVAILABLE = "disponible"
    WAREHOUSE = "en_bodega"
    ASSIGNED = "asignado"
    INSTALLED = "instalado"
    REPAIR = "en_reparacion"
    RETIRED = "baja"


class WorkflowAction(StrEnum):
    ASSIGN = "asignar"
    RETURN = "devolver"
    TRANSFER = "transferir"
    INSTALL = "instalar"
    REPAIR = "mantenimiento"
    RETIRE = "dar_de_baja"


@dataclass(frozen=True)
class DateRange:
    """Rango inclusivo validado, útil para reportes y filtros."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("La fecha final no puede ser anterior a la inicial")

    @classmethod
    def from_optional(cls, start: date | None, end: date | None) -> "DateRange | None":
        if start is None and end is None:
            return None
        if start is None or end is None:
            raise ValueError("Debe indicar ambas fechas del rango")
        return cls(start, end)


class WorkflowGuard:
    """Reglas puras de transición: se pueden probar sin BD ni HTTP."""

    _allowed: dict[AssetStatus, frozenset[WorkflowAction]] = {
        AssetStatus.AVAILABLE: frozenset({WorkflowAction.ASSIGN, WorkflowAction.INSTALL, WorkflowAction.REPAIR, WorkflowAction.RETIRE}),
        AssetStatus.WAREHOUSE: frozenset({WorkflowAction.ASSIGN, WorkflowAction.INSTALL, WorkflowAction.REPAIR, WorkflowAction.RETIRE}),
        AssetStatus.ASSIGNED: frozenset({WorkflowAction.RETURN, WorkflowAction.TRANSFER, WorkflowAction.REPAIR, WorkflowAction.RETIRE}),
        AssetStatus.INSTALLED: frozenset({WorkflowAction.RETURN, WorkflowAction.REPAIR, WorkflowAction.RETIRE}),
        AssetStatus.REPAIR: frozenset({WorkflowAction.RETURN, WorkflowAction.ASSIGN, WorkflowAction.RETIRE}),
        AssetStatus.RETIRED: frozenset(),
    }

    @classmethod
    def assert_allowed(cls, status: AssetStatus, action: WorkflowAction) -> None:
        if action not in cls._allowed.get(status, frozenset()):
            raise ValueError(f"La acción '{action}' no es válida para el estado '{status}'")

    @classmethod
    def actions_for(cls, status: AssetStatus) -> tuple[WorkflowAction, ...]:
        return tuple(sorted(cls._allowed.get(status, frozenset()), key=str))
