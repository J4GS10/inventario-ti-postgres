"""Ayudas para las pruebas: baja con aprobación del Jefe de TI."""


async def baja_aprobada(client, solicita_h, aprueba_h, activo_id, payload: dict):
    """Solicita la baja y la aprueba otra persona. Devuelve la respuesta de la aprobación."""
    r = await client.post(f"/api/v1/core/activos/{activo_id}/baja", headers=solicita_h, json=payload)
    if r.status_code != 202:
        return r
    return await client.post(f"/api/v1/aprobaciones/{r.json()['APR_Aprobacion']}/aprobar", headers=aprueba_h, json={})
