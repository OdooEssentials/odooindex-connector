import json
import urllib.error
import urllib.request

DEFAULT_API_URL = "https://odooindex.com/api/v1"


class OdooIndexError(Exception):
    pass


class ConfigurationError(OdooIndexError):
    pass


class OdooIndexAPIError(OdooIndexError):
    pass


class OdooIndexConnectionError(OdooIndexError):
    pass


class OdooIndexClient:
    """Low-level HTTPS client for the OdooIndex API."""

    def __init__(self, api_token=None, base_url=DEFAULT_API_URL):
        self.api_token = api_token or ""
        self.base_url = base_url.rstrip("/")

    def require_token(self):
        if not self.api_token:
            raise ConfigurationError("OdooIndex API token must be configured.")

    def request(self, path, method="GET", payload=None, timeout=30):
        url = f"{self.base_url}{path}"
        data = None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8") if exc else ""
            raise OdooIndexAPIError(f"OdooIndex API error: {body}") from exc
        except Exception as exc:
            raise OdooIndexConnectionError(f"OdooIndex request failed: {exc}") from exc
