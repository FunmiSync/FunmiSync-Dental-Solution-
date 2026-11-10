"""Compatibility alias for app.services.auth.login_attempts.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.services.auth import login_attempts as _implementation

sys.modules[__name__] = _implementation
