"""Compatibility alias for app.infrastructure.workers.appointments.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.infrastructure.workers import appointments as _implementation

sys.modules[__name__] = _implementation
