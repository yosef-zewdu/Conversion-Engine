"""Minimal webhook listener for Africa's Talking SMS sandbox testing."""
from http.server import HTTPServer, BaseHTTPRequestHandler


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        print("POST received:", body.decode())
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass  # suppress default access log noise


if __name__ == "__main__":
    server = HTTPServer(("", 8000), WebhookHandler)
    print("Listening on http://localhost:8000 ...")
    server.serve_forever()
