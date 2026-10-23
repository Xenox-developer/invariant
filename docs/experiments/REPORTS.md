# Share a comparison

```sh
invariant experiment export STUDY_ID --output comparison.md
invariant experiment export STUDY_ID --format json --output comparison.json
invariant experiment export STUDY_ID --format csv --output comparison.csv
```

Destinations must be new files. Output is created with private file permissions;
an existing file or symlink is never overwritten. Omit `--output` for stdout.
With `--json`, the command returns the usual version-1 envelope containing the
report content or saved-file metadata.

The default report includes the INVARIANT version, study/attempt IDs, requested
and observed model/effort when available, outcome/error codes, evaluator verdicts,
finite numeric metrics, runtime, reported total tokens, selected attempt, pinned
image and baseline digest. All retries are separate rows; comparison denominators
continue to use first attempts. Missing values are JSON null, empty CSV cells or
`unknown` in Markdown. No winner, token price or statistical significance is inferred.

Raw configurations, command arguments, private paths, source, prompts, auth and
logs are not copied into the default report. Custom numeric metric names must
be bounded letters/digits/underscore/dot/dash names. Review even summary reports
before sharing: you control configuration/case IDs and metric names.

Include additional private content only when intended, using JSON or Markdown:

```sh
invariant experiment export STUDY_ID --include-task --include-diff --include-logs \
  --format json --output reviewed-details.json
```

Diff inclusion requires a selected complete result, as ordinary `diff` does.
Logs are a bounded saved tail. These flags can reveal proprietary code, prompts
or workload-emitted secrets; inspect the resulting file before uploading it.
CSV contains comparison rows and rejects private-content flags.

The JSON document is version 1, kind `invariant-comparison`, described in
[report-schema-v1.json](report-schema-v1.json). It is a shareable evidence summary,
not a restorable backup or a promise that remote services/model responses can be
reproduced. See [storage and private backups](../STORAGE_AND_RECOVERY.md).
