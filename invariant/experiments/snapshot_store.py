"""Durable owned snapshots, independent of model output and host application."""
import json
import time
from pathlib import Path

from .. import environment, workspace
from .definitions import digest
from .errors import ExperimentError
from .files import capture_workspace
from .runners import owned_info, stop_owned, reclaim_private
from .snapshots import seal_tree


class EnvironmentSnapshotStore:
    """Docker backend retains a stopped lease until validation is accepted.

    A failed validation can restart that same environment; an accepted snapshot
    is durable before the worker receives success. No asynchronous save is hidden.
    """
    def capture(self,context,folder,limits):
        folder=Path(folder)
        started=time.monotonic()
        info=owned_info(context)
        paused=bool(info and info['State'].get('Running'))
        if paused:
            workspace.docker('pause',context.owner_token)
            info=owned_info(context)
            if not info or not info['State'].get('Paused'):raise ExperimentError('UNRESOLVED_EXECUTION','Snapshot freeze was not confirmed.')
        elif not stop_owned(context):raise ExperimentError('UNRESOLVED_EXECUTION','Snapshot requires confirmed process stop.')
        reclaim_private(context)
        info=owned_info(context)
        if info is None:raise ExperimentError('CONTAINER_LOST','Snapshot source disappeared.')
        (folder/'snapshot').mkdir(mode=0o700)
        saved=capture_workspace(context.workspace,folder/'snapshot/workspace',limits)
        workspace.private_write(folder/'snapshot/manifest.json',saved)
        captured=time.monotonic()
        checkpoint=environment.checkpoint(context.owner_token,folder/'control',context.project,context.emit,
                                          owner=context.checkpoint_owner,**({'frozen':True} if paused else {}))
        if not checkpoint:raise ExperimentError('CHECKPOINT_FAILED','No durable writable environment snapshot.')
        seal_tree(folder/'snapshot')
        return {'schema_version':1,'container_id':info['Id'],'stopped_state':info['State'],
                'paused':paused,
                'timings':{'stop_and_capture_seconds':captured-started,'checkpoint_seconds':time.monotonic()-captured},
                'workspace_digest':saved['content_digest'],'image':checkpoint['image'],
                'owner':context.checkpoint_owner,'checkpoint_record':checkpoint,'manifest':saved}

    def matches(self,context,folder,snapshot,limits):
        info=owned_info(context)
        if (info is None or info['Id']!=snapshot['container_id'] or info['State'].get('Running')
                or any(info['State'].get(k)!=snapshot['stopped_state'].get(k) for k in ('StartedAt','FinishedAt'))):return False
        image=environment.selected_image(Path(folder)/'control',context.project,context.image,owner=context.checkpoint_owner)
        if image!=snapshot['image']:raise ExperimentError('CORRUPT_STATE','Validation snapshot image changed.')
        # Capture into a fresh private scratch tree; never trust a worker report.
        from .results import verify_tree
        verify_tree(Path(folder)/'snapshot/workspace',snapshot['manifest']['entries'])
        if digest(snapshot['manifest']['entries'])!=snapshot['workspace_digest']:
            raise ExperimentError('CORRUPT_STATE','Validation manifest changed.')
        # Rehash the stopped source in place; equality needs no second copy of
        # installed .venv/node_modules trees. The accepted tree stays immutable.
        current=capture_workspace(context.workspace,None,limits)
        return current['content_digest']==snapshot['workspace_digest']
