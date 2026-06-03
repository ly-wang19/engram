import os
import sys

# Ensure the repo root is importable regardless of how pytest is invoked.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
