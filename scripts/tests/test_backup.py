import contextlib
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from invariant import workspace
from invariant.experiments.backup import extract,recover_restore,verify_images
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.errors import ExperimentError


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.project=self.root/'project';self.project.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):workspace.init(self.project)
        self.engine=ExperimentEngine(self.project,state=self.root/'private')
        self.engine.root.mkdir(parents=True)

    def archive(self, *, corrupt=False, escape=False):
        payload=b'saved private state'
        files={'state':{'type':'directory','mode':0o700},
               'state/store.sqlite3':{'type':'file','mode':0o600,'size':len(payload),
                                      'sha256':'bad' if corrupt else hashlib.sha256(payload).hexdigest()},
               'images.tar':{'type':'file','mode':0o600,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}}
        if escape:files['../escape']={'type':'file','mode':0o600,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
        manifest={'schema_version':1,'kind':'invariant-private-backup','project':str(self.project),
                  'state':str(self.engine.root),'images':[], 'config_sha256':hashlib.sha256((self.project/'.invariant/config.json').read_bytes()).hexdigest(),'files':files}
        path=self.root/'backup.tar.gz'
        with tarfile.open(path,'w:gz') as archive:
            raw=json.dumps(manifest).encode();info=tarfile.TarInfo('manifest.json');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
            for name,row in files.items():
                info=tarfile.TarInfo(name)
                if row['type']=='directory':info.type=tarfile.DIRTYPE;archive.addfile(info)
                else:info.size=len(payload);archive.addfile(info,io.BytesIO(payload))
        return path

    def test_integrity_identity_and_escape_checks_precede_state_publication(self):
        for kwargs in ({'corrupt':True},{'escape':True}):
            archive=self.archive(**kwargs);destination=self.root/('extract-'+str(len(kwargs))+str(kwargs));destination.mkdir()
            with self.assertRaises(ExperimentError):extract(archive,destination,project=self.project,destination=self.engine.root)
        self.assertFalse((self.root/'escape').exists())
        target=self.root/'other';target.mkdir()
        with self.assertRaises(ExperimentError):extract(self.archive(),target,project=self.root/'wrong',destination=self.engine.root)

    def marker(self,token):
        (self.engine.state/'restore-active.json').write_text(json.dumps({'token':token,'project':str(self.project)}))
        (self.engine.state/'restore-active.json').chmod(0o600)

    def test_crash_after_old_state_rename_rolls_back_without_replaying_work(self):
        token='a'*32;old=self.engine.state/('restore-before-'+token);self.engine.root.rename(old)
        (old/'keep').write_text('original')
        stage=self.engine.state/('restore-stage-'+token)/'state';stage.mkdir(parents=True)
        self.marker(token)
        with self.assertRaises(ExperimentError):ExperimentEngine(self.project,state=self.engine.state)
        self.assertEqual(recover_restore(self.engine)['outcome'],'rolled_back')
        self.assertEqual((self.engine.root/'keep').read_text(),'original')

    def test_published_state_keeps_the_previous_copy_and_clears_the_journal(self):
        token='b'*32;old=self.engine.state/('restore-before-'+token);old.mkdir()
        (old/'old').write_text('old');(self.engine.root/'new').write_text('new')
        self.marker(token)
        self.assertEqual(recover_restore(self.engine)['outcome'],'published')
        self.assertEqual((old/'old').read_text(),'old')
        self.assertFalse((self.engine.state/'restore-active.json').exists())

    def test_ambiguous_external_change_blocks_recovery_and_preserves_every_copy(self):
        token='c'*32;(self.engine.state/('restore-before-'+token)).mkdir()
        (self.engine.state/('restore-stage-'+token)/'state').mkdir(parents=True)
        self.marker(token)
        with self.assertRaises(ExperimentError):recover_restore(self.engine)
        self.assertTrue((self.engine.state/'restore-active.json').exists())

    def test_image_verification_accepts_classic_and_oci_ids_but_rejects_retagging(self):
        config=b'{}';config_id='sha256:'+hashlib.sha256(config).hexdigest()
        manifest=json.dumps({'schemaVersion':2,'config':{'digest':config_id},'layers':[]}).encode()
        identity='sha256:'+hashlib.sha256(manifest).hexdigest()
        archive=self.root/'images.tar'
        def write(tags=None):
            parts={'config.json':config,'blobs/sha256/'+identity.split(':')[1]:manifest,
                   'manifest.json':json.dumps([{'Config':'config.json','RepoTags':tags,'Layers':[]}]).encode(),
                   'index.json':json.dumps({'manifests':[{'digest':identity}]}).encode()}
            with tarfile.open(archive,'w') as output:
                for name,raw in parts.items():
                    info=tarfile.TarInfo(name);info.size=len(raw);output.addfile(info,io.BytesIO(raw))
        write();verify_images(archive,{config_id});verify_images(archive,{identity})
        with self.assertRaises(ExperimentError):verify_images(archive,{'sha256:'+'f'*64})
        write(['foreign:tag'])
        with self.assertRaises(ExperimentError):verify_images(archive,{identity})
