"""Compatibility alias for app.models.entities.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.models import entities as _implementation

sys.modules[__name__] = _implementation
