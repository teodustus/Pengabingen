"""Tester för simulerade ordrar och portföljvärde i live.py."""
import pytest

from live import (
    _get_cash,
    _set_cash,
    load_positions,
    portfolio_value,
    sim_buy,
    sim_close,
    sim_sell,
)
from config import PAPER_INITIAL_CAPITAL, TRANSACTION_COST


class TestGetCash:
    def test_initial_cash_is_paper_capital(self, live_db):
        cash = _get_cash(live_db)
        assert cash == PAPER_INITIAL_CAPITAL

    def test_set_and_get_cash(self, live_db):
        _set_cash(live_db, 50_000.0)
        assert _get_cash(live_db) == 50_000.0


class TestSimBuy:
    def test_buy_reduces_cash(self, live_db):
        before = _get_cash(live_db)
        sim_buy(live_db, "AAPL", amount=10_000.0, price=200.0)
        after = _get_cash(live_db)
        assert after == pytest.approx(before - 10_000.0)

    def test_buy_creates_position(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=200.0)
        positions = load_positions(live_db)
        assert "AAPL" in positions
        expected_qty = (10_000.0 * (1 - TRANSACTION_COST)) / 200.0
        assert positions["AAPL"]["qty"] == pytest.approx(expected_qty)

    def test_buy_sets_avg_price(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=200.0)
        positions = load_positions(live_db)
        assert positions["AAPL"]["avg_price"] == pytest.approx(200.0)

    def test_buy_twice_weighted_avg(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=100.0)
        sim_buy(live_db, "AAPL", amount=10_000.0, price=200.0)
        positions = load_positions(live_db)
        # Viktat genomsnitt bör vara mellan 100 och 200
        avg = positions["AAPL"]["avg_price"]
        assert 100.0 < avg < 200.0


class TestSimSell:
    def test_sell_increases_cash(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=100.0)
        cash_after_buy = _get_cash(live_db)
        positions = load_positions(live_db)
        qty = positions["AAPL"]["qty"]
        sim_sell(live_db, "AAPL", qty=qty / 2, price=100.0)
        cash_after_sell = _get_cash(live_db)
        assert cash_after_sell > cash_after_buy

    def test_sell_all_removes_position(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=100.0)
        qty = load_positions(live_db)["AAPL"]["qty"]
        sim_sell(live_db, "AAPL", qty=qty, price=100.0)
        assert "AAPL" not in load_positions(live_db)

    def test_sell_partial_keeps_position(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=100.0)
        qty = load_positions(live_db)["AAPL"]["qty"]
        sim_sell(live_db, "AAPL", qty=qty / 2, price=100.0)
        assert "AAPL" in load_positions(live_db)

    def test_transaction_cost_applied(self, live_db):
        _set_cash(live_db, 0.0)
        # Sätt upp position manuellt
        from live import save_position
        save_position(live_db, "AAPL", qty=100.0, avg_price=100.0)
        net = sim_sell(live_db, "AAPL", qty=100.0, price=100.0)
        expected_net = 100.0 * 100.0 * (1 - TRANSACTION_COST)
        assert net == pytest.approx(expected_net)


class TestSimClose:
    def test_close_removes_position(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=100.0)
        sim_close(live_db, "AAPL", price=100.0)
        assert "AAPL" not in load_positions(live_db)

    def test_close_nonexistent_returns_zero(self, live_db):
        result = sim_close(live_db, "NONEXISTENT", price=100.0)
        assert result == 0.0

    def test_close_action_logged(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=100.0)
        sim_close(live_db, "AAPL", price=90.0, action="STOP")
        row = live_db.execute(
            "SELECT action FROM live_trades ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row[0] == "STOP"


class TestPortfolioValue:
    def test_initial_value_is_capital(self, live_db):
        pv = portfolio_value(live_db, {})
        assert pv == pytest.approx(PAPER_INITIAL_CAPITAL)

    def test_value_includes_position(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=100.0)
        qty = load_positions(live_db)["AAPL"]["qty"]
        pv = portfolio_value(live_db, {"AAPL": 150.0})
        expected = _get_cash(live_db) + qty * 150.0
        assert pv == pytest.approx(expected)

    def test_value_with_price_appreciation(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=100.0)
        pv_flat  = portfolio_value(live_db, {"AAPL": 100.0})
        pv_up    = portfolio_value(live_db, {"AAPL": 200.0})
        assert pv_up > pv_flat

    def test_value_falls_back_to_avg_price_if_no_live_price(self, live_db):
        sim_buy(live_db, "AAPL", amount=10_000.0, price=100.0)
        # Utan live-pris för AAPL används avg_price
        pv_no_price = portfolio_value(live_db, {})
        pv_at_cost  = portfolio_value(live_db, {"AAPL": 100.0})
        assert pv_no_price == pytest.approx(pv_at_cost)
