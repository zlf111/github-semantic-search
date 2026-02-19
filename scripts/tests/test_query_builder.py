"""Tests for core.query_builder — auto query building & seed synonym merge."""

import json
import os
import pytest

# Ensure scripts dir is on path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.models import SearchConfig
from core.query_builder import build_queries, merge_seed_synonyms, _quote, _or_join


# ---------------------------------------------------------------------------
# _quote / _or_join helpers
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_quote_single_word(self):
        assert _quote("sigsegv") == "sigsegv"

    def test_quote_multi_word(self):
        assert _quote("page fault") == '"page fault"'

    def test_or_join_mixed(self):
        result = _or_join(["page fault", "sigsegv"])
        assert result == '"page fault" OR sigsegv'

    def test_or_join_single(self):
        assert _or_join(["crash"]) == "crash"


# ---------------------------------------------------------------------------
# build_queries
# ---------------------------------------------------------------------------
class TestBuildQueries:
    def _make_config(self, *, high=None, medium=None, low=None, component=""):
        cfg = SearchConfig(component=component, topic="test")
        cfg.keywords_high = high or []
        cfg.keywords_medium = medium or []
        cfg.keywords_low = low or []
        return cfg

    def test_empty_keywords_returns_empty(self):
        cfg = self._make_config()
        assert build_queries(cfg) == []

    def test_high_only(self):
        cfg = self._make_config(high=["page fault", "segfault"])
        queries = build_queries(cfg)
        assert len(queries) >= 2
        # R1: individual high queries should be first
        assert '"page fault"' in queries[0]
        assert "segfault" in queries[1]

    def test_component_in_r1(self):
        cfg = self._make_config(high=["page fault"], component="hipblaslt")
        queries = build_queries(cfg)
        assert queries[0] == '{component} "page fault"'

    def test_no_component_no_r2(self):
        """R2 (broader sweep without component) should be skipped when no component."""
        cfg = self._make_config(
            high=["page fault", "segfault"],
            medium=["sigsegv"],
        )
        queries = build_queries(cfg)
        # All queries should contain the keywords directly (no {component})
        for q in queries:
            assert "{component}" not in q

    def test_r2_generated_with_component(self):
        """R2 should generate broader queries without {component} when component exists."""
        cfg = self._make_config(
            high=["page fault", "segfault"],
            component="hipblaslt",
        )
        queries = build_queries(cfg)
        # Should have both {component} queries (R1) and non-{component} queries (R2)
        has_comp = any("{component}" in q for q in queries)
        has_broad = any("{component}" not in q for q in queries)
        assert has_comp and has_broad

    def test_medium_pairs(self):
        cfg = self._make_config(medium=["sigsegv", "sigbus", "signal 11", "signal 7"])
        queries = build_queries(cfg)
        # Should have OR'd pairs
        or_queries = [q for q in queries if " OR " in q]
        assert len(or_queries) >= 2

    def test_low_groups_of_three(self):
        cfg = self._make_config(low=["a", "b", "c", "d", "e", "f"])
        queries = build_queries(cfg)
        # First low group should have 3 items OR'd
        low_q = [q for q in queries if "a" in q]
        assert len(low_q) >= 1
        assert low_q[0].count(" OR ") == 2  # 3 items = 2 ORs

    def test_max_queries_cap(self):
        cfg = self._make_config(
            high=["h1", "h2", "h3", "h4", "h5"],
            medium=["m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8"],
            low=["l1", "l2", "l3", "l4", "l5", "l6", "l7", "l8", "l9"],
            component="comp",
        )
        queries = build_queries(cfg, max_queries=10)
        assert len(queries) <= 10

    def test_deduplication(self):
        cfg = self._make_config(high=["crash"])
        queries = build_queries(cfg)
        # Should not have duplicate queries
        assert len(queries) == len(set(queries))

    def test_interleaved_r1_r3(self):
        """R1 and R3 should be interleaved (not sequential) in output."""
        cfg = self._make_config(
            high=["H1", "H2", "H3"],
            medium=["M1", "M2", "M3", "M4"],
            component="comp",
        )
        queries = build_queries(cfg)
        # Expected: R1[0], R3[0], R1[1], R3[1], R1[2], R3[2], then R2, R4
        assert queries[0] == "{component} H1"          # R1[0]
        assert "M1 OR M2" in queries[1]                 # R3[0]
        assert queries[2] == "{component} H2"           # R1[1]
        assert "M3 OR M4" in queries[3]                 # R3[1]
        assert queries[4] == "{component} H3"           # R1[2]

    def test_interleaved_behavioral_keywords(self):
        """Behavioral topic: medium queries interleave for early-stop defense."""
        cfg = self._make_config(
            high=["tuning no effect", "tuning not working"],
            medium=["tuning file", "logic yaml", "match table", "tuning data"],
        )
        queries = build_queries(cfg)
        # First 4 queries should alternate: R1, R3, R1, R3
        assert '"tuning no effect"' in queries[0]       # R1[0]
        assert " OR " in queries[1]                      # R3[0] is an OR pair
        assert '"tuning not working"' in queries[2]      # R1[1]
        assert " OR " in queries[3]                      # R3[1] is an OR pair

    def test_interleaved_high_only_no_medium(self):
        """When medium is empty, interleave degrades to R1-only (no R3)."""
        cfg = self._make_config(high=["A", "B", "C"])
        queries = build_queries(cfg)
        # Should just be R1 queries in order (no R3 to interleave)
        assert queries[0] == "A"
        assert queries[1] == "B"
        assert queries[2] == "C"

    def test_full_scenario_pagefault(self):
        """Realistic page fault scenario with all tiers."""
        cfg = self._make_config(
            high=["page fault", "memory access fault"],
            medium=["sigsegv", "segfault"],
            low=["l2_protection_fault"],
            component="hipblaslt",
        )
        queries = build_queries(cfg)
        assert len(queries) >= 5
        assert len(queries) <= 15
        # First query should be individual high with component (R1[0])
        assert queries[0] == '{component} "page fault"'
        # Second query should be medium pair (R3[0]) — interleaved
        assert "sigsegv" in queries[1] or "segfault" in queries[1]


# ---------------------------------------------------------------------------
# merge_seed_synonyms
# ---------------------------------------------------------------------------
class TestMergeSeedSynonyms:
    def _make_config(self, topic, high=None, medium=None, low=None):
        cfg = SearchConfig(topic=topic)
        cfg.keywords_high = list(high or [])
        cfg.keywords_medium = list(medium or [])
        cfg.keywords_low = list(low or [])
        return cfg

    def test_page_fault_match(self):
        cfg = self._make_config("page fault", high=["page fault"])
        added = merge_seed_synonyms(cfg)
        assert added > 0
        # Should have added memory_fault topic keywords
        all_kw = set(k.lower() for k in
                     cfg.keywords_high + cfg.keywords_medium + cfg.keywords_low)
        assert "sigsegv" in all_kw
        assert "segfault" in all_kw

    def test_no_duplicates(self):
        cfg = self._make_config(
            "page fault",
            high=["page fault", "segmentation fault"],
            medium=["sigsegv"],
        )
        original_count = len(cfg.keywords_high) + len(cfg.keywords_medium) + len(cfg.keywords_low)
        added = merge_seed_synonyms(cfg)
        final_count = len(cfg.keywords_high) + len(cfg.keywords_medium) + len(cfg.keywords_low)
        assert final_count == original_count + added
        # No duplicates
        all_kw_lower = [k.lower() for k in
                        cfg.keywords_high + cfg.keywords_medium + cfg.keywords_low]
        assert len(all_kw_lower) == len(set(all_kw_lower))

    def test_unmatched_topic(self):
        cfg = self._make_config("expert scheduling mode")
        added = merge_seed_synonyms(cfg)
        assert added == 0

    def test_oom_match(self):
        cfg = self._make_config("out of memory error")
        added = merge_seed_synonyms(cfg)
        assert added > 0
        all_kw = set(k.lower() for k in
                     cfg.keywords_high + cfg.keywords_medium + cfg.keywords_low)
        assert "oom" in all_kw

    def test_multi_topic_match(self):
        """A topic like 'page fault crash' should match both memory_fault and crash."""
        cfg = self._make_config("page fault causing crash")
        added = merge_seed_synonyms(cfg)
        all_kw = set(k.lower() for k in
                     cfg.keywords_high + cfg.keywords_medium + cfg.keywords_low)
        # Should have keywords from both topics
        assert "sigsegv" in all_kw    # from memory_fault
        assert "sigabrt" in all_kw    # from crash

    def test_cache_invalidation(self):
        """After merge, keyword_weight_map should reflect new keywords."""
        cfg = self._make_config("page fault", high=["page fault"])
        # Access cached property BEFORE merge
        old_map = cfg.keyword_weight_map
        assert "sigsegv" not in old_map

        merge_seed_synonyms(cfg)
        # After merge, cache should be invalidated
        new_map = cfg.keyword_weight_map
        assert "sigsegv" in new_map
