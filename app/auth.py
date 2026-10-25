"""Who is asking, and which document groups they may see.

entra: Azure Container Apps / App Service built-in authentication ("Easy Auth") signs the user in
       with Microsoft Entra ID and forwards their identity in the X-MS-CLIENT-PRINCIPAL header.
       Group IDs come from the "groups" claim; map them to friendly names with GROUP_MAP below.
demo:  the UI sends X-Demo-User, so you can show role-based access without an Entra tenant.
"""

from __future__ import annotations

import base64
import json

from fastapi import HTTPException, Request

from .config import get_settings
from .models import User

DEMO_USERS = {
    "Asha (employee)": ["employee"],
    "Ravi (engineer)": ["employee", "engineering"],
    "Admin": ["employee", "engineering", "admin"],
}

# Entra group object IDs -> group names used in access.json. Fill in after creating groups in Entra ID.
GROUP_MAP: dict[str, str] = {}


def parse_client_principal(header: str, admin_users: str = "") -> User:
    principal = json.loads(base64.b64decode(header))
    claims = principal.get("claims", [])

    def claim(*types: str) -> str:
        return next((c["val"] for c in claims if c["typ"] in types), "")

    email = claim("preferred_username", "email",
                  "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress").lower()
    name = claim("name", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name") or email or "unknown"
    groups = [GROUP_MAP.get(c["val"], c["val"]) for c in claims if c["typ"] == "groups"]
    admins = {a.strip().lower() for a in admin_users.split(",") if a.strip()}
    if email and email in admins:
        groups += ["engineering", "admin"]
    return User(name=name, groups=["employee", *groups])


def current_user(request: Request) -> User:
    settings = get_settings()
    if settings.auth_mode == "entra":
        header = request.headers.get("x-ms-client-principal")
        if not header:
            raise HTTPException(status_code=401, detail="Sign-in required")
        return parse_client_principal(header, settings.admin_users)
    # Header from the UI's fetch calls; query parameter for plain links such as citation file links.
    name = request.headers.get("x-demo-user") or request.query_params.get("as") or "Asha (employee)"
    return User(name=name, groups=DEMO_USERS.get(name, ["employee"]))
