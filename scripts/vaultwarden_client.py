import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


class VaultwardenClient:
    """Client wrapper abstracting Bitwarden CLI (`bw`) commands for Vaultwarden."""

    def __init__(self, org_id: str | None = None, session: str | None = None):
        resolved_org_id = org_id or os.getenv("VAULTWARDEN_ORG_ID")
        resolved_session = session or os.getenv("BW_SESSION")

        if not resolved_org_id:
            raise ValueError("Organization ID ('org_id') is required.")
        if not resolved_session:
            raise ValueError("Bitwarden CLI session token ('BW_SESSION') is missing or invalid.")

        self.org_id = resolved_org_id
        self.session = resolved_session

    def _run_cmd(self, cmd: list[str], input_data: str | None = None) -> str:
        """Executes a `bw` CLI command with session environment variables."""
        env = os.environ.copy()
        env["BW_SESSION"] = self.session

        try:
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"Bitwarden CLI command failed: {' '.join(cmd)}")
            logger.error(f"Error output (stderr): {e.stderr}")
            logger.error(f"Standard output (stdout): {e.stdout}")
            raise RuntimeError(f"CLI command failed: {e.stderr}") from e

    def create_collection(self, region_name: str, postgres_id: str) -> dict:
        """Creates an encrypted collection mapped to a Region name and Postgres external ID."""
        raw_template = self._run_cmd(["bw", "get", "template", "org-collection"])
        template = json.loads(raw_template)

        # Populate fields and clear dummy UUID placeholders
        template["organizationId"] = self.org_id
        template["name"] = region_name
        template["externalId"] = str(postgres_id)
        template["groups"] = []
        template["users"] = []

        payload_json = json.dumps(template)
        encoded_data = self._run_cmd(["bw", "encode"], input_data=payload_json)

        response_json = self._run_cmd(
            ["bw", "create", "org-collection", encoded_data, "--organizationid", self.org_id]
        )

        data = json.loads(response_json)
        logger.info(
            f"Collection created successfully | Name: '{region_name}' | "
            f"Collection ID: {data.get('id')} | External ID: {postgres_id}"
        )
        return data

    def invite_members(
        self, collection_id: str, emails: list[str], read_only: bool = False, role_type: int = 2
    ) -> None:
        """Invites members to the organization with access to a specific collection."""
        raw_template = self._run_cmd(["bw", "get", "template", "org-member"])

        for email in emails:
            template = json.loads(raw_template)
            template["organizationId"] = self.org_id
            template["emails"] = [email]
            template["type"] = role_type  # 2 = Standard User (Member)
            template["accessAll"] = False
            template["collections"] = [
                {
                    "id": collection_id,
                    "readOnly": read_only,
                    "hidePasswords": False,
                }
            ]
            # Clear out groups placeholder if present in template
            if "groups" in template:
                template["groups"] = []

            payload_json = json.dumps(template)
            encoded_data = self._run_cmd(["bw", "encode"], input_data=payload_json)

            self._run_cmd(
                ["bw", "create", "org-member", encoded_data, "--organizationid", self.org_id]
            )
            logger.info(f"Invited '{email}' to collection ID '{collection_id}'.")