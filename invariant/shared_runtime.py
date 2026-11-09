"""One checked runtime per immutable image, reused across project managers."""
import hashlib
from pathlib import Path


def directory(image):
    return Path.home()/'.invariant/shared-runtime'/hashlib.sha256(image.encode()).hexdigest()


def prepare(project,image,path=None):
    from . import environment
    from .execution import project_lock
    from .workspace import private_write,state_for
    from .telemetry import span
    if not environment.IMAGE_ID.fullmatch(image):
        import json
        from .workspace import docker
        image=json.loads(docker('image','inspect',image))[0]['Id']
    root=directory(image)
    with span('runtime.prepare',image=image):
        with project_lock(root,reconcile=False,timeout=120):
            result=environment._prepare_control(project,image,path=root/'control')
    if path is None:
        private_write(state_for(project)/'runtime-v1/control-ref.json',{'path':str(result),'base_image':image})
    return result
