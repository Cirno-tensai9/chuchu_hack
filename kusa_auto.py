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
# 固定登录用 QQ 号（如需修改，直接改这里或后续扩展为从环境变量读取）
LOGIN_QQ = "530958461"
# 生完草后写入的信号文件（用这个文件就知道「生完了」）
SIGNAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kusa_done.json")
HEADLESS = os.environ.get("HEADLESS", "false").lower() in ("1", "true", "yes")
# 页面中「生草」入口（可以在左侧菜单、顶部导航等任意位置）
TAB_SELECTORS = [
    'li.el-menu-item:has-text("生草")',
    '[role="menuitem"]:has-text("生草")',
    'a:has-text("生草")',
    'button:has-text("生草")',
    'text=生草',
]
# 进入生草页后，只找「过载生草」按钮
TRIGGER_SELECTORS = [
    'button:has-text("过载生草")',
    'a:has-text("过载生草")',
    '[class*="overload"]:has-text("过载生草")',
]
# 任一项出现即视为本轮回合结束，进入「等 3 分钟 → 轮询按钮」；以「正在生长中」为主（无生完信号时用此即可）
DONE_INDICATORS = [
    'text=正在生长中',
    'text=生完',
    'text=完成',
    'text=半灵草',
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
# 轮询「正在生长中」等：每 FAST_POLL 秒检查一次（越小反应越快，默认 0.5）；超时后改为 SLOW_POLL
FAST_POLL = 0.5
SLOW_POLL = 60.0
SLOW_AFTER = 180   # 5 分钟（秒）
MAX_WAIT = 600
# 循环模式：每轮结束后等多久再开始下一轮（默认 5 分钟）
LOOP_INTERVAL = int(os.environ.get("KUSA_LOOP_SEC", "180"))


def write_done_signal(done_selector: str = ""):
    """生完草后写入信号文件，方便其它脚本或你自己查看。"""
    data = {
        "done": True,
        "at": datetime.now().isoformat(),
        "message": "过载生半灵草已完成",
        "detected_by": done_selector or "unknown",
    }
    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("[信号] 已写入:", SIGNAL_FILE)


def clear_signal():
    """清空信号文件（新一轮爬取前可调用）。"""
    if os.path.exists(SIGNAL_FILE):
        os.remove(SIGNAL_FILE)


async def run_once(
    p,
    headless: bool,
    game_url: str,
    login_qq: str = "",
    browser=None,
    page=None,
) -> bool:
    """执行一轮：打开页面 → 点触发 → 等完成。返回是否检测到完成。
    若传入 browser 与 page，则复用同一浏览器；未找到按钮时不会关闭，便于重试。
    """
    qq = login_qq or LOGIN_QQ
    reuse = browser is not None and page is not None
    if not reuse:
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
        if not reuse:
            await browser.close()
        return False

    await asyncio.sleep(1)

    # 如在登录页，先自动输入 QQ 并点击登录，再进行后续「生草」tab 和按钮查找
    try:
        qq_input = page.locator('input[placeholder="请输入QQ号"]').first
        login_button = page.locator('button:has-text("登录")').first
        if await qq_input.is_visible() and await login_button.is_visible():
            print("[爬虫] 检测到登录页，自动输入 QQ 并登录:", qq)
            await qq_input.fill(qq)
            await login_button.click()
            # 等待登录完成并进入主界面
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(1)
    except Exception:
        # 如果没有登录表单或操作失败，继续后续流程（可能已登录）
        pass

    # 尝试先点击页面上的「生草」入口（侧边栏 / 顶部导航等），然后再找「过载生草」按钮
    tab_clicked = False
    for tab_sel in TAB_SELECTORS:
        try:
            if await page.locator(tab_sel).first.is_visible():
                print("[爬虫] 点击生草入口:", tab_sel)
                await page.click(tab_sel)
                tab_clicked = True
                break
        except Exception:
            continue

    if tab_clicked:
        await asyncio.sleep(1)
    else:
        print("未在导航中找到「生草」入口，将直接在当前页面查找「过载生草」按钮")

    # 在选择草种为「半灵草」之前，优先点击一次「恢复承载力」（如果按钮可见）
    try:
        for sel in RESTORE_SELECTORS:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible():
                    print("[爬虫] 预先点击恢复承载力按钮:", sel)
                    await btn.click()
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue
    except Exception:
        # 如果找不到或点击失败，忽略，继续后续流程
        pass

    # 然后在页面里强行把草种参数改写成「半灵草」：无论下拉怎么选，plantKusa 一律用「半灵草」
    try:
        await page.evaluate(
            "() => {\
                try {\
                    if (window.S && typeof window.S.plantKusa === 'function' && !window.S._kusaAutoPatched) {\
                        const orig = window.S.plantKusa.bind(window.S);\
                        window.S.plantKusa = async function (_type, ...rest) {\
                            return await orig('半灵草', ...rest);\
                        };\
                        window.S._kusaAutoPatched = true;\
                        console.log('[kusa_auto] patched S.plantKusa to always use 半灵草');\
                    }\
                    window.localStorage && window.localStorage.setItem('lastKusaType', '半灵草');\
                } catch (e) { console.error('[kusa_auto] patch error', e); }\
            }"
        )
        print("[爬虫] 已强制将 plantKusa 的草种参数改写为「半灵草」")
        await asyncio.sleep(1)
    except Exception as e:
        print(f"[爬虫] 强制改写 plantKusa 草种参数为半灵草时出错，跳过草种强制切换: {e}")

    triggered = False
    for sel in TRIGGER_SELECTORS:
        try:
            if await page.locator(sel).first.is_visible():
                print("[爬虫] 点击过载生草按钮:", sel)
                await page.click(sel)
                triggered = True
                break
        except Exception:
            continue

    if not triggered:
        print("在生草 tab 内未找到「过载生草」按钮")
        if not reuse:
            await browser.close()
        return False

    # 轮询直到出现「正在生长中」或「生完」等，即进入「等 3 分钟 → 按 poll-min 轮询按钮」阶段
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
    return True


async def _check_overload_button_on_page(page, qq: str, game_url: str) -> bool:
    """在当前页面内执行一次：登录(如需) → 点生草入口 → 检查过载生草按钮是否可见。不关浏览器。"""
    try:
        await page.goto(game_url, wait_until="networkidle", timeout=15000)
    except Exception:
        return False
    await asyncio.sleep(1)
    try:
        qq_input = page.locator('input[placeholder="请输入QQ号"]').first
        login_btn = page.locator('button:has-text("登录")').first
        if await qq_input.is_visible() and await login_btn.is_visible():
            await qq_input.fill(qq)
            await login_btn.click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(1)
    except Exception:
        pass
    try:
        for tab_sel in TAB_SELECTORS:
            try:
                if await page.locator(tab_sel).first.is_visible():
                    await page.click(tab_sel)
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue
    except Exception:
        pass
    for sel in TRIGGER_SELECTORS:
        try:
            if await page.locator(sel).first.is_visible():
                return True
        except Exception:
            continue
    return False


async def is_overload_available(p, headless: bool, game_url: str, login_qq: str = "") -> bool:
    """单次检查：打开页面，看「过载生草」按钮是否可见（每次新建并关闭浏览器）。"""
    qq = login_qq or LOGIN_QQ
    browser = await p.chromium.launch(headless=headless)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    )
    page = await context.new_page()
    try:
        found = await _check_overload_button_on_page(page, qq, game_url)
        if found:
            print("[轮询] 检测到过载生草按钮已重新出现")
        await browser.close()
        return found
    except Exception as e:
        print("检查过载生草可用性时出错:", e)
        await browser.close()
        return False


async def wait_for_overload_button(
    p,
    headless: bool,
    game_url: str,
    login_qq: str,
    poll_interval_sec: float = 30,
    max_poll_count: int = 10,
) -> bool:
    """轮询直到「过载生草」按钮出现；超过 max_poll_count 次仍无结果则返回 False，由调用方重试整轮。"""
    qq = login_qq or LOGIN_QQ
    browser = await p.chromium.launch(headless=headless)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    )
    page = await context.new_page()
    poll_count = 0
    try:
        while poll_count < max_poll_count:
            poll_count += 1
            print(f"轮询检查「过载生草」按钮是否已刷新可用...（第 {poll_count}/{max_poll_count} 次）")
            found = await _check_overload_button_on_page(page, qq, game_url)
            if found:
                print("检测到按钮已刷新，开始下一轮过载生草。")
                await browser.close()
                return True
            print(f"按钮尚未刷新，{poll_interval_sec:.0f} 秒后再次检查...")
            await asyncio.sleep(poll_interval_sec)
        print(f"超过 {max_poll_count} 次轮询仍未发现按钮，关闭浏览器并重试整轮流程")
        await browser.close()
        return False
    except Exception as e:
        print("轮询等待按钮时出错:", e)
        await browser.close()
        return False


async def main_loop(
    headless: bool,
    game_url: str,
    login_qq: str = "",
    retry_sec: float = 60,
    poll_interval_sec: float = 30,
    max_poll_count: int = 10,
):
    """循环模式：完成一次过载生草后，等按钮刷新再自动下一轮；轮询超过 max_poll_count 次未发现按钮则重试整轮。"""
    print(
        f"爬虫循环模式：完成一次过载生草后，等待 3 分钟再每 {poll_interval_sec:.0f} 秒轮询（最多 {max_poll_count} 次），Ctrl+C 退出"
    )
    async with async_playwright() as p:
        browser, context, page = None, None, None
        while True:
            clear_signal()
            if browser is None or not browser.is_connected():
                browser = await p.chromium.launch(headless=headless)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                )
                page = await context.new_page()
            ok = await run_once(p, headless, game_url, login_qq, browser=browser, page=page)
            if not ok:
                print(f"本轮未检测到完成（若已点过过载生草，可能仍在生长），{retry_sec:.0f} 秒后同一浏览器内重试...")
                await asyncio.sleep(retry_sec)
                continue

            browser = None
            # 已检测到完成：先固定等待 3 分钟，再轮询按钮；超过 max_poll_count 次未发现则重试整轮
            print("本轮过载生草完成，先等待 3 分钟再开始轮询按钮刷新...")
            await asyncio.sleep(180)

            if not await wait_for_overload_button(
                p, headless, game_url, login_qq, poll_interval_sec, max_poll_count
            ):
                continue


async def main_once(headless: bool, game_url: str, login_qq: str = ""):
    """单次模式：爬一次，生完写信号并退出。"""
    async with async_playwright() as p:
        clear_signal()
        ok = await run_once(p, headless, game_url, login_qq)
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
    parser.add_argument(
        "--qq",
        default=None,
        metavar="QQ",
        help="登录用 QQ 号（覆盖环境变量 KUSA_QQ 和代码内默认值）",
    )
    parser.add_argument(
        "--retry-min",
        type=float,
        default=1,
        metavar="MIN",
        help="loop 时未检测到完成时，多少分钟后重试（支持小数，如 0.1、0.5，默认 1）",
    )
    parser.add_argument(
        "--poll-min",
        type=float,
        default=0.5,
        metavar="MIN",
        help="loop 时等待 3 分钟后，每隔多少分钟检查一次按钮（支持小数，如 0.1、0.5，默认 0.5 即 30 秒）",
    )
    parser.add_argument(
        "--max-poll",
        type=int,
        default=10,
        metavar="N",
        help="loop 时轮询按钮最多次数，超过仍未发现则重试整轮（默认 10）",
    )
    args = parser.parse_args()
    headless = not args.no_headless
    game_url = (args.url or "").strip() or os.environ.get("KUSA_GAME_URL") or _DEFAULT_GAME_URL
    login_qq = (args.qq or "").strip() or os.environ.get("KUSA_QQ") or ""
    retry_sec = max(1, int(round(args.retry_min * 60)))
    poll_interval_sec = max(5, int(round(args.poll_min * 60)))
    max_poll_count = max(1, args.max_poll)

    if args.loop:
        asyncio.run(main_loop(headless, game_url, login_qq, retry_sec, poll_interval_sec, max_poll_count))
    else:
        asyncio.run(main_once(headless, game_url, login_qq))
