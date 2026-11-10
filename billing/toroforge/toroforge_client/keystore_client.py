"""Compatibility alias for app.infrastructure.providers.toroforge.keystore_client.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.infrastructure.providers.toroforge import keystore_client as _implementation

sys.modules[__name__] = _implementation
