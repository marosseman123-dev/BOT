from __future__ import annotations

import unittest

from tools.deduplicate_marketing_columns import canonical_value, choose_removals, semantic_name


class MarketingColumnDedupTests(unittest.TestCase):
    def test_canonical_values_compare_boolean_and_numeric_spellings(self):
        self.assertEqual(canonical_value(" TRUE "), "true")
        self.assertEqual(canonical_value("70.0"), "70")
        self.assertEqual(canonical_value("  text  "), "text")

    def test_semantic_name_removes_provenance_prefixes(self):
        self.assertEqual(semantic_name("new_technical_score"), "tech_score")
        self.assertEqual(semantic_name("selected_overall_score"), "score")

    def test_only_smaller_fully_matching_column_is_removed(self):
        fields = ["domain", "score", "new_score", "different"]
        counts = [200, 200, 150, 200]
        comparisons = [
            {"left_index": 1, "right_index": 2, "overlap_rows": 150, "equal_rows": 150, "conflicting_rows": 0, "matched_distinct_values_capped": 3},
            {"left_index": 1, "right_index": 3, "overlap_rows": 200, "equal_rows": 199, "conflicting_rows": 1, "matched_distinct_values_capped": 3},
        ]
        removed, proven = choose_removals(fields, counts, comparisons)
        self.assertEqual(removed, ["new_score"])
        self.assertEqual(len(proven), 1)
        self.assertEqual(proven[0]["kept_column"], "score")

    def test_constant_flag_match_is_not_removed_as_semantic_duplicate(self):
        fields = ["seeded", "is_competitor"]
        counts = [200, 200]
        comparisons = [
            {"left_index": 0, "right_index": 1, "overlap_rows": 200, "equal_rows": 200, "conflicting_rows": 0, "matched_distinct_values_capped": 1},
        ]
        removed, proven = choose_removals(fields, counts, comparisons)
        self.assertEqual(removed, [])
        self.assertEqual(proven, [])
