"""
Valoración de consumibles por COSTO PROMEDIO PONDERADO.

- Entrada con precio: nuevo promedio = (existencia × promedio + cantidad × precio)
  / (existencia + cantidad).
- Entrada sin precio: entra al promedio vigente (no lo altera).
- Salidas y ajustes: salen al promedio vigente (no lo alteran).
- Si no había existencias o costo conocido, el promedio pasa a ser el precio
  de la entrada.
- Cada consumible se valora en una sola moneda: no se promedian quetzales con
  dólares (no hay tipo de cambio oficial en el sistema). Una entrada en otra
  moneda solo se acepta cuando la existencia es cero.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException

_CUATRO = Decimal("0.0001")
_DOS = Decimal("0.01")


def _dec(valor) -> Decimal | None:
    return None if valor is None else Decimal(str(valor))


def entrada(stock_antes: int, promedio, moneda_valoracion: str | None, cantidad: int, precio,
            moneda_entrada: str | None) -> tuple[Decimal | None, str | None, Decimal | None]:
    """Devuelve (nuevo promedio, moneda de valoración, costo unitario del movimiento)."""
    promedio, precio = _dec(promedio), _dec(precio)
    if precio is None:
        return promedio, moneda_valoracion, promedio
    moneda = moneda_entrada or moneda_valoracion or "GTQ"
    existencia = max(stock_antes or 0, 0)
    if promedio is None or existencia == 0:
        return precio.quantize(_CUATRO, ROUND_HALF_UP), moneda, precio
    if moneda_valoracion and moneda != moneda_valoracion:
        raise HTTPException(400, f"CURRENCY_DIFFERS_FROM_VALUATION:{moneda_valoracion}")
    nuevo = (existencia * promedio + cantidad * precio) / (existencia + cantidad)
    return nuevo.quantize(_CUATRO, ROUND_HALF_UP), moneda, precio


def valor_saldo(saldo: int | None, promedio) -> Decimal | None:
    promedio = _dec(promedio)
    if saldo is None or promedio is None:
        return None
    return (Decimal(saldo) * promedio).quantize(_DOS, ROUND_HALF_UP)
