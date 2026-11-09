"""Extract original stage timestamps without modifying the historical databases."""
import json
from pathlib import Path
import sys
from plain_backend import docker
HERE=Path(__file__).resolve().parent
RUN=HERE/'results/pair-20261107-a'
heads=[]
for stage in ('methods','checker'):
    execution=json.loads((RUN/'with-invariant'/stage/'execution.json').read_text())
    first=execution['trials'][0]['runner']['native']['home_ref']['path']
    prefix=first.split('/experiments/')[0]
    baseline=json.loads((RUN/'with-invariant'/stage/'baseline.json').read_text())
    heads.append({'stage':stage,'state':prefix,'studies':[execution['study_id'],baseline['study_id']]})
script=r'''
import json,os,shutil,sqlite3,tempfile
from pathlib import Path
out=[]
for head in json.loads(os.environ['STAGE_HEADS']):
    source=Path('/state')/head['state']/'experiments/store.sqlite3'
    with tempfile.TemporaryDirectory() as temp:
        db=Path(temp)/source.name
        shutil.copyfile(source,db)
        wal=source.with_name(source.name+'-wal')
        if wal.exists():shutil.copyfile(wal,db.with_name(db.name+'-wal'))
        con=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
        studies=[]
        for sid in head['studies']:
            study=con.execute('SELECT id,created_at,definition FROM studies WHERE id=?',(sid,)).fetchone()
            attempts=[]
            for a in con.execute('SELECT a.id,a.record,t.variant_id FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE t.study_id=?',(sid,)):
                r=json.loads(a['record'])
                attempts.append({'id':a['id'],'variant':a['variant_id'],'started_at':r.get('started_at'),
                    'finished_at':r.get('finished_at'),'runner':{k:r.get('runner',{}).get(k) for k in ('started_at','finished_at','duration_seconds','evaluation_only')},
                    'evaluation':{k:r.get('evaluation',{}).get(k) for k in ('started_at','finished_at','duration_seconds','passed')},
                    'intents':{k:{'operation':v.get('operation'),'updated_at':v.get('updated_at')} for k,v in r.get('intents',{}).items()}})
            events=[]
            for e in con.execute('SELECT time,kind,facts FROM events WHERE study_id=? ORDER BY sequence',(sid,)):
                facts=json.loads(e['facts'])
                events.append({'time':e['time'],'kind':e['kind'],'state':facts.get('state'),'reason':facts.get('reason')})
            studies.append({'id':sid,'created_at':study['created_at'],'evaluation_only':json.loads(study['definition']).get('evaluation_only',False),'attempts':attempts,'events':events})
        con.close()
    out.append({'stage':head['stage'],'studies':studies})
print(json.dumps(out))
'''
result=docker('run','--rm','--network=none','--read-only','--cap-drop=ALL','--tmpfs=/tmp',
    '--mount','type=volume,src=invariant-state-v2,dst=/state,readonly','--env','STAGE_HEADS='+json.dumps(heads),
    '--entrypoint','python3','invariant-agent-runtime:0.163.0-v2','-B','-c',script)
target=RUN/'diagnostics/stage-times.json';target.write_text(json.dumps(json.loads(result.stdout),indent=2)+'\n')
for stage in json.loads(result.stdout):
    print(stage['stage'])
    for study in stage['studies']:
        print('study',study['id'],'created',study['created_at'],'baseline',study['evaluation_only'])
        for a in study['attempts']:print('attempt',a)
