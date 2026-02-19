"""Tests for core/scorer.py — keyword relevance scoring engine."""

import pytest
from core.scorer import KeywordScorer
from core.models import Issue, PullRequest, CodeResult, CommitResult, DiscussionResult


class TestContainmentFilter:
    """Test _build_containment_filter: short keywords suppressed by longer ones."""

    def test_short_keyword_suppressed_by_long(self):
        suppressed = KeywordScorer._build_containment_filter(
            ["page fault", "fault", "memory"]
        )
        assert "fault" in suppressed
        assert "page fault" not in suppressed
        assert "memory" not in suppressed

    def test_no_suppression_when_no_overlap(self):
        suppressed = KeywordScorer._build_containment_filter(
            ["sigsegv", "oom", "crash"]
        )
        assert len(suppressed) == 0

    def test_identical_keywords_not_suppressed(self):
        suppressed = KeywordScorer._build_containment_filter(
            ["fault", "fault"]
        )
        assert "fault" not in suppressed

    def test_multiple_nesting_levels(self):
        """'a' in 'ab' in 'abc' — both 'a' and 'ab' suppressed."""
        suppressed = KeywordScorer._build_containment_filter(
            ["abc", "ab", "a"]
        )
        assert "a" in suppressed
        assert "ab" in suppressed
        assert "abc" not in suppressed


class TestScoreIssues:
    """Test score_issues with various keyword/component combinations."""

    def test_high_relevance_issue_scores_above_8(self, basic_config, issue_high_relevance):
        scorer = KeywordScorer()
        issues = {100: issue_high_relevance}
        scorer.score_issues(issues, basic_config)
        assert issue_high_relevance.relevance_score >= 8.0
        assert "memory leak" in issue_high_relevance.matched_keywords

    def test_component_label_match_adds_3(self, basic_config, issue_medium_relevance):
        scorer = KeywordScorer()
        issues = {200: issue_medium_relevance}
        scorer.score_issues(issues, basic_config)
        # "project: mylib" label contains "mylib" -> +3.0 label bonus
        assert issue_medium_relevance.relevance_score >= 3.0

    def test_no_match_scores_zero(self, basic_config):
        scorer = KeywordScorer()
        issue = Issue(
            number=999, title="Unrelated issue about UI color",
            body="The button color should be blue.",
            labels=[], state="open",
            url="https://example.com/999", created_at="2025-01-01",
        )
        issues = {999: issue}
        scorer.score_issues(issues, basic_config)
        assert issue.relevance_score == 0.0
        assert len(issue.matched_keywords) == 0

    def test_title_bonus_adds_2_per_keyword(self, basic_config, issue_high_relevance):
        scorer = KeywordScorer()
        issues = {100: issue_high_relevance}
        scorer.score_issues(issues, basic_config)
        # "memory leak" and "out of memory" both in title → each gets +2.0 title bonus
        # Expected minimum: 5+5 (kw) + 2+2 (title) + 2 (comp body) + 3 (comp label) = 19
        assert issue_high_relevance.relevance_score >= 14.0

    def test_containment_dedup_prevents_double_scoring(self, basic_config):
        """If 'memory leak' and 'memory' are both keywords, 'memory' should be suppressed."""
        scorer = KeywordScorer()
        config = basic_config
        # Manually add "memory" as a low keyword that's a substring of "memory leak"
        config.keywords_low = list(config.keywords_low) + ["memory"]
        # Clear cached properties
        try:
            object.__delattr__(config, "_all_keywords")
        except AttributeError:
            pass
        try:
            object.__delattr__(config, "_kw_weight_map")
        except AttributeError:
            pass
        issue = Issue(
            number=50, title="memory leak detected",
            body="There is a memory leak in the system.",
            labels=[], state="open",
            url="https://example.com/50", created_at="2025-01-01",
        )
        issues = {50: issue}
        scorer.score_issues(issues, config)
        # "memory" should be suppressed since "memory leak" is a longer match
        assert "memory leak" in issue.matched_keywords

    def test_frequency_bonus_capped_at_2(self, config_no_component):
        scorer = KeywordScorer()
        # Body repeats "segmentation fault" 10 times
        issue = Issue(
            number=77, title="crash report",
            body=" ".join(["segmentation fault"] * 10),
            labels=[], state="open",
            url="https://example.com/77", created_at="2025-01-01",
        )
        issues = {77: issue}
        scorer.score_issues(issues, config_no_component)
        # score = 5.0 (high kw) + min((10-1)*0.3, 2.0) = 5.0 + 2.0 = 7.0
        assert issue.relevance_score == pytest.approx(7.0, abs=0.5)


class TestScorePRs:
    """Test PR-specific scoring bonuses."""

    def test_merged_pr_gets_bonus(self, basic_config, pr_merged_with_fix):
        scorer = KeywordScorer()
        prs = {400: pr_merged_with_fix}
        scorer.score_prs(prs, basic_config)
        # merged=True → +2.0 bonus
        assert pr_merged_with_fix.relevance_score >= 2.0

    def test_linked_issue_gets_bonus(self, basic_config, pr_merged_with_fix):
        scorer = KeywordScorer()
        prs = {400: pr_merged_with_fix}
        scorer.score_prs(prs, basic_config)
        # linked_issues=[100] → +1.5 bonus
        assert pr_merged_with_fix.relevance_score >= 3.5  # merged + linked

    def test_fix_in_title_gets_bonus(self, basic_config, pr_merged_with_fix):
        scorer = KeywordScorer()
        prs = {400: pr_merged_with_fix}
        scorer.score_prs(prs, basic_config)
        # "Fix" in title → +1.0 bonus
        assert pr_merged_with_fix.relevance_score >= 4.5  # merged + linked + fix


class TestScoreCode:
    """Test Code scoring with path matching and containment dedup."""

    def test_component_in_path_adds_3(self, basic_config, code_result_path_match):
        scorer = KeywordScorer()
        results = {"projects/mylib/src/memory_manager.cpp": code_result_path_match}
        scorer.score_code(results, basic_config)
        # "mylib" in path → +3.0
        assert code_result_path_match.relevance_score >= 3.0

    def test_keyword_in_snippet_scores(self, basic_config, code_result_path_match):
        scorer = KeywordScorer()
        results = {"projects/mylib/src/memory_manager.cpp": code_result_path_match}
        scorer.score_code(results, basic_config)
        # "memory leak" in snippet → +5.0 (high)
        assert "memory leak" in code_result_path_match.matched_keywords
        assert code_result_path_match.relevance_score >= 8.0  # component + keyword

    def test_containment_dedup_works_for_code(self, basic_config):
        """Ensure short keywords suppressed by longer ones in code scoring too."""
        scorer = KeywordScorer()
        config = basic_config
        config.keywords_low = list(config.keywords_low) + ["memory"]
        try:
            object.__delattr__(config, "_all_keywords")
        except AttributeError:
            pass
        try:
            object.__delattr__(config, "_kw_weight_map")
        except AttributeError:
            pass
        result = CodeResult(
            path="src/handler.cpp", url="", repo="test",
            content_snippet="fix memory leak in handler",
        )
        results = {"src/handler.cpp": result}
        scorer.score_code(results, config)
        # "memory" should be suppressed — "memory leak" covers it
        assert "memory leak" in result.matched_keywords


class TestScoreCommits:
    """Test Commit scoring with summary line bonus and containment dedup."""

    def test_summary_line_bonus(self, basic_config, commit_result):
        scorer = KeywordScorer()
        results = {"def456": commit_result}
        scorer.score_commits(results, basic_config)
        # "memory leak" in first line → +5.0 (high) + 1.5 (summary)
        assert commit_result.relevance_score >= 6.5
        assert "memory leak" in commit_result.matched_keywords

    def test_keyword_only_in_body_no_summary_bonus(self, basic_config):
        scorer = KeywordScorer()
        result = CommitResult(
            sha="aaa", message="Update docs\n\nFix memory leak description.",
            url="", author="dev", date="2025-01-01",
        )
        results = {"aaa": result}
        scorer.score_commits(results, basic_config)
        # "memory leak" in body but NOT in first line → +5.0 only, no +1.5
        assert result.relevance_score == pytest.approx(5.0, abs=0.1)


class TestScoreDiscussions:
    """Test Discussion scoring with answer bonus."""

    def test_discussion_with_answer_bonus(self, basic_config, discussion_with_answer):
        scorer = KeywordScorer()
        results = {500: discussion_with_answer}
        scorer.score_discussions(results, basic_config)
        assert discussion_with_answer.relevance_score >= 5.0
        assert "memory leak" in discussion_with_answer.matched_keywords
