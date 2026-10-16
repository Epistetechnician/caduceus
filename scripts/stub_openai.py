"""Canned OpenAI-compatible chat-completions server on 127.0.0.1 (test only).

`serve(respond)` starts a daemon-thread server and returns
`(server, base_url, calls)`. `respond(body, calls)` gets the parsed request and
the list of chat requests so far (this one included) and returns an assistant
message dict. Supports streaming and non-streaming replies. No model, no
network beyond loopback.
"""

import http.server
import json
import threading


def serve(respond):
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self._send({"object": "list", "data": [{"id": "stub-model", "object": "model"}]})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not self.path.endswith("/chat/completions"):
                self._send({})
                return
            calls.append(body)
            message = respond(body, calls)
            finish = "tool_calls" if message.get("tool_calls") else "stop"
            if body.get("stream"):
                self._stream(message, finish)
            else:
                self._send({
                    "id": "stub", "object": "chat.completion", "created": 0, "model": "stub-model",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                })

        def _send(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _stream(self, message, finish):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = {"role": "assistant"}
            if message.get("tool_calls"):
                delta["tool_calls"] = [dict(tc, index=i) for i, tc in enumerate(message["tool_calls"])]
            else:
                delta["content"] = message["content"]
            base = {"id": "stub", "object": "chat.completion.chunk", "created": 0, "model": "stub-model"}
            chunks = [
                dict(base, choices=[{"index": 0, "delta": delta, "finish_reason": None}]),
                dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": finish}],
                     usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
            ]
            for c in chunks:
                self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d/v1" % server.server_address[1], calls
