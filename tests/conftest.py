import pytest


class FakeAPIClient:
    """Records every call made to it and returns a canned response."""

    def __init__(self, response=(200, {})):
        self.response = response
        self.calls = []

    async def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return self.response

    async def get_me(self, chat_id):
        return await self._record("get_me", chat_id)

    async def get_users(self, chat_id):
        return await self._record("get_users", chat_id)

    async def get_user(self, chat_id, username):
        return await self._record("get_user", chat_id, username)

    async def recharge_user(self, chat_id, username, amount):
        return await self._record("recharge_user", chat_id, username, amount)

    async def adjust_balance(self, chat_id, username, amount):
        return await self._record("adjust_balance", chat_id, username, amount)

    async def create_recharge_request(self, chat_id, username, amount, **kwargs):
        return await self._record("create_recharge_request", chat_id, username, amount, **kwargs)

    async def resolve_recharge_request(self, chat_id, request_id, action):
        return await self._record("resolve_recharge_request", chat_id, request_id, action)

    async def resolve_product_purchase(self, chat_id, purchase_id, action):
        return await self._record("resolve_product_purchase", chat_id, purchase_id, action)

    async def get_inventory(self, chat_id):
        return await self._record("get_inventory", chat_id)

    async def adjust_stock(self, chat_id, item_name, delta):
        return await self._record("adjust_stock", chat_id, item_name, delta)

    async def create_expense(self, chat_id, category, amount, description=None):
        return await self._record("create_expense", chat_id, category, amount, description)


@pytest.fixture
def fake_client():
    return FakeAPIClient()
