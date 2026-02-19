"""JSON cache for incremental search — supports all 5 result types."""

import json
import logging
import os
import time

log = logging.getLogger("gss.cache")

from core.models import (
    Issue, PullRequest, CodeResult, CommitResult, DiscussionResult,
)


# ============================================================================
# Serialization helpers  (to_dict / from_dict for each type)
# ============================================================================

def _issue_to_dict(issue: Issue) -> dict:
    return {
        "number": issue.number,
        "title": issue.title,
        "state": issue.state,
        "url": issue.url,
        "labels": issue.labels,
        "created_at": issue.created_at,
        "body": issue.body,
        "comments_text": issue.comments_text,
        "comments_fetched": issue.comments_fetched,
        "matched_keywords": sorted(issue.matched_keywords),
        "matched_in_comments": sorted(issue.matched_in_comments),
        "relevance_score": issue.relevance_score,
    }


def _issue_from_dict(d: dict) -> Issue:
    return Issue(
        number=d["number"],
        title=d["title"],
        state=d["state"],
        url=d["url"],
        labels=d["labels"],
        created_at=d["created_at"],
        body=d.get("body", ""),
        comments_text=d.get("comments_text", ""),
        comments_fetched=d.get("comments_fetched", False),
        matched_keywords=set(d.get("matched_keywords", [])),
        matched_in_comments=set(d.get("matched_in_comments", [])),
        relevance_score=d.get("relevance_score", 0.0),
    )


def _pr_to_dict(pr: PullRequest) -> dict:
    return {
        "number": pr.number,
        "title": pr.title,
        "state": pr.state,
        "merged": pr.merged,
        "url": pr.url,
        "labels": pr.labels,
        "created_at": pr.created_at,
        "body": pr.body,
        "review_comments_text": pr.review_comments_text,
        "comments_fetched": pr.comments_fetched,
        "linked_issues": pr.linked_issues,
        "changed_files": pr.changed_files,
        "matched_keywords": sorted(pr.matched_keywords),
        "matched_in_comments": sorted(pr.matched_in_comments),
        "relevance_score": pr.relevance_score,
    }


def _pr_from_dict(d: dict) -> PullRequest:
    return PullRequest(
        number=d["number"],
        title=d["title"],
        state=d["state"],
        merged=d.get("merged", False),
        url=d["url"],
        labels=d.get("labels", []),
        created_at=d["created_at"],
        body=d.get("body", ""),
        review_comments_text=d.get("review_comments_text", ""),
        comments_fetched=d.get("comments_fetched", False),
        linked_issues=d.get("linked_issues", []),
        changed_files=d.get("changed_files", []),
        matched_keywords=set(d.get("matched_keywords", [])),
        matched_in_comments=set(d.get("matched_in_comments", [])),
        relevance_score=d.get("relevance_score", 0.0),
    )


def _code_to_dict(r: CodeResult) -> dict:
    return {
        "path": r.path,
        "url": r.url,
        "repo": r.repo,
        "sha": r.sha,
        "content_snippet": r.content_snippet[:2000],  # truncate for cache size
        "matched_keywords": sorted(r.matched_keywords),
        "relevance_score": r.relevance_score,
    }


def _code_from_dict(d: dict) -> CodeResult:
    return CodeResult(
        path=d["path"],
        url=d["url"],
        repo=d.get("repo", ""),
        sha=d.get("sha", ""),
        content_snippet=d.get("content_snippet", ""),
        matched_keywords=set(d.get("matched_keywords", [])),
        relevance_score=d.get("relevance_score", 0.0),
    )


def _commit_to_dict(r: CommitResult) -> dict:
    return {
        "sha": r.sha,
        "message": r.message[:2000],
        "url": r.url,
        "author": r.author,
        "date": r.date,
        "changed_files": r.changed_files[:20],
        "matched_keywords": sorted(r.matched_keywords),
        "relevance_score": r.relevance_score,
    }


def _commit_from_dict(d: dict) -> CommitResult:
    return CommitResult(
        sha=d["sha"],
        message=d.get("message", ""),
        url=d["url"],
        author=d.get("author", ""),
        date=d.get("date", ""),
        changed_files=d.get("changed_files", []),
        matched_keywords=set(d.get("matched_keywords", [])),
        relevance_score=d.get("relevance_score", 0.0),
    )


def _discussion_to_dict(r: DiscussionResult) -> dict:
    return {
        "number": r.number,
        "title": r.title,
        "url": r.url,
        "category": r.category,
        "created_at": r.created_at,
        "body": r.body[:50000],
        "answer_body": r.answer_body[:10000],
        "comments_text": r.comments_text[:20000],
        "matched_keywords": sorted(r.matched_keywords),
        "matched_in_comments": sorted(r.matched_in_comments),
        "relevance_score": r.relevance_score,
    }


def _discussion_from_dict(d: dict) -> DiscussionResult:
    return DiscussionResult(
        number=d["number"],
        title=d["title"],
        url=d["url"],
        category=d.get("category", ""),
        created_at=d.get("created_at", ""),
        body=d.get("body", ""),
        answer_body=d.get("answer_body", ""),
        comments_text=d.get("comments_text", ""),
        matched_keywords=set(d.get("matched_keywords", [])),
        matched_in_comments=set(d.get("matched_in_comments", [])),
        relevance_score=d.get("relevance_score", 0.0),
    )


# Type registry: (section_key, key_extractor, to_dict_fn, from_dict_fn)
_TYPE_REGISTRY = {
    "issues":      (lambda r: r.number, _issue_to_dict,      _issue_from_dict),
    "prs":         (lambda r: r.number, _pr_to_dict,          _pr_from_dict),
    "code":        (lambda r: r.path,   _code_to_dict,        _code_from_dict),
    "commits":     (lambda r: r.sha,    _commit_to_dict,      _commit_from_dict),
    "discussions": (lambda r: r.number, _discussion_to_dict,  _discussion_from_dict),
}


# ============================================================================
# Save / Load — unified multi-type interface
# ============================================================================

def save_cache(results: dict, repo: str, path: str,
               type_key: str = "issues"):
    """Save results to disk (atomic write).

    Args:
        results: dict of (key -> dataclass) for a single type.
        repo: Repository name for validation on load.
        path: File path for the cache.
        type_key: One of 'issues', 'prs', 'code', 'commits', 'discussions'.
    """
    key_fn, to_dict_fn, _ = _TYPE_REGISTRY[type_key]

    # Load existing cache to merge (multi-type in one file)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("repo") != repo:
                existing = {}  # repo mismatch, start fresh
        except (json.JSONDecodeError, IOError):
            existing = {}

    if not existing:
        existing = {"repo": repo, "saved_at": "", "version": "v5"}

    existing["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    section = {}
    for item in results.values():
        k = str(key_fn(item))
        section[k] = to_dict_fn(item)
    existing[type_key] = section

    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    log.info("[缓存] 已保存 %d 个 %s 到 %s", len(results), type_key, path)


def load_cache(path: str, repo: str, target: dict,
               type_key: str = "issues") -> bool:
    """Load cached results from disk into *target* dict.

    Args:
        path: Cache file path.
        repo: Expected repository name.
        target: Mutable dict to populate (key -> dataclass).
        type_key: Which section to load.

    Returns:
        True on success, False on failure or cache miss.
    """
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get("repo") != repo:
            log.warning("[缓存] 仓库不匹配 (%s vs %s)，忽略缓存",
                        data.get("repo"), repo)
            return False

        section = data.get(type_key)
        if section is None:
            return False

        _, _, from_dict_fn = _TYPE_REGISTRY[type_key]
        count = 0
        for _key_str, item_data in section.items():
            obj = from_dict_fn(item_data)
            # Determine the dict key from the reconstructed object
            key_fn = _TYPE_REGISTRY[type_key][0]
            obj_key = key_fn(obj)
            if obj_key not in target:
                target[obj_key] = obj
                count += 1

        log.info("[缓存] 从 %s 恢复 %d 个 %s (缓存时间: %s)",
                 path, count, type_key, data.get("saved_at", "?"))
        return True
    except (json.JSONDecodeError, KeyError) as e:
        log.error("[缓存] 加载失败: %s", e)
        return False
