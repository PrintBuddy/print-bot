import pytest

from src.services import UserService

pytestmark = pytest.mark.asyncio


class TestRecharge:
    async def test_rejects_non_numeric_amount(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.recharge(1, "alice", "not-a-number")
        assert status == 400
        assert fake_client.calls == []

    async def test_rejects_non_positive_amount(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.recharge(1, "alice", 0)
        assert status == 400
        assert fake_client.calls == []

    async def test_accepts_positive_amount(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.recharge(1, "alice", "5.50")
        assert status == 200
        assert fake_client.calls == [("recharge_user", (1, "alice", 5.50), {})]


class TestAdjust:
    async def test_rejects_non_numeric_amount(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.adjust(1, "alice", "not-a-number")
        assert status == 400
        assert fake_client.calls == []

    async def test_rejects_negative_target(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.adjust(1, "alice", "-5")
        assert status == 400
        assert fake_client.calls == []

    async def test_allows_zero_target(self, fake_client):
        # Setting a balance to exactly 0 is a legitimate absolute target,
        # unlike a recharge of 0 which would be a no-op.
        service = UserService(fake_client)
        status, res = await service.adjust(1, "alice", "0")
        assert status == 200
        assert fake_client.calls == [("adjust_balance", (1, "alice", 0.0), {})]

    async def test_accepts_positive_target(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.adjust(1, "alice", "10")
        assert status == 200
        assert fake_client.calls == [("adjust_balance", (1, "alice", 10.0), {})]


class TestRequestRecharge:
    async def test_rejects_non_positive_amount(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.request_recharge(1, "alice", "0")
        assert status == 400
        assert fake_client.calls == []

    async def test_accepts_positive_amount(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.request_recharge(1, "alice", "5")
        assert status == 200
        assert len(fake_client.calls) == 1
        assert fake_client.calls[0][0] == "create_recharge_request"
