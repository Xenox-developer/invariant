import copy
import unittest

from invariant import measurements, resources
from invariant.experiments.errors import ExperimentError
from invariant.policy import NEW_PROJECT_CONFIG, normalize


class MeasurementTests(unittest.TestCase):
    def run_record(self):
        budget=resources.execution_contract(normalize({**NEW_PROJECT_CONFIG,'resource_mode':'manual'}))
        return {'state':'completed','passed':True,'wall_seconds':10,'model':'model','effort':'medium',
                'contract':{'task_digest':'a'*64,'model':'model','effort':'medium','image':'sha256:'+'b'*64,'resources':budget},
                'host_config':copy.deepcopy(budget['host_config']),
                'calls':[{'call_id':'manager','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':20}},
                         {'call_id':'worker','usage':{'total':{'inputTokens':200,'cachedInputTokens':150,'outputTokens':30,'totalTokens':230}}}]}

    def test_manager_and_worker_usage_counted_once_with_cached_input_as_subset(self):
        value=measurements.usage_totals(self.run_record()['calls'])
        self.assertEqual(value['input_tokens'],300)
        self.assertEqual(value['cached_input_tokens'],230)
        self.assertEqual(value['total_tokens'],350)
        self.assertEqual(value['model_calls'],2)

    def test_unknown_usage_stays_unknown_and_duplicates_are_rejected(self):
        rows=self.run_record()['calls']
        rows[1]['usage']=None
        self.assertIsNone(measurements.usage_totals(rows)['total_tokens'])
        with self.assertRaises(ExperimentError):measurements.usage_totals([rows[0],rows[0]])
        self.assertIsNone(measurements.usage_totals(None)['total_tokens'])

    def test_imported_contract_cannot_omit_the_actual_resource_limits(self):
        a=self.run_record();b=copy.deepcopy(a)
        a['contract']['resources']={'resolved':True,'host_config':{}}
        b['contract']=copy.deepcopy(a['contract'])
        self.assertFalse(measurements.compare_runs([a,b])['comparable'])

    def test_successful_identical_contracts_can_be_compared(self):
        a=self.run_record();b=copy.deepcopy(a);b['wall_seconds']=20
        result=measurements.compare_runs([a,b])
        self.assertTrue(result['comparable']);self.assertEqual(result['time_ratio'],0.5)

    def test_swap_drift_blocks_comparison_even_when_both_tests_pass(self):
        a=self.run_record();b=copy.deepcopy(a)
        b['host_config']['MemorySwap']*=2
        result=measurements.compare_runs([a,b])
        self.assertFalse(result['comparable']);self.assertIsNone(result['time_ratio'])
        self.assertIn('resource_mismatch',result['reasons'])

    def test_fast_failure_never_wins_against_a_successful_solution(self):
        a=self.run_record();b=copy.deepcopy(a);a.update(state='blocked',passed=None,wall_seconds=1)
        result=measurements.compare_runs([a,b])
        self.assertFalse(result['comparable']);self.assertIsNone(result['time_ratio'])
        self.assertIsNone(result['token_ratio'])

    def test_task_image_or_model_difference_blocks_comparison(self):
        for field in ('task_digest','image','model','effort'):
            a=self.run_record();b=copy.deepcopy(a);b['contract'][field]='different'
            self.assertFalse(measurements.compare_runs([a,b])['comparable'])


if __name__=='__main__':unittest.main()
