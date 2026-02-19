"""Auto query builder & seed synonym merger.

将 AI 只需提供 **关键词**（Phase 2 同义词扩展），查询构建交给代码。
同时从种子词库补充 AI 可能遗漏的基线同义词。

设计原则
--------
1. **Recall-first** — 查询侧重召回，评分器负责精准度
2. **Interleave** — R1/R3 交叉编排，防止某一 tier 全 miss 导致早停
3. **向后兼容** — config 已有 queries 就直接使用，不干预
"""

import json
import logging
import os
from functools import lru_cache
from itertools import zip_longest

log = logging.getLogger("gss.query_builder")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_SEED_DB_PATH = os.path.join(_DATA_DIR, "seed_synonyms.json")

# GitHub Search API: total query ≈ 256 chars
# Overhead: "repo:X is:issue {qualifiers}" ≈ 80-100 chars
# Remaining for template: ~160 chars
_MAX_TEMPLATE_LEN = 160

# Default query budget
DEFAULT_MAX_QUERIES = 15

# Round caps — prevent any single round from dominating the budget
_R1_CAP = 5   # Individual high keywords
_R2_CAP = 3   # High OR pairs (no component)
_R3_CAP = 4   # Medium keyword pairs
_R4_CAP = 2   # Cross-tier combinations
# R5 fills the remainder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _quote(kw: str) -> str:
    """Quote multi-word keywords for GitHub Search syntax.

    Single words pass through unchanged; multi-word phrases get double-quoted.
    >>> _quote("sigsegv")
    'sigsegv'
    >>> _quote("page fault")
    '"page fault"'
    """
    return f'"{kw}"' if " " in kw else kw


def _or_join(keywords: list[str]) -> str:
    """Join keywords with OR, quoting multi-word ones.

    >>> _or_join(["page fault", "sigsegv"])
    '"page fault" OR sigsegv'
    """
    return " OR ".join(_quote(kw) for kw in keywords)


def _template_len(tmpl: str, component: str = "") -> int:
    """Estimate final query length after {component} substitution."""
    expanded = tmpl.replace("{component}", component)
    return len(expanded)


# ---------------------------------------------------------------------------
# Core: build_queries
# ---------------------------------------------------------------------------
def build_queries(config, max_queries: int = DEFAULT_MAX_QUERIES) -> list[str]:
    """从关键词自动构建搜索查询模板列表。

    策略（5 轮生成 + 交叉编排）：
      R1: 每个 high 关键词独立查询（精准，最高命中率）
      R2: high 关键词 OR 对（无 component — 更广撤网）
      R3: medium 关键词两两 OR 对（含 component）
      R4: 跨 tier 组合 high + medium（广泛覆盖）
      R5: low 关键词三个一组 OR（含 component）

    编排策略：R1 和 R3 交叉排列（zip），防止 high 关键词全 miss 时
    因连续零结果触发早停。这对"行为描述型"搜索尤为关键——high 关键词
    可能是技术术语（容易命中），也可能是描述短语（不易命中）；而 R3
    的 medium 关键词通常包含替代表述，能打破连续零结果链。

    Parameters
    ----------
    config : SearchConfig
        已加载的搜索配置，需至少包含 keywords。
    max_queries : int
        最大查询数（默认 15）。

    Returns
    -------
    list[str]
        查询模板列表，可含 {component} 占位符。
    """
    has_comp = bool(config.component and config.component.strip())
    component = config.component.strip() if has_comp else ""

    high = list(config.keywords_high or [])
    medium = list(config.keywords_medium or [])
    low = list(config.keywords_low or [])

    if not high and not medium and not low:
        log.warning("[查询构建] 无关键词，无法生成查询")
        return []

    def _with_comp(q: str) -> str:
        """Prepend {component} placeholder when component exists."""
        return f"{{component}} {q}" if has_comp else q

    def _safe_append(target: list, tmpl: str):
        """Append template if within length limit."""
        if _template_len(tmpl, component) <= _MAX_TEMPLATE_LEN:
            target.append(tmpl)
        else:
            log.debug("[查询构建] 模板过长，跳过: %s...", tmpl[:60])

    # ── Generate each round into separate lists ──────────────────
    r1, r2, r3, r4, r5 = [], [], [], [], []

    # R1: Individual high keywords (most precise)
    r1_count = min(len(high), _R1_CAP)
    for kw in high[:r1_count]:
        _safe_append(r1, _with_comp(_quote(kw)))

    # R2: High OR pairs without component (broader sweep)
    if has_comp and len(high) >= 2:
        for i in range(0, min(len(high), _R2_CAP * 2), 2):
            pair = high[i:i + 2]
            if len(pair) == 2:
                _safe_append(r2, _or_join(pair))

    # R3: Medium keyword pairs (with component)
    r3_generated = 0
    for i in range(0, len(medium), 2):
        if r3_generated >= _R3_CAP:
            break
        group = medium[i:i + 2]
        _safe_append(r3, _with_comp(_or_join(group)))
        r3_generated += 1

    # R4: Cross-tier combinations (high + medium, broad)
    if high and medium:
        mix = [high[0]] + medium[:2]
        _safe_append(r4, _or_join(mix))
    if len(high) >= 2 and len(medium) >= 3:
        mix2 = [high[1]] + medium[2:4]
        _safe_append(r4, _or_join(mix2))

    # R5: Low keyword groups of 3 (with component)
    for i in range(0, len(low), 3):
        group = low[i:i + 3]
        _safe_append(r5, _with_comp(_or_join(group)))

    # ── Interleave R1 and R3 (core defense against early-stop) ──
    #
    #   旧顺序: R1 R1 R1 R1 R1 | R3 R3 R3 R3 | R2 R4 R5 ...
    #   新顺序: R1 R3 R1 R3 R1 R3 R1 R3 R1 | R2 | R4 | R5 ...
    #
    # 若 R1 全 miss（如"行为描述型"关键词），R3 仍有机会在 Q2
    # 就命中，打破连续零结果计数器，避免早停误杀。
    interleaved: list[str] = []
    for q_r1, q_r3 in zip_longest(r1, r3, fillvalue=None):
        if q_r1 is not None:
            interleaved.append(q_r1)
        if q_r3 is not None:
            interleaved.append(q_r3)

    # Append remaining rounds in order of expected breadth
    interleaved.extend(r2)
    interleaved.extend(r4)
    interleaved.extend(r5)

    # ── Deduplicate & cap ────────────────────────────────────────
    seen: set[str] = set()
    unique: list[str] = []
    for q in interleaved:
        q_norm = " ".join(q.split())
        if q_norm not in seen:
            seen.add(q_norm)
            unique.append(q)

    result = unique[:max_queries]

    # Log summary
    log.info("[查询构建] 从 %dH+%dM+%dL 关键词生成 %d 条查询 "
             "(R1=%d, R3=%d, R2=%d, R4=%d, R5=%d) [交叉编排]",
             len(high), len(medium), len(low), len(result),
             len(r1), len(r3), len(r2), len(r4), len(r5))

    if log.isEnabledFor(logging.DEBUG):
        for i, q in enumerate(result, 1):
            log.debug("  [Q%02d] %s", i, q)

    return result


# ---------------------------------------------------------------------------
# Core: merge_seed_synonyms
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _load_seed_db() -> dict:
    """Load seed synonym database from JSON (cached)."""
    if not os.path.isfile(_SEED_DB_PATH):
        log.warning("[种子词库] 文件不存在: %s", _SEED_DB_PATH)
        return {"topics": []}
    with open(_SEED_DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def merge_seed_synonyms(config) -> int:
    """将种子词库中匹配的主题关键词合并到 config 中。

    匹配逻辑：config.topic 的小写形式包含某 topic 的任一 trigger 子串。
    合并行为：仅添加 config 中尚不存在的关键词（去重），保持 tier 归属。

    Parameters
    ----------
    config : SearchConfig
        搜索配置，其 keywords_high/medium/low 列表会被原地修改。

    Returns
    -------
    int
        新增的关键词总数。
    """
    db = _load_seed_db()
    topic_lower = config.topic.lower()
    total_added = 0

    # Build set of all existing keywords (case-insensitive)
    existing = set(kw.lower() for kw in
                   (config.keywords_high + config.keywords_medium + config.keywords_low))

    for topic in db.get("topics", []):
        triggers = topic.get("triggers", [])
        # Check if any trigger appears in the user's topic
        matched_trigger = None
        for t in triggers:
            if t.lower() in topic_lower:
                matched_trigger = t
                break
        if not matched_trigger:
            continue

        topic_added = 0
        tier_map = [
            ("high", config.keywords_high),
            ("medium", config.keywords_medium),
            ("low", config.keywords_low),
        ]

        for tier_name, config_list in tier_map:
            for kw in topic.get(tier_name, []):
                if kw.lower() not in existing:
                    config_list.append(kw)
                    existing.add(kw.lower())
                    topic_added += 1

        if topic_added > 0:
            log.info("[种子词库] 匹配 \"%s\" (trigger: \"%s\") → 补充 %d 个关键词",
                     topic["id"], matched_trigger, topic_added)
            total_added += topic_added

    # Invalidate keyword caches on config (if any were set)
    if total_added > 0:
        _invalidate_config_cache(config)

    return total_added


def _invalidate_config_cache(config):
    """Clear cached keyword properties on SearchConfig after mutation."""
    for key in ("_all_keywords_cache", "_kw_weight_cache"):
        try:
            object.__delattr__(config, key)
        except AttributeError:
            pass
