"""Compatibility alias for app.controllers.sync_logs.sync_log_dso.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.controllers.sync_logs import sync_log_dso as _implementation

sys.modules[__name__] = _implementation
