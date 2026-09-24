# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP client for the backend. Standard library only (no pip installs inside
Blender), and no bpy imports, so it can run on a worker thread."""

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "thornbury-lighting-addon"


class BackendError(Exception):
    def __init__(self, message, status=0, code=""):
        super().__init__(message)
        self.status = status
        self.code = code


def _ssl_context():
    # Some Blender builds (notably on macOS) ship Python without the system CA
    # store; use certifi's bundle when it's available.
    try:
        import certifi  # noqa: PLC0415

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover - depends on the Blender build
        return ssl.create_default_context()


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def check_url(base_url):
    """https anywhere; plain http only to this machine (the key is a secret)."""
    base_url = (base_url or "").strip().rstrip("/")
    try:
        u = urllib.parse.urlsplit(base_url)
        host = u.hostname
        u.port  # raises ValueError on a malformed port
    except ValueError:
        host = None
    ok = host and not u.username and not u.password and "@" not in u.netloc and (
        u.scheme == "https" or (u.scheme == "http" and host in LOCAL_HOSTS))
    if not ok:
        raise BackendError("The backend URL must start with https:// (or be http://127.0.0.1 for local testing).")
    return base_url


def _request(method, base_url, path, api_key, body=None, timeout=35):
    base_url = check_url(base_url)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base_url + path, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", "Bearer " + api_key.strip())
    ctx = _ssl_context() if base_url.startswith("https://") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
            msg, code = payload.get("error") or e.reason, payload.get("code", "")
        except Exception:
            msg, code = "The backend returned HTTP %d." % e.code, ""
        if e.code == 403 and not code:
            msg = "The backend refused the request (HTTP 403). Check the backend URL."
        raise BackendError(msg, e.code, code) from None
    except urllib.error.URLError as e:
        raise BackendError("Can't reach the backend (%s). Check the URL and your connection." % e.reason) from None
    except (TimeoutError, OSError) as e:
        raise BackendError("The backend didn't answer in time (%s)." % e) from None
    except ValueError:
        raise BackendError("The backend sent a response that isn't JSON.") from None


def me(base_url, api_key, timeout=15):
    return _request("GET", base_url, "/v1/me", api_key, timeout=timeout)


def suggest(base_url, api_key, payload, timeout=35):
    return _request("POST", base_url, "/v1/suggest", api_key, payload, timeout=timeout)


def outcome(base_url, api_key, request_id, outcome_name, applied_values=None, timeout=15):
    body = {"request_id": request_id, "outcome": outcome_name}
    if applied_values is not None:
        body["applied_values"] = applied_values
    return _request("POST", base_url, "/v1/outcome", api_key, body, timeout=timeout)
