#!/usr/bin/env python3
"""Rebuild both current and compatibility Codex images before release tests."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from invariant.workspace import prepare

if __name__=='__main__':
    prepare(persistent=True)
    prepare(persistent=False)
