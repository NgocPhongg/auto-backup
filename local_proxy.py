"""
Local SOCKS5-to-HTTP Proxy Bridge.
Chrome kết nối HTTP proxy tại 127.0.0.1:LOCAL_PORT
→ Forward qua SOCKS5 proxy có auth tại upstream.
"""
import socket
import struct
import threading
import select


class Socks5Bridge:
    """Bridge: HTTP Proxy (localhost) → SOCKS5 Proxy (upstream có auth)."""

    def __init__(self, local_port, socks_host, socks_port, socks_user="", socks_pass=""):
        self.local_port = local_port
        self.socks_host = socks_host
        self.socks_port = int(socks_port)
        self.socks_user = socks_user
        self.socks_pass = socks_pass
        self._server = None
        self._running = False

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.settimeout(1)
        self._server.bind(("127.0.0.1", self.local_port))
        self._server.listen(50)
        self._running = True
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        return self.local_port

    def stop(self):
        self._running = False
        if self._server:
            try: self._server.close()
            except: pass

    def _run(self):
        print(f"[SOCKS5 Bridge] 127.0.0.1:{self.local_port} -> {self.socks_host}:{self.socks_port}")

        while self._running:
            try:
                client, _ = self._server.accept()
                threading.Thread(target=self._handle, args=(client,), daemon=True).start()
            except socket.timeout:
                continue
            except:
                break

    def _socks5_connect(self, target_host, target_port):
        """Kết nối tới target qua SOCKS5 proxy."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(15)
        s.connect((self.socks_host, self.socks_port))

        # Handshake: hỗ trợ username/password auth (method 0x02)
        if self.socks_user:
            s.sendall(b'\x05\x01\x02')
        else:
            s.sendall(b'\x05\x01\x00')

        resp = s.recv(2)
        if len(resp) < 2 or resp[0] != 5:
            s.close()
            return None

        # Auth nếu cần
        if resp[1] == 0x02:
            user = self.socks_user.encode()
            pw = self.socks_pass.encode()
            s.sendall(b'\x01' + bytes([len(user)]) + user + bytes([len(pw)]) + pw)
            auth_resp = s.recv(2)
            if len(auth_resp) < 2 or auth_resp[1] != 0x00:
                s.close()
                return None

        # CONNECT request
        # Dùng domain name (type 0x03) thay vì resolve IP
        host_bytes = target_host.encode()
        req = b'\x05\x01\x00\x03' + bytes([len(host_bytes)]) + host_bytes
        req += struct.pack('>H', target_port)
        s.sendall(req)

        resp = s.recv(10)
        if len(resp) < 2 or resp[1] != 0x00:
            s.close()
            return None

        # Đọc hết phần response còn lại (BND.ADDR + BND.PORT)
        if resp[3] == 0x01:  # IPv4
            if len(resp) < 10:
                s.recv(10 - len(resp))
        elif resp[3] == 0x03:  # Domain
            domain_len = resp[4]
            remaining = 5 + domain_len + 2 - len(resp)
            if remaining > 0:
                s.recv(remaining)

        return s

    def _handle(self, client):
        """Xử lý 1 request từ Chrome."""
        try:
            data = client.recv(8192)
            if not data:
                client.close()
                return

            first_line = data.split(b'\r\n')[0].decode('utf-8', errors='ignore')
            parts = first_line.split(' ')

            if parts[0] == 'CONNECT':
                # HTTPS: CONNECT host:port HTTP/1.1
                host_port = parts[1].split(':')
                host = host_port[0]
                port = int(host_port[1]) if len(host_port) > 1 else 443

                upstream = self._socks5_connect(host, port)
                if upstream:
                    client.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    self._tunnel(client, upstream)
                else:
                    client.sendall(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
                    client.close()
            else:
                # HTTP: GET http://host/path HTTP/1.1
                if parts[1].startswith('http://'):
                    url = parts[1][7:]  # Bỏ http://
                    slash = url.find('/')
                    if slash > 0:
                        host_part = url[:slash]
                    else:
                        host_part = url

                    if ':' in host_part:
                        host, port = host_part.split(':')
                        port = int(port)
                    else:
                        host = host_part
                        port = 80

                    upstream = self._socks5_connect(host, port)
                    if upstream:
                        # Sửa lại request: bỏ http://host khỏi URL
                        modified = data.replace(f'http://{host_part}'.encode(), b'', 1)
                        upstream.sendall(modified)
                        self._tunnel(client, upstream)
                    else:
                        client.sendall(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
                        client.close()
                else:
                    client.close()

        except Exception as e:
            print(f"[SOCKS5 Bridge] Error: {e}")
            try: client.close()
            except: pass

    def _tunnel(self, s1, s2):
        """Forward data 2 chiều."""
        socks = [s1, s2]
        try:
            while True:
                r, _, e = select.select(socks, [], socks, 60)
                if e: break
                if not r: break
                for s in r:
                    data = s.recv(65536)
                    if not data: return
                    (s2 if s is s1 else s1).sendall(data)
        except:
            pass
        finally:
            try: s1.close()
            except: pass
            try: s2.close()
            except: pass


class HttpBridge:
    """Bridge: HTTP Proxy (localhost, no auth) -> HTTP Proxy (upstream, co auth)."""

    def __init__(self, local_port, upstream_host, upstream_port, upstream_user="", upstream_pass=""):
        self.local_port = local_port
        self.upstream_host = upstream_host
        self.upstream_port = int(upstream_port)
        self.upstream_user = upstream_user
        self.upstream_pass = upstream_pass
        self._server = None
        self._running = False

        # Tao header Proxy-Authorization
        import base64
        if upstream_user and upstream_pass:
            creds = f"{upstream_user}:{upstream_pass}"
            self._auth_header = f"Proxy-Authorization: Basic {base64.b64encode(creds.encode()).decode()}\r\n"
        else:
            self._auth_header = ""

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.settimeout(1)
        self._server.bind(("127.0.0.1", self.local_port))
        self._server.listen(50)
        self._running = True
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        return self.local_port

    def stop(self):
        self._running = False
        if self._server:
            try: self._server.close()
            except: pass

    def _run(self):
        print(f"[HTTP Bridge] 127.0.0.1:{self.local_port} -> {self.upstream_host}:{self.upstream_port}")

        while self._running:
            try:
                client, _ = self._server.accept()
                threading.Thread(target=self._handle, args=(client,), daemon=True).start()
            except socket.timeout:
                continue
            except:
                break

    def _handle(self, client):
        """Xu ly 1 request tu Chrome, forward qua HTTP proxy upstream."""
        try:
            data = client.recv(8192)
            if not data:
                client.close()
                return

            # Ket noi toi upstream HTTP proxy
            upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            upstream.settimeout(15)
            upstream.connect((self.upstream_host, self.upstream_port))

            # Chen Auth header vao request
            if self._auth_header:
                request_str = data.decode("utf-8", errors="ignore")
                parts = request_str.split("\r\n", 1)
                modified = parts[0] + "\r\n" + self._auth_header
                if len(parts) > 1:
                    modified += parts[1]
                upstream.sendall(modified.encode())
            else:
                upstream.sendall(data)

            # Kiem tra CONNECT hay HTTP thuong
            first_line = data.split(b'\r\n')[0].decode('utf-8', errors='ignore')
            if first_line.startswith('CONNECT'):
                # Doc response tu upstream
                resp = upstream.recv(8192)
                resp_line = resp.decode('utf-8', errors='ignore')
                if '200' in resp_line.split('\r\n')[0]:
                    client.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    self._tunnel(client, upstream)
                else:
                    client.sendall(resp)
                    client.close()
                    upstream.close()
            else:
                # HTTP thuong - tunnel 2 chieu
                self._tunnel(client, upstream)

        except Exception as e:
            print(f"[HTTP Bridge] Error: {e}")
            try: client.close()
            except: pass

    def _tunnel(self, s1, s2):
        """Forward data 2 chieu."""
        socks = [s1, s2]
        try:
            while True:
                r, _, e = select.select(socks, [], socks, 60)
                if e: break
                if not r: break
                for s in r:
                    data = s.recv(65536)
                    if not data: return
                    (s2 if s is s1 else s1).sendall(data)
        except:
            pass
        finally:
            try: s1.close()
            except: pass
            try: s2.close()
            except: pass


def create_local_proxy(local_port, proxy_string, proxy_type="socks5"):
    """
    Tao bridge tu "host:port:user:pass".
    proxy_type: "socks5" hoac "http"
    Chrome ket noi HTTP toi 127.0.0.1:local_port
    """
    proxy_string = (proxy_string or "").strip()
    proxy_type = (proxy_type or "socks5").strip().lower()
    if "://" in proxy_string:
        scheme, proxy_string = proxy_string.split("://", 1)
        proxy_type = scheme.strip().lower()
    if proxy_type == "socks5h":
        proxy_type = "socks5"

    parts = proxy_string.split(":", 3)
    if len(parts) < 2:
        return None
    host, port = parts[0].strip(), parts[1].strip()
    if not host or not port.isdigit():
        return None
    user = parts[2].strip() if len(parts) >= 3 else ""
    pw = parts[3].strip() if len(parts) >= 4 else ""

    if proxy_type == "socks5":
        bridge = Socks5Bridge(local_port, host, int(port), user, pw)
    elif proxy_type in ("http", "https"):
        bridge = HttpBridge(local_port, host, int(port), user, pw)
    else:
        return None

    try:
        bridge.start()
    except OSError:
        return None
    return bridge


if __name__ == "__main__":
    import sys
    ptype = sys.argv[1] if len(sys.argv) > 1 else "socks5"
    pstring = sys.argv[2] if len(sys.argv) > 2 else "27.76.81.180:31477:viproxy:PYoCcJnmSr"
    bridge = create_local_proxy(18080, pstring, ptype)
    print(f"Bridge ({ptype}) running on 127.0.0.1:18080")
    input("Enter to stop...")
    bridge.stop()
