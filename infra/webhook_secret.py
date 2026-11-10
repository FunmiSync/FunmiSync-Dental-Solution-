"""Compatibility alias for app.security.webhook_secret.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.security import webhook_secret as _implementation

sys.modules[__name__] = _implementation
