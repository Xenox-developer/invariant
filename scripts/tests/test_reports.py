import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from invariant.experiments import exporting
from invariant.experiments.errors import ExperimentError


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.engine=Mock()
        self.study={'kind':'study','id':'study-example','state':'completed','selection':{'attempt_id':'attempt-2','revision':1},
            'frozen':{'definition':{'runner':{'type':'codex'},'repetitions':1,
                'variants':[{'id':'variant','model':'model','effort':'high','parameters':{'secret':'DO_NOT_EXPORT'}}],
                'cases':[{'id':'case','prompt_file':'private-prompt.md'}]},'image':{'id':'sha256:example'},
                'baseline_content_digest':'digest'},'trials':[{'id':'trial','variant_id':'variant','case_id':'case','repetition':1,
            'attempts':[{'id':'attempt-1','number':1,'state':'failed','reason':'THROTTLED','record':{
                'runner':{'native':{'model':'model','effort':'high','home':'/private/DO_NOT_EXPORT'},'log':'DO_NOT_EXPORT'},
                'evaluation':{'status':'not_run'}}},
                {'id':'attempt-2','number':2,'state':'succeeded','reason':None,'record':{
                'runner':{'status':'completed','duration_seconds':2.5,'token_usage':{'total':{'totalTokens':42}}},
                'evaluation':{'status':'completed','passed':True,'metrics':{'score':1,'<private>':2}}}}]}]}
        self.engine.inspect.return_value=self.study
        self.engine.results.return_value={'summary':{'scheduled':1,'quality_passes':0,'missing_quality':1,'metrics':{}}}

    def test_default_report_is_allowlisted_and_retries_do_not_change_denominators(self):
        value=exporting.document(self.engine,'study-example')
        self.assertNotIn('DO_NOT_EXPORT',json.dumps(value))
        self.assertNotIn('private-prompt',json.dumps(value))
        self.assertEqual(len(value['attempts']),2)
        self.assertIsNone(value['attempts'][0]['total_tokens'])
        self.assertEqual(value['attempts'][1]['total_tokens'],42)
        self.assertFalse(value['attempts'][1]['comparison_attempt'])
        self.assertEqual(value['summary']['quality_passes'],0)
        self.assertEqual(value['attempts'][1]['metrics'],{'score':1})
        self.engine.logs.assert_not_called();self.engine.diff.assert_not_called()

    def test_private_logs_require_an_explicit_flag(self):
        self.engine.logs.return_value={'text':'EXPLICIT_PRIVATE_OUTPUT'}
        self.assertNotIn('EXPLICIT_PRIVATE_OUTPUT',exporting.render(exporting.document(self.engine,'id'),'json'))
        self.assertIn('EXPLICIT_PRIVATE_OUTPUT',exporting.render(exporting.document(self.engine,'id',include_logs=True),'markdown'))

    def test_formats_keep_missing_values_and_prevent_spreadsheet_formulas(self):
        self.study['trials'][0]['variant_id']='=BAD()';self.study['frozen']['definition']['variants'][0]['id']='=BAD()'
        value=exporting.document(self.engine,'id')
        rows=list(csv.DictReader(io.StringIO(exporting.render(value,'csv'))))
        self.assertEqual(rows[0]['variant'],"'=BAD()")
        self.assertEqual(rows[0]['total_tokens'],'')
        self.assertIn('unknown',exporting.render(value,'markdown'))
        self.assertIsNone(json.loads(exporting.render(value,'json'))['attempts'][0]['passed'])

    def test_output_never_overwrites_existing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'report.json'
            exporting.export(self.engine,'id',format='json',output=path)
            before=path.read_bytes();self.assertEqual(path.stat().st_mode&0o077,0)
            with self.assertRaises(ExperimentError):exporting.export(self.engine,'id',output=path)
            self.assertEqual(path.read_bytes(),before)
        with self.assertRaises(ExperimentError):exporting.export(self.engine,'id',format='csv',include_logs=True)
