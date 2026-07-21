import pytest

from src.services import UserService, validate_expense_input

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


class TestAdjustStock:
    async def test_rejects_non_numeric_delta(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.adjust_stock(1, "A4 Paper", "not-a-number")
        assert status == 400
        assert fake_client.calls == []

    async def test_rejects_zero_delta(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.adjust_stock(1, "A4 Paper", "0")
        assert status == 400
        assert fake_client.calls == []

    async def test_accepts_negative_delta(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.adjust_stock(1, "A4 Paper", "-50")
        assert status == 200
        assert fake_client.calls == [("adjust_stock", (1, "A4 Paper", -50.0), {})]

    async def test_accepts_positive_delta(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.adjust_stock(1, "A4 Paper", "20")
        assert status == 200
        assert fake_client.calls == [("adjust_stock", (1, "A4 Paper", 20.0), {})]


class TestValidateExpenseInput:
    async def test_rejects_unknown_category(self):
        ok, category, amount, error = validate_expense_input("snacks", "10")
        assert ok is False
        assert "Category must be one of" in error

    async def test_rejects_non_numeric_amount(self):
        ok, category, amount, error = validate_expense_input("toner", "not-a-number")
        assert ok is False
        assert error == "Amount must be a number"

    async def test_rejects_non_positive_amount(self):
        ok, category, amount, error = validate_expense_input("toner", "0")
        assert ok is False
        assert error == "Amount must be positive"

    async def test_normalizes_category_case(self):
        ok, category, amount, error = validate_expense_input("  TONER ", "15.5")
        assert ok is True
        assert category == "toner"
        assert amount == 15.5
        assert error is None


class TestCreateExpense:
    async def test_rejects_invalid_category(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.create_expense(1, "snacks", "10")
        assert status == 400
        assert fake_client.calls == []

    async def test_rejects_non_positive_amount(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.create_expense(1, "toner", "0")
        assert status == 400
        assert fake_client.calls == []

    async def test_accepts_valid_input(self, fake_client):
        service = UserService(fake_client)
        status, res = await service.create_expense(1, "Toner", "15.5", "cartridge")
        assert status == 200
        assert fake_client.calls == [("create_expense", (1, "toner", 15.5, "cartridge"), {})]
