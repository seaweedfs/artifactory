#!/usr/bin/env python3
import base64
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class BasicAuthHandler(SimpleHTTPRequestHandler):
    expected = ""

    def do_AUTHHEAD(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="RDMA Lab CI"')
        self.send_header("Content-type", "text/plain")
        self.end_headers()

    def is_authenticated(self):
        header = self.headers.get("Authorization")
        return header == self.expected

    def do_GET(self):
        if not self.is_authenticated():
            self.do_AUTHHEAD()
            self.wfile.write(b"authentication required\n")
            return
        super().do_GET()

    def do_HEAD(self):
        if not self.is_authenticated():
            self.do_AUTHHEAD()
            return
        super().do_HEAD()


def main():
    root = os.environ.get("RDMA_CI_ROOT", "/opt/rdma-lab-ci/artifacts")
    host = os.environ.get("RDMA_CI_HOST", "0.0.0.0")
    port = int(os.environ.get("RDMA_CI_PORT", "8091"))
    user = os.environ["RDMA_CI_USER"]
    password = os.environ["RDMA_CI_PASSWORD"]
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    BasicAuthHandler.expected = f"Basic {token}"

    handler = partial(BasicAuthHandler, directory=root)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"serving {root} on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
