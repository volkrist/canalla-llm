"""Local backup, verification and restore (0.9.3 → 1.0 upgrade safety).

* ``format``  — paths, manifest, hashing, validation rules (no I/O side effects)
* ``archive`` — create / verify / list / retention for one data root
* ``restore`` — transactional restore that runs while the backend is stopped
* ``routes``  — the authenticated HTTP surface (create/list/verify/diagnose)
"""

from .archive import BackupService, verify_backup
from .format import BACKUP_FORMAT_VERSION, BackupError

__all__ = ["BackupError", "BackupService", "BACKUP_FORMAT_VERSION", "verify_backup"]
