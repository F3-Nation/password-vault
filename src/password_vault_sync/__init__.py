"""Syncs the F3 Nation API (orgs/users) with a self-hosted Vaultwarden instance.

One Cloud Run Job, three modes (``--task orgs|users|all``):

* ``orgs``  — mirror F3 region orgs (``meta.password_vault_enabled``) to Vaultwarden collections.
* ``users`` — mirror F3 password roles to Vaultwarden collection membership/permissions.
* ``all``   — run orgs first, then users.
"""

__version__ = "1.1.2"
