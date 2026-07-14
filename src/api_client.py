import asyncio
import httpx
from typing import Optional, Tuple
from .logger import get_logger

logger = get_logger(__name__)

_RETRY_STATUS = (500, 502, 503, 504)


class APIClient:
    """Async HTTP client for the backend API with retries, timeout and safe JSON parsing.

    Methods mirror the previous `api.py` functions and return (status_code, dict-like).
    """

    def __init__(self, base_url: str, secret: str, timeout: int = 5, retries: int = 3, backoff: float = 0.3):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        # Every backend /telegram/* route requires this shared secret — the
        # chat_id in each request body only identifies who's calling, it
        # doesn't prove the call actually came from this bot.
        self._client = httpx.AsyncClient(
            headers={"X-Telegram-Secret": secret},
            timeout=timeout,
        )

    def _safe_json(self, res: httpx.Response):
        try:
            return res.json()
        except Exception:
            logger.exception("Failed to parse JSON response from %s", res.url)
            try:
                return {"detail": res.text}
            except Exception:
                logger.exception("Failed to read response text from %s", res.url)
                return {"detail": "Invalid response from server"}

    async def _request(self, method: str, path: str, json: Optional[dict] = None) -> Tuple[int, dict]:
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            try:
                res = await self._client.request(method, url, json=json)
            except httpx.RequestError:
                if attempt >= self.retries:
                    logger.exception("%s %s failed after %d retries", method, url, attempt)
                    return 503, {"detail": "Could not reach the server. Please try again later."}
                await asyncio.sleep(self.backoff * (2 ** attempt))
                attempt += 1
                continue

            if res.status_code in _RETRY_STATUS and attempt < self.retries:
                await asyncio.sleep(self.backoff * (2 ** attempt))
                attempt += 1
                continue

            if method == "PATCH":
                return res.status_code, (self._safe_json(res) if res.content else {})
            return res.status_code, self._safe_json(res)

    async def _get(self, path: str, json: Optional[dict] = None) -> Tuple[int, dict]:
        return await self._request("GET", path, json)

    async def _post(self, path: str, json: Optional[dict] = None) -> Tuple[int, dict]:
        return await self._request("POST", path, json)

    async def _patch(self, path: str, json: Optional[dict] = None) -> Tuple[int, dict]:
        return await self._request("PATCH", path, json)

    # Public API methods
    async def get_users(self, chat_id: int):
        return await self._get("/telegram/users", json={"chat_id": str(chat_id)})

    async def get_me(self, chat_id: int):
        return await self._get("/telegram/me", json={"chat_id": str(chat_id)})

    async def get_user(self, chat_id: int, username: str):
        return await self._get(f"/telegram/user/{username}", json={"chat_id": str(chat_id)})

    async def recharge_user(self, chat_id: int, username: str, amount: float):
        payload = {"chat_id": str(chat_id), "username": username, "amount": amount}
        return await self._patch("/telegram/recharge", json=payload)

    async def adjust_balance(self, chat_id: int, username: str, amount: float):
        payload = {"chat_id": str(chat_id), "username": username, "amount": amount}
        return await self._patch("/telegram/balance-adjust", json=payload)

    async def create_recharge_request(
        self,
        chat_id: int,
        username: str,
        amount: float,
        message: str | None = None,
        telegram_username: str | None = None,
        telegram_first_name: str | None = None,
        telegram_last_name: str | None = None,
    ):
        payload = {
            "chat_id": str(chat_id),
            "username": username,
            "amount": amount,
            "message": message,
            "telegram_username": telegram_username,
            "telegram_first_name": telegram_first_name,
            "telegram_last_name": telegram_last_name,
        }
        return await self._post("/telegram/recharge-requests", json=payload)

    async def resolve_recharge_request(self, chat_id: int, request_id: str, action: str):
        payload = {"chat_id": str(chat_id), "action": action}
        return await self._patch(f"/telegram/recharge-requests/{request_id}", json=payload)

    async def close(self):
        try:
            await self._client.aclose()
        except Exception:
            logger.exception("Failed to close the HTTP client cleanly")
