# GitHub Semantic Search

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**GitHub 搜不到的 Issue，它能搜到。**

搜索 `"page fault"` → GitHub 只返回标题/正文里写了 "page fault" 的结果。
但很多相关 Issue 写的是 `"segmentation fault"`、`"SIGSEGV"`、`"illegal memory access"`——它们全被漏掉了。

GitHub Semantic Search 用 AI 同义词扩展 + 加权评分 + 交叉引用分析，让你一句话搜遍 **Issues / PRs / Code / Commits / Discussions**，不遗漏任何相关结果。

## 效果对比

| | GitHub 原生搜索 | GitHub Semantic Search |
|---|---|---|
| 搜索 "page fault" | 返回 3 个精确匹配 | 返回 **49 个相关结果**（7 个高度相关） |
| 跨术语发现 | 找不到写了 "SIGSEGV" 的 Issue | 自动扩展 **14 个同义词**，全部覆盖 |
| 结果排序 | 按时间排序 | 按**语义相关度**评分排序 |
| 跨类型关联 | 手动逐个点击 | 自动发现 Issue↔PR↔Commit **引用链** |

## 两种使用方式

### 方式一：命令行直接使用

不依赖任何 IDE，纯 Python 脚本。

```bash
# 1. 克隆
git clone https://github.com/zlf111/github-semantic-search.git
cd github-semantic-search

# 2. 安装依赖
pip install requests matplotlib networkx

# 3. （可选）设置 Token 提升速率
export GITHUB_TOKEN=ghp_xxx   # Linux/macOS
$env:GITHUB_TOKEN = "ghp_xxx" # PowerShell

# 4. 准备搜索配置
cat > search_config.json << 'EOF'
{
  "repo": "ROCm/hipBLASLt",
  "component": "",
  "topic": "page fault",
  "search_types": ["issues", "prs"],
  "filters": {"state": "", "date_from": "", "date_to": ""},
  "keywords": {
    "high": ["page fault", "segmentation fault", "SIGSEGV"],
    "medium": ["memory access fault", "gpu hang", "signal 11"],
    "low": ["core dump", "abort"]
  }
}
EOF

# 5. 搜索！
python scripts/search_github.py --config search_config.json --output results.md --search-comments
```

输出一份 Markdown 报告，包含评分排序的结果、匹配关键词高亮、交叉引用关系图。

### 方式二：AI 编程助手集成

将本仓库作为 **Skill** 集成到 AI 编程助手中，AI 会自动完成同义词扩展、配置生成、结果分析全流程——你只需要用自然语言描述需求。

<details>
<summary><b>Cursor</b></summary>

```bash
cd 你的项目/.cursor/skills/
git clone https://github.com/zlf111/github-semantic-search.git github-issue-search
```

在 Agent 模式下对话：
- *"帮我搜 rocm-libraries 里 page fault 相关的 issue"*
- *"搜一下 PR 和 Issue，看看这个 bug 被修过没有"*
- *"噪声太多了，帮我过滤一下"*

</details>

<details>
<summary><b>Windsurf / Cline / 其他支持 Skills 的 AI IDE</b></summary>

将 `SKILL.md` 放到对应 IDE 的 Skill/Prompt 配置目录中，`scripts/` 放到项目可访问的路径下。
不同 IDE 的集成路径不同，核心原理一致：AI 读取 `SKILL.md` → 生成配置 → 调用 Python 脚本 → 分析结果。

</details>

<details>
<summary><b>作为 Python 库在自己的脚本中调用</b></summary>

```python
import subprocess, json

config = {
    "repo": "owner/repo",
    "topic": "your topic",
    "search_types": ["issues"],
    "keywords": {
        "high": ["exact term"],
        "medium": ["related term"],
        "low": ["loose term"]
    }
}

with open("config.json", "w") as f:
    json.dump(config, f)

subprocess.run([
    "python", "scripts/search_github.py",
    "--config", "config.json",
    "--output", "results.md", "-q"
])
```

</details>

## 核心特性

### 同义词扩展

内置 12 个常见主题的种子词库，AI 补充领域特定术语。一个关键词自动扩展为多个搜索维度：

```
用户输入: "page fault"
  ↓
扩展结果:
  high:   page fault, segmentation fault, SIGSEGV, memory access fault
  medium: signal 11, sigbus, gpu hang, illegal memory access
  low:    core dump, coredump, abort, process killed
```

### 5 轮查询自动构建

不需要手写 GitHub 搜索语法。代码自动将关键词编排为 5 轮查询策略，R1/R3 交叉执行确保即使第一轮全部 miss，后续轮次仍能覆盖：

| 轮次 | 策略 | 示例 |
|------|------|------|
| R1 | 每个 high 词独立查询 | `hipblaslt "page fault"` |
| R3 | medium 词两两 OR 组合 | `hipblaslt SIGSEGV OR segfault` |
| R2 | high 词 OR 对（广撒网） | `"page fault" OR "segmentation fault"` |
| R4 | 跨层组合 | `"page fault" OR SIGSEGV OR segfault` |
| R5 | low 词三个一组 | `hipblaslt "core dump" OR abort` |

### 加权评分排序

不是简单的命中/未命中。每个结果有一个 **综合评分**，基于：

- 关键词权重：high +5 / medium +3 / low +1
- 位置加成：标题命中 +2，组件匹配 +2~3
- 类型加成：PR 已合并 +2，关联 Issue +1.5
- 频率加成：同一词多次出现 +0.3（上限 +2）

### 交叉引用检测

搜索 Issues + PRs + Commits 时，自动分析引用关系，生成关联图谱：

```
Issue #3211 ← "illegal memory access"
  ↑ 被 PR #3164 修复 (fixes #3211)
  ↑ PR #3164 关联 Commit 29b7468
  ↑ 同时被 Issue #5245, #5581 引用
```

### Smart 模式

AI 审阅中间结果，过滤误报、补充遗漏关键词、发起第二轮搜索。适合噪声多或多类型搜索场景。

## CLI 参考

```bash
python scripts/search_github.py \
  --config config.json \        # 搜索配置文件
  --output results.md \         # 输出报告
  --search-comments \           # 搜索评论（推荐）
  --intermediate-json inter.json  # Smart 模式中间文件
  --cache-file cache.json \     # 缓存文件（增量搜索）
  --resume \                    # 从缓存恢复
  --append-queries "new term" \ # 追加查询
  --dry-run \                   # 预览查询不执行
  -q                            # 静默模式
```

完整 CLI 文档见 [`references/cli.md`](references/cli.md)。

## 关于 GitHub Token

**不设置 Token 也能用**，但速率很低（Search 10 次/分钟，REST 60 次/小时）。

设置后提升到 Search 30 次/分钟，REST 5000 次/小时。生成 Token 时 **无需勾选任何权限**：

1. 打开 https://github.com/settings/tokens
2. Generate new token (classic)
3. 不勾选任何 scope → Generate
4. 复制 token，设置环境变量

详见 [`docs/token-tutorial/`](docs/token-tutorial/) 图文教程。

## 项目结构

```
├── SKILL.md                 # AI 助手集成定义
├── scripts/
│   ├── search_github.py     # 主入口
│   ├── core/                # 评分、查询构建、交叉引用、缓存
│   ├── searchers/           # Issues / PRs / Code / Commits / Discussions
│   ├── data/                # 种子同义词库 (12 主题)
│   └── tests/               # 单元测试
├── references/              # 详细参考文档
├── docs/                    # 交互式文档 + Token 教程
└── presentation/            # PPT + 视频演示
```

## 依赖

```bash
pip install requests                  # 必需
pip install matplotlib networkx       # 可选，交叉引用图
```

## 文档 & 演示

- **交互式文档**：用浏览器打开 [`docs/index.html`](docs/index.html)，包含 12 个真实搜索场景
- **PPT 演示**：[`presentation/github-issue-search-intro-v7.pptx`](presentation/github-issue-search-intro-v7.pptx)（12 页，含演讲备注）
- **视频演示**：[`presentation/ppt-presentation-v8.mp4`](presentation/ppt-presentation-v8.mp4)（5 分钟，含旁白动画）

## License

[MIT](LICENSE) — 随意使用、修改、分发。
