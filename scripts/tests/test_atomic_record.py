from pathlib import Path
import json,os,tempfile,unittest
from unittest.mock import patch
from invariant.environment import read_record

class AtomicRecordTests(unittest.TestCase):
 def test_atomic_replacement_after_open_retries_new_inode_instead_of_rejecting_unlink(self):
  with tempfile.TemporaryDirectory() as name:
   p=Path(name)/'record.json';p.write_text('{"version":1}');other=p.with_name('next.json');other.write_text('{"version":2}')
   original=os.fstat;changed=False
   def replace(fd):
    nonlocal changed
    if not changed:other.replace(p);changed=True
    return original(fd)
   with patch('invariant.environment.os.fstat',side_effect=replace):self.assertEqual(read_record(p),{'version':2})
 def test_revoked_record_is_not_returned_from_an_unlinked_descriptor(self):
  with tempfile.TemporaryDirectory() as name:
   p=Path(name)/'record.json';p.write_text('{"version":1}');original=os.fstat
   def remove(fd):p.unlink();return original(fd)
   with patch('invariant.environment.os.fstat',side_effect=remove):self.assertIsNone(read_record(p))
 def test_symlinks_hardlinks_and_oversized_records_still_fail_closed(self):
  with tempfile.TemporaryDirectory() as name:
   p=Path(name)/'record.json';p.write_text('{}');link=p.with_name('link.json');link.symlink_to(p)
   with self.assertRaises(RuntimeError):read_record(link)
   link.unlink();os.link(p,link)
   with self.assertRaises(RuntimeError):read_record(p)
   link.unlink();p.write_text(json.dumps({'x':'a'*8192}))
   with self.assertRaises(RuntimeError):read_record(p)
