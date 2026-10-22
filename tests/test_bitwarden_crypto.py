"""Tests for the Bitwarden crypto primitives.

Vectors: the RFC 5869 HKDF-SHA256 test case 1 (expand-only, as Bitwarden
uses it), a hand-computed PBKDF2 master key, plus round-trip and tamper
checks on the type-2 (AES-256-CBC + HMAC-SHA256) envelope.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from password_vault_sync.bitwarden_crypto import (
    CryptoError,
    decrypt_enc_string,
    derive_master_key,
    encrypt_string,
    hkdf_expand_sha256,
    rsa_oaep_sha256_decrypt,
    split_symmetric_key,
    stretch_master_key,
)

ITERATIONS = 600_000


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def test_hkdf_expand_rfc5869_case_1() -> None:
    # RFC 5869 test case 1: our function is expand-only, so the PRK from
    # the RFC's extract step is computed here by hand.
    ikm = bytes.fromhex("0b" * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = hkdf_expand_sha256(prk, info, 42)
    assert okm.hex() == ("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865")


def test_hkdf_expand_first_block_is_a_plain_hmac() -> None:
    out = hkdf_expand_sha256(b"\x01" * 32, b"enc", 32)
    assert out == hmac.new(b"\x01" * 32, b"enc\x01", hashlib.sha256).digest()


def test_hkdf_expand_multi_block_output() -> None:
    out = hkdf_expand_sha256(b"\x02" * 32, "mac", 64)
    assert len(out) == 64
    first = hmac.new(b"\x02" * 32, b"mac\x01", hashlib.sha256).digest()
    second = hmac.new(b"\x02" * 32, first + b"mac\x02", hashlib.sha256).digest()
    assert out == first + second


def test_hkdf_expand_rejects_oversized_output() -> None:
    with pytest.raises(CryptoError, match="too long"):
        hkdf_expand_sha256(b"\x01" * 32, b"enc", 255 * 32 + 1)


def test_derive_master_key_matches_reference_pbkdf2() -> None:
    expected = hashlib.pbkdf2_hmac("sha256", b"hunter2", b"user@example.com", ITERATIONS, dklen=32)
    assert derive_master_key("hunter2", "user@example.com", ITERATIONS) == expected


def test_derive_master_key_normalizes_email() -> None:
    # Bitwarden lowercases and trims the email before salting.
    a = derive_master_key("pw", "  User@Example.COM ", ITERATIONS)
    b = derive_master_key("pw", "user@example.com", ITERATIONS)
    assert a == b


def test_derive_master_key_rejects_bad_iteration_count() -> None:
    with pytest.raises(CryptoError, match="iteration"):
        derive_master_key("pw", "user@example.com", 0)


def test_stretch_master_key_is_enc_then_mac() -> None:
    master = derive_master_key("hunter2", "user@example.com", ITERATIONS)
    stretched = stretch_master_key(master)
    assert len(stretched) == 64
    enc_expected = hmac.new(master, b"enc\x01", hashlib.sha256).digest()
    mac_expected = hmac.new(master, b"mac\x01", hashlib.sha256).digest()
    assert stretched == enc_expected + mac_expected


def test_stretch_master_key_requires_32_bytes() -> None:
    with pytest.raises(CryptoError, match="32 bytes"):
        stretch_master_key(b"short")


def test_split_symmetric_key() -> None:
    key64 = bytes(range(64))
    assert split_symmetric_key(key64) == (key64[:32], key64[32:])
    # 32-byte keys (some org key generations) are used for both halves.
    key32 = bytes(range(32))
    assert split_symmetric_key(key32) == (key32, key32)
    with pytest.raises(CryptoError, match="32 or 64"):
        split_symmetric_key(b"nope")


def test_encrypt_decrypt_round_trip() -> None:
    key = stretch_master_key(derive_master_key("hunter2", "user@example.com", ITERATIONS))
    blob = encrypt_string("My Collection", key)
    assert blob.startswith("2.")
    assert decrypt_enc_string(blob, key) == b"My Collection"


def test_type2_layout_is_iv_data_mac() -> None:
    key = stretch_master_key(derive_master_key("hunter2", "user@example.com", ITERATIONS))
    blob = encrypt_string("hello", key)
    iv_s, data_s, mac_s = blob[2:].split("|")
    iv, data, tag = (base64.b64decode(part) for part in (iv_s, data_s, mac_s))
    assert len(iv) == 16
    assert len(tag) == 32
    assert len(data) % 16 == 0
    # The MAC covers iv || ciphertext exactly as Bitwarden defines it.
    _, mac_key = split_symmetric_key(key)
    assert tag == hmac.new(mac_key, iv + data, hashlib.sha256).digest()


def test_round_trip_with_32_byte_org_key() -> None:
    # Real organization keys are 32 raw bytes (used for both halves).
    key = bytes([0x42]) * 32
    blob = encrypt_string("Résumé — names with unicode ✓", key)
    assert decrypt_enc_string(blob, key).decode("utf-8") == "Résumé — names with unicode ✓"


def test_decrypt_rejects_tampered_ciphertext() -> None:
    key = bytes([0x07]) * 64
    blob = encrypt_string("Milwaukee", key)
    iv_s, data_s, mac_s = blob[2:].split("|")
    data = bytearray(base64.b64decode(data_s))
    data[0] ^= 0xFF
    forged = "2." + "|".join([iv_s, b64(bytes(data)), mac_s])
    with pytest.raises(CryptoError, match="MAC verification failed"):
        decrypt_enc_string(forged, key)


def test_decrypt_rejects_tampered_mac() -> None:
    key = bytes([0x07]) * 64
    blob = encrypt_string("Milwaukee", key)
    iv_s, data_s, mac_s = blob[2:].split("|")
    tag = bytearray(base64.b64decode(mac_s))
    tag[0] ^= 0xFF
    forged = "2." + "|".join([iv_s, data_s, b64(bytes(tag))])
    with pytest.raises(CryptoError, match="MAC verification failed"):
        decrypt_enc_string(forged, key)


def test_decrypt_rejects_wrong_key() -> None:
    blob = encrypt_string("secret", bytes([0x01]) * 64)
    with pytest.raises(CryptoError, match="MAC verification failed"):
        decrypt_enc_string(blob, bytes([0x02]) * 64)


def test_decrypt_rejects_unknown_type() -> None:
    with pytest.raises(CryptoError, match="Unsupported enc type '4'"):
        decrypt_enc_string("4.aGVsbG8=|aGVsbG8=|aGVsbG8=", bytes([0x01]) * 64)


def test_decrypt_rejects_malformed_inputs() -> None:
    key = bytes([0x01]) * 64
    with pytest.raises(CryptoError, match="Not an enc string"):
        decrypt_enc_string("plaintext", key)
    with pytest.raises(CryptoError, match="3 parts"):
        decrypt_enc_string("2.aGVsbG8=", key)
    with pytest.raises(CryptoError, match="IV must be 16 bytes"):
        decrypt_enc_string("2." + "|".join([b64(b"short"), b64(b"x" * 16), b64(b"y" * 32)]), key)


def test_decrypt_rejects_bad_pkcs7_padding() -> None:
    # Encrypt a block, then swap in ciphertext that unpads to garbage.
    key = bytes([0x03]) * 64
    iv = b"\x00" * 16
    data = b"\x00" * 16
    _, mac_key = split_symmetric_key(key)
    tag = hmac.new(mac_key, iv + data, hashlib.sha256).digest()
    blob = f"2.{b64(iv)}|{b64(data)}|{b64(tag)}"
    with pytest.raises(CryptoError, match="PKCS7"):
        decrypt_enc_string(blob, key)


def test_rsa_oaep_sha256_decrypt_round_trip() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    org_key = bytes([0x99]) * 32
    ciphertext = private_key.public_key().encrypt(
        org_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    assert rsa_oaep_sha256_decrypt(b64(ciphertext), der) == org_key


def test_rsa_oaep_decrypts_legacy_oaep_sha1_wrapped_key() -> None:
    """Older clients sealed type-4 payloads with OAEP-SHA1; the fallback must handle them."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    org_key = bytes([0x77]) * 32
    ciphertext = private_key.public_key().encrypt(
        org_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None),
    )
    assert rsa_oaep_sha256_decrypt(b64(ciphertext), der) == org_key


def test_rsa_oaep_sha256_decrypt_rejects_garbage() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with pytest.raises(CryptoError, match="SHA-256 and SHA-1"):
        rsa_oaep_sha256_decrypt(b64(b"\x01" * 256), der)


def test_rsa_oaep_sha256_decrypt_rejects_non_rsa_key() -> None:
    der = ed25519.Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with pytest.raises(CryptoError, match="RSA private key"):
        rsa_oaep_sha256_decrypt(b64(b"\x00" * 256), der)
