"""Source-checkout wrapper for Engram's zero-setup quickstart.

    python examples/quickstart.py

Installed packages expose the same demo as:

    engram-quickstart
"""
from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram.quickstart import main  # noqa: E402


if __name__ == "__main__":
    main()
