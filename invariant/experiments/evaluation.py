"""Strict evaluator results and explicit first-attempt comparison denominators."""
import math
import statistics

from .definitions import parse_json, source_path
from .errors import ExperimentError


def parse_result(raw):
    try:
        value = parse_json(raw, 'evaluator output')
        if not isinstance(value, dict) or set(value) - {'schema_version', 'passed', 'metrics', 'artifacts'}:
            raise ValueError()
        if type(value.get('schema_version')) is not int or value['schema_version'] != 1:
            raise ValueError()
        if value.get('passed') is not None and type(value['passed']) is not bool:
            raise ValueError()
        metrics = value.get('metrics', {})
        artifacts = value.get('artifacts', [])
        if not isinstance(metrics, dict) or len(metrics) > 1024 or not isinstance(artifacts, list) or len(artifacts) > 1024:
            raise ValueError()
        for key, number in metrics.items():
            if not key or len(key) > 256 or type(number) not in (int, float) or not math.isfinite(number):
                raise ValueError()
        for path in artifacts:
            source_path(path, 'evaluator artifact')
        return {'schema_version': 1, 'passed': value.get('passed'), 'metrics': metrics, 'artifacts': artifacts}
    except (ExperimentError, ValueError, TypeError, OverflowError):
        raise ExperimentError('INVALID_EVALUATION', 'Evaluator must emit one version-1 JSON result with finite numeric metrics, boolean/null passed and relative artifact paths.') from None


def compare(study):
    rows = []
    for trial in study['trials']:
        attempts = sorted(trial['attempts'], key=lambda item: item['number'])
        attempt = attempts[0]
        record = attempt.get('record', {})
        evaluation = record.get('evaluation', {'status': 'not_run'})
        rows.append({'trial_id': trial['id'], 'variant_id': trial['variant_id'], 'case_id': trial['case_id'],
                     'repetition': trial['repetition'], 'attempt_id': attempt['id'], 'attempt_count': len(attempts),
                     'state': attempt['state'], 'reason': attempt['reason'], 'runner': record.get('runner', {'status': 'not_started'}),
                     'evaluation': evaluation, 'passed': evaluation.get('passed'),
                     'metrics': evaluation.get('metrics', {}), 'duration_seconds': record.get('duration_seconds'),
                     'capture': record.get('capture'), 'artifacts': record.get('artifacts'),
                     'checkpoint': record.get('checkpoint'), 'workspace_diff': record.get('workspace_diff'),
                     'disk': record.get('disk'), 'token_usage': record.get('runner', {}).get('token_usage'),
                     'usage_unavailable_reason': record.get('runner', {}).get('usage_unavailable_reason', 'not_reported')})
    def aggregate(selected):
        scheduled = len(selected)
        judged = sum(row['passed'] is not None for row in selected)
        passed = sum(row['passed'] is True for row in selected)
        names = sorted({name for row in selected for name in row['metrics']})
        metrics = {}
        for name in names:
            values = [row['metrics'][name] for row in selected if name in row['metrics']]
            metrics[name] = {'count': len(values), 'missing': scheduled - len(values),
                             'mean': statistics.mean(values), 'min': min(values), 'max': max(values),
                             'stdev': statistics.stdev(values) if len(values) > 1 else None}
        return {'scheduled': scheduled, 'runner_successes': sum(row['runner'].get('status') == 'completed' for row in selected),
                'failed': sum(row['state'] == 'failed' for row in selected),
                'cancelled': sum(row['state'] == 'cancelled' for row in selected),
                'pending': sum(row['state'] not in ('succeeded', 'failed', 'cancelled', 'interrupted') for row in selected),
                'evaluated': sum(row['evaluation']['status'] == 'completed' for row in selected),
                'missing_evaluations': sum(row['evaluation']['status'] != 'completed' for row in selected),
                'quality_passes': passed, 'quality_failures': sum(row['passed'] is False for row in selected),
                'quality_judged': judged, 'missing_quality': scheduled - judged,
                'quality_pass_rate_over_scheduled': passed / scheduled if judged else None, 'metrics': metrics}
    variants = []
    for variant in study['frozen']['definition']['variants']:
        selected = [row for row in rows if row['variant_id'] == variant['id']]
        variants.append({'variant_id': variant['id'], 'configuration': variant, **aggregate(selected),
                         'cases': [{'case_id': case['id'], **aggregate([row for row in selected if row['case_id'] == case['id']])}
                                   for case in study['frozen']['definition']['cases']]})
    return {'study_id': study['id'], 'state': study['state'], 'attempt_policy': 'first_attempt',
            'repetitions': study['frozen']['definition']['repetitions'], 'summary': aggregate(rows),
            'variants': variants, 'trials': rows, 'automatic_winner': None,
            'selection': study.get('selection', {'attempt_id': None, 'revision': study['selection_revision']})}
