"""Compatibility alias for app.config.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app import config as _implementation

sys.modules[__name__] = _implementation
