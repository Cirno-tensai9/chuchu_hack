#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监听「生完草」信号文件：kusa_done.json 一旦出现就打印并退出。
可单独开一个终端运行，配合 kusa_auto.py 使用。
"""

import json
import os
import sys
import time

SIGNAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kusa_done.json")
POLL_SEC = 2


def main():
    print("监听信号文件:", SIGNAL_FILE)
    print("等待生草完成... (Ctrl+C 退出)")
    while True:
        try:
            if os.path.exists(SIGNAL_FILE):
                with open(SIGNAL_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print("\n【生完草】", data.get("message", ""), "—", data.get("at", ""))
                sys.exit(0)
        except (json.JSONDecodeError, IOError) as e:
            print("读信号文件异常:", e)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
