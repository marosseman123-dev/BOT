from __future__ import annotations

import unittest

from tools.normalize_marketing_leads import campaign_for, clean_emails, normalize_row


class MarketingNormalizationTests(unittest.TestCase):
    def test_c6_requires_all_quality_thresholds(self):
        row = {
            "selected_overall_score": "90",
            "selected_seo_score": "85",
            "selected_technical_score": "90",
            "selected_security_score": "80",
            "selected_performance_score": "80",
        }
        self.assertEqual(campaign_for(row), "C6")
        row["selected_security_score"] = "74"
        self.assertEqual(campaign_for(row), "C1")

    def test_clean_emails_removes_blacklist_developer_and_keeps_safe_candidates(self):
        row = {
            "new_emails_final": "info@example.sk, podatelna@gov.sk, developer@example.sk, sales@example.sk, hello@example.sk, office@gmail.com",
            "new_is_competitor": "False",
        }
        emails, removed = clean_emails(row, "example.sk")
        self.assertEqual(emails[0], "info@example.sk")
        self.assertIn("sales@example.sk", emails)
        self.assertNotIn("developer@example.sk", emails)
        self.assertTrue(any("podatelna@gov.sk" in item for item in removed))
        self.assertTrue(any("developer@example.sk" in item for item in removed))

    def test_new_row_populates_legacy_columns_and_campaign(self):
        row = {
            "domain": "example.sk",
            "emails": "info@example.sk, office@gmail.com, sales@example.sk, extra@example.sk",
            "new_emails_final": "info@example.sk, office@gmail.com, sales@example.sk, extra@example.sk",
            "new_state": "ok",
            "new_alive": "True",
            "new_ssl_certificate": "True",
            "new_modernity_score": "100",
            "new_cms": "Shoptet",
            "new_schema_valid": "True",
            "new_meta_description_ok": "True",
            "new_h1_count": "1",
            "new_canonical": "True",
            "new_noindex": "False",
            "new_lang": "sk-SK",
            "new_favicon": "True",
            "new_og_tags": "True",
            "new_images_total": "4",
            "new_images_with_alt_pct": "100",
            "new_images_nextgen_pct": "50",
            "new_img_webp": "2",
            "new_img_avif": "0",
            "new_images_legacy": "2",
            "new_internal_links": "8",
            "new_compression_type": "gzip",
            "new_html_kb": "12.5",
            "new_technical_score": "100",
            "selected_overall_score": "92",
            "selected_seo_score": "90",
            "selected_technical_score": "100",
            "selected_security_score": "90",
            "selected_performance_score": "90",
            "selected_modernity_score": "100",
            "selected_issues": "Missing HSTS header",
            "merge_removed_emails": "0",
            "campaign": "",
        }
        normalized, _ = normalize_row(row, 115498, 115498)
        self.assertEqual(normalized["campaign"], "C6")
        self.assertEqual(normalized["score"], "92.0")
        self.assertEqual(normalized["seo_score"], "90.0")
        self.assertLessEqual(len(normalized["emails"].split(", ")), 3)
        self.assertEqual(normalized["platform"], "Shoptet")
        self.assertEqual(normalized["img_nextgen_count"], "2")

    def test_unavailable_new_row_gets_nonempty_issue_text(self):
        row = {
            "domain": "offline.sk",
            "new_state": "error",
            "new_alive": "False",
            "new_dns_dead": "True",
            "selected_overall_score": "0",
            "selected_seo_score": "0",
            "selected_technical_score": "0",
            "selected_security_score": "0",
            "selected_performance_score": "0",
            "campaign": "",
        }
        normalized, _ = normalize_row(row, 115498, 115498)
        self.assertEqual(normalized["campaign"], "C1")
        self.assertTrue(normalized["issues"])


if __name__ == "__main__":
    unittest.main()
