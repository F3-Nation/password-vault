"""Client-side cryptography compatible with Bitwarden / Vaultwarden clients.

Everything here was verified against the Bitwarden CLI bundle (2026.9.1) and the
``bitwarden/clients`` sources, because Vaultwarden stores collection names exactly
as the client encrypted them and will render garbage for anything else:

* Enc strings of type ``2`` (``AesCbc256_HmacSha256_B64``) are laid out as
  ``2.<iv b64>|<data b64>|<mac b64>``: 16-byte IV, AES-256-CBC with PKCS7
  padding, and ``mac = HMAC-SHA256(mac_key, iv || data)`` (32 bytes).
* Symmetric keys are 64 bytes: ``encryption_key (32) || mac_key (32)``. A few
  keys (organization keys, depending on generation) can be 32 bytes, in which
  case the same bytes are used for both halves; callers verify by round-trip.
* Master keys are ``PBKDF2-SHA256(password, email.strip().lower(), iterations)``.
* The stretched master key is RFC 5869 HKDF-Expand (expand only — the raw PRK is
  passed in directly, there is no extract step) with info ``"enc"`` / ``"mac"``.
* Private keys and organization keys are RSA-2048; organization keys are wrapped
  with OAEP-SHA256 (enc type ``4``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

ENC_TYPE_CBC_HMAC_SHA256 = 2
ENC_TYPE_RSA_OAEP_SHA256 = 4


class CryptoError(ValueError):
    """Raised when an encrypted value cannot be parsed, verified, or decrypted."""


def hkdf_expand_sha256(prk: bytes, info: str | bytes, length: int) -> bytes:
    """RFC 5869 HKDF-Expand with SHA-256 (expand only; ``prk`` is used directly)."""
    info_bytes = info.encode("utf-8") if isinstance(info, str) else info
    if length > 255 * 32:
        raise CryptoError(f"HKDF-Expand output too long: {length}")
    blocks = b""
    previous = b""
    counter = 1
    while len(blocks) < length:
        previous = hmac.new(prk, previous + info_bytes + bytes([counter]), hashlib.sha256).digest()
        blocks += previous
        counter += 1
    return blocks[:length]


def derive_master_key(password: str, email: str, iterations: int) -> bytes:
    """PBKDF2-SHA256 master key; Bitwarden salts with the trimmed, lowercased email."""
    if iterations <= 0:
        raise CryptoError(f"Invalid PBKDF2 iteration count: {iterations}")
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), email.strip().lower().encode("utf-8"), iterations, dklen=32
    )


def stretch_master_key(master_key: bytes) -> bytes:
    """Expand a 32-byte master key into the 64-byte key used to wrap the user key."""
    if len(master_key) != 32:
        raise CryptoError(f"Master key must be 32 bytes, got {len(master_key)}")
    return hkdf_expand_sha256(master_key, "enc", 32) + hkdf_expand_sha256(master_key, "mac", 32)


def split_symmetric_key(key: bytes) -> tuple[bytes, bytes]:
    """Returns ``(encryption_key, mac_key)`` for a 64-byte key (32-byte keys map to both halves)."""
    if len(key) == 64:
        return key[:32], key[32:]
    if len(key) == 32:
        return key, key
    raise CryptoError(f"Symmetric key must be 32 or 64 bytes, got {len(key)}")


def _b64decode(segment: str, what: str) -> bytes:
    try:
        return base64.b64decode(segment)
    except Exception as exc:  # binascii.Error and friends
        raise CryptoError(f"Invalid base64 in {what}") from exc


def decrypt_enc_string(enc: str, key: bytes) -> bytes:
    """Decrypts a type-2 enc string (``2.iv|data|mac``) and verifies its MAC."""
    enc_key, mac_key = split_symmetric_key(key)
    if not isinstance(enc, str) or "." not in enc:
        raise CryptoError(f"Not an enc string: {enc!r}")
    header, _, body = enc.partition(".")
    if header != str(ENC_TYPE_CBC_HMAC_SHA256):
        raise CryptoError(f"Unsupported enc type {header!r} (only type 2 is symmetric)")
    pieces = body.split("|")
    if len(pieces) != 3:
        raise CryptoError(f"Type-2 enc string must have 3 parts, got {len(pieces)}")
    iv = _b64decode(pieces[0], "iv")
    data = _b64decode(pieces[1], "data")
    mac = _b64decode(pieces[2], "mac")
    if len(iv) != 16:
        raise CryptoError(f"IV must be 16 bytes, got {len(iv)}")
    expected = hmac.new(mac_key, iv + data, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise CryptoError("MAC verification failed (wrong key or corrupted data)")
    decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    return _pkcs7_unpad(padded)


def encrypt_string(plaintext: str | bytes, key: bytes) -> str:
    """Encrypts ``plaintext`` as a type-2 enc string (``2.iv|data|mac``)."""
    enc_key, mac_key = split_symmetric_key(key)
    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    iv = os.urandom(16)
    encryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).encryptor()
    ct = encryptor.update(_pkcs7_pad(data)) + encryptor.finalize()
    mac = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()
    b64 = base64.b64encode
    return f"{ENC_TYPE_CBC_HMAC_SHA256}.{b64(iv).decode()}|{b64(ct).decode()}|{b64(mac).decode()}"


def rsa_oaep_sha256_decrypt(ciphertext_b64: str, private_key_der: bytes) -> bytes:
    """RSA-2048 OAEP decryption (enc type 4), e.g. for organization keys.

    Modern clients seal type-4 payloads with OAEP-SHA256, but older Bitwarden
    clients used OAEP-SHA1 under the same type number; try both, SHA256 first,
    the way the official clients' legacy path does.
    """
    from cryptography.hazmat.primitives.serialization import load_der_private_key

    private_key = load_der_private_key(private_key_der, password=None)
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise CryptoError(f"Expected an RSA private key, got {type(private_key).__name__}")
    ciphertext = _b64decode(ciphertext_b64, "rsa ciphertext")
    for hash_alg in (hashes.SHA256(), hashes.SHA1()):
        try:
            plaintext = private_key.decrypt(
                ciphertext,
                padding.OAEP(mgf=padding.MGF1(algorithm=hash_alg), algorithm=hash_alg, label=None),
            )
        except ValueError:  # OAEP decode failure: wrong hash variant (or corrupt data)
            continue
        if isinstance(hash_alg, hashes.SHA1):
            # Not a vulnerability (OAEP's security needs preimage resistance, which
            # SHA-1 still provides), but worth surfacing so operators know the key
            # material is legacy-wrapped and could be re-wrapped by a modern client.
            logger.warning("RSA-OAEP payload was wrapped with legacy OAEP-SHA1; SHA-256 was tried first and failed.")
        return plaintext
    raise CryptoError("RSA-OAEP decryption failed with both SHA-256 and SHA-1 (corrupt or foreign key material)")


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    if not data or len(data) % block_size != 0:
        raise CryptoError("Invalid PKCS7 padding (bad length)")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size or data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise CryptoError("Invalid PKCS7 padding (bad bytes)")
    return data[:-pad_len]
