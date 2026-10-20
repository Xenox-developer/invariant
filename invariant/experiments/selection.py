"""Selection and deletion share a short mutation lease, independent of running trials."""
from ..execution import project_lock
from .lifecycle import request_key


def select(engine, identifier, *, revision, request_id=None, clear=False):
    request_key(request_id)
    with project_lock(engine.root / 'mutations', timeout=3):
        return engine.store.selection(identifier, revision=revision, request_id=request_id, clear=clear)
