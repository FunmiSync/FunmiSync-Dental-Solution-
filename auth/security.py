"""Compatibility alias for app.security.encryption.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.security import encryption as _implementation

sys.modules[__name__] = _implementation
