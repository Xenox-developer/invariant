"""Numeric, credential-free spans kept in Docker beside durable project state."""
import contextlib
import contextvars
import functools
import json
import os
from pathlib import Path
import time
import uuid

_destination=contextvars.ContextVar('invariant_trace',default=None)
_parent=contextvars.ContextVar('invariant_span',default=None)


@contextlib.contextmanager
def session(project,run_ref=None):
    import re
    if run_ref is not None and not re.fullmatch(r'run-[0-9a-f]{32}',str(run_ref)):raise RuntimeError('Invalid trace run reference')
    from .workspace import state_for
    directory=state_for(Path(project))/'traces';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    token=_destination.set((directory/(str(run_ref or 'manager')+'.jsonl'),run_ref))
    try:yield
    finally:_destination.reset(token)


@contextlib.contextmanager
def span(stage,**facts):
    target=_destination.get()
    if target is None:
        yield;return
    identifier=uuid.uuid4().hex;parent=_parent.get();token=_parent.set(identifier)
    started=time.time();clock=time.monotonic();ok=False
    try:
        yield;ok=True
    finally:
        _parent.reset(token)
        row={'span_id':identifier,'parent_id':parent,'run_ref':target[1],'stage':stage,
             'started_at':started,'seconds':time.monotonic()-clock,'ok':ok,**facts}
        fd=os.open(target[0],os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
        try:os.write(fd,(json.dumps(row)+'\n').encode())
        finally:os.close(fd)


def traced(stage):
    def decorate(function):
        @functools.wraps(function)
        def invoke(*args,**kwargs):
            with span(stage):return function(*args,**kwargs)
        return invoke
    return decorate
