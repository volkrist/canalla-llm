"""Secret-path denylist. Canonical path matching, not basename-only."""

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
    r"(?i)(\\(\.ssh|\\.gnupg|\\.aws|\\.azure|password-store|1password|keepass|"
    r"google\\chrome|microsoft\\edge|mozilla\\firefox|bravesoftware|"
    r"microsoft\\credentials|microsoft\\protect)\\)"
)


def deny_secret(canonical: str):
    path = canonical.replace("/", "\\")
    name = PureWindowsPath(path).name
    if SECRET_NAME.match(name) or SECRET_DIR.search("\\" + path.strip("\\") + "\\"):
        raise ToolError("secret_path")
    lowered = path.casefold()
    for fragment in (
        "\\appdata\\roaming\\microsoft\\credentials",
        "\\appdata\\local\\google\\chrome\\user data",
        "\\appdata\\roaming\\mozilla\\firefox",
        "\\appdata\\local\\microsoft\\edge\\user data",
        "\\.ssh\\",
        "\\wallet",
    ):
        if fragment in lowered:
            raise ToolError("secret_path")
