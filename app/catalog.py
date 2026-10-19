"""Moved to spinmate_core.catalog in version 2.0; this name stays for the web app and old imports."""
import sys

from spinmate_core import catalog as _module

sys.modules[__name__] = _module
