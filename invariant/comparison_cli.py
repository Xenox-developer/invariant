"""Validate two recorded workflow measurements before publishing speed ratios."""
import json
from pathlib import Path

from .experiments.cli import Parser
from .experiments.definitions import parse_json
from .experiments.errors import ExperimentError
from .measurements import compare_runs


def main(arguments):
    parser=Parser(prog='invariant compare',description='Gate comparison on identical verified contracts and successful evaluations; no workloads are started.')
    parser.add_argument('file',help='JSON array of two recorded runs')
    machine='--json' in arguments
    try:
        args=parser.parse_args([arg for arg in arguments if arg!='--json'])
        value=compare_runs(parse_json(Path(args.file).read_bytes(),'comparison measurements'))
        print(json.dumps({'schema_version':1,'ok':True,'data':value,'error':None}) if machine else json.dumps(value,indent=2))
        return 0 if value['comparable'] else 1
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as error:
        record=error.record() if isinstance(error,ExperimentError) else {'code':'INVALID_MEASUREMENT','message':str(error)}
        print(json.dumps({'schema_version':1,'ok':False,'data':None,'error':record}) if machine else record['message'])
        return 2
