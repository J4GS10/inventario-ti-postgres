"""Quita costos y montos de las respuestas JSON para los roles que no deben verlos.

TECNICO y CONSULTA operan el inventario pero no ven valores: costo del activo,
costo unitario y saldo valorizado de consumibles, precios de órdenes y
recepciones, costo de licencias ni las valoraciones del tablero y los reportes.
El costo de cada mantenimiento sigue visible en su ticket (lo registra el
técnico), pero no el total acumulado del tablero.

Es un middleware ASGI puro: el rol lo deja `get_current_user` en
`scope["state"]["rol"]` y aquí se reescribe el cuerpo antes de enviarlo, así
ningún endpoint nuevo puede filtrar un costo por olvido.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.roles import HIDE_COST_ROLES

# Llaves con dinero en las respuestas de la API (modelos y reportes).
LLAVES_DINERO = frozenset({
    "ACT_Costo", "CON_Costo_Unitario", "LIC_Costo", "KDX_Costo", "KDX_Saldo_Valor",
    "OCL_Precio_Unitario", "RCL_Precio_Real", "precio_unitario", "precio_real",
    "valor_por_moneda", "costo_inventario_por_moneda", "costo_mantenimiento_por_moneda", "saldo_valor", "valor_saldo",
})


def quitar_dinero(dato: Any) -> Any:
    if isinstance(dato, dict):
        return {k: quitar_dinero(v) for k, v in dato.items() if k not in LLAVES_DINERO}
    if isinstance(dato, list):
        return [quitar_dinero(v) for v in dato]
    return dato


def oculta_costos(rol: str | None) -> bool:
    return rol in HIDE_COST_ROLES


def descartar_costos(schema, rol: str | None, *campos: str):
    """Quien no ve costos tampoco los escribe: el campo se ignora (como si no
    viniera), así una edición no borra el costo que registró un administrador."""
    if oculta_costos(rol):
        for campo in campos:
            if campo in schema.model_fields_set:
                setattr(schema, campo, None)
                schema.model_fields_set.discard(campo)
    return schema


class OcultarCostosMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        inicio: dict | None = None
        partes: list[bytes] = []

        async def enviar(mensaje):
            nonlocal inicio
            if mensaje["type"] == "http.response.start":
                rol = (scope.get("state") or {}).get("rol")
                tipo = dict(mensaje.get("headers") or []).get(b"content-type", b"")
                if oculta_costos(rol) and tipo.startswith(b"application/json"):
                    inicio = mensaje
                    return
                await send(mensaje)
                return
            if inicio is None or mensaje["type"] != "http.response.body":
                await send(mensaje)
                return
            partes.append(mensaje.get("body", b""))
            if mensaje.get("more_body"):
                return
            cuerpo = b"".join(partes)
            try:
                cuerpo = json.dumps(quitar_dinero(json.loads(cuerpo)), ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")
            except ValueError:
                pass
            encabezados = [(k, v) for k, v in inicio.get("headers", []) if k.lower() != b"content-length"]
            encabezados.append((b"content-length", str(len(cuerpo)).encode()))
            await send({**inicio, "headers": encabezados})
            await send({"type": "http.response.body", "body": cuerpo, "more_body": False})

        await self.app(scope, receive, enviar)
