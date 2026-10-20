"""One request, LAN-only, random capability URL; archive is never exposed as a directory."""
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import secrets,shutil

archive=Path('/root/wtaobj/maas-p2/ocr-p2-r1.tar.gz')
path='/'+secrets.token_urlsafe(32)
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.client_address[0]!='10.11.31.2' or self.path!=path:
            self.send_error(403);return
        self.send_response(200);self.send_header('Content-Length',str(archive.stat().st_size));self.end_headers()
        with archive.open('rb') as source:shutil.copyfileobj(source,self.wfile,1024*1024)

server=HTTPServer(('10.11.31.1',18839),Handler);server.timeout=600
print('http://10.11.31.1:18839'+path,flush=True)
server.handle_request();server.server_close()
