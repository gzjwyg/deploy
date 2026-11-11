"""lx-deploy system operations."""

import http.client
import socket
import ssl
import subprocess
import time

from . import layout


def say(message):
    print(message, flush=True)


class Server:
    """Commands and HTTP requests. Tests replace this with a fake."""

    def sh(self, command, *, input=None, check=True, quiet=False, timeout=900):
        result = subprocess.run(
            command,
            input=input,
            capture_output=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            # Only the command's name: its output may hold paths or settings.
            raise CommandFailed(command[:3], result.returncode)
        return result

    def compose(self, *args, check=True, timeout=900):
        return self.sh(
            ["docker", "compose", "--project-directory", str(layout.L.etc), *args], check=check, timeout=timeout
        )

    def request(self, target, method, path, headers=None, body=None):
        """target: a public host name (HTTPS to this server's own Caddy, with that name's real
        certificate) or a loopback port number (plain HTTP). Returns (status, headers, body)."""
        headers = {"User-Agent": "lx-deploy", **(headers or {})}
        if isinstance(target, int):
            connection = http.client.HTTPConnection("127.0.0.1", target, timeout=10)
        else:
            connection = LocalHTTPS(target)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read(1_000_000)
        finally:
            connection.close()

    def wait(self, seconds):
        time.sleep(seconds)


class LocalHTTPS(http.client.HTTPSConnection):
    """HTTPS to 127.0.0.1:443 with the public name for SNI and certificate checks, so the
    checks test Caddy and its real certificates without depending on DNS from the server."""

    def __init__(self, host):
        super().__init__(host, 443, timeout=10, context=ssl.create_default_context())

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class CommandFailed(Exception):
    def __init__(self, command, code):
        super().__init__(f"{' '.join(command)} exited with {code}")


class Busy(Exception):
    pass
