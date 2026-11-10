"""Compatibility alias for app.services.appointment_sync_log_helper.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.services import appointment_sync_log_helper as _implementation

sys.modules[__name__] = _implementation
