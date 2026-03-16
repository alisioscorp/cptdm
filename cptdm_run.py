#!/usr/bin/env python3
"""
Standalone runner for CPT-DM.
Use this if you haven't installed the package yet:

    python cptdm_run.py --timescale seasonal ...
    # or make it executable:
    chmod +x cptdm_run.py
    ./cptdm_run.py --timescale seasonal ...

This script adds src/ to sys.path automatically.
"""
import sys
import os

# Add src/ to path so cptdm package is importable without installation
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from cptdm.cli import main

if __name__ == "__main__":
    main()
