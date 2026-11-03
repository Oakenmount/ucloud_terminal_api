"""Thin client for the UCloud/Core REST API (the same API the cloud.sdu.dk web UI uses).

Endpoint conventions (from UCloud's ``rpc`` package):
  create    -> POST   /api/<ctx>                 body: {"items": [...]}
  browse    -> GET    /api/<ctx>/browse          query params
  retrieve  -> GET    /api/<ctx>/retrieve<Op>    query params
  update    -> POST   /api/<ctx>/<op>            body: {"items": [...]}

Authentication uses a platform API token (create one at https://cloud.sdu.dk/app/api-tokens).
The token is bound to a workspace (personal or project), so no ``Project`` header is needed
unless you want to override it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

DEFAULT_BASE_URL = "https://cloud.sdu.dk"
DEFAULT_TOKEN_FILE = Path("~/.config/ucloud/token").expanduser()


class UCloudError(RuntimeError):
    def __init__(self, status: int, message: str, path: str = ""):
        self.status = status
        self.path = path
        self.message = message
        super().__init__(f"UCloud API error {status} on {path}: {message}" if path else message)


def load_token(token: Optional[str] = None) -> str:
    """Token resolution order: explicit arg, $UCLOUD_TOKEN, $UCLOUD_TOKEN_FILE, ~/.config/ucloud/token."""
    if token:
        return token.strip()
    env = os.environ.get("UCLOUD_TOKEN")
    if env:
        return env.strip()
    path = Path(os.environ.get("UCLOUD_TOKEN_FILE", DEFAULT_TOKEN_FILE)).expanduser()
    if path.exists():
        return path.read_text().strip()
    raise UCloudError(
        401,
        "No API token found. Create a platform token at https://cloud.sdu.dk/app/api-tokens and "
        f"save it to {DEFAULT_TOKEN_FILE} (chmod 600) or set $UCLOUD_TOKEN.",
    )


class UCloudClient:
    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
        project: Optional[str] = None,
        timeout: float = 60.0,
    ):
        self.token = load_token(token)
        self.base_url = (base_url or os.environ.get("UCLOUD_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.project = project or os.environ.get("UCLOUD_PROJECT")
        self.timeout = timeout

    # ------------------------------------------------------------------ transport

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                 body: Any = None) -> Any:
        url = self.base_url + path
        if params:
            clean = {k: _qs(v) for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = None
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if self.project:
            headers["Project"] = self.project
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            msg = raw.decode(errors="replace")
            try:
                parsed = json.loads(msg)
                msg = parsed.get("why") or parsed.get("message") or msg
            except (ValueError, AttributeError):
                pass
            raise UCloudError(e.code, msg.strip() or e.reason, path) from None
        except urllib.error.URLError as e:
            raise UCloudError(0, str(e.reason), path) from None
        if not raw:
            return None
        return json.loads(raw)

    def get(self, endpoint: str, **params: Any) -> Any:
        return self._request("GET", endpoint, params=params)

    def post(self, path: str, body: Any) -> Any:
        return self._request("POST", path, body=body)

    def browse_all(self, endpoint: str, items_per_page: int = 250, **params: Any) -> Iterator[Dict]:
        nxt = None
        while True:
            page = self.get(endpoint, itemsPerPage=items_per_page, next=nxt, **params)
            yield from page.get("items", [])
            nxt = page.get("next")
            if not nxt:
                return

    # ------------------------------------------------------------------ applications

    def retrieve_application(self, name: str, version: Optional[str] = None) -> Dict:
        return self.get("/api/hpc/apps/byNameAndVersion", appName=name, appVersion=version)

    # ------------------------------------------------------------------ products / machine types

    def job_products(self) -> List[Dict]:
        """Flattened list of {product, support} for every compute product available to the workspace."""
        resp = self.get("/api/jobs/retrieveProducts")
        out = []
        for _provider, items in (resp.get("productsByProvider") or {}).items():
            out.extend(items)
        return out

    def wallets(self, product_type: str = "COMPUTE") -> List[Dict]:
        """Allocations of the active workspace (quota / usage / maxUsable per product category)."""
        return list(self.browse_all("/api/accounting/v2/browseWallets", filterType=product_type))

    # ------------------------------------------------------------------ jobs

    def create_jobs(self, specs: List[Dict]) -> List[str]:
        resp = self.post("/api/jobs", {"items": specs})
        return [r["id"] for r in (resp or {}).get("responses", []) if r]

    def retrieve_job(self, job_id: str) -> Dict:
        return self.get("/api/jobs/retrieve", id=job_id, includeUpdates=True,
                        includeParameters=True, includeProduct=True)

    def browse_jobs(self, application: Optional[str] = None, state: Optional[str] = None,
                    limit: int = 50) -> List[Dict]:
        page = self.get("/api/jobs/browse", itemsPerPage=limit, includeUpdates=True,
                        includeParameters=True, filterApplication=application, filterState=state)
        return page.get("items", [])

    def extend_job(self, job_id: str, hours: int = 0, minutes: int = 0, seconds: int = 0) -> None:
        self.post("/api/jobs/extend", {"items": [{
            "jobId": job_id,
            "requestedTime": {"hours": hours, "minutes": minutes, "seconds": seconds},
        }]})

    def terminate_job(self, job_id: str) -> None:
        self.post("/api/jobs/terminate", {"items": [{"id": job_id}]})

    # ------------------------------------------------------------------ files

    def browse_drives(self) -> List[Dict]:
        return list(self.browse_all("/api/files/collections/browse"))

    def list_files(self, path: str) -> List[Dict]:
        return list(self.browse_all("/api/files/browse", path=path))

    def stat_file(self, path: str) -> Dict:
        return self.get("/api/files/retrieve", id=path)

    # ------------------------------------------------------------------ ssh keys

    def ssh_keys(self) -> List[Dict]:
        return list(self.browse_all("/api/ssh/browse"))

    def add_ssh_key(self, title: str, public_key: str) -> str:
        resp = self.post("/api/ssh", {"items": [{"title": title, "key": public_key.strip()}]})
        return resp["responses"][0]["id"]


def _qs(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)
