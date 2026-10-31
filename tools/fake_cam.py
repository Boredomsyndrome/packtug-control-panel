#!/usr/bin/env python3
"""Fake MJPEG camera (emulates web_video_server) for offline testing.

Usage: fake_cam.py [port]   (default 9093)
"""
import http.server
import socketserver
import sys

# smallest 1x1 JPEG
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
    "07090908080a0c140d0c0b0b0c1912130f141d181f1f1e1d2b2427252d2c2932373c"
    "32333a36343a3b3d3b3c39353a3a3e4143444145444344ffc0000b0800010001010111"
    "00ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
    "ffc400b5100002010303020403050504040000017d0102030004110512213141061351"
    "6107227114328191a12342b1c1081552d1f02433627282090a161718191a2526272829"
    "2a3435363738393a33434434534644748494a535455565758595a636465666768696a"
    "737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aac2"
    "c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8"
    "f9faffda0008010100063f02a116e5c00ffd9"
)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.path.startswith("/stream"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            import time
            while True:
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                self.wfile.write(JPEG)
                self.wfile.write(b"\r\n")
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9093
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        print(f"fake cam on http://127.0.0.1:{port}/stream")
        httpd.serve_forever()
