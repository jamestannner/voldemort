"""Django-aware dead-code detection, built on Vulture."""

from voldemort.core import Voldemort
from voldemort.version import __version__

__all__ = ["Voldemort", "__version__"]
