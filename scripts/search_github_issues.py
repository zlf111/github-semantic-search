#!/usr/bin/env python3
"""
GitHub Issue 语义搜索工具 v4 (向后兼容 wrapper)
================================================

此文件保留为向后兼容入口。新功能请使用 search_github.py。

所有参数和行为与 v4 完全一致。
"""

import os
import sys

# 将当前脚本目录加入 path，以便导入新模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from search_github import main

if __name__ == "__main__":
    main()
