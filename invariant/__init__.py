"""INVARIANT agent workspaces."""

# The host interface must not create caches while loading container clients.
import sys
sys.dont_write_bytecode = True

__version__ = "0.3.1"
