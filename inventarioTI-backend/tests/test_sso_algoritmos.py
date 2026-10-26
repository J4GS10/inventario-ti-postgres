"""
SSO OIDC: el id_token solo se acepta con firma asimétrica del JWKS del
proveedor; HS*/none (confusión de algoritmo) se rechazan antes de ir a la red.
"""
import pytest
from fastapi import HTTPException

from app.services.sso import SSOService


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [
    # HS256 firmado con la clave pública del JWKS (confusión de algoritmo).
    # Se arma a mano: PyJWT ya se niega a firmar HMAC con algo que parece un JWK.
    "eyJhbGciOiJIUzI1NiIsImtpZCI6ImsxIn0.eyJzdWIiOiJ4In0.ZmlybWEtZm9yamFkYS1jb24tbGEtY2xhdmUtcHVibGljYQ",
    # alg «none»: sin firma.
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJ4In0.",
    "no-es-un-jwt",
])
async def test_id_token_solo_acepta_firmas_asimetricas(token):
    with pytest.raises(HTTPException) as exc:
        await SSOService._verify_id_token(None, None, {"jwks_uri": "https://idp/keys"}, token, expected_nonce="n")
    assert exc.value.detail == "SSO_ID_TOKEN_INVALID"
