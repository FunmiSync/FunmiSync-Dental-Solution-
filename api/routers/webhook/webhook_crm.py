"""Compatibility alias for app.controllers.webhooks.webhook_crm.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.controllers.webhooks import webhook_crm as _implementation

sys.modules[__name__] = _implementation
