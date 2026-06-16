from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.core.errors import AuthenticationError
from app.core.security import get_current_user


@pytest.fixture
def mock_settings():
    with patch("app.core.security.settings") as mock_settings:
        mock_settings.AUTH_BYPASS_ENABLED = False
        mock_settings.is_production = True
        mock_settings.SUPABASE_URL = "https://test.supabase.co"
        yield mock_settings


@pytest.fixture
def mock_jwks():
    with patch("app.core.security.get_jwks_client") as mock_get_client:
        mock_client = MagicMock()
        mock_key = MagicMock()
        mock_key.key = "secret"
        mock_client.get_signing_key_from_jwt.return_value = mock_key
        mock_get_client.return_value = mock_client
        yield mock_get_client


@pytest.mark.asyncio
async def test_missing_bearer(mock_settings):
    with pytest.raises(AuthenticationError):
        await get_current_user(MagicMock(), None)


@pytest.mark.asyncio
async def test_invalid_token(mock_settings, mock_jwks):
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid_token")
    with patch("app.core.security.jwt.decode", side_effect=jwt.InvalidTokenError):
        with pytest.raises(AuthenticationError):
            await get_current_user(MagicMock(), creds)


@pytest.mark.asyncio
async def test_expired_token(mock_settings, mock_jwks):
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="expired_token")
    with patch("app.core.security.jwt.decode", side_effect=jwt.ExpiredSignatureError):
        with pytest.raises(AuthenticationError):
            await get_current_user(MagicMock(), creds)


@pytest.mark.asyncio
async def test_missing_sub(mock_settings, mock_jwks):
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    with patch("app.core.security.jwt.decode", return_value={"role": "authenticated"}):
        with pytest.raises(AuthenticationError):
            await get_current_user(MagicMock(), creds)


@pytest.mark.asyncio
async def test_invalid_role(mock_settings, mock_jwks):
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    with patch("app.core.security.jwt.decode", return_value={"sub": "123", "role": "anon"}):
        with pytest.raises(AuthenticationError):
            await get_current_user(MagicMock(), creds)


@pytest.mark.asyncio
async def test_valid_jwt(mock_settings, mock_jwks):
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    with patch(
        "app.core.security.jwt.decode",
        return_value={"sub": "123", "role": "authenticated", "email": "test@test.com"},
    ):
        user = await get_current_user(MagicMock(), creds)
        assert user.user_id == "123"
        assert user.email == "test@test.com"
        assert user.is_dev is False


@pytest.mark.asyncio
async def test_auth_bypass_dev():
    with patch("app.core.security.settings") as mock_settings:
        mock_settings.AUTH_BYPASS_ENABLED = True
        mock_settings.is_production = False
        mock_settings.DEV_USER_ID = "dev"
        mock_settings.DEV_USER_EMAIL = "dev"
        user = await get_current_user(MagicMock(), None)
        assert user.user_id == "dev"
        assert user.is_dev is True
