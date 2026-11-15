"""Client for Arbetsförmedlingen JobSearch (https://jobsearch.api.jobtechdev.se).

Verified in Phase 0: anonymous GETs work; limit max 100; /ad/{id} returns 200 with a
nulled stub for removed ads and 404 for unknown ids; 429 is documented for rate limits.
"""
from __future__ import annotations

import json
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://jobsearch.api.jobtechdev.se"
USER_AGENT = "personal-job-assistant/0.1 (single-user local tool)"


def _ssl_context() -> ssl.SSLContext:
    """Verified TLS always. python.org builds on macOS ship no CA bundle, so when the default
    store is empty we load the macOS system root certificates (the same trust curl uses).
    Verification is never disabled."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"] == 0 and sys.platform == "darwin":
        pem = subprocess.run(
            ["/usr/bin/security", "find-certificate", "-a", "-p",
             "/System/Library/Keychains/SystemRootCertificates.keychain"],
            capture_output=True, text=True, check=False).stdout
        if pem:
            ctx.load_verify_locations(cadata=pem)
    return ctx


class SourceError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class JobTechClient:
    def __init__(self, api_key: str | None = None, min_interval: float = 0.5,
                 retries: int = 3, timeout: float = 30.0, sleep=time.sleep):
        self.api_key = api_key
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self._sleep = sleep
        self._last = 0.0
        self._ctx = _ssl_context()

    def _get(self, path: str, params: dict | None = None) -> tuple[int, dict | None]:
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"accept": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["api-key"] = self.api_key
        last_err = "unknown"
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                            timeout=self.timeout, context=self._ctx) as r:
                    return r.status, json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return 404, None
                last_err = f"HTTP {e.code}"
                if e.code == 429 or e.code >= 500:
                    ra = e.headers.get("Retry-After")
                    delay = float(ra) if ra and ra.isdigit() else 2.0 * (2 ** attempt)
                    if attempt < self.retries:
                        self._sleep(delay)
                        continue
                raise SourceError(last_err, e.code) from None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
                last_err = f"{type(e).__name__}: {e}"
                if attempt < self.retries:
                    self._sleep(2.0 * (2 ** attempt))
                    continue
        raise SourceError(last_err)

    def search(self, params: dict) -> dict:
        status, body = self._get("/search", params)
        if body is None or "hits" not in body:
            raise SourceError("search response missing 'hits'", status)
        return body

    def get_ad(self, ad_id: str) -> dict | None:
        """Full ad, a removed-stub (removed=True, headline None), or None when 404."""
        _, body = self._get(f"/ad/{urllib.parse.quote(str(ad_id))}")
        return body
