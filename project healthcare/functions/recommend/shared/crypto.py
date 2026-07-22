"""Application-layer encryption for third-party secrets this app stores at
rest — specifically OAuth tokens from wearable/EHR integrations
(DeviceAuthorizations table). This is separate from and in addition to Azure
SQL's own encryption (TDE) — defense in depth for the one place this app
holds a credential to an external system, not just its own data.

Uses Fernet (AES-128-CBC + HMAC, from the `cryptography` package). The key
comes from config.PHI_ENCRYPTION_KEY (sourced from Key Vault in a real
deployment, secret name "encryption-key-phi" — see infra/README.md). If unset
(local/demo default), encrypt()/decrypt() return None rather than silently
falling back to plaintext storage — callers must treat that as "cannot
store/read this token yet", not paper over it.
"""
from cryptography.fernet import Fernet, InvalidToken

from . import config


def _fernet() -> Fernet | None:
    if not config.PHI_ENCRYPTION_KEY:
        return None
    try:
        return Fernet(config.PHI_ENCRYPTION_KEY.encode())
    except (ValueError, TypeError):
        return None


def encrypt(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    f = _fernet()
    if not f:
        return None
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    f = _fernet()
    if not f:
        return None
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return None


def generate_key() -> str:
    """Utility for provisioning: generates a new Fernet key to store in Key
    Vault as PHI_ENCRYPTION_KEY. Not called by any request path."""
    return Fernet.generate_key().decode()
