"""Compatibility alias for app.services.team_member_service.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.services import team_member_service as _implementation

sys.modules[__name__] = _implementation
