"""Bounded facts from the frozen manifest; no model summarizer or source dumping."""
import json

COMMANDS=('python3','git','rg','find','sed','node','npm','pytest')


def render(entries,capabilities):
    paths=sorted(path for path,row in entries.items() if not path.startswith('@input/') and row.get('type')=='file')
    shown=[]
    for path in paths[:60]:
        if len(json.dumps(shown+[path],ensure_ascii=False))>3000:break
        shown.append(path)
    return '\nFrozen workspace facts: '+json.dumps({'files':shown,'additional_files':len(paths)-len(shown),
        'commands_on_base_path':capabilities},ensure_ascii=False,separators=(',',':'))+(
        '\nRead implementation files and targeted test sections as needed; batch related reads in one awaited call. The entire fixed suite is available in the workspace. '
        'Keep operational steps in separate short commands from source-writing scripts; their actual completion events are retained for review. '
        'The validation tool waits for its final result. When using exec, await the tool in that call; '
        'keep the configured default yield deadline and do not request early yielding or polling.')
