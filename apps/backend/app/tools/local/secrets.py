"""Secret-path denylist. Canonical path matching, not basename-only."""

import os
import re
from pathlib import PureWindowsPath

from ..contracts import ToolError

SECRET_NAME = re.compile(
    r"(?i)^(\.env(\..+)?|id_rsa|id_dsa|id_ecdsa|id_ed25519|.*\.pem|.*\.ppk|"
    r"login data|logins\.json|key4\.db|signons\.sqlite|cookies\.sqlite|"
    r".*\.(kdbx|kdb|1pif)|wallet\.dat|.*wallet.*\.(json|dat)|"
    r"credentials\.json|gdrive\.json|service-account.*\.json|"
    r"masterkey\.key|ntuser\.dat|sam|system\.hive)$"
)
SECRET_DIR = re.compile(
    r"(?i)([\\/](\.ssh|\.gnupg|\.aws|\.azure|password-store|1password|keepass|"
    r"google[\\/]chrome|microsoft[\\/]edge|mozilla[\\/]firefox|bravesoftware|"
    r"microsoft[\\/]credentials|microsoft[\\/]protect)[\\/])"
)
SECRET_FRAGMENT = re.compile(
    r"[\\/]appdata[\\/]roaming[\\/]microsoft[\\/]credentials|"
    r"[\\/]appdata[\\/]local[\\/]google[\\/]chrome[\\/]user data|"
    r"[\\/]appdata[\\/]roaming[\\/]mozilla[\\/]firefox|"
    r"[\\/]appdata[\\/]local[\\/]microsoft[\\/]edge[\\/]user data|"
    r"[\\/]\.ssh[\\/]|"
    r"[\\/]wallet",
    re.I,
)


def deny_secret(canonical: str):
    path = str(canonical or "")
    name = PureWindowsPath(path).name
    if SECRET_NAME.match(name) or SECRET_DIR.search(f"{os.sep}{path.strip('/\\')}{os.sep}"):
        raise ToolError("secret_path")
    lowered = path.casefold()
    if SECRET_FRAGMENT.search(lowered):
        raise ToolError("secret_path")
