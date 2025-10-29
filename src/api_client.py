import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3.util.retry import Retry
from typing import Optional, Tuple
from .logger import get_logger

logger = get_logger(__name__)


class APIClient:
    """HTTP client for the backend API with retries, timeout and safe JSON parsing.

    Methods mirror the previous `api.py` functions and return (status_code, dict-like).
    """

    def __init__(self, base_url: str, timeout: int = 5, retries: int = 3, backoff: float = 0.3):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        retry = Retry(
            total=retries,
            read=retries,
            connect=retries,
            backoff_factor=backoff,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=["GET", "POST", "PATCH"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def _safe_json(self, res: requests.Response):
        try:
            return res.json()
        except Exception:
            try:
                return {"detail": res.text}
            except Exception:
                return {"detail": "Invalid response from server"}

    def _get(self, path: str, json: Optional[dict] = None) -> Tuple[int, dict]:
        url = f"{self.base_url}{path}"
        try:
            res = self._session.get(url, json=json, timeout=self.timeout)
            return res.status_code, self._safe_json(res)
        except RequestException:
            logger.exception("GET %s failed", url)
            return 503, {"detail": "Could not reach the server. Please try again later."}

    def _post(self, path: str, json: Optional[dict] = None) -> Tuple[int, dict]:
        url = f"{self.base_url}{path}"
        try:
            res = self._session.post(url, json=json, timeout=self.timeout)
            return res.status_code, self._safe_json(res)
        except RequestException:
            logger.exception("POST %s failed", url)
            return 503, {"detail": "Could not reach the server. Please try again later."}

    def _patch(self, path: str, json: Optional[dict] = None) -> Tuple[int, dict]:
        url = f"{self.base_url}{path}"
        try:
            res = self._session.patch(url, json=json, timeout=self.timeout)
            return res.status_code, self._safe_json(res) if res.content else {}
        except RequestException:
            logger.exception("PATCH %s failed", url)
            return 503, {"detail": "Could not reach the server. Please try again later."}

    # Public API methods
    def get_users(self, chat_id: int):
        return self._get("/telegram/users", json={"chat_id": str(chat_id)})

    def get_me(self, chat_id: int):
        return self._get("/telegram/me", json={"chat_id": str(chat_id)})

    def generate_voucher(self, chat_id: int, amount: float):
        return self._post("/telegram/generate-voucher", json={"chat_id": str(chat_id), "amount": amount})

    def get_user(self, chat_id: int, username: str):
        return self._get(f"/telegram/user/{username}", json={"chat_id": str(chat_id)})

    def recharge_user(self, chat_id: int, username: str, amount: float):
        payload = {"chat_id": str(chat_id), "username": username, "amount": amount}
        return self._patch("/telegram/recharge", json=payload)

    def adjust_balance(self, chat_id: int, username: str, amount: float):
        payload = {"chat_id": str(chat_id), "username": username, "amount": amount}
        return self._patch("/telegram/balance-adjust", json=payload)

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass
