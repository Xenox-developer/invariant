# Python experiment example

This standard-library project intentionally starts with red tests. No dependency
installation or command network is needed in the prepared Linux image.

```sh
invariant init
invariant experiment init --name slug \
  --task "Implement slugify as specified in task.md" \
  --test-command "python3 -m unittest discover -s tests -v" \
  --test-path tests --request-id python-slug-v1 --run
```

The first run uses three real Codex turns (default model, low/medium/high effort).
Host Docker, prepared image and Codex ChatGPT sign-in must be ready. Setup alone
is offline; omit `--run` to inspect files. Guided execution checks the red baseline
before model calls. Reusing the same command/key reopens the saved study.

Review a result, continue it or apply its reviewed changes in the terminal flow.
Use `invariant experiment review STUDY_ID` later. Shared tests remain frozen.
