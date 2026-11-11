"""Compatibility alias for app.services.billing.money.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.services.billing import money as _implementation

sys.modules[__name__] = _implementation
