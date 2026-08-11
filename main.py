"""Entrypoint -- imports app/models/storage/handlers/panels so decorators register."""
import os
import sys

_EXT_DIR = os.path.dirname(os.path.abspath(__file__))
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

_LOCAL = ("app", "codes", "models", "shared", "psi_client", "storage", "handlers", "handlers_schedule", "panels")
for _mod in _LOCAL:
    sys.modules.pop(_mod, None)

from app import ext, chat  # noqa: E402,F401
import handlers  # noqa: E402,F401
import handlers_schedule  # noqa: E402,F401
import panels  # noqa: E402,F401
