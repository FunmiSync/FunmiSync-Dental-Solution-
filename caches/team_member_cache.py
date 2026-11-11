"""Compatibility alias for app.infrastructure.cache.team_member_cache.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.infrastructure.cache import team_member_cache as _implementation

sys.modules[__name__] = _implementation
