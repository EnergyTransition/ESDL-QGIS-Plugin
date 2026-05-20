import base64
import hashlib
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from qgis.PyQt.QtCore import QUrl, QThread, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices

_REDIRECT_PORT = 9988
_REDIRECT_URI = f"http://localhost:{_REDIRECT_PORT}/callback"


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b'=').decode()
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.auth_code = params.get('code', [None])[0]
        err_list = params.get('error_description', params.get('error', [None]))
        self.server.auth_error = err_list[0] if err_list else None
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(
            b"<html><body>"
            b"<h2>Authentication complete.</h2>"
            b"<p>You may close this tab and return to QGIS.</p>"
            b"</body></html>"
        )

    def log_message(self, format, *args):
        pass  # suppress console output


class AuthWorker(QThread):
    """
    Runs the OAuth2 Authorization Code + PKCE flow off the main Qt thread.

    Flow:
      1. Builds the Keycloak auth URL and starts a local callback server.
      2. Emits open_browser — the main thread must connect this to
         QDesktopServices.openUrl so the browser opens on the right thread.
      3. Blocks (up to 120 s) for the redirect callback.
      4. Exchanges the code for an access token and emits success or failed.
    """

    open_browser = pyqtSignal(str)  # auth URL — open on main thread
    success = pyqtSignal(str)       # access_token
    failed = pyqtSignal(str)        # error message

    def __init__(self, keycloak_realm_url: str, client_id: str = "cdo-mondaine",
                 client_secret: str = None):
        super().__init__()
        self.keycloak_realm_url = keycloak_realm_url.rstrip('/')
        self.client_id = client_id
        self.client_secret = client_secret

    def run(self):
        try:
            verifier, challenge = _pkce_pair()

            auth_url = (
                f"{self.keycloak_realm_url}/protocol/openid-connect/auth?"
                + urllib.parse.urlencode({
                    'response_type': 'code',
                    'client_id': self.client_id,
                    'redirect_uri': _REDIRECT_URI,
                    'code_challenge': challenge,
                    'code_challenge_method': 'S256',
                    'scope': 'openid',
                })
            )

            # Start the callback server *before* opening the browser so the
            # redirect is captured even if the user logs in very quickly.
            server = HTTPServer(('127.0.0.1', _REDIRECT_PORT), _CallbackHandler)
            server.auth_code = None
            server.auth_error = None
            server.timeout = 120  # seconds to wait for the browser redirect

            # Tell the main thread to open the browser
            self.open_browser.emit(auth_url)

            # Block until the browser sends the redirect callback (one request)
            server.handle_request()
            server.server_close()

            if server.auth_error:
                self.failed.emit(f"Keycloak error: {server.auth_error}")
                return

            if server.auth_code is None:
                self.failed.emit(
                    "No authorisation code received — authentication may have timed out."
                )
                return

            # Exchange authorisation code for tokens
            token_data = {
                'grant_type': 'authorization_code',
                'client_id': self.client_id,
                'redirect_uri': _REDIRECT_URI,
                'code': server.auth_code,
                'code_verifier': verifier,
            }
            if self.client_secret:
                token_data['client_secret'] = self.client_secret
            resp = requests.post(
                f"{self.keycloak_realm_url}/protocol/openid-connect/token",
                data=token_data,
                timeout=30,
            )
            resp.raise_for_status()
            self.success.emit(resp.json()['access_token'])

        except Exception as exc:
            self.failed.emit(str(exc))


class PasswordAuthWorker(QThread):
    """
    Authenticates via Keycloak's Resource Owner Password Credentials grant.
    No browser redirect — credentials are sent directly to the token endpoint.

    Requires "Direct Access Grants Enabled" on the Keycloak client.
    """

    success = pyqtSignal(str)   # access_token
    failed  = pyqtSignal(str)   # error message

    def __init__(self, keycloak_realm_url: str, username: str, password: str,
                 client_id: str = "cdo-mondaine", client_secret: str = None):
        super().__init__()
        self.keycloak_realm_url = keycloak_realm_url.rstrip('/')
        self.username = username
        self.password = password
        self.client_id = client_id
        self.client_secret = client_secret

    def run(self):
        try:
            token_url = f"{self.keycloak_realm_url}/protocol/openid-connect/token"
            data = {
                'grant_type': 'password',
                'client_id': self.client_id,
                'username': self.username,
                'password': self.password,
                'scope': 'openid',
            }
            if self.client_secret:
                data['client_secret'] = self.client_secret

            resp = requests.post(token_url, data=data, timeout=30)
            resp.raise_for_status()
            self.success.emit(resp.json()['access_token'])

        except Exception as exc:
            self.failed.emit(str(exc))
