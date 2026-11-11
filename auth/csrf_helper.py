"""Compatibility alias for app.security.csrf.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.security import csrf as _implementation

sys.modules[__name__] = _implementation
