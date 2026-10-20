"""Demonstrate denial of operations outside an execution's grant."""

from invariant_client import BrokerClient


def main():
    client = BrokerClient.from_env()
    for request_id, method, path in [
        ("delete", "DELETE", "/invoices/123"),
        ("other-query", "GET", "/invoices?tenant=other"),
    ]:
        reply = client.request(method, path, request_id=request_id)
        assert reply == {"type": "denied", "reason": "out_of_scope"}
        print(method, path, "-> denied (out_of_scope)")


if __name__ == "__main__":
    main()
