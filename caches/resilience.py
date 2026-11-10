"""Compatibility alias for app.infrastructure.cache.resilience.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.infrastructure.cache import resilience as _implementation

sys.modules[__name__] = _implementation
