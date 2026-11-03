from cryptography.fernet import Fernet

from . import config

_fernet = Fernet(config.FERNET_KEY.encode())


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet.decrypt(value.encode()).decode()
