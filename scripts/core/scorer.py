"""Keyword relevance scoring engine for all content types."""

import logging

from core.models import (Issue, PullRequest, CodeResult, CommitResult,
                         DiscussionResult, SearchConfig)

log = logging.getLogger("gss.scorer")


class KeywordScorer:
    """Score search results by keyword relevance.

    Scoring rules (shared):
    1. Component match (if specified): label +3.0, body +2.0
    2. Keyword match in title+body: weight per tier (high=5, medium=3, low=1)
    3. Title bonus: +2.0 if keyword appears in title
    4. Frequency bonus: +0.3 per extra occurrence (cap +2.0)
    5. Comment keywords: discounted weight (0.8x)
    6. Containment dedup: short keyword suppressed if long keyword already matched

    PR-specific bonuses:
    7. Merged PR: +2.0
    8. Linked issues: +1.5
    9. Fix/resolve in title: +1.0
    10. Component in changed files: +1.5
    """

    COMMENT_DISCOUNT = 0.8
    FREQ_BONUS_FACTOR = 0.3
    FREQ_BONUS_CAP = 2.0
    PARTIAL_MATCH_DISCOUNT = 0.6  # Partial multi-word match gets 60% weight

    # PR-specific bonuses
    PR_MERGED_BONUS = 2.0
    PR_LINKED_ISSUE_BONUS = 1.5
    PR_FIX_TITLE_BONUS = 1.0
    PR_FILE_COMPONENT_BONUS = 1.5

    @staticmethod
    def _build_containment_filter(keywords_lower: list[str]) -> set[str]:
        """Build set of short keywords that are substrings of longer ones."""
        suppressed = set()
        sorted_kws = sorted(keywords_lower, key=len, reverse=True)
        for i, long_kw in enumerate(sorted_kws):
            for short_kw in sorted_kws[i + 1:]:
                if short_kw != long_kw and short_kw in long_kw:
                    suppressed.add(short_kw)
        return suppressed

    @staticmethod
    def _partial_match(kw_lower: str, text: str) -> str | None:
        """Try partial matching for multi-word keywords.

        For a keyword with N words (N >= 3), check if the first N-1
        consecutive words appear as a substring. Returns the matched
        partial string, or None.

        Examples:
            "memory access fault" → tries "memory access" (2 of 3 words)
            "illegal memory access" → tries "illegal memory" (2 of 3 words)
            "gpu hang" → None (only 2 words, too short for partial)
        """
        words = kw_lower.split()
        if len(words) < 3:
            return None
        # Try dropping the last word, then the first word
        candidates = [
            " ".join(words[:-1]),  # drop last: "memory access fault" → "memory access"
            " ".join(words[1:]),   # drop first: "illegal memory access" → "memory access"
        ]
        for candidate in candidates:
            if candidate in text:
                return candidate
        return None

    def _score_keywords(self, text: str, title_lower: str,
                        kw_weights: dict, kw_all_lower: list,
                        suppressed: set,
                        comments_keywords: set) -> tuple[float, set]:
        """Core keyword scoring logic shared by all types.

        Supports exact match and partial multi-word matching.
        Returns (score, matched_keywords_set).
        """
        score = 0.0
        matched = set()
        long_matched = set()

        for kw_lower in kw_all_lower:
            # --- Exact match (full keyword found in text) ---
            if kw_lower in text:
                if kw_lower in suppressed:
                    if any(kw_lower in lm for lm in long_matched):
                        continue
                matched.add(kw_lower)
                long_matched.add(kw_lower)
                score += kw_weights[kw_lower]
                if kw_lower in title_lower:
                    score += 2.0
                count = text.count(kw_lower)
                if count > 1:
                    freq_bonus = min((count - 1) * self.FREQ_BONUS_FACTOR, self.FREQ_BONUS_CAP)
                    score += freq_bonus
            elif kw_lower in title_lower:
                if kw_lower in suppressed:
                    if any(kw_lower in lm for lm in long_matched):
                        continue
                matched.add(kw_lower)
                long_matched.add(kw_lower)
                score += kw_weights[kw_lower] + 2.0
            else:
                # --- Partial match for multi-word keywords (3+ words) ---
                partial = self._partial_match(kw_lower, text)
                if partial and kw_lower not in matched:
                    if kw_lower in suppressed:
                        if any(kw_lower in lm for lm in long_matched):
                            continue
                    matched.add(kw_lower)
                    long_matched.add(kw_lower)
                    weight = kw_weights[kw_lower] * self.PARTIAL_MATCH_DISCOUNT
                    score += weight
                    if partial in title_lower:
                        score += 1.0  # reduced title bonus for partial

        # Comment keywords (discounted)
        for keyword in comments_keywords:
            kw_lower = keyword.lower()
            if kw_lower not in matched:
                if kw_lower in suppressed:
                    if any(kw_lower in lm for lm in long_matched):
                        continue
                matched.add(kw_lower)
                weight = kw_weights.get(kw_lower, 1.0)
                score += weight * self.COMMENT_DISCOUNT

        return score, matched

    def score_issues(self, issues: dict[int, Issue], config: SearchConfig):
        """Score all issues in-place based on keyword matching."""
        n = len(issues)
        log.info("正在评分 %d 个 issues...", n)
        component = config.component.lower() if config.has_component else ""
        kw_weights = config.keyword_weight_map
        kw_all_lower = sorted(kw_weights.keys(), key=len, reverse=True)
        suppressed = self._build_containment_filter(kw_all_lower)

        for issue in issues.values():
            text = f"{issue.title} {issue.body}".lower()
            title_lower = issue.title.lower()
            score = 0.0

            # Component match
            if config.has_component:
                if component in text:
                    score += 2.0
                if any(component in l.lower() for l in issue.labels):
                    score += 3.0

            kw_score, matched = self._score_keywords(
                text, title_lower, kw_weights, kw_all_lower,
                suppressed, issue.matched_in_comments
            )
            score += kw_score
            issue.matched_keywords = matched
            issue.relevance_score = score

    def score_prs(self, prs: dict[int, PullRequest], config: SearchConfig):
        """Score all PRs in-place based on keyword matching + PR bonuses."""
        n = len(prs)
        log.info("正在评分 %d 个 PRs...", n)
        component = config.component.lower() if config.has_component else ""
        kw_weights = config.keyword_weight_map
        kw_all_lower = sorted(kw_weights.keys(), key=len, reverse=True)
        suppressed = self._build_containment_filter(kw_all_lower)

        import re
        fix_re = re.compile(r"\b(fix|resolve|close)\b", re.IGNORECASE)

        for pr in prs.values():
            text = f"{pr.title} {pr.body}".lower()
            title_lower = pr.title.lower()
            score = 0.0

            # Component match
            if config.has_component:
                if component in text:
                    score += 2.0
                if any(component in l.lower() for l in pr.labels):
                    score += 3.0
                # Component in changed files
                if pr.changed_files:
                    if any(component in f.lower() for f in pr.changed_files):
                        score += self.PR_FILE_COMPONENT_BONUS

            # Keyword scoring
            kw_score, matched = self._score_keywords(
                text, title_lower, kw_weights, kw_all_lower,
                suppressed, pr.matched_in_comments
            )
            score += kw_score

            # PR-specific bonuses
            if pr.merged:
                score += self.PR_MERGED_BONUS
            if pr.linked_issues:
                score += self.PR_LINKED_ISSUE_BONUS
            if fix_re.search(pr.title):
                score += self.PR_FIX_TITLE_BONUS

            pr.matched_keywords = matched
            pr.relevance_score = score

    def score_code(self, results: dict[str, CodeResult], config: SearchConfig):
        """Score code results based on keyword matching in path + snippet."""
        n = len(results)
        log.info("正在评分 %d 个代码文件...", n)
        component = config.component.lower() if config.has_component else ""
        kw_weights = config.keyword_weight_map
        kw_all_lower = sorted(kw_weights.keys(), key=len, reverse=True)
        suppressed = self._build_containment_filter(kw_all_lower)

        for result in results.values():
            text = f"{result.path} {result.content_snippet}".lower()
            score = 0.0
            matched = set()
            long_matched = set()

            # Component in file path
            if config.has_component and component in result.path.lower():
                score += 3.0

            for kw_lower in kw_all_lower:
                if kw_lower not in text:
                    continue
                # Containment dedup
                if kw_lower in suppressed:
                    if any(kw_lower in lm for lm in long_matched):
                        continue
                matched.add(kw_lower)
                long_matched.add(kw_lower)
                weight = kw_weights[kw_lower]
                score += weight
                # Path match bonus
                if kw_lower in result.path.lower():
                    score += 1.0

            result.matched_keywords = matched
            result.relevance_score = score

    def score_commits(self, results: dict[str, CommitResult], config: SearchConfig):
        """Score commit results based on keyword matching in message."""
        n = len(results)
        log.info("正在评分 %d 个 commits...", n)
        kw_weights = config.keyword_weight_map
        kw_all_lower = sorted(kw_weights.keys(), key=len, reverse=True)
        suppressed = self._build_containment_filter(kw_all_lower)

        for result in results.values():
            text = result.message.lower()
            score = 0.0
            matched = set()
            long_matched = set()

            # First line (summary) gets bonus
            first_line = text.split("\n")[0] if text else ""

            for kw_lower in kw_all_lower:
                if kw_lower not in text:
                    continue
                # Containment dedup
                if kw_lower in suppressed:
                    if any(kw_lower in lm for lm in long_matched):
                        continue
                matched.add(kw_lower)
                long_matched.add(kw_lower)
                weight = kw_weights[kw_lower]
                score += weight
                # Summary line bonus
                if kw_lower in first_line:
                    score += 1.5

            result.matched_keywords = matched
            result.relevance_score = score

    def score_discussions(self, results: dict[int, DiscussionResult], config: SearchConfig):
        """Score discussion results based on keyword matching."""
        n = len(results)
        log.info("正在评分 %d 个 discussions...", n)
        kw_weights = config.keyword_weight_map
        kw_all_lower = sorted(kw_weights.keys(), key=len, reverse=True)
        suppressed = self._build_containment_filter(kw_all_lower)

        for disc in results.values():
            text = f"{disc.title} {disc.body} {disc.answer_body}".lower()
            title_lower = disc.title.lower()

            if disc.comments_text:
                comments_lower = disc.comments_text.lower()
                for kw_lower in kw_all_lower:
                    if kw_lower in comments_lower:
                        disc.matched_in_comments.add(kw_lower)

            kw_score, matched = self._score_keywords(
                text, title_lower, kw_weights, kw_all_lower,
                suppressed, disc.matched_in_comments
            )
            if disc.answer_body:
                answer_lower = disc.answer_body.lower()
                for kw_lower in kw_all_lower:
                    if kw_lower in answer_lower and kw_lower in matched:
                        kw_score += 1.0

            disc.matched_keywords = matched
            disc.relevance_score = kw_score
