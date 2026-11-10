"""Compatibility alias for app.services.clinic_health.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.services import clinic_health as _implementation

sys.modules[__name__] = _implementation
