# INVARIANT comparison

Study: `study-280091f32df14db3987b42cfa95bd338` · INVARIANT 0.3.1

State: completed. Comparison uses first attempts; retries are shown separately.

| Configuration | Case | Attempt | Requested / observed | Quality | Seconds | Tokens | State / error |
|---|---|---:|---|---|---:|---:|---|
| ast-grep | logging-migration | 1 | gpt-6.1-sol/medium → gpt-6.1-sol/medium | pass | 97.48029899597168 | 154745 | succeeded / COMPLETED |
| jscodeshift | logging-migration | 1 | gpt-6.1-sol/medium → gpt-6.1-sol/medium | pass | 110.37528324127197 | 269096 | succeeded / COMPLETED |

## Summary

```json
{
  "scheduled": 2,
  "runner_successes": 2,
  "failed": 0,
  "cancelled": 0,
  "pending": 0,
  "evaluated": 2,
  "missing_evaluations": 0,
  "quality_passes": 2,
  "quality_failures": 0,
  "quality_judged": 2,
  "missing_quality": 0,
  "quality_pass_rate_over_scheduled": 1.0,
  "metrics": {
    "tests_failed": {
      "count": 2,
      "missing": 0,
      "mean": 0,
      "min": 0,
      "max": 0,
      "stdev": 0.0
    },
    "tests_passed": {
      "count": 2,
      "missing": 0,
      "mean": 34,
      "min": 34,
      "max": 34,
      "stdev": 0.0
    },
    "tests_total": {
      "count": 2,
      "missing": 0,
      "mean": 34,
      "min": 34,
      "max": 34,
      "stdev": 0.0
    }
  }
}
```

Selected attempt: unknown

Image: `sha256:b1a7fd8c539da978db4df9de25753d8b829922a60e94d175896cdd39f40deeae`

- Frozen local inputs do not make model responses or external services deterministic.
- Only first attempts contribute to comparison; all retries are listed separately.
- Missing quality, timing or token values remain unknown. No automatic winner or statistical ranking.
