"""Private stdio-to-WebSocket exec-server transport; credentials never reach argv."""
import json
from pathlib import Path
import sys
import threading
import websocket

url,token_file=sys.argv[1:]
token=Path(token_file).read_text().strip()
ws=websocket.create_connection(url,header=['Authorization: Bearer '+token],timeout=60,
                               http_proxy_host=None,http_no_proxy=['*'],suppress_origin=True)
ws.settimeout(None)
def write_requests():
    try:
        for line in sys.stdin.buffer:
            if line.strip():ws.send(line.decode().rstrip('\n'))
    finally:ws.close()
threading.Thread(target=write_requests,daemon=True).start()
try:
    while True:
        message=ws.recv()
        if not message:break
        if isinstance(message,bytes):message=message.decode()
        sys.stdout.write(message.rstrip('\n')+'\n');sys.stdout.flush()
finally:ws.close()
