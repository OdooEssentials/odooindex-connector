import sys

from . import core

if "odoo" in sys.modules:
    from . import models
    from . import wizards
