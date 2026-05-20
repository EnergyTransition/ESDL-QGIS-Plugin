import os

import requests


_DEFAULT_DRIVE_URL = os.environ.get('ESDL_DRIVE_URL', 'http://localhost:9080')

browse_endpoint = "/store/browse"
resource_endpoint = "/store/resource"

class ESDLDrive:
    def __init__(self, base_url: str = None):
        self.base_url = (base_url or _DEFAULT_DRIVE_URL).rstrip('/')

    def browse_cdo(self, params=None, token=None):
        headers = {}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            r = requests.get(self.base_url + browse_endpoint,
                             params=params or {}, headers=headers, timeout=30)
        except Exception as e:
            return {'status': 500, 'error': str(e)}
        if r.ok:
            if 'application/json' in r.headers.get('Content-Type', ''):
                return {'status': r.status_code, 'json': r.json()}
            return {'status': r.status_code, 'error': 'Unexpected content type: ' + r.headers.get('Content-Type', '')}
        return {'status': r.status_code, 'error': f"HTTP {r.status_code}: {r.reason}"}

    def get_resource(self, url: str, token: str = None) -> dict:
        """Fetch a single ESDL resource by its full URL.

        Returns {'status': int, 'content': str} on success,
        or {'status': int, 'error': str} on failure.
        """
        headers = {}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            r = requests.get(url, headers=headers, timeout=30)
        except Exception as e:
            return {'status': 500, 'error': str(e)}
        if r.ok:
            return {
                'status': r.status_code,
                'content': r.text,
                'content_type': r.headers.get('Content-Type', ''),
            }
        return {'status': r.status_code, 'error': f"HTTP {r.status_code}: {r.reason}"}

    def put_resource(self, url: str, xml_content: str, token: str = None) -> dict:
        """Save (overwrite) an ESDL resource on the Drive via HTTP PUT.

        Returns {'status': int} on success,
        or {'status': int, 'error': str} on failure.
        """
        headers = {'Content-Type': 'application/xml'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            r = requests.put(url, data=xml_content.encode('utf-8'),
                             headers=headers, timeout=30)
        except Exception as e:
            return {'status': 500, 'error': str(e)}
        if r.ok:
            return {'status': r.status_code}
        return {'status': r.status_code, 'error': f"HTTP {r.status_code}: {r.reason}"}