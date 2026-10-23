# JavaScript experiment example

This project uses Node's built-in test runner and has no npm dependencies.
Its initial tests intentionally fail. Prepare Node 18+ inside the project image:

```sh
invariant init
invariant config set command_network internet
invariant environment prepare --command "apt-get update && apt-get install -y nodejs npm && npm ci --ignore-scripts --no-audit --no-fund" --timeout 600
invariant config set command_network none
invariant experiment init --name slug \
  --task "Implement slugify as specified in task.md" \
  --test-command "npm test" --test-path tests \
  --test-path package.json --test-path package-lock.json \
  --request-id javascript-slug-v1 --run
```

The first guided run uses three real Codex turns after the baseline command is
checked. Docker, the prepared base image and host Codex ChatGPT sign-in must be
ready. Setup alone is offline; omit `--run` to inspect files. Dependency
preparation makes no model calls and persists tools inside Docker.

Review, continue or apply a result through the terminal flow. Reopen it with
`invariant experiment review STUDY_ID`. Reusing the setup request key finds the
saved run; it does not silently spend three more turns.
