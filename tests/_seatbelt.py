import sys
import unittest

from caduceus.checks import confinement_available

needs_confinement = unittest.skipUnless(confinement_available(), "no usable confinement here (Seatbelt on macOS, Landlock on Linux; absent or nested in another sandbox)")
needs_seatbelt = unittest.skipUnless(sys.platform == "darwin" and confinement_available(), "macOS Seatbelt is not usable here")
