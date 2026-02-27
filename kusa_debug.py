#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试脚本：打开生草页面，点击「生草」tab，保存 HTML 并列出「巨草」「过载生草」相关元素。
运行: python kusa_debug.py
"""

import asyncio
import os
import sys

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("请先安装: pip install playwright && playwright install chromium")
    sys.exit(1)

GAME_URL = "http://110.41.149.62/kusa"
TAB_SELECTORS = [
    '[role="tab"]:has-text("生草")',
    'a:has-text("生草")',
    'button:has-text("生草")',
    '.tab:has-text("生草")',
    '[class*="tab"]:has-text("生草")',
]
# 要查找的按钮文案
TARGET_TEXTS = ["巨草", "过载生草"]


async def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(script_dir, "kusa_debug.html")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        )
        page = await context.new_page()
        try:
            await page.goto(GAME_URL, wait_until="networkidle", timeout=20000)
        except Exception as e:
            print("打开页面失败:", e)
            await browser.close()
            return
        await asyncio.sleep(2)

        # 尝试点击「生草」tab
        tab_clicked = False
        for tab_sel in TAB_SELECTORS:
            try:
                if await page.locator(tab_sel).first.is_visible():
                    print("已点击生草 tab:", tab_sel)
                    await page.click(tab_sel)
                    tab_clicked = True
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                continue
        if not tab_clicked:
            print("未找到「生草」tab，继续用当前页面查找按钮")

        # 保存完整 HTML
        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print("已保存页面到:", html_path)

        # 查找包含「巨草」「过载生草」的可点击元素
        for text in TARGET_TEXTS:
            print(f"\n--- 包含「{text}」的元素 ---")
            for sel in [f'button:has-text("{text}")', f'a:has-text("{text}")', f'[role="button"]:has-text("{text}")', f'text="{text}"']:
                try:
                    loc = page.get_by_text(text)
                    n = await loc.count()
                    if n == 0:
                        continue
                    print(f"  get_by_text(\"{text}\") 数量: {n}")
                    for i in range(min(n, 5)):
                        try:
                            el = loc.nth(i)
                            tag = await el.evaluate("e => e.tagName")
                            visible = await el.is_visible()
                            inner = (await el.inner_text()).strip()[:60]
                            print(f"    [{i}] <{tag}> visible={visible} text={inner!r}")
                        except Exception as e:
                            print(f"    [{i}] 读取失败: {e}")
                    break
                except Exception as e:
                    continue
            else:
                print(f"  未找到包含「{text}」的元素")

        print("\n浏览器 5 秒后关闭，可先查看页面...")
        await asyncio.sleep(5)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
