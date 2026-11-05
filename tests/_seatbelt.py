import unittest

from caduceus.checks import confinement_available

needs_seatbelt = unittest.skipUnless(confinement_available(), "macOS Seatbelt confinement is not usable here (absent or nested in another sandbox)")
