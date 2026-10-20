"""A command harness can invoke any Linux agent/toolchain; this example needs no API."""
import json
import os
from pathlib import Path

variant = json.loads(Path(os.environ['INVARIANT_VARIANT_FILE']).read_text())
case = json.loads(Path(os.environ['INVARIANT_CASE_FILE']).read_text())
Path('result.txt').write_text(str(case['value'] * variant['parameters']['multiplier'])+'\n')
