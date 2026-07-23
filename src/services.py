from typing import Tuple, Optional, Any, Union

from .api_client import APIClient
from .logger import LOGGER_MANAGER
from .config import get_config


EXPENSE_CATEGORIES = ("toner", "paper", "maintenance", "other")


def validate_expense_input(category, amount) -> Tuple[bool, str, float, Optional[str]]:
    """Shared by UserService.create_expense (before hitting the backend)
    and the bot's /expense entry point (before showing the confirm
    screen for the one-shot fallback form) — one source of truth for
    what counts as a valid expense."""
    normalized_category = str(category).strip().lower()
    if normalized_category not in EXPENSE_CATEGORIES:
        return False, normalized_category, 0.0, f"Category must be one of: {', '.join(EXPENSE_CATEGORIES)}"

    try:
        a = float(amount)
    except Exception:
        return False, normalized_category, 0.0, "Amount must be a number"

    if a <= 0:
        return False, normalized_category, 0.0, "Amount must be positive"

    return True, normalized_category, a, None


class UserService:
    def __init__(self, client: APIClient, logger=None):
        self.client = client
        self.logger = logger or LOGGER_MANAGER.get_logger(self.__class__.__name__)

    async def get_me(self, chat_id: int) -> Tuple[int, dict]:
        status, res = await self.client.get_me(chat_id)
        self.logger.info("get_me chat_id=%s status=%s", chat_id, status)
        return status, res

    async def list_users(self, chat_id: int) -> Tuple[int, Union[list, dict, Any]]:
        status, res = await self.client.get_users(chat_id)
        self.logger.info("list_users chat_id=%s status=%s", chat_id, status)
        return status, res

    async def get_user(self, chat_id: int, username: str) -> Tuple[int, dict]:
        status, res = await self.client.get_user(chat_id, username)
        self.logger.info("get_user chat_id=%s username=%s status=%s", chat_id, username, status)
        return status, res

    async def recharge(self, chat_id: int, username: str, amount) -> Tuple[int, dict]:
        try:
            a = float(amount)
        except Exception:
            self.logger.warning("Invalid recharge amount: %s by chat_id=%s", amount, chat_id)
            return 400, {"detail": "Amount must be a number"}

        if a <= 0:
            self.logger.warning("Non-positive recharge amount: %s by chat_id=%s", amount, chat_id)
            return 400, {"detail": "Amount must be positive"}

        status, res = await self.client.recharge_user(chat_id, username, a)
        self.logger.info("recharge chat_id=%s username=%s amount=%s status=%s", chat_id, username, a, status)
        return status, res

    async def adjust(self, chat_id: int, username: str, amount) -> Tuple[int, dict]:
        try:
            a = float(amount)
        except Exception:
            self.logger.warning("Invalid adjust amount: %s by chat_id=%s", amount, chat_id)
            return 400, {"detail": "Amount must be a number"}

        if a < 0:
            self.logger.warning("Negative adjust target: %s by chat_id=%s", amount, chat_id)
            return 400, {"detail": "Balance target cannot be negative"}

        status, res = await self.client.adjust_balance(chat_id, username, a)
        self.logger.info("adjust chat_id=%s username=%s amount=%s status=%s", chat_id, username, a, status)
        return status, res

    async def request_recharge(
        self,
        chat_id: int,
        username: str,
        amount,
        message: str | None = None,
        telegram_username: str | None = None,
        telegram_first_name: str | None = None,
        telegram_last_name: str | None = None,
    ) -> Tuple[int, dict]:
        try:
            a = float(amount)
        except Exception:
            self.logger.warning("Invalid recharge request amount: %s by chat_id=%s", amount, chat_id)
            return 400, {"detail": "Amount must be a number"}

        if a <= 0:
            self.logger.warning("Non-positive recharge request amount: %s by chat_id=%s", amount, chat_id)
            return 400, {"detail": "Amount must be positive"}

        status, res = await self.client.create_recharge_request(
            chat_id,
            username,
            a,
            message=message.strip() if isinstance(message, str) and message.strip() else None,
            telegram_username=telegram_username,
            telegram_first_name=telegram_first_name,
            telegram_last_name=telegram_last_name,
        )
        self.logger.info("request_recharge chat_id=%s username=%s amount=%s status=%s", chat_id, username, a, status)
        return status, res

    async def resolve_recharge_request(self, chat_id: int, request_id: str, action: str) -> Tuple[int, dict]:
        status, res = await self.client.resolve_recharge_request(chat_id, request_id, action)
        self.logger.info(
            "resolve_recharge_request chat_id=%s request_id=%s action=%s status=%s",
            chat_id,
            request_id,
            action,
            status,
        )
        return status, res

    async def resolve_product_purchase(self, chat_id: int, purchase_id: str, action: str) -> Tuple[int, dict]:
        status, res = await self.client.resolve_product_purchase(chat_id, purchase_id, action)
        self.logger.info(
            "resolve_product_purchase chat_id=%s purchase_id=%s action=%s status=%s",
            chat_id,
            purchase_id,
            action,
            status,
        )
        return status, res

    async def list_inventory(self, chat_id: int) -> Tuple[int, Union[list, dict, Any]]:
        status, res = await self.client.get_inventory(chat_id)
        self.logger.info("list_inventory chat_id=%s status=%s", chat_id, status)
        return status, res

    async def adjust_stock(self, chat_id: int, item_name: str, delta) -> Tuple[int, dict]:
        try:
            d = float(delta)
        except Exception:
            self.logger.warning("Invalid stock delta: %s by chat_id=%s", delta, chat_id)
            return 400, {"detail": "Amount must be a number"}

        if d == 0:
            self.logger.warning("Zero stock delta by chat_id=%s", chat_id)
            return 400, {"detail": "Amount cannot be zero"}

        status, res = await self.client.adjust_stock(chat_id, item_name, d)
        self.logger.info(
            "adjust_stock chat_id=%s item_name=%s delta=%s status=%s", chat_id, item_name, d, status
        )
        return status, res

    async def create_expense(self, chat_id: int, category, amount, description: str | None = None) -> Tuple[int, dict]:
        ok, normalized_category, a, error = validate_expense_input(category, amount)
        if not ok:
            self.logger.warning(
                "Invalid expense input category=%s amount=%s by chat_id=%s", category, amount, chat_id
            )
            return 400, {"detail": error}

        status, res = await self.client.create_expense(chat_id, normalized_category, a, description)
        self.logger.info(
            "create_expense chat_id=%s category=%s amount=%s status=%s", chat_id, normalized_category, a, status
        )
        return status, res


def create_services(client: Optional[APIClient] = None):
    cfg = get_config()
    if client is None:
        client = APIClient(base_url=cfg.API_BASE_URL, secret=cfg.TELEGRAM_SECRET, timeout=cfg.API_TIMEOUT)
    user = UserService(client)
    return {
        "user": user,
        "client": client,
    }
