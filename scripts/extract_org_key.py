#!/usr/bin/env python3
"""One-time extraction of the Vaultwarden organization key (run locally only).

The Cloud Run job cannot hold your master password, so the org key (which encrypts
collection *names*) is extracted once, here, with your master password, and stored
in Secret Manager as ``VAULTWARDEN_ORG_KEY``. After that the job encrypts and
decrypts names with pure Python and never sees a password again.

Flow:
  1. Log in to Vaultwarden with the API key already in ``.env``
     (falls back to a password grant if that response has no key material).
  2. Derive your master key from the master password you type (PBKDF2-SHA256),
     stretch it, decrypt the account key -> user key -> RSA private key.
  3. Read ``/api/sync`` and RSA-OAEP-SHA256 decrypt the target org's key.
  4. Verify: decrypt the org's collection names and cross-check one against the
     F3 API's org name (best effort).
  5. Print the base64 key plus ready-to-run ``gcloud`` commands.

Usage:
    uv run python scripts/extract_org_key.py                 # uses .env defaults
    uv run python scripts/extract_org_key.py --email you@x
    uv run python scripts/extract_org_key.py --apply-secret  # also push to GCP
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import subprocess
import sys
import uuid
from typing import Any, NoReturn

import requests
from dotenv import load_dotenv

from password_vault_sync.bitwarden_crypto import (
    CryptoError,
    decrypt_enc_string,
    derive_master_key,
    rsa_oaep_sha256_decrypt,
    stretch_master_key,
)
from password_vault_sync.config import DEFAULT_F3_API_URL

DEFAULT_PROJECT = "f3-passwords"
DEFAULT_SECRET = "vaultwarden-org-key"
TIMEOUT = 30


def die(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _post_token(url: str, data: dict, session: requests.Session) -> dict:
    resp = session.post(f"{url.rstrip('/')}/identity/connect/token", data=data, timeout=TIMEOUT)
    if not resp.ok:
        die(f"token request failed ({resp.status_code}): {resp.text.strip()}")
    return resp.json()


def login_with_api_key(url: str, client_id: str, client_secret: str, session: requests.Session) -> dict:
    return _post_token(
        url,
        {
            "grant_type": "client_credentials",
            "scope": "api",
            "client_id": client_id,
            "client_secret": client_secret,
            "device_identifier": str(uuid.uuid4()),
            "device_name": "password-vault-sync-extract",
            "device_type": "8",
        },
        session,
    )


def login_with_password(url: str, email: str, password: str, session: requests.Session) -> dict:
    return _post_token(
        url,
        {
            "grant_type": "password",
            "scope": "api",
            "client_id": "web",
            "username": email,
            "password": password,
            "device_identifier": str(uuid.uuid4()),
            "device_name": "password-vault-sync-extract",
            "device_type": "8",
        },
        session,
    )


def unlock_account_key(token: dict[str, Any], password: str, email: str) -> tuple[bytes, bytes]:
    """Returns ``(user_key, private_key_der)`` from a token's key material."""
    kdf = int(token.get("Kdf") or 0)
    if kdf != 0:
        die(
            f"this account uses KDF type {kdf} (Argon2); only PBKDF2 (0) is supported here. "
            "Extract the org key from a Bitwarden CLI session instead."
        )
    iterations = int(token.get("KdfIterations") or 600_000)
    key_field, private_field = token.get("Key"), token.get("PrivateKey")
    if not key_field or not private_field:
        die("token response has no Key/PrivateKey material")

    print(f"Deriving master key (PBKDF2-SHA256, {iterations} iterations)...", file=sys.stderr)
    master_key = derive_master_key(password, email, iterations)
    stretched = stretch_master_key(master_key)
    try:
        user_key = decrypt_enc_string(key_field, stretched)
        private_key_der = decrypt_enc_string(private_field, user_key)
    except CryptoError as exc:
        die(f"could not decrypt account keys (wrong master password or email?): {exc}")
    return user_key, private_key_der


def find_org(sync: dict[str, Any], org_id: str | None) -> dict[str, Any]:
    """Picks the target org from ``/api/sync`` (by id, or the only one)."""
    profile = sync.get("profile") or {}
    orgs = profile.get("organizations") or sync.get("organizations") or []
    if not orgs:
        die("no organizations on this account (are you an owner/admin of the target org?)")
    if org_id:
        for org in orgs:
            if str(org.get("id")) == org_id:
                return org
        die(f"org {org_id} not found on this account; available: {[o.get('id') for o in orgs]}")
    if len(orgs) == 1:
        return orgs[0]
    for i, org in enumerate(orgs, start=1):
        print(f"  [{i}] {org.get('id')}  {org.get('name')}", file=sys.stderr)
    choice = input("Org number: ").strip()
    try:
        return orgs[int(choice) - 1]
    except (ValueError, IndexError):
        die("invalid selection")


def decrypt_org_key(org: dict[str, Any], private_key_der: bytes) -> bytes:
    wrapped = org.get("key")
    if not wrapped:
        die(f"org {org.get('id')} has no key field")
    # The wrapped key is a Bitwarden enc string: type 4 = RSA-2048-OAEP-SHA256,
    # laid out as "4.<base64 ciphertext>". Tolerate a bare base64 value too.
    if "." in wrapped:
        header, _, body = wrapped.partition(".")
        if header != "4":
            die(f"unexpected org key enc type {header!r} (expected 4 = RSA-OAEP-SHA256)")
        wrapped = body
    try:
        org_key = rsa_oaep_sha256_decrypt(wrapped, private_key_der)
    except Exception as exc:  # noqa: BLE001 - cryptography raises several types
        die(f"RSA decryption of the org key failed: {exc}")
    if len(org_key) not in (32, 64):
        die(f"unexpected org key length: {len(org_key)} bytes")
    return org_key


def f3_org_names(f3_url: str) -> dict[str, str]:
    """Best-effort ``{org_id: name}`` map from the public F3 org endpoint."""
    names: dict[str, str] = {}
    url = f3_url.rstrip("/") + "/v1/org"
    page_index = 0
    while True:
        resp = requests.get(
            url,
            params=[("orgTypes", "region"), ("pageIndex", str(page_index)), ("pageSize", "100")],
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        chunk = payload.get("orgs") or []
        names.update({str(org["id"]): org.get("name") or "" for org in chunk})
        total = int(payload.get("total") or 0)
        if not chunk or len(names) >= total or page_index >= 20:
            break
        page_index += 1
    return names


def verify(sync: dict[str, Any], org: dict[str, Any], org_key: bytes, f3_url: str) -> bool:
    """Decrypts the org's collection names and cross-checks them against the F3 API."""
    collections = [c for c in (sync.get("collections") or []) if str(c.get("organizationId")) == str(org.get("id"))]
    if not collections:
        print("warning: no collections found for this org yet (run the orgs task first)", file=sys.stderr)
        return True

    try:
        f3_names = f3_org_names(f3_url)
    except Exception as exc:  # noqa: BLE001 - F3 cross-check is best effort
        print(f"warning: F3 API cross-check skipped ({exc})", file=sys.stderr)
        f3_names = {}

    ok = True
    for collection in collections:
        external_id = str(collection.get("externalId"))
        try:
            name = decrypt_enc_string(collection.get("name") or "", org_key).decode("utf-8")
        except CryptoError as exc:
            print(f"  [FAIL] externalId={external_id}: {exc}", file=sys.stderr)
            ok = False
            continue
        expected = f3_names.get(external_id)
        if expected is None:
            print(f"  [ok]   externalId={external_id} -> {name!r} (no F3 org to compare)", file=sys.stderr)
        elif expected.strip().lower() == name.strip().lower():
            print(f"  [ok]   externalId={external_id} -> {name!r} matches F3 {expected!r}", file=sys.stderr)
        else:
            print(
                f"  [FAIL] externalId={external_id} -> {name!r} but F3 says {expected!r}",
                file=sys.stderr,
            )
            ok = False
    return ok


def push_secret(project: str, secret: str, b64_key: str) -> None:
    """Creates the secret or adds a version, using the active gcloud account."""
    env = dict(os.environ)
    create = subprocess.run(
        ["gcloud", "secrets", "create", secret, "--data-file=-", "--project", project],
        input=b64_key,
        text=True,
        capture_output=True,
        env=env,
    )
    if create.returncode == 0:
        print(f"created secret {secret} in {project}", file=sys.stderr)
        return
    if "Already Exists" not in (create.stderr or ""):
        die(f"gcloud secrets create failed: {create.stderr.strip()}")
    add = subprocess.run(
        ["gcloud", "secrets", "versions", "add", secret, "--data-file=-", "--project", project],
        input=b64_key,
        text=True,
        capture_output=True,
        env=env,
    )
    if add.returncode != 0:
        die(f"gcloud secrets versions add failed: {add.stderr.strip()}")
    print(f"added new version to secret {secret} in {project}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.getenv("VAULTWARDEN_URL"), help="Vaultwarden base URL")
    parser.add_argument("--email", default=os.getenv("VW_EMAIL"), help="Your account email (PBKDF2 salt)")
    parser.add_argument("--org-id", default=os.getenv("VAULTWARDEN_ORG_ID"), help="Target organization id")
    parser.add_argument("--f3-url", default=DEFAULT_F3_API_URL, help="F3 API base URL for cross-checking names")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="GCP project for the secret")
    parser.add_argument("--secret", default=DEFAULT_SECRET, help="Secret Manager secret name")
    parser.add_argument(
        "--apply-secret",
        action="store_true",
        help="Also push the key to Secret Manager with gcloud (otherwise only the commands are printed).",
    )
    parser.add_argument("--skip-verify", action="store_true", help="Skip decrypting collections to verify.")
    args = parser.parse_args(argv)

    load_dotenv()  # pick up .env for URL / client credentials / org id
    args.url = args.url or os.getenv("VAULTWARDEN_URL")
    args.org_id = args.org_id or os.getenv("VAULTWARDEN_ORG_ID")
    client_id = os.getenv("VAULTWARDEN_CLIENT_ID")
    client_secret = os.getenv("VAULTWARDEN_CLIENT_SECRET")
    args.email = args.email or os.getenv("VW_EMAIL") or input("Account email: ").strip()
    if not args.url:
        die("no Vaultwarden URL (pass --url or set VAULTWARDEN_URL in .env)")
    if not args.email:
        die("no account email (pass --email)")

    password = getpass.getpass("Vaultwarden master password (never stored): ")
    if not password:
        die("empty master password")

    session = requests.Session()
    token: dict = {}
    if client_id and client_secret:
        token = login_with_api_key(args.url, client_id, client_secret, session)
    if not token.get("Key"):
        # Older/different responses: fall back to an interactive password grant.
        print("API key login had no key material; retrying with a password grant...", file=sys.stderr)
        token = login_with_password(args.url, args.email, password, session)

    user_key, private_key_der = unlock_account_key(token, password, args.email)

    headers = {"Authorization": f"Bearer {token['access_token']}"}
    resp = session.get(f"{args.url.rstrip('/')}/api/sync", headers=headers, timeout=TIMEOUT)
    if not resp.ok:
        die(f"/api/sync failed ({resp.status_code}): {resp.text.strip()}")
    sync = resp.json()

    org = find_org(sync, args.org_id or None)
    print(f"Target org: {org.get('id')} ({org.get('name')})", file=sys.stderr)
    org_key = decrypt_org_key(org, private_key_der)

    if not args.skip_verify:
        if not verify(sync, org, org_key, args.f3_url):
            die("verification failed - the derived key does not decrypt the collection names")

    b64_key = base64.b64encode(org_key).decode()
    print(f"\nVAULTWARDEN_ORG_KEY (base64, {len(org_key)} bytes):")
    print(b64_key)
    print(
        "\nStore it in Secret Manager:\n"
        f"  printf '%s' '{b64_key}' | gcloud secrets create {args.secret} "
        f"--data-file=- --project {args.project}\n"
        f"  # (already exists? use: gcloud secrets versions add {args.secret} "
        f"--data-file=- --project {args.project})"
    )
    if args.apply_secret:
        push_secret(args.project, args.secret, b64_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
