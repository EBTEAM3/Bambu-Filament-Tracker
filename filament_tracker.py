#!/usr/bin/env python3
"""
Compatibility wrapper for legacy entrypoint imports.

Public API kept stable:
    - from filament_tracker import FilamentTracker
    - python filament_tracker.py
"""

import os

from app import FilamentTracker, main

_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.environ.get("FILAMENT_TRACKER_DATA_DIR", _SERVER_DIR)
DB_PATH = os.path.join(_DATA_DIR, "filament_tracker.db")
TEST_DB_PATH = os.path.join(_DATA_DIR, "filament_tracker_test.db")


if __name__ == "__main__":
    main()
