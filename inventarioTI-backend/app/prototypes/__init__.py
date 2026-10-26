"""Prototipos reutilizables de dominio.

Estos componentes son una zona segura para diseñar nuevas funcionalidades antes
de conectarlas a una API o a un motor de base de datos concreto.
"""

from .domain import AssetStatus, DateRange, WorkflowAction, WorkflowGuard

__all__ = ["AssetStatus", "DateRange", "WorkflowAction", "WorkflowGuard"]
