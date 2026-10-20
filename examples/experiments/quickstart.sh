#!/bin/sh
# Run from a new project containing compare.json, agent.py, case.json and judge.py.
set -eu
invariant init
invariant prepare --agent codex
invariant experiment validate compare.json
# Human submission, JSON selection/review, human apply.
invariant experiment run compare.json --wait --request-id quickstart-human
invariant experiment status --request-id quickstart-human --json > study.json
study=$(python3 -c 'import json;print(json.load(open("study.json"))["data"]["id"])')
invariant experiment results "$study" --json > results.json
attempt=$(python3 -c 'import json;print(next(t["attempt_id"] for t in json.load(open("results.json"))["data"]["trials"] if t["passed"]))')
invariant experiment select "$attempt" --if-revision 0 --json
invariant experiment diff "$study"
invariant experiment apply "$study" --dry-run --json > plan.json
plan=$(python3 -c 'import json;print(json.load(open("plan.json"))["data"]["plan_id"])')
invariant experiment apply --plan "$plan"
test "$(cat result.txt)" = 42
# The same state also supports the reverse client handoff.
rm result.txt
invariant experiment run compare.json --wait --request-id quickstart-machine --json > second.json
second=$(python3 -c 'import json;print(json.load(open("second.json"))["data"]["id"])')
invariant experiment results "$second" --json > second-results.json
attempt=$(python3 -c 'import json;print(next(t["attempt_id"] for t in json.load(open("second-results.json"))["data"]["trials"] if t["passed"]))')
invariant experiment select "$attempt" --if-revision 0
invariant experiment apply "$second" --dry-run
invariant experiment apply "$second" --dry-run --json > second-plan.json
plan=$(python3 -c 'import json;print(json.load(open("second-plan.json"))["data"]["plan_id"])')
invariant experiment apply --plan "$plan" --json
invariant experiment init codex.json --runner codex
invariant chats --json
invariant config --json
invariant guardrails --json
