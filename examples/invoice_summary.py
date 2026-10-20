"""Read invoices through INVARIANT and compute a local summary."""

import json

from invariant_client import BrokerClient


def main():
    client = BrokerClient.from_env()
    reply = client.request("GET", "/invoices", request_id="summary-read")
    if reply["type"] != "response" or reply["status"] != 200:
        raise RuntimeError("Invoice read did not complete: " + reply["type"])
    invoices = json.loads(reply["body"])
    print("Invoices read:", len(invoices))
    print("Total amount:", sum(invoice["amount"] for invoice in invoices))
    duplicate = client.request("GET", "/invoices", request_id="summary-read")
    assert duplicate == {"type": "replay", "outcome": {"state": "completed", "status": 200}}
    print("Duplicate read: completed status returned; no new API call")


if __name__ == "__main__":
    main()
