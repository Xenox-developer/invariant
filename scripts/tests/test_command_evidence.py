import json
from pathlib import Path
import tempfile
import unittest
from invariant.command_evidence import CommandEvidence,verified_summary
from invariant import review_input
from invariant.experiments.errors import ExperimentError
from test_review_input import payload

class CommandEvidenceTests(unittest.TestCase):
 def test_secret_bearing_execution_never_copies_commands_to_the_journal(self):
  with tempfile.TemporaryDirectory() as name:
   journal=CommandEvidence(Path(name)/'commands.jsonl',capture=False)
   journal.observe({'id':'secret','command':'opaque-sensitive-argument','exitCode':0,'status':'completed'})
   reference=journal.finish();self.assertNotIn('opaque-sensitive-argument',Path(reference['path']).read_text())
   evidence=verified_summary(reference);self.assertEqual(evidence['commands'],[])
   self.assertEqual(evidence['omitted_commands'],1)
 def test_operational_commands_survive_without_duplicate_source_and_bind_to_receipt(self):
  with tempfile.TemporaryDirectory() as name:
   journal=CommandEvidence(Path(name)/'commands.jsonl')
   journal.observe({'id':'write','command':'x'*4000,'exitCode':0,'status':'completed'})
   migration={'id':'migrate','command':['python3','-c','q.migrate(); assert before == after'],'exitCode':0,'status':'completed','cwd':'/workspace'}
   journal.observe(migration);journal.observe(migration)
   journal.observe({'id':'stop','command':['python3','helper.py','stop'],'exitCode':0,'status':'completed','cwd':'/workspace'})
   reference=journal.finish();summary=verified_summary(reference)
   self.assertEqual(len(summary['commands']),2);self.assertEqual(summary['omitted_commands'],1)
   data=payload();data['evidence'][0]['execution_commands']=summary;text=review_input.render(data)
   self.assertIn('q.migrate(); assert before == after',text);self.assertIn('helper.py',text)
   self.assertNotIn('x'*4000,text);self.assertIn('do not infer omitted actions',text)
   Path(reference['path']).write_text('[]')
   with self.assertRaises(ExperimentError):verified_summary(reference)
 def test_changed_event_identity_fails_closed_and_bounds_are_explicit(self):
  with tempfile.TemporaryDirectory() as name:
   journal=CommandEvidence(Path(name)/'commands.jsonl')
   journal.observe({'id':'a','command':'one','exitCode':1,'status':'completed'})
   with self.assertRaises(ExperimentError):journal.observe({'id':'a','command':'other','exitCode':0})
   for i in range(50):journal.observe({'id':str(i),'command':'command'+str(i),'exitCode':0,'status':'completed'})
   summary=verified_summary(journal.finish());self.assertEqual(len(summary['commands']),8)
   self.assertEqual(summary['omitted_commands'],43);self.assertLess(len(json.dumps(summary)),9000)
 def test_failed_commands_do_not_turn_into_success_claims(self):
  with tempfile.TemporaryDirectory() as name:
   journal=CommandEvidence(Path(name)/'commands.jsonl');journal.observe({'id':'a','command':'migration','exitCode':2,'status':'completed'})
   self.assertEqual(verified_summary(journal.finish())['commands'][0]['exit_code'],2)
