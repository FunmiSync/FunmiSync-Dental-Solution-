"""Compatibility alias for app.controllers.registration.dso_registration.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.controllers.registration import dso_registration as _implementation

sys.modules[__name__] = _implementation
