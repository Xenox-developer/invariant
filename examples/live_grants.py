"""Observe live operator grants. Every probe is an explicit new request.

Start with --no-grant, then use status/grant/revoke from another terminal.
Output is captured and shown after the program observes revocation.
"""
import json
import time

from invariant_client import BrokerClient

client = BrokerClient.from_env()
phase = "waiting_for_grant"
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    reply = client.request("GET", "/invoices")
    if reply == {"type": "denied", "reason": "missing_grant"}:
        if phase == "waiting_for_grant":
            print("Before grant: denied (missing_grant)")
            phase = "missing_observed"
    elif reply["type"] == "response":
        if phase in ("waiting_for_grant", "missing_observed"):
            print("After grant: invoice total", sum(i["amount"] for i in json.loads(reply["body"])))
            phase = "allowed_observed"
    elif reply == {"type": "denied", "reason": "revoked"}:
        print("After revocation: denied (revoked)")
        break
    elif reply == {"type": "denied", "reason": "budget_exhausted"}:
        print("Grant budget exhausted; waiting for revocation")
    elif reply == {"type": "denied", "reason": "invalid_capability"}:
        # This request may carry the handle read before an operator's change.
        # Keep waiting; the next explicit probe gets a fresh request ID.
        print("Capability changed; waiting for the next explicit probe")
    else:
        raise RuntimeError("Unexpected broker result: " + reply["type"])
    time.sleep(1)
else:
    raise RuntimeError("Operator demonstration timed out")
