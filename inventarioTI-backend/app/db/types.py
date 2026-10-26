"""Tipos SQLAlchemy portables de la versión PostgreSQL."""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator


class PortableJSON(TypeDecorator[Any]):
    """JSON nativo (JSON/JSONB en PostgreSQL, JSON en SQLite para pruebas)."""

    impl = JSON
    cache_ok = True
