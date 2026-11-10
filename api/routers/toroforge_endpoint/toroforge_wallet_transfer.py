"""Compatibility alias for app.controllers.billing.toroforge_wallet_transfer.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.controllers.billing import toroforge_wallet_transfer as _implementation

sys.modules[__name__] = _implementation
