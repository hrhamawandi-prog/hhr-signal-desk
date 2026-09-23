"""Compatibility import; maintained implementation is in core."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "core"))
from core.status_server import *
