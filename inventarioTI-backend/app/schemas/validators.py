"""Validaciones compartidas de fechas para entradas de API."""
from datetime import date

from pydantic import model_validator


def validate_date_range(start_field: str, end_field: str):
    @model_validator(mode="after")
    def _validate(instance):
        start = getattr(instance, start_field, None)
        end = getattr(instance, end_field, None)
        if (start is None) != (end is None):
            raise ValueError(f"{start_field} y {end_field} deben enviarse juntos")
        if isinstance(start, date) and isinstance(end, date) and end < start:
            raise ValueError(f"{end_field} no puede ser anterior a {start_field}")
        return instance
    return _validate
