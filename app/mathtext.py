"""Moved to spinmate_core.mathtext in version 2.0; this name stays for the web app and old imports."""
import sys

from spinmate_core import mathtext as _module

sys.modules[__name__] = _module
