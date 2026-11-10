"""Compatibility alias for app.controllers.workspaces.dso_clinic_page.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.controllers.workspaces import dso_clinic_page as _implementation

sys.modules[__name__] = _implementation
