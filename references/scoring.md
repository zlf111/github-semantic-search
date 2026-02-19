# Scoring Reference

Detailed scoring rules per content type.

## Issue/PR shared base scoring

| Rule | Points | Condition |
|------|--------|-----------|
| Component in body text | +2.0 | Component name found in title+body |
| Component label match | +3.0 | Component name found in any label |
| Keyword match (high) | +5.0 | High-tier keyword in title+body |
| Keyword match (medium) | +3.0 | Medium-tier keyword in title+body |
| Keyword match (low) | +1.0 | Low-tier keyword in title+body |
| Title bonus | +2.0 | Per keyword found in title |
| Frequency bonus | +0.3/occ | Extra occurrences, cap +2.0 |
| Comment keywords | 0.8x | Keywords found only in comments get discounted weight |
| Containment dedup | — | Short keyword suppressed when long keyword already matched |
| Partial multi-word match | tier × 0.6 | For keywords with 3+ words, if the first N-1 consecutive words match in text (e.g., "memory access" from "memory access fault"). Title bonus reduced to +1.0 for partial matches. Only applies to keywords with ≥3 words. |

## PR-specific bonuses (added on top)

| Rule | Points | Condition |
|------|--------|-----------|
| Merged | +2.0 | PR was merged |
| Linked issues | +1.5 | PR body contains `fixes #N` / `closes #N` |
| Fix/resolve in title | +1.0 | Title contains `fix`, `resolve`, or `close` |
| Component in changed files | +1.5 | Component name found in changed file paths |

## Code-specific scoring

| Rule | Points | Condition |
|------|--------|-----------|
| Component in file path | +3.0 | Component name in the file path |
| Keyword in path | +1.0 | Per keyword found in file path |
| Keyword in snippet | +weight | Standard keyword weight from tiers |

## Commit-specific scoring

| Rule | Points | Condition |
|------|--------|-----------|
| Keyword in summary | +1.5 | Keyword in first line of commit message |
| Keyword in body | +weight | Standard keyword weight from tiers |

## Discussion-specific scoring

| Rule | Points | Condition |
|------|--------|-----------|
| Keyword in answer | +1.0 | Per keyword found in the accepted answer body |
| Standard keyword scoring | +weight | Same as Issues (title+body+comments) |
