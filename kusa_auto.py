#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生草系统 爬虫式自动化
- 自动打开页面 → 触发过载生巨草 → 轮询直到检测到完成
- 生完后写入「信号文件」，不依赖 QQ；可配合 --loop 循环爬
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("请先安装: pip install playwright && playwright install chromium")
    sys.exit(1)

# ---------- 配置 ----------
# 默认 URL；可从环境变量 KUSA_GAME_URL 覆盖（仅在此处读取一次，避免重复 env 开销）
_DEFAULT_GAME_URL = "http://110.41.149.62/kusa"
# 生完草后写入的信号文件（用这个文件就知道「生完了」）
SIGNAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kusa_done.json")
HEADLESS = os.environ.get("HEADLESS", "false").lower() in ("1", "true", "yes")
# 先点击「生草」tab，再在 tab 内找按钮
TAB_SELECTORS = [
    '[role="tab"]:has-text("生草")',
    'a:has-text("生草")',
    'button:has-text("生草")',
    '.tab:has-text("生草")',
    '[class*="tab"]:has-text("生草")',
]
# 在生草 tab 里找「过载生草」「巨草」按钮
TRIGGER_SELECTORS = [
    'button:has-text("过载生草")',
    'a:has-text("过载生草")',
    'button:has-text("巨草")',
    'a:has-text("巨草")',
    'button:has-text("过载")',
    'a:has-text("过载")',
    'button:has-text("生巨草")',
    'a:has-text("生巨草")',
    '[class*="overload"]',
    '[class*="big-grass"]',
]
DONE_INDICATORS = [
    'text=生完',
    'text=完成',
    'text=巨草',
    '[class*="done"]',
    '[class*="complete"]',
]
# 生完草后要点击的「恢复承载力」按钮
RESTORE_SELECTORS = [
    'button:has-text("恢复承载力")',
    'a:has-text("恢复承载力")',
    '[class*="restore"]:has-text("恢复承载力")',
]
# 前 5 分钟：每 FAST_POLL 秒检查一次；超过 5 分钟未完成则改为每 SLOW_POLL 秒检查，直到完成
FAST_POLL = 2.0
SLOW_POLL = 60.0   # 一分钟检查一次
SLOW_AFTER = 300   # 5 分钟（秒）
MAX_WAIT = 600
# 循环模式：每轮结束后等多久再开始下一轮（默认 5 分钟）
LOOP_INTERVAL = int(os.environ.get("KUSA_LOOP_SEC", "300"))


def write_done_signal(done_selector: str = ""):
    """生完草后写入信号文件，方便其它脚本或你自己查看。"""
    data = {
        "done": True,
        "at": datetime.now().isoformat(),
        "message": "过载生巨草已完成",
        "detected_by": done_selector or "unknown",
    }
    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("[信号] 已写入:", SIGNAL_FILE)


def clear_signal():
    """清空信号文件（新一轮爬取前可调用）。"""
    if os.path.exists(SIGNAL_FILE):
        os.remove(SIGNAL_FILE)


async def run_once(p, headless: bool, game_url: str) -> bool:
    """执行一轮：打开页面 → 点触发 → 等完成。返回是否检测到完成。"""
    browser = await p.chromium.launch(headless=headless)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    )
    page = await context.new_page()
    try:
        await page.goto(game_url, wait_until="networkidle", timeout=15000)
    except Exception as e:
        print("打开页面失败:", e)
        await browser.close()
        return False

    await asyncio.sleep(2)

    # 先尝试点击「生草」tab
    for tab_sel in TAB_SELECTORS:
        try:
            if await page.locator(tab_sel).first.is_visible():
                print("[爬虫] 点击生草 tab:", tab_sel)
                await page.click(tab_sel)
                await asyncio.sleep(1)
                break
        except Exception:
            continue

    triggered = False
    for sel in TRIGGER_SELECTORS:
        try:
            if await page.locator(sel).first.is_visible():
                print("[爬虫] 点击触发:", sel)
                await page.click(sel)
                triggered = True
                break
        except Exception:
            continue

    if not triggered:
        print("未找到过载/生巨草按钮，保存页面到 kusa_debug.html")
        with open("kusa_debug.html", "w", encoding="utf-8") as f:
            f.write(await page.content())
        await browser.close()
        return False

    deadline = time.monotonic() + MAX_WAIT
    t0 = time.monotonic()
    done_selector = ""
    while time.monotonic() < deadline:
        for ind in DONE_INDICATORS:
            try:
                if await page.locator(ind).first.is_visible():
                    done_selector = ind
                    break
            except Exception:
                continue
        if done_selector:
            break
        # 前 5 分钟每 2 秒检查，超过 5 分钟未完成则改为每 1 分钟检查一次
        elapsed = time.monotonic() - t0
        interval = FAST_POLL if elapsed < SLOW_AFTER else SLOW_POLL
        await asyncio.sleep(interval)

    if done_selector:
        print("检测到完成:", done_selector)
        write_done_signal(done_selector)
        # 生完后点击「恢复承载力」
        for sel in RESTORE_SELECTORS:
            try:
                if await page.locator(sel).first.is_visible():
                    print("[爬虫] 点击:", sel)
                    await page.click(sel)
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue
        else:
            print("未找到「恢复承载力」按钮，请检查 RESTORE_SELECTORS 或页面")
    await browser.close()
    return bool(done_selector)


async def main_loop(headless: bool, game_url: str):
    """循环模式：每轮结束后等 LOOP_INTERVAL 秒（默认 5 分钟）再开始下一轮。"""
    print("爬虫循环模式，每轮结束后等待", LOOP_INTERVAL, "秒再下一轮，Ctrl+C 退出")
    async with async_playwright() as p:
        while True:
            clear_signal()
            await run_once(p, headless, game_url)
            print("下一轮在", LOOP_INTERVAL, "秒后...")
            await asyncio.sleep(LOOP_INTERVAL)


async def main_once(headless: bool, game_url: str):
    """单次模式：爬一次，生完写信号并退出。"""
    async with async_playwright() as p:
        clear_signal()
        ok = await run_once(p, headless, game_url)
        if not ok:
            print("本轮未在限定时间内检测到完成，请检查页面或 DONE_INDICATORS")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生草系统爬虫：自动过载生巨草，生完写信号文件")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="循环爬取：每隔一段时间执行一轮（间隔由环境变量 KUSA_LOOP_SEC 控制，默认 300 秒）",
    )
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口")
    parser.add_argument(
        "--url",
        default=None,
        metavar="URL",
        help="游戏页面 URL（覆盖环境变量 KUSA_GAME_URL 和默认值）",
    )
    args = parser.parse_args()
    headless = not args.no_headless
    # 仅在此处解析 URL 一次：CLI > 环境变量 > 默认值（无重复 env 查找，无额外内存）
    game_url = (args.url or "").strip() or os.environ.get("KUSA_GAME_URL") or _DEFAULT_GAME_URL

    if args.loop:
        asyncio.run(main_loop(headless, game_url))
    else:
        asyncio.run(main_once(headless, game_url))
