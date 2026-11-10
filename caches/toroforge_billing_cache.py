"""Compatibility alias for app.infrastructure.cache.toroforge_billing_cache.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.infrastructure.cache import toroforge_billing_cache as _implementation

sys.modules[__name__] = _implementation
