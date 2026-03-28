#!/usr/bin/env python3
"""Launch the improvement dashboard.

Usage:
    python dashboard.py
    python dashboard.py --port 8080
    python dashboard.py --eval-dir ./eval

This is a thin shim that delegates to `recursive-improve dashboard`.
For direct usage, run: recursive-improve dashboard
"""

import sys

if __name__ == "__main__":
    sys.argv = ["recursive-improve", "dashboard"] + sys.argv[1:]
    from recursive_improve.cli import main
    main()
