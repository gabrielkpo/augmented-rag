"""Minimal stdio MCP client for a LAB gbrain server (`gbrain-lab <name> serve`)."""
import json
import os
import subprocess


class Lab:
    def __init__(self, name, src):
        self.proc = subprocess.Popen(
            [os.path.expanduser('~/gbrain-labs/bin/gbrain-lab'), name, 'serve'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env={**os.environ, 'GBRAIN_SRC': src})
        self.next_id = 0
        self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                'clientInfo': {'name': 'dated-lab', 'version': '1'}})
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + '\n')
        self.proc.stdin.flush()

    def rpc(self, method, params):
        self.next_id += 1
        self.send({'jsonrpc': '2.0', 'id': self.next_id, 'method': method, 'params': params})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError('lab server closed')
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get('id') == self.next_id:
                if 'error' in msg:
                    raise RuntimeError(json.dumps(msg['error'])[:400])
                return msg['result']

    def call(self, tool, args):
        """Tool result as parsed JSON (or text), and whether the tool reported an error."""
        res = self.rpc('tools/call', {'name': tool, 'arguments': args})
        text = ''.join(c.get('text', '') for c in res.get('content') or [])
        # The JSON body can be followed by `[gbrain notice ...]` blocks: read the leading JSON value.
        try:
            body, _ = json.JSONDecoder().raw_decode(text.lstrip())
        except ValueError:
            body = text
        return body, bool(res.get('isError'))

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=30)
