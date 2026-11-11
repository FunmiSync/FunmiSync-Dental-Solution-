"""Compatibility alias for app.controllers.workspaces.team_members.

Edit the canonical module under app/; this path stays for existing imports/jobs.
Aliasing the module keeps class identities and patched globals shared.
"""
import sys
from app.controllers.workspaces import team_members as _implementation

sys.modules[__name__] = _implementation
