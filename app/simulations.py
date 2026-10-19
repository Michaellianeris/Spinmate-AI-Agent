"""Moved to spinmate_core.simulations in version 2.0; this name stays for the web app and old imports."""
import sys

from spinmate_core import simulations as _module

sys.modules[__name__] = _module
