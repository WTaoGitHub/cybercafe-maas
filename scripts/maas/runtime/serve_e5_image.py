"""Temporary LAN transfer of one non-secret image archive to one pilot worker.

Bind only the controller LAN IP; no directory listing or arbitrary paths. Stop
the transient systemd unit after transfer. This is not a model registry service.
"""
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import shutil

IMAGE=Path('/root/wtaobj/maas-e5-ocr/ocr-image.tar.gz')


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.client_address[0]!='10.11.31.2' or self.path!='/ocr-image.tar.gz':
            self.send_error(403);return
        if not IMAGE.is_file():self.send_error(503);return
        with IMAGE.open('rb') as source:
            self.send_response(200)
            self.send_header('Content-Type','application/gzip')
            self.send_header('Content-Length',str(IMAGE.stat().st_size))
            self.end_headers()
            shutil.copyfileobj(source,self.wfile,1024*1024)


if __name__=='__main__':
    HTTPServer(('10.11.31.1',18841),Handler).serve_forever()
