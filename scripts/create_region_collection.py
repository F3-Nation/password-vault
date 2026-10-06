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
    parser = argparse.ArgumentParser(description="Create a Vaultwarden Region Collection.")
    parser.add_argument("--region-name", required=True, help="Name of the F3 Region")
    parser.add_argument("--postgres-id", required=True, help="Postgres DB ID for the region")
    parser.add_argument("--org-id", default=os.getenv("VAULTWARDEN_ORG_ID"), help="Organization ID")

    args = parser.parse_args()

    try:
        client = VaultwardenClient(org_id=args.org_id)
        client.create_collection(args.region_name, args.postgres_id)
    except Exception:
        sys.exit(1)

if __name__ == "__main__":
    main()