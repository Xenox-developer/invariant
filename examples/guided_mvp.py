"""Workload for `invariant demo`; every effect goes through the real SDK.

The host guide issues/revokes authority through the protected operator endpoint.
This program has no operator connection or host-side progress files.
"""
import json
import time

from invariant_client import BrokerClient


def main():
    client = BrokerClient.from_env()

    def request(method, path, request_id):
        deadline = time.monotonic() + 5
        while True:
            reply = client.request(method, path, request_id=request_id)
            if reply != {"type": "denied", "reason": "capacity_exceeded"}:
                return reply
            # Only an explicit local capacity rejection is safe to retry.
            # Preserve its ID; never retry a transport error/unknown outcome.
            if time.monotonic() >= deadline:
                raise RuntimeError("Demo broker remained at capacity")
            time.sleep(.01)

    assert request("GET", "/invoices", "demo-no-grant") == {"type": "denied", "reason": "missing_grant"}
    counter = 0
    while True:
        counter += 1
        first_id = "demo-await-grant-" + str(counter)
        reply = request("GET", "/invoices", first_id)
        if reply["type"] == "response":
            assert reply["status"] == 200
            break
        assert reply["type"] == "denied" and reply["reason"] in ("missing_grant", "invalid_capability")
        time.sleep(.05)

    invoices = json.loads(reply["body"])
    summary = {"invoices": len(invoices), "total": sum(invoice["amount"] for invoice in invoices)}
    print("INVARIANT_DEMO_SUMMARY " + json.dumps(summary), flush=True)
    assert request("DELETE", "/invoices/123", "demo-delete") == {"type": "denied", "reason": "out_of_scope"}

    for index in range(1, 20):
        reply = request("GET", "/invoices", "demo-read-" + str(index))
        assert reply["type"] == "response" and reply["status"] == 200
    assert request("GET", "/invoices", "demo-over-budget") == {"type": "denied", "reason": "budget_exhausted"}

    # Repeating an admitted ID returns metadata without spending another read.
    # The same request must fail after revocation, even though it is a replay.
    while True:
        reply = request("GET", "/invoices", first_id)
        if reply == {"type": "denied", "reason": "revoked"}:
            break
        assert reply == {"type": "replay", "outcome": {"state": "completed", "status": 200}}
        time.sleep(.05)
    assert request("GET", "/invoices", "demo-after-revoke") == {"type": "denied", "reason": "revoked"}
    print("INVARIANT_DEMO_REVOKED", flush=True)

    # Remain alive so the guide can demonstrate the real operator stop command.
    while True:
        time.sleep(.1)


if __name__ == "__main__":
    main()
