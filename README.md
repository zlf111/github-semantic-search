# GitHub Semantic Search v6 — Cursor AI Skill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Cursor AI](https://img.shields.io/badge/Cursor-AI%20Skill-purple.svg)](https://cursor.sh)

> 用一句话搜遍 GitHub Issues / PRs / Code / Commits / Discussions，AI 同义词扩展 + 代码自动构建查询 + 多维度评分排序。

## 快速开始

### 1. 安装 Skill

**方式 A：克隆仓库到 Cursor 项目**

```bash
cd 你的项目/.cursor/skills/
git clone https://github.com/zlf111/github-semantic-search.git github-issue-search
```

**方式 B：手动复制**

将本仓库的内容放入你的 Cursor 项目下：

```
你的项目/
├── .cursor/
│   └── skills/
│       └── github-issue-search/
│           ├── SKILL.md          ← 仓库根目录
│           ├── scripts/          ← 搜索核心代码
│           ├── references/       ← 参考文档
│           └── docs/             ← 文档（可选）
```

### 2. 配置 GitHub Token

```powershell
# PowerShell
$env:GITHUB_TOKEN = "ghp_你的token"

# Linux / macOS
export GITHUB_TOKEN=ghp_你的token
```

Token 生成时**无需勾选任何权限** (scope)。详见 `docs/token-tutorial/` 图文教程。

### 3. 使用

在 Cursor Agent 模式下直接对话：

- "帮我搜 rocm-libraries 里 hipblaslt page fault 相关的 issue"
- "搜一下 PR 和 Issue，看看这个 bug 被修过没有"
- "噪声太多了，帮我过滤一下"

## 文件结构

```
share-github-semantic-search/
├── README.md               ← 本文件
├── SKILL.md                ← Skill 定义（Cursor Agent 读取）
├── scripts/                ← 搜索核心代码
│   ├── search_github.py    ← 主入口
│   ├── core/               ← 核心模块（评分、查询构建、交叉引用等）
│   ├── searchers/          ← 5 种搜索类型实现
│   ├── data/               ← 种子同义词库
│   └── tests/              ← 单元测试
├── references/             ← 参考文档（CLI、评分、同义词等）
├── docs/                   ← 在线文档
│   ├── index.html          ← 主文档页面（浏览器打开）
│   ├── cross_ref_graph.png ← 交叉引用关系图示例
│   └── token-tutorial/     ← Token 申请图文教程
└── presentation/           ← 演示材料
    ├── github-issue-search-intro-v7.pptx   ← 12 页 PPT（含演讲备注）
    └── ppt-presentation-v8.mp4             ← 5 分钟演示视频（含旁白）
```

## 核心功能

| 功能 | 说明 |
|------|------|
| **同义词扩展** | AI 分析 + 种子词库，一个 "page fault" 扩展出 14 个相关词 |
| **查询自动构建** | 代码编排 5 轮查询，R1/R3 交叉保证覆盖 |
| **多类型搜索** | Issues / PRs / Code / Commits / Discussions 并行搜索 |
| **加权评分** | 组件 +5、高权重词 +5、中权重词 +3、标题 +2、频率 +1~2 |
| **交叉引用** | 自动检测 Issue↔PR、PR↔PR 关联，生成关系图 |
| **Smart 模式** | AI 审阅结果，过滤噪声，二轮补搜遗漏 |
| **增量缓存** | 多轮调优不重复搜索 |

## 依赖

```
Python >= 3.10
requests
matplotlib (交叉引用图，可选)
networkx (交叉引用图，可选)
```

## 文档

用浏览器打开 `docs/index.html` 查看完整的交互式文档，包含 12 个 AI 对话示例。

## 演示

`presentation/` 目录下包含：
- **github-issue-search-intro-v7.pptx** — 12 页 PPT 演示（含演讲备注）
- **ppt-presentation-v8.mp4** — 5 分钟演示视频（含旁白动画）

## License

[MIT](LICENSE)
