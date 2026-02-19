# Synonym Expansion Guide

How to expand a search topic into comprehensive keyword lists.

## Expansion Dimensions

For any topic, think along these 6 axes:

| Dimension | Question | Example (page fault) |
|-----------|----------|---------------------|
| **Terminology layers** | What do OS, driver, runtime, and user each call it? | OS: page fault; Driver: protection fault; Runtime: illegal memory access; User: crash |
| **Signals & codes** | What error codes or signals are associated? | SIGSEGV, SIGBUS, signal 11 |
| **Consequences** | What happens as a result? | GPU hang, GPU reset, core dump |
| **Vendor-specific** | How does each vendor name it? | AMD: memory access fault, agent handle; NVIDIA: illegal memory access; Intel: page fault |
| **Log patterns** | What appears in logs? | GCVM_L2_PROTECTION_FAULT, UTCL2, MES failed |
| **Abbreviations** | Common short forms? | segfault, OOB, OOM |

## Weight Assignment

| Weight | Criteria | Score |
|--------|----------|-------|
| **high** | Direct synonym or exact technical term for the topic | +5 per match |
| **medium** | Related error that commonly co-occurs or is a consequence | +3 per match |
| **low** | Loosely associated term that *might* indicate the topic | +1 per match |

### Keyword Tier Strategy by Topic Type

high 关键词决定搜索前几条查询的内容。由于查询构建器将 R1（high 独立查询）和 R3（medium 对查询）交叉编排，且早停机制会在连续 3 条零结果后终止搜索，**high 关键词必须是"issue 正文中大概率原样出现的字符串"**。

| 话题类型 | high 关键词策略 | low 关键词策略 | 实例 |
|---------|---------------|---------------|------|
| **错误消息型** | 错误消息文本、信号名、错误码 | 宽泛后果、工具名 | `"segmentation fault"` → high: `"segmentation fault"`, `"sigsegv"`, `"signal 11"` |
| **行为描述型** | 涉及的技术术语、配置文件名、API 名 | 英语行为描述短语 | "tuning 无效" → high: `"tuning file"`, `"logic yaml"`, `"match table"` ; low: `"tuning no effect"`, `"tuning not working"` |
| **功能搜索型** | 功能名称、模块名、API 函数名 | 相关概念、上下游模块 | "expert scheduling" → high: `"expert scheduling"`, `"MoE"`, `"grouped gemm"` |

**反面教训**：将 `"tuning no effect"` 放入 high 会导致 R1 查询全部返回 0（因为 issue 中很少有人写这个完整短语），触发早停机制，丢失本可在 R3 命中的技术术语查询。

## Worked Examples

### Example 1: "page fault"

```json
{
  "keywords": {
    "high": [
      "page fault", "memory access fault", "illegal memory access",
      "segmentation fault", "protection fault"
    ],
    "medium": [
      "sigsegv", "sigbus", "segfault", "page not present",
      "read-only page", "gpu hang", "gpu reset", "gpu fault",
      "illegal address"
    ],
    "low": [
      "page table", "memory access violation", "memory access error",
      "illegal instruction", "signal 11", "signal 7",
      "protection_fault", "l2_protection_fault",
      "gcvm_l2_protection_fault", "utcl2",
      "write access to a read-only", "supervisor privilege",
      "gpu node", "out of bounds", "out-of-bounds",
      "abort", "core dump", "coredump",
      "amdgpu: page fault", "agent handle",
      "mes failed", "unrecoverable state"
    ]
  }
}
```

### Example 2: "performance regression"

```json
{
  "keywords": {
    "high": [
      "performance regression", "perf regression",
      "performance degradation", "slowdown",
      "performance drop"
    ],
    "medium": [
      "slow performance", "slower than", "throughput drop",
      "latency increase", "TFLOPS decrease",
      "bandwidth regression", "perf drop"
    ],
    "low": [
      "benchmark", "profiling", "bottleneck",
      "cache miss", "occupancy", "stall",
      "memory bandwidth", "compute utilization"
    ]
  }
}
```

### Example 3: "build failure"

```json
{
  "keywords": {
    "high": [
      "build failure", "build error", "compilation error",
      "compile error", "build failed", "cmake error"
    ],
    "medium": [
      "linker error", "undefined reference", "undefined symbol",
      "include error", "header not found", "missing dependency",
      "build broken"
    ],
    "low": [
      "cmake", "make", "ninja", "install error",
      "pkg-config", "find_package", "target_link",
      "ld: error", "clang error", "hipcc error"
    ]
  }
}
```

## Query Generation (Auto-Build)

**查询由代码自动构建** (`core/query_builder.py`)，AI 只需提供关键词。

如果 config 中省略 `queries` 字段，脚本会自动按 5 轮策略从关键词生成 ≤15 条查询：

1. **R1**: 每个 high 关键词独立查询 (含 component)
2. **R2**: high 关键词 OR 对 (无 component，仅当 component 非空时)
3. **R3**: medium 关键词两两 OR 对 (含 component)
4. **R4**: 跨 tier 组合 high + medium (广覆盖)
5. **R5**: low 关键词三个一组 OR (含 component)

如需手动控制查询，仍可在 config 中写 `queries` 字段（向后兼容）。

## Seed Synonym Database

脚本启动时自动根据 `config.topic` 匹配内置种子词库 (`scripts/data/seed_synonyms.json`)，补充 AI 可能遗漏的基线同义词。

覆盖 12 个常见主题：memory_fault, oom, memory_leak, crash, hang_deadlock, build_error, performance, race_condition, gpu_error, hip_cuda_error, test_failure, compatibility。

AI 只需尽力扩展同义词——种子词库兜底。
