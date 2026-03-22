from typing import Tuple, Optional, Any, Union

from .api_client import APIClient
from .logger import LOGGER_MANAGER
from .config import get_config


class VoucherService:
    def __init__(self, client: APIClient, logger=None):
        self.client = client
        self.logger = logger or LOGGER_MANAGER.get_logger(self.__class__.__name__)

    def validate_amount(self, amount) -> bool:
        try:
            a = float(amount)
            return a > 0
        except Exception:
            return False

    def generate(self, chat_id: int, amount) -> Tuple[int, dict]:
        if not self.validate_amount(amount):
            self.logger.warning("Invalid amount for voucher: %s (chat_id=%s)", amount, chat_id)
            return 400, {"detail": "Amount must be a positive number"}

        a = float(amount)
        status, res = self.client.generate_voucher(chat_id, a)
        self.logger.info("generate_voucher result chat_id=%s amount=%s status=%s", chat_id, a, status)
        return status, res


class UserService:
    def __init__(self, client: APIClient, logger=None):
        self.client = client
        self.logger = logger or LOGGER_MANAGER.get_logger(self.__class__.__name__)

    def get_me(self, chat_id: int) -> Tuple[int, dict]:
        status, res = self.client.get_me(chat_id)
        self.logger.info("get_me chat_id=%s status=%s", chat_id, status)
        return status, res

    def list_users(self, chat_id: int) -> Tuple[int, Union[list, dict, Any]]:
        status, res = self.client.get_users(chat_id)
        self.logger.info("list_users chat_id=%s status=%s", chat_id, status)
        return status, res

    def get_user(self, chat_id: int, username: str) -> Tuple[int, dict]:
        status, res = self.client.get_user(chat_id, username)
        self.logger.info("get_user chat_id=%s username=%s status=%s", chat_id, username, status)
        return status, res

    def recharge(self, chat_id: int, username: str, amount) -> Tuple[int, dict]:
        try:
            a = float(amount)
        except Exception:
            self.logger.warning("Invalid recharge amount: %s by chat_id=%s", amount, chat_id)
            return 400, {"detail": "Amount must be a number"}

        if a <= 0:
            self.logger.warning("Non-positive recharge amount: %s by chat_id=%s", amount, chat_id)
            return 400, {"detail": "Amount must be positive"}

        status, res = self.client.recharge_user(chat_id, username, a)
        self.logger.info("recharge chat_id=%s username=%s amount=%s status=%s", chat_id, username, a, status)
        return status, res

    def adjust(self, chat_id: int, username: str, amount) -> Tuple[int, dict]:
        try:
            a = float(amount)
        except Exception:
            self.logger.warning("Invalid adjust amount: %s by chat_id=%s", amount, chat_id)
            return 400, {"detail": "Amount must be a number"}

        status, res = self.client.adjust_balance(chat_id, username, a)
        self.logger.info("adjust chat_id=%s username=%s amount=%s status=%s", chat_id, username, a, status)
        return status, res

    def request_recharge(
        self,
        chat_id: int,
        username: str,
        amount,
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

        status, res = self.client.create_recharge_request(
            chat_id,
            username,
            a,
            telegram_username=telegram_username,
            telegram_first_name=telegram_first_name,
            telegram_last_name=telegram_last_name,
        )
        self.logger.info("request_recharge chat_id=%s username=%s amount=%s status=%s", chat_id, username, a, status)
        return status, res

    def resolve_recharge_request(self, chat_id: int, request_id: str, action: str) -> Tuple[int, dict]:
        status, res = self.client.resolve_recharge_request(chat_id, request_id, action)
        self.logger.info(
            "resolve_recharge_request chat_id=%s request_id=%s action=%s status=%s",
            chat_id,
            request_id,
            action,
            status,
        )
        return status, res


def create_services(client: Optional[APIClient] = None):
    cfg = get_config()
    if client is None:
        client = APIClient(base_url=cfg.API_BASE_URL, timeout=cfg.API_TIMEOUT)
    voucher = VoucherService(client)
    user = UserService(client)
    return {
        "voucher": voucher,
        "user": user,
        "client": client,
    }
