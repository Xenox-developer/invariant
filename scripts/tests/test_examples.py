from pathlib import Path
import tempfile
import unittest

from invariant.experiments.examples import create
from invariant.experiments.errors import ExperimentError


class ExampleTests(unittest.TestCase):
    def test_packaged_examples_are_complete_and_never_replace_a_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            for language in ('python','javascript'):
                target=Path(folder)/language
                value=create(language,target)
                self.assertEqual(value['model_turns'],0)
                self.assertTrue((target/'tests').is_dir())
                self.assertTrue((target/'task.md').is_file())
                if language=='javascript':self.assertTrue((target/'package-lock.json').is_file())
                with self.assertRaises(ExperimentError):create(language,target)
