from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from src.config import get_settings


class CredentialCryptoError(Exception):
    """A stored credential couldn't be decrypted - almost always means CREDENTIAL_ENCRYPTION_KEY
    changed after the data was written. Callers should treat this the same as "never connected"
    and prompt the user to reconnect, not surface it as a generic 500."""


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.credential_encryption_key:
        raise RuntimeError(
            'CREDENTIAL_ENCRYPTION_KEY is not set in .env. Generate one with: python -c '
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.credential_encryption_key.encode())


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise CredentialCryptoError("Could not decrypt stored credential") from exc
