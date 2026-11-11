"""Compatibility alias for app.infrastructure.cache.dso_clinic_page_cache.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.infrastructure.cache import dso_clinic_page_cache as _implementation

sys.modules[__name__] = _implementation
