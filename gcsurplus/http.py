"""HTTP client for gcsurplus.ca with the certificate-chain fix, retries and rate limiting.

gcsurplus.ca serves its leaf certificate without the Entrust intermediate that
signs it. Browsers silently download the missing link (via the AIA URL in the
certificate); Python's ssl module does not, so plain `requests` fails with
"unable to get local issuer certificate". We ship that public intermediate and
append it to the normal trust bundle. TLS verification stays fully on.
"""
import os
import random
import threading
import time
from pathlib import Path

import requests

from .util import BASE_URL

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0 Safari/537.36 gcsurplus-search/0.1 (+personal, low volume)"
)
CERT_DIR = Path(__file__).resolve().parent / "certs"
INTERMEDIATE = CERT_DIR / "entrust-ov-tls-issuing-rsa-ca-2.pem"


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def build_ca_bundle(cache_dir):
    """Write (trusted roots + Entrust intermediate) to cache_dir/ca-bundle.pem and return its path."""
    parts = []
    for env in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
        p = os.environ.get(env)
        if p and Path(p).is_file():
            parts.append(_read(p))
            break
    try:
        import certifi

        parts.append(_read(certifi.where()))
    except ImportError:
        pass
    if not any(parts):
        for sys_path in (
            "/etc/ssl/certs/ca-certificates.crt",
            "/etc/pki/tls/certs/ca-bundle.crt",
            "/etc/ssl/cert.pem",
        ):
            if Path(sys_path).is_file():
                parts.append(_read(sys_path))
                break
    parts.append(_read(INTERMEDIATE))
    data = "\n".join(p.strip() + "\n" for p in parts if p.strip())
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / "ca-bundle.pem"
    if not out.exists() or _read(out) != data:
        out.write_text(data, encoding="utf-8")
    return str(out)


class Client:
    """Thread-safe GET client: one requests.Session per thread, shared politeness delay."""

    def __init__(self, cache_dir, delay=0.4, timeout=40, max_retries=4):
        self.verify = build_ca_bundle(cache_dir)
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self._local = threading.local()
        self._gate = threading.Lock()
        self._next_slot = 0.0
        self.requests_made = 0

    def _session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "en-CA,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml",
                }
            )
            self._local.session = s
        return s

    def _wait_turn(self):
        """Global rate limit: at most one request every `delay` seconds across all threads."""
        with self._gate:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self.delay
        if wait > 0:
            time.sleep(wait)

    def get(self, path, params=None):
        url = BASE_URL + path.lstrip("/")
        last_error = None
        for attempt in range(self.max_retries):
            self._wait_turn()
            try:
                r = self._session().get(url, params=params, timeout=self.timeout, verify=self.verify)
                self.requests_made += 1
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                r.raise_for_status()
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            except requests.RequestException as e:
                last_error = e
                time.sleep(min(2 ** attempt, 20) + random.random())
        raise RuntimeError(f"GET {url} failed after {self.max_retries} attempts: {last_error}")
