#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生草系统 爬虫式自动化（可配置版）
在 kusa_auto.py 基础上支持：选定草种（默认巨草）、等待时间（默认5分钟）、
等待后轮询间隔（默认1分钟）、触发方式（默认「过载生草」或「开始生草」）。
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import List, Optional

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("请先安装: pip install playwright && playwright install chromium")
    sys.exit(1)

# ---------- 配置（可被命令行覆盖）----------
_DEFAULT_GAME_URL = "http://110.41.149.62/kusa"
LOGIN_QQ = "530958461"
SIGNAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kusa_done.json")
HEADLESS = os.environ.get("HEADLESS", "false").lower() in ("1", "true", "yes")

TAB_SELECTORS = [
    'li.el-menu-item:has-text("生草")',
    '[role="menuitem"]:has-text("生草")',
    'a:has-text("生草")',
    'button:has-text("生草")',
    'text=生草',
]

# 根据 trigger_mode 动态生成，见下方 get_trigger_selectors()
# 任一项出现即视为本轮回合结束，进入「等 wait-min → 按 poll-min 轮询按钮」；以「正在生长中」为主
DONE_INDICATORS = [
    'text=正在生长中',
    'text=生完',
    'text=完成',
    'text=巨草',
    'text=半灵草',
    '[class*="done"]',
    '[class*="complete"]',
]
RESTORE_SELECTORS = [
    'button:has-text("恢复承载力")',
    'a:has-text("恢复承载力")',
    '[class*="restore"]:has-text("恢复承载力")',
]
# 轮询「正在生长中」等：每 FAST_POLL 秒检查（越小反应越快）
FAST_POLL = 0.5
SLOW_POLL = 60.0
SLOW_AFTER = 300
MAX_WAIT = 600


def get_trigger_selectors(trigger_mode: str):
    """trigger_mode: '过载生草' 或 '开始生草'"""
    if trigger_mode == "开始生草":
        return [
            'button:has-text("开始生草")',
            'a:has-text("开始生草")',
        ]
    # 默认 过载生草
    return [
        'button:has-text("过载生草")',
        'a:has-text("过载生草")',
        '[class*="overload"]:has-text("过载生草")',
    ]


def write_done_signal(done_selector: str = "", trigger_mode: str = "过载生草"):
    data = {
        "done": True,
        "at": datetime.now().isoformat(),
        "message": f"{trigger_mode}已完成",
        "detected_by": done_selector or "unknown",
    }
    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("[信号] 已写入:", SIGNAL_FILE)


def clear_signal():
    if os.path.exists(SIGNAL_FILE):
        os.remove(SIGNAL_FILE)


async def run_once(
    p,
    headless: bool,
    game_url: str,
    kusa_type: str = "巨草",
    trigger_mode: str = "过载生草",
    login_qq: str = "",
    browser=None,
    page=None,
    state: Optional[dict] = None,
) -> bool:
    """若传入 browser 与 page 则复用同一浏览器；未找到触发按钮时不会关闭，便于重试。"""
    qq = login_qq or LOGIN_QQ
    trigger_selectors = get_trigger_selectors(trigger_mode)
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

    try:
        qq_input = page.locator('input[placeholder="请输入QQ号"]').first
        login_button = page.locator('button:has-text("登录")').first
        if await qq_input.is_visible() and await login_button.is_visible():
            print("[爬虫] 检测到登录页，自动输入 QQ 并登录:", qq)
            await qq_input.fill(qq)
            await login_button.click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(1)
    except Exception:
        pass

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
        print("未在导航中找到「生草」入口，将直接在当前页面查找按钮")

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
        pass

    # 强制将 plantKusa 的草种参数改为用户指定的 kusa_type
    kusa_escaped = kusa_type.replace("\\", "\\\\").replace("'", "\\'")
    try:
        await page.evaluate(
            f"""() => {{
                try {{
                    if (window.S && typeof window.S.plantKusa === 'function' && !window.S._kusaAutoPatched) {{
                        const orig = window.S.plantKusa.bind(window.S);
                        const kusaType = '{kusa_escaped}';
                        window.S.plantKusa = async function (_type, ...rest) {{
                            return await orig(kusaType, ...rest);
                        }};
                        window.S._kusaAutoPatched = true;
                        console.log('[kusa_auto_config] patched S.plantKusa to always use', kusaType);
                    }}
                    window.localStorage && window.localStorage.setItem('lastKusaType', '{kusa_escaped}');
                }} catch (e) {{ console.error('[kusa_auto_config] patch error', e); }}
            }}"""
        )
        print(f"[爬虫] 已强制将 plantKusa 的草种参数改写为「{kusa_type}」")
        await asyncio.sleep(2)
    except Exception as e:
        print(f"[爬虫] 强制改写 plantKusa 草种参数时出错: {e}")

    triggered = False
    for sel in trigger_selectors:
        try:
            if await page.locator(sel).first.is_visible():
                print(f"[爬虫] 点击 {trigger_mode} 按钮:", sel)
                await page.click(sel)
                triggered = True
                break
        except Exception:
            continue

    if not triggered:
        print(f"未找到「{trigger_mode}」按钮")
        if not reuse:
            await browser.close()
        return False

    # 轮询直到出现「正在生长中」等，即进入「等 wait-min → 按 poll-min 轮询按钮」阶段
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
        elapsed = time.monotonic() - t0
        interval = FAST_POLL if elapsed < SLOW_AFTER else SLOW_POLL
        await asyncio.sleep(interval)

    if done_selector:
        print("检测到完成:", done_selector)
        # 检查是否存在「灵性」标签，用于不灵草/灵灵草轮换策略
        if state is not None:
            has_lingxing = False
            try:
                # 更精确地匹配「灵性」标签：<span class="el-tag__content">灵性</span>
                ling = page.locator('.el-tag__content:has-text("灵性")').first
                if await ling.is_visible():
                    has_lingxing = True
            except Exception:
                pass
            state["has_lingxing"] = has_lingxing

        write_done_signal(done_selector, trigger_mode)
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
            print("未找到「恢复承载力」按钮")
    await browser.close()
    return bool(done_selector)


async def is_trigger_available(
    p, headless: bool, game_url: str, trigger_mode: str = "过载生草", login_qq: str = ""
) -> bool:
    qq = login_qq or LOGIN_QQ
    trigger_selectors = get_trigger_selectors(trigger_mode)
    browser = await p.chromium.launch(headless=headless)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    )
    page = await context.new_page()
    try:
        await page.goto(game_url, wait_until="networkidle", timeout=15000)
    except Exception as e:
        print("检查按钮可用性时打开页面失败:", e)
        await browser.close()
        return False

    await asyncio.sleep(1)

    try:
        qq_input = page.locator('input[placeholder="请输入QQ号"]').first
        login_button = page.locator('button:has-text("登录")').first
        if await qq_input.is_visible() and await login_button.is_visible():
            await qq_input.fill(qq)
            await login_button.click()
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

    for sel in trigger_selectors:
        try:
            if await page.locator(sel).first.is_visible():
                print(f"[轮询] 检测到「{trigger_mode}」按钮已重新出现:", sel)
                await browser.close()
                return True
        except Exception:
            continue

    await browser.close()
    return False


async def detect_lingxing(
    p, headless: bool, game_url: str, login_qq: str = ""
) -> bool:
    """单次检测当前页面是否存在「灵性」标签。"""
    qq = login_qq or LOGIN_QQ
    browser = await p.chromium.launch(headless=headless)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    )
    page = await context.new_page()
    try:
        try:
            await page.goto(game_url, wait_until="networkidle", timeout=15000)
        except Exception as e:
            print("灵性检测时打开页面失败:", e)
            await browser.close()
            return False

        await asyncio.sleep(1)

        try:
            qq_input = page.locator('input[placeholder="请输入QQ号"]').first
            login_button = page.locator('button:has-text("登录")').first
            if await qq_input.is_visible() and await login_button.is_visible():
                await qq_input.fill(qq)
                await login_button.click()
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(1)
        except Exception:
            pass

        try:
            tab_clicked = False
            for tab_sel in TAB_SELECTORS:
                try:
                    if await page.locator(tab_sel).first.is_visible():
                        await page.click(tab_sel)
                        tab_clicked = True
                        break
                except Exception:
                    continue
            if tab_clicked:
                await asyncio.sleep(1)
        except Exception:
            pass

        try:
            ling = page.locator('.el-tag__content:has-text("灵性")').first
            if await ling.is_visible():
                print("[灵性检测] 检测到灵性标签")
                await browser.close()
                return True
        except Exception:
            pass

        print("[灵性检测] 未检测到灵性标签")
        await browser.close()
        return False
    except Exception as e:
        print("灵性检测时发生异常:", e)
        await browser.close()
        return False


async def main_loop(
    headless: bool,
    game_url: str,
    kusa_type: str = "巨草",
    trigger_mode: str = "过载生草",
    wait_sec: int = 300,
    poll_interval_sec: int = 60,
    retry_sec: float = 60,
    max_poll_count: int = 10,
    cycle_types: Optional[List[str]] = None,
    login_qq: str = "",
):
    special_cycle = bool(cycle_types and cycle_types == ["不灵草", "灵灵草"])
    if cycle_types:
        if special_cycle:
            print(
                f"爬虫循环模式：草种轮换={cycle_types}（特殊模式：不灵草/灵灵草，按每轮灵性标签决定），触发={trigger_mode}，"
                f"先等待 {wait_sec} 秒再每 {poll_interval_sec} 秒轮询（最多 {max_poll_count} 次），未完成时 {retry_sec:.0f} 秒后重试，Ctrl+C 退出"
            )
        else:
            print(
                f"爬虫循环模式：草种轮换={cycle_types}，触发={trigger_mode}，"
                f"先等待 {wait_sec} 秒再每 {poll_interval_sec} 秒轮询（最多 {max_poll_count} 次），未完成时 {retry_sec:.0f} 秒后重试，Ctrl+C 退出"
            )
    else:
        print(
            f"爬虫循环模式：草种={kusa_type}，触发={trigger_mode}，"
            f"先等待 {wait_sec} 秒再每 {poll_interval_sec} 秒轮询（最多 {max_poll_count} 次），未完成时 {retry_sec:.0f} 秒后重试，Ctrl+C 退出"
        )
    async with async_playwright() as p:
        browser, context, page = None, None, None
        while True:
            if cycle_types:
                if special_cycle:
                    # 特殊模式：在每一轮开始前先检测灵性标签，
                    # 若有灵性标签则本轮生灵灵草，否则生不灵草
                    has_lingxing = await detect_lingxing(p, headless, game_url, login_qq)
                    print(f"[special cycle] 本轮灵性标签检测结果: {'有灵性' if has_lingxing else '无灵性'}")
                    current_type = "灵灵草" if has_lingxing else "不灵草"
                    print(f"[本轮草种] {current_type}（基于当前灵性标签）")
                else:
                    current_type = cycle_types[0] if len(cycle_types) == 1 else cycle_types[0]
                    print(f"[本轮草种] {current_type}")
            else:
                current_type = kusa_type
                print(f"[本轮草种] {current_type}")

            clear_signal()
            if browser is None or not browser.is_connected():
                browser = await p.chromium.launch(headless=headless)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                )
                page = await context.new_page()
            state: dict = {}
            ok = await run_once(
                p, headless, game_url, current_type, trigger_mode, login_qq,
                browser=browser, page=page, state=state,
            )
            if not ok:
                print(f"本轮未检测到完成，{retry_sec:.0f} 秒后同一浏览器内重试...")
                await asyncio.sleep(retry_sec)
                continue

            browser = None
            print(f"本轮完成，先等待 {wait_sec} 秒再开始轮询按钮刷新...")
            await asyncio.sleep(wait_sec)

            poll_count = 0
            while poll_count < max_poll_count:
                poll_count += 1
                print(f"轮询检查「{trigger_mode}」按钮是否已刷新可用...（第 {poll_count}/{max_poll_count} 次）")
                available = await is_trigger_available(p, headless, game_url, trigger_mode, login_qq)
                if available:
                    print("检测到按钮已刷新，开始下一轮。")
                    break
                print(f"按钮尚未刷新，{poll_interval_sec} 秒后再次检查...")
                await asyncio.sleep(poll_interval_sec)
            else:
                print(f"超过 {max_poll_count} 次轮询仍未发现按钮，重试整轮流程")
                continue


async def main_once(
    headless: bool,
    game_url: str,
    kusa_type: str = "巨草",
    trigger_mode: str = "过载生草",
    login_qq: str = "",
):
    async with async_playwright() as p:
        clear_signal()
        ok = await run_once(p, headless, game_url, kusa_type, trigger_mode, login_qq)
        if not ok:
            print("本轮未在限定时间内检测到完成")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="生草系统爬虫（可配置）：草种、等待时间、轮询间隔、过载/开始生草"
    )
    parser.add_argument(
        "--kusa-type",
        default="巨草",
        metavar="TYPE",
        help="草种，如 巨草、草 等（默认: 巨草）",
    )
    parser.add_argument(
        "--wait-min",
        type=float,
        default=5,
        metavar="MIN",
        help="loop 时每轮完成后先等待的分钟数（支持小数如 0.5，默认: 5）",
    )
    parser.add_argument(
        "--poll-min",
        type=float,
        default=1,
        metavar="MIN",
        help="等待后每隔多少分钟检查一次按钮是否出现（支持小数如 0.5，默认: 1）",
    )
    parser.add_argument(
        "--retry-min",
        type=float,
        default=1,
        metavar="MIN",
        help="loop 时未检测到完成时，多少分钟后重试（支持小数，如 0.1、0.5，默认 1）",
    )
    parser.add_argument(
        "--max-poll",
        type=int,
        default=10,
        metavar="N",
        help="loop 时轮询按钮最多次数，超过仍未发现则重试整轮（默认 10）",
    )
    parser.add_argument(
        "--trigger",
        choices=["过载生草", "开始生草"],
        default="过载生草",
        help="点击的按钮：过载生草 或 开始生草（默认: 过载生草）",
    )
    parser.add_argument("--loop", action="store_true", help="循环模式")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口")
    parser.add_argument(
        "--url",
        default=None,
        metavar="URL",
        help="游戏页面 URL（覆盖环境变量 KUSA_GAME_URL）",
    )
    parser.add_argument( 
        "--qq",
        default=None,
        metavar="QQ",
        help="登录用 QQ 号（覆盖环境变量 KUSA_QQ 和代码内默认值）",
    )
    parser.add_argument(
        "--cycle-kusa",
        default=None,
        metavar="TYPES",
        help="loop 时轮流使用的草种，逗号分隔，如 不灵草,灵灵草；指定后忽略 --kusa-type",
    )
    args = parser.parse_args()
    headless = not args.no_headless
    game_url = (args.url or "").strip() or os.environ.get("KUSA_GAME_URL") or _DEFAULT_GAME_URL
    login_qq = (args.qq or "").strip() or os.environ.get("KUSA_QQ") or ""
    wait_sec = args.wait_min * 60
    poll_interval_sec = args.poll_min * 60
    retry_sec = max(1, int(round(args.retry_min * 60)))
    max_poll_count = max(1, args.max_poll)
    cycle_types = None
    if args.cycle_kusa and args.cycle_kusa.strip():
        cycle_types = [x.strip() for x in args.cycle_kusa.split(",") if x.strip()]
    if cycle_types is not None and len(cycle_types) == 0:
        cycle_types = None

    if args.loop:
        asyncio.run(
            main_loop(
                headless,
                game_url,
                kusa_type=args.kusa_type,
                trigger_mode=args.trigger,
                wait_sec=wait_sec,
                poll_interval_sec=poll_interval_sec,
                retry_sec=retry_sec,
                max_poll_count=max_poll_count,
                cycle_types=cycle_types,
                login_qq=login_qq,
            )
        )
    else:
        asyncio.run(
            main_once(
                headless,
                game_url,
                kusa_type=args.kusa_type,
                trigger_mode=args.trigger,
                login_qq=login_qq,
            )
        )
