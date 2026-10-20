#!/usr/bin/env python3
"""Complete P01–P21 release gate (real Codex requires --live MODEL)."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from invariant.experiments.acceptance import main

if __name__=='__main__':raise SystemExit(main(root=ROOT))
