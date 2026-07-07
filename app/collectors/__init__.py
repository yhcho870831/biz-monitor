from __future__ import annotations

from .base import BaseCollector
from .d2b import D2BCollector
from .g2b import G2BCollector
from .iris import IRISCollector
from .kimst import KIMSTCollector
from .kmiti import KMITICollector
from .nia import NIACollector


class NotImplementedCollector(BaseCollector):
    site_code = "unknown"

    def search(self, term):
        raise NotImplementedError("This collector is not implemented yet.")


def build_collector_registry():
    return {
        "kimst": KIMSTCollector(),
        "g2b": G2BCollector(),
        "nia": NIACollector(),
        "d2b": D2BCollector(),
        "kmiti": KMITICollector(),
        "iris": IRISCollector(),
    }
