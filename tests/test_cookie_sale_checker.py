"""Regression checks for cookie-sale change detection and notification deduplication."""
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi"))
import cookie_sale_checker as cookie


def offer(key: str, purchase_state: str = "available") -> dict:
    base, bonus = map(int, key.split("+"))
    return {
        "key": key,
        "base": base,
        "bonus": bonus,
        "label": f"쿠키 {base}개 + 보너스 {bonus}개 ({bonus / base * 100:g}%)",
        "purchase_state": purchase_state,
        "price": base * 100,
    }


def notified_state(*keys: str) -> dict:
    return {
        "offer_history": {
            key: {
                "generation": 0,
                "notified_generation": 0,
                "last_known": "available",
            }
            for key in keys
        },
        "last_check": {"status": "sale", "offers": [offer(keys[0])]},
    }


def observe(state: dict, status: str, offers: list[dict] | None = None) -> list[dict]:
    offers = offers or []
    cookie.track_sale_cycle(state, status, offers)
    if status == "sale":
        cookie.advance_offer_history(state, offers)
    state["last_check"] = {"status": status, "offers": offers}
    return cookie.pending_available_offers(state, offers)


class CookieSaleCycleTests(unittest.TestCase):
    def test_upgrade_does_not_resend_ongoing_sale(self):
        state = notified_state("500+25")
        self.assertFalse(observe(state, "sale", [offer("500+25")]))

    def test_changed_package_rearms_previously_notified_package(self):
        state = notified_state("500+25", "30+3")
        self.assertFalse(observe(state, "sale", [offer("500+25")]))
        changed = observe(state, "sale", [offer("30+3")])
        self.assertEqual([x["key"] for x in changed], ["30+3"])
        cookie.mark_offers_notified(state, changed)
        self.assertFalse(observe(state, "sale", [offer("30+3")]))
        returned = observe(state, "sale", [offer("500+25")])
        self.assertEqual([x["key"] for x in returned], ["500+25"])
        cookie.mark_offers_notified(state, returned)
        self.assertFalse(observe(state, "sale", [offer("500+25")]))

    def test_no_sale_resets_same_package(self):
        state = notified_state("500+25")
        self.assertFalse(observe(state, "no_sale"))
        again = observe(state, "sale", [offer("500+25")])
        self.assertEqual([x["key"] for x in again], ["500+25"])

    def test_purchase_then_available_resends_same_package(self):
        state = notified_state("500+25")
        self.assertFalse(observe(state, "sale", [offer("500+25", "purchased")]))
        again = observe(state, "sale", [offer("500+25")])
        self.assertEqual([x["key"] for x in again], ["500+25"])
        self.assertEqual(state["offer_history"]["500+25"]["generation"], 1)
        cookie.mark_offers_notified(state, again)
        self.assertFalse(observe(state, "sale", [offer("500+25")]))

    def test_transient_error_does_not_rearm(self):
        state = notified_state("500+25")
        self.assertFalse(observe(state, "sale", [offer("500+25")]))
        observe(state, "login_required")
        observe(state, "error")
        self.assertFalse(observe(state, "sale", [offer("500+25")]))

    def test_new_sale_unknown_purchase_button_does_not_notify(self):
        state = notified_state("500+25")
        self.assertFalse(observe(state, "sale", [offer("30+3", "unknown")]))
        again = observe(state, "sale", [offer("30+3", "available")])
        self.assertEqual([x["key"] for x in again], ["30+3"])

    def test_legacy_digest_does_not_suppress_new_cycle(self):
        item = offer("500+25")
        digest = hashlib.sha256(item["label"].encode("utf-8")).hexdigest()[:24]
        state = {
            "notified_offer": digest,
            "last_check": {"status": "no_sale", "offers": []},
        }
        again = observe(state, "sale", [item])
        self.assertEqual([x["key"] for x in again], ["500+25"])
        self.assertNotIn("notified_offer", state)

    def test_offer_listing_order_does_not_create_false_change(self):
        state = notified_state("500+25", "30+3")
        state["last_check"]["offers"] = [offer("500+25"), offer("30+3")]
        self.assertFalse(observe(state, "sale", [offer("30+3"), offer("500+25")]))
        self.assertEqual(state["active_offer_keys"], ["30+3", "500+25"])

    def test_unknown_preupgrade_history_is_conservative(self):
        state = notified_state("500+25")
        state["last_check"] = {"status": "error", "offers": []}
        self.assertFalse(observe(state, "sale", [offer("500+25")]))


if __name__ == "__main__":
    unittest.main()
