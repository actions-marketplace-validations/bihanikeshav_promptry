"""Route modules for the promptry dashboard API, split out of server.py.

Each module exposes a FastAPI ``router`` (an ``APIRouter``) that server.py
mounts onto the app. Handlers fetch storage via
``from promptry.dashboard.server import get_storage`` *inside* each function
(not at module import time) for two reasons: it avoids a circular import with
server.py (which imports these router modules), and it ensures a test that
monkeypatches ``promptry.dashboard.server.get_storage`` is honored by every
router, since the lookup happens fresh on each call.
"""
