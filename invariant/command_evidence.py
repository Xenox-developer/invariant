"""Durable executor facts, distinct from worker claims and model-visible code."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .experiments.errors import ExperimentError

MAX_BYTES=8*1024*1024
MAX_COMMAND=2048
MAX_VISIBLE=8192


class CommandEvidence:
    def __init__(self,path,*,capture=True):
        self.path=Path(path);self.seen={};self.bytes=0;self.omitted=0
        self.capture=capture
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        # Each invocation owns a new attempt; never append another execution.
        fd,name=tempfile.mkstemp(prefix=self.path.stem+'-',suffix=self.path.suffix,dir=self.path.parent)
        self.path=Path(name);self.stream=os.fdopen(fd,'wb')

    def observe(self,item):
        row={'id':item['id'],'command':item.get('command'),'cwd':item.get('cwd'),
             'status':item.get('status'),'exit_code':item.get('exitCode')}
        raw=(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode()
        identity=hashlib.sha256(raw).hexdigest()
        if row['id'] in self.seen:
            if self.seen[row['id']]!=identity:raise ExperimentError('INVALID_EXECUTION_EVIDENCE','Executor command identity changed.')
            return
        self.seen[row['id']]=identity
        if not self.capture or self.bytes+len(raw)>MAX_BYTES:self.omitted+=1;return
        self.stream.write(raw);self.stream.flush();os.fsync(self.stream.fileno());self.bytes+=len(raw)

    def finish(self):
        self.stream.close()
        return {'path':str(self.path),'sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),
                'unrecorded_commands':self.omitted}


def verified_summary(reference):
    raw=Path(reference['path']).read_bytes()
    if len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=reference['sha256']:
        raise ExperimentError('CORRUPT_STATE','Executor evidence changed after execution.')
    rows=[json.loads(line) for line in raw.splitlines()];shown=[];size=0
    for row in reversed(rows):
        text=json.dumps(row['command'],ensure_ascii=False)
        if len(text.encode())>MAX_COMMAND or len(shown)>=8:continue
        visible={k:row[k] for k in ('command','cwd','status','exit_code')}
        length=len(json.dumps(visible,ensure_ascii=False).encode())
        if size+length>MAX_VISIBLE:continue
        shown.append(visible);size+=length
    return {'provenance':'executor command-completion events; not worker assertions',
            'commands':list(reversed(shown)),
            'omitted_commands':len(rows)-len(shown)+reference['unrecorded_commands']}
