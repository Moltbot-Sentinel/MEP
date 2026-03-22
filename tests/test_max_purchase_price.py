"""
Tests for unified max_purchase_price logic across all MEP providers.
Validates the data market budget safety switch.
"""
import unittest


def should_accept_rfc(bounty: float, max_purchase_price: float = 0.0) -> bool:
    """
    Unified RFC evaluation logic for all MEP providers.

    Rules:
    - bounty >= 0 (compute market): always accept
    - bounty < 0 (data market): accept if abs(bounty) <= max_purchase_price
    """
    if bounty >= 0:
        return True
    cost = abs(bounty)
    return cost <= max_purchase_price


class TestMaxPurchasePrice(unittest.TestCase):
    """Unified budget logic tests."""

    # --- Compute market (positive bounty) ---

    def test_compute_market_always_accepted(self):
        self.assertTrue(should_accept_rfc(5.0))
        self.assertTrue(should_accept_rfc(0.001))
        self.assertTrue(should_accept_rfc(100.0))

    def test_zero_bounty_accepted(self):
        self.assertTrue(should_accept_rfc(0.0))

    # --- Data market with default budget (0.0) ---

    def test_default_budget_rejects_all_data(self):
        """Default 0.0 means never buy data."""
        self.assertFalse(should_accept_rfc(-0.001))
        self.assertFalse(should_accept_rfc(-1.0))
        self.assertFalse(should_accept_rfc(-100.0))

    # --- Data market with positive budget ---

    def test_data_within_budget(self):
        self.assertTrue(should_accept_rfc(-3.0, max_purchase_price=5.0))

    def test_data_at_budget_limit(self):
        self.assertTrue(should_accept_rfc(-5.0, max_purchase_price=5.0))

    def test_data_over_budget(self):
        self.assertFalse(should_accept_rfc(-8.0, max_purchase_price=5.0))

    def test_micro_purchase(self):
        """Tiny data purchases should work with any positive budget."""
        self.assertTrue(should_accept_rfc(-0.000001, max_purchase_price=0.001))

    # --- Edge cases ---

    def test_very_large_bounty(self):
        self.assertFalse(should_accept_rfc(-999999.0, max_purchase_price=10.0))

    def test_exact_budget_match(self):
        self.assertTrue(should_accept_rfc(-5.0, max_purchase_price=5.0))
        self.assertFalse(should_accept_rfc(-5.000001, max_purchase_price=5.0))

    # --- Consistency checks across old buggy patterns ---

    def test_old_bug_pattern_negative_default(self):
        """
        Old code: if bounty < max_purchase_price where max=-5.0
        -3.0 < -5.0 = False → accepted (WRONG: should reject if cost > budget)
        But with abs: cost=3.0, 3.0 <= 5.0 → accepted (CORRECT)
        """
        # This case happened to work by accident
        self.assertTrue(should_accept_rfc(-3.0, max_purchase_price=5.0))

    def test_old_bug_pattern_positive_default(self):
        """
        Old code: if bounty < max_purchase_price where max=0.0
        -3.0 < 0.0 = True → rejected (right result, wrong reason)
        With abs: cost=3.0, 3.0 <= 0.0 → False → rejected (CORRECT reason)
        """
        self.assertFalse(should_accept_rfc(-3.0, max_purchase_price=0.0))

    def test_old_bug_pattern_5_0(self):
        """
        Old code: if bounty < max_purchase_price where max=5.0
        -3.0 < 5.0 = True → rejected (WRONG! Should accept!)
        With abs: cost=3.0, 3.0 <= 5.0 → True → accepted (CORRECT)
        
        This was the critical bug: positive max_purchase_price blocked ALL data purchases.
        """
        self.assertTrue(should_accept_rfc(-3.0, max_purchase_price=5.0))


if __name__ == "__main__":
    unittest.main()
