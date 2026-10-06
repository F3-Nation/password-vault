#!/usr/bin/env python3
import argparse
import logging
import os
import sys
from dotenv import load_dotenv
from vaultwarden_client import VaultwardenClient

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def main():
    parser = argparse.ArgumentParser(description="Invite emails to a Vaultwarden Collection.")
    parser.add_argument("--collection-id", required=True, help="Target Collection ID")
    parser.add_argument("--emails", nargs="+", required=True, help="Space-separated list of emails")
    parser.add_argument("--org-id", default=os.getenv("VAULTWARDEN_ORG_ID"), help="Organization ID")

    args = parser.parse_args()

    try:
        client = VaultwardenClient(org_id=args.org_id)
        client.invite_members(args.collection_id, args.emails)
    except Exception:
        sys.exit(1)

if __name__ == "__main__":
    main()