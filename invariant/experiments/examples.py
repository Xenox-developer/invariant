"""Copy packaged example projects without requiring a source checkout."""
from pathlib import Path
import shutil

from ..workspace import ASSETS
from .errors import ExperimentError


def create(language,directory):
    if language not in ('python','javascript'):raise ExperimentError('INVALID_ARGUMENTS','Choose python or javascript.')
    target=Path(directory).expanduser().absolute()
    if target.exists() or target.is_symlink():raise ExperimentError('ALREADY_EXISTS','Example destination already exists.','Choose a new folder; no existing files were replaced.')
    shutil.copytree(ASSETS/'examples'/language,target)
    return {'project':str(target),'guide':str(target/'README.md'),'task':str(target/'task.md'),
            'test_command':'npm test' if language=='javascript' else 'python3 -m unittest discover -s tests -v',
            'model_turns':0,'initialized':False}
