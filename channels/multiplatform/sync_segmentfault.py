#!/usr/bin/env python3
"""一键同步思否（segmentfault）。

用法：
  python3 sync_segmentfault.py 文章.md [--title 标题] [--cover 封面URL] [--tags 标签a,标签b] [--retry]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp_bridge


def main():
    args = mcp_bridge.build_sync_args("segmentfault", sys.argv[1:])
    asyncio.run(mcp_bridge.cmd_sync(args))


if __name__ == "__main__":
    main()
