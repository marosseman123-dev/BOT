from __future__ import annotations

import unittest

from tools.email_safety import clean_row, verify_resume_domain
from tools.normalize_marketing_leads import normalize_row


class EmailSafetyTests(unittest.TestCase):
    def test_category_rules_and_high_score_do_not_override_safety(self):
        cases = {
            "podatelna@gov.sk": "public_authority",
            "hello@csob.sk": "bank_or_finance",
            "info@websupport.sk": "hosting_provider",
            "no-reply@shop.sk": "technical_or_system_address",
            "hello@digital-agency.sk": "agency_or_marketing_competitor",
        }
        for email, reason in cases.items():
            cleaned, decisions, has_primary = clean_row({"domain": "shop.sk", "emails": email, "new_best_email_score": "99"})
            self.assertFalse(has_primary)
            self.assertEqual(decisions[0].reason, reason)
            self.assertEqual(cleaned["emails"], "")

    def test_competitor_and_safe_replacement(self):
        cleaned, decisions, _ = clean_row({"domain": "shop.sk", "emails": "webmaster@shop.sk, sales@shop.sk"})
        self.assertEqual(cleaned["emails"], "sales@shop.sk")
        self.assertIn("technical_or_system_address", cleaned["safety_reasons"])
        cleaned, decisions, _ = clean_row({"domain": "agency.sk", "emails": "sales@agency.sk", "new_is_competitor": "TRUE"})
        self.assertEqual(cleaned["emails"], "")
        self.assertEqual(decisions[0].reason, "agency_or_marketing_competitor")

    def test_global_dedup_keeps_order_and_one_primary(self):
        used: set[str] = set()
        first, _, _ = clean_row({"domain": "one.sk", "emails": "sales@shared.sk, info@one.sk"}, used)
        second, decisions, _ = clean_row({"domain": "two.sk", "emails": "sales@shared.sk, info@two.sk"}, used)
        self.assertEqual(first["emails"], "info@one.sk")
        self.assertEqual(second["emails"], "info@two.sk")
        self.assertTrue(any(d.reason == "duplicate_already_used" for d in decisions))
        self.assertNotIn(",", first["emails"])

    def test_normalization_preserves_domain_order_and_used_history(self):
        used = {"old@a.sk"}
        first, _ = normalize_row({"domain": "a.sk", "emails": "old@a.sk;info@a.sk", "campaign": "C1"}, 2, 2, used)
        second, _ = normalize_row({"domain": "b.sk", "emails": "info@b.sk;info@b2.sk", "campaign": "C1"}, 3, 2, used)
        self.assertEqual([first["domain"], second["domain"]], ["a.sk", "b.sk"])
        self.assertEqual(first["emails"], "info@a.sk")
        self.assertEqual(second["emails"], "info@b.sk")
        self.assertEqual(first["other_emails"], "")

    def test_resume_guard_uses_physical_csv_row(self):
        verify_resume_domain("a.sk", "a.sk")
        with self.assertRaises(ValueError):
            verify_resume_domain("a.sk", "wrong.sk")


if __name__ == "__main__":
    unittest.main()
