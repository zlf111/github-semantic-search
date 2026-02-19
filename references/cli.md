# CLI Reference

Full command-line options for `search_github.py`.

```
python search_github.py [options]
```

## Basic options

| Flag | Default | Description |
|------|---------|-------------|
| `--config`, `-c` | — | JSON config file path |
| `--repo` | `ROCm/rocm-libraries` | Target repository |
| `--component` | `""` | Component filter |
| `--topic` | `page fault` | Search topic |
| `--search-types` | *(from config)* | Override search types: `issues prs code commits discussions` |

## Filter options

| Flag | Default | Description |
|------|---------|-------------|
| `--state` | `""` | Filter by state: `open`, `closed`, or `""` (all) |
| `--date-from` | `""` | Start date `YYYY-MM-DD` |
| `--date-to` | `""` | End date `YYYY-MM-DD` |

## Search options

| Flag | Default | Description |
|------|---------|-------------|
| `--keywords` | *(from config)* | High-priority keywords (CLI shorthand) |
| `--queries` | *(from config)* | Search query templates (CLI shorthand) |
| `--search-comments` | off | Fetch comments for borderline items (Issues + PRs) |
| `--comments-low` | `3.0` | Lower score threshold for comment fetching |
| `--comments-high` | `8.0` | Upper score threshold (items above skip fetching) |
| `--concurrency` | auto | Parallel workers for comment fetching (auto: 4 with token, 1 without) |

## Cache options

| Flag | Default | Description |
|------|---------|-------------|
| `--cache-file` | `""` | JSON cache file path (supports all 5 types in one file) |
| `--resume` | off | Resume from cached results (all types) |

## Output options

| Flag | Default | Description |
|------|---------|-------------|
| `--output`, `-o` | stdout | Output file path |
| `--min-score` | `3.0` | Minimum relevance score to include |
| `--max-component` | `10` | Max component-only items shown per type |
| `--json` | off | Output as JSON (all types, unified schema) |
| `--dry-run` | off | Preview queries without executing |

## Smart options (v6)

| Flag | Default | Description |
|------|---------|-------------|
| `--intermediate-json` | `""` | Output intermediate JSON for AI review (top-30 + borderline per type) |
| `--score-overrides` | `""` | Apply AI score override JSON file |
| `--append-queries` | — | Append queries (multi-round search, use with `--resume`) |

## Execution options (v6)

| Flag | Default | Description |
|------|---------|-------------|
| `--no-parallel` | off | Disable parallel search (for debugging; multi-type parallelizes by default) |
| `--verbose`, `-v` | off | Verbose output (DEBUG level logging) |
| `--quiet`, `-q` | off | Quiet mode (warnings and errors only) |
