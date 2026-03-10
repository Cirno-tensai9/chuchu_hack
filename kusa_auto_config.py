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
import re
import sys
import time
from datetime import datetime
from typing import List, Optional, Tuple, Set

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
FAST_POLL = 0.35
SLOW_POLL = 60.0
SLOW_AFTER = 300
MAX_WAIT = 600
# 页面加载用 load 比 networkidle 快不少，再用短 sleep 等 SPA 稳定
GOTO_WAIT = "load"
GOTO_STABLE_SLEEP = 0.8


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
    yield_threshold: Optional[float] = None,
    yield_max_retry: int = 3,
    yield_protect_kusa: Optional[Set[str]] = None,
    yield_lianhao_min: int = 3,
    yield_grass_min: Optional[float] = None,
    yield_biogas_min: Optional[float] = None,
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
        await page.goto(game_url, wait_until=GOTO_WAIT, timeout=50000)
        await asyncio.sleep(GOTO_STABLE_SLEEP)
    except Exception as e:
        print("打开页面失败:", e)
        if not reuse:
            await browser.close()
        return False

    try:
        qq_input = page.locator('input[placeholder="请输入QQ号"]').first
        login_button = page.locator('button:has-text("登录")').first
        if await qq_input.is_visible() and await login_button.is_visible():
            print("[爬虫] 检测到登录页，自动输入 QQ 并登录:", qq)
            await qq_input.fill(qq)
            await login_button.click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(0.5)
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
        await asyncio.sleep(0.5)
    else:
        print("未在导航中找到「生草」入口，将直接在当前页面查找按钮")

    # 若处于特殊循环模式（不灵草/灵灵草），在真正选择草种前，先在生草页检测一次「灵性」标签
    effective_kusa_type = kusa_type
    has_lingxing_flag = None
    if state is not None and state.get("special_cycle"):
        try:
            ling = page.locator('.el-tag__content:has-text("灵性")').first
            if await ling.is_visible():
                has_lingxing_flag = True
                print("[special cycle] 本轮灵性标签检测结果: 有灵性")
            else:
                has_lingxing_flag = False
                print("[special cycle] 本轮灵性标签检测结果: 无灵性")
        except Exception:
            # 若检测异常，则按“无灵性”处理，避免整轮报错中断
            has_lingxing_flag = False
            print("[special cycle] 灵性标签检测异常，按无灵性处理")

        effective_kusa_type = "灵灵草" if has_lingxing_flag else "不灵草"
        print(f"[本轮草种] {effective_kusa_type}（基于当前灵性标签）")

        state["has_lingxing"] = bool(has_lingxing_flag)
        state["kusa_type_used"] = effective_kusa_type
    else:
        if state is not None:
            state["kusa_type_used"] = kusa_type

    try:
        for sel in RESTORE_SELECTORS:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible():
                    print("[爬虫] 预先点击恢复承载力按钮:", sel)
                    await btn.click()
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                continue
    except Exception:
        pass

    async def _restore_capacity_silent():
        """在预知草精多次尝试时，默默点击一次『恢复承载力』（若按钮可见）。"""
        try:
            for sel in RESTORE_SELECTORS:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(0.5)
                        break
                except Exception:
                    continue
        except Exception:
            pass

    # 强制将 plantKusa 的草种参数改为本轮实际要使用的 effective_kusa_type
    kusa_escaped = effective_kusa_type.replace("\\", "\\\\").replace("'", "\\'")
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
        print(f"[爬虫] 已强制将 plantKusa 的草种参数改写为「{effective_kusa_type}」")
        await asyncio.sleep(2)
    except Exception as e:
        print(f"[爬虫] 强制改写 plantKusa 草种参数时出错: {e}")

    # 若配置了预知草精/生草数阈值，则在真正等待长成前，尝试基于「预知产量」进行多次除草重生
    async def _read_predicted_jing() -> Tuple[Optional[float], Optional[float], bool]:
        """从当前生草弹窗中读取『草之精华』与『草』的预知数值，并检测草数字是否有「连号」。

        返回 (jing_value, grass_value, has_lianhao)：
        - jing_value: 草之精华的预知数值，失败则为 None
        - grass_value: 预知「草」的数量（如 296825），失败则为 None
        - has_lianhao: 草 数字部分是否存在 >= yield_lianhao_min 位相同数字连续（视为连号）

        DOM 结构类似：<span>草 296,825, 草之精华 2</span>
        """
        try:
            label = page.locator('span:has-text("预知产量")').first
            if not await label.is_visible():
                return None, None, False
            parent = label.locator("xpath=..")
            info_spans = parent.locator("span")
            count = await info_spans.count()
            if count < 2:
                return None, None, False
            info_text = await info_spans.nth(1).inner_text()
            import re

            grass_match = re.search(r"草\s*([0-9,]+)", info_text)
            digits = grass_match.group(1).replace(",", "") if grass_match else ""
            grass_value = None
            if digits:
                try:
                    grass_value = float(digits)
                except ValueError:
                    pass
            has_lianhao = False
            if yield_lianhao_min > 0 and digits:
                run_char = None
                run_len = 0
                for ch in digits:
                    if ch == run_char:
                        run_len += 1
                    else:
                        run_char = ch
                        run_len = 1
                    if run_len >= yield_lianhao_min:
                        has_lianhao = True
                        break

            m = re.search(r"草之精华\s*([0-9]+(?:\.[0-9]+)?)", info_text)
            if not m:
                m = re.search(r"([0-9]+(?:\.[0-9]+)?)", info_text)
            if not m:
                return None, grass_value, has_lianhao
            try:
                return float(m.group(1)), grass_value, has_lianhao
            except ValueError:
                return None, grass_value, has_lianhao
        except Exception:
            return None, None, False

    async def _get_current_kusa_name() -> Optional[str]:
        """从『正在生长中』标题前提取实际生长的草种名称，例如『灵草正在生长中』。"""
        try:
            title = page.locator('text=正在生长中').first
            if not await title.is_visible():
                return None
            full = await title.inner_text()
            if not full:
                return None
            idx = full.find("正在生长中")
            if idx <= 0:
                return None
            name = full[:idx].strip()
            return name or None
        except Exception:
            return None

    async def _read_biogas_multiplier() -> Optional[float]:
        """从页面中读取沼气倍率，格式为「沼气×1.n」或「沼气×2」等，通常在灵性附近。"""
        try:
            # 先找包含「沼气」的节点（灵性附近）
            node = page.locator("text=沼气").first
            if not await node.is_visible():
                return None
            text = await node.inner_text()
            if not text:
                return None
            m = re.search(r"沼气\s*[×xX]\s*([\d.]+)", text)
            if not m:
                return None
            return float(m.group(1))
        except Exception:
            return None

    triggered = False
    attempt = 0
    read_attempts_for_click = 0
    protect_attempt = 0
    while True:
        if not triggered:
            # 在每次尝试生草前，优先默默补充一次承载力（若按钮可见）
            await _restore_capacity_silent()
            for sel in trigger_selectors:
                try:
                    if await page.locator(sel).first.is_visible():
                        print(f"[爬虫] 点击 {trigger_mode} 按钮:", sel)
                        await page.click(sel)
                        triggered = True
                        read_attempts_for_click = 0
                        break
                except Exception:
                    continue

        if not triggered:
            break

        # 若配置了保护草种列表：先检查当前草种是否在列表中，命中则直接保留；否则在「仅保护草种」模式下循环除草重试
        if yield_protect_kusa:
            await asyncio.sleep(0.6)
            current_kusa = await _get_current_kusa_name()
            if current_kusa and current_kusa in yield_protect_kusa:
                print(f"[草种筛选] 当前实际生长草种为「{current_kusa}」，命中保护草种列表，直接保留。")
                break
            # 当前草种不在保护列表中；仅当未配置草精/生草数阈值时，才做「仅保护草种」循环除草重试
            if yield_threshold is None and yield_grass_min is None and yield_biogas_min is None:
                # 仅保护草种模式：循环除草并重新生草，直到刷到保护草种或达到最大次数
                protect_attempt += 1
                max_protect = yield_max_retry if yield_max_retry > 0 else 10
                if protect_attempt > max_protect:
                    print(f"[草种筛选] 已尝试 {max_protect} 次仍未刷到保护草种，保留当前草继续生长。")
                    break
                print("[草种筛选] 当前草种不在保护列表中，点击除草并重新生草。")
                removed = False
                for remove_retry in range(3):
                    if remove_retry > 0:
                        print(f"[草种筛选] 未找到『除草』按钮（第 {remove_retry + 1} 次重试寻找），稍候再试。")
                        await asyncio.sleep(1)
                    for sel in ['button:has-text("除草")', 'a:has-text("除草")', '[class*="remove"]:has-text("除草")']:
                        try:
                            btn = page.locator(sel).first
                            if await btn.is_visible():
                                await btn.click()
                                removed = True
                                break
                        except Exception:
                            continue
                    if removed:
                        break
                if not removed:
                    print("[草种筛选] 连续 3 次未找到『除草』按钮，保留当前草继续生长。")
                    break
                await asyncio.sleep(1)
                triggered = False
                continue

        # 若未配置任何预知阈值（草精/生草数/沼气倍率），或达到最大尝试次数，则不再进行「预知产量」循环
        if (yield_threshold is None and yield_grass_min is None and yield_biogas_min is None) or yield_max_retry <= 0:
            break

        attempt += 1
        if attempt > yield_max_retry:
            print(f"[预知草精] 已达到最大尝试次数 {yield_max_retry}，不再继续除草重生。")
            break

        # 给页面一点时间刷新预知信息
        await asyncio.sleep(0.6)
        predicted, grass_value, has_lianhao = await _read_predicted_jing()
        print(
            f"[预知调试] 解析结果：草精={predicted}，生草数={grass_value}，连号={has_lianhao}；"
            f"阈值：草精={yield_threshold}，生草数={yield_grass_min}，沼气倍率={yield_biogas_min}"
        )
        # 【最高优先级】草种筛选：若配置了保护草种列表，先根据「正在生长中」标题检查当前实际草种，命中则直接保留，不再看连号/阈值
        if yield_protect_kusa:
            current_kusa = await _get_current_kusa_name()
            if current_kusa and current_kusa in yield_protect_kusa:
                print(f"[预知草精] 当前实际生长草种为「{current_kusa}」，命中保护草种列表，忽略草精阈值和除草逻辑，直接保留。")
                break

        if has_lianhao:
            print(f"[预知草精] 检测到草数字存在连号（>={yield_lianhao_min} 位相同数字连续），本轮视为特殊号，忽略草精阈值检测。")
            break

        if yield_threshold is not None and predicted is None:
            read_attempts_for_click += 1
            if read_attempts_for_click == 1:
                print("[预知] 第一次未能读取到预知产量（草之精华）信息，将稍候再次尝试读取。")
                continue
            print("[预知] 连续两次未能读取到预知产量信息，本轮视为未达标，将点击一次除草并重新尝试生草。")
            removed = False
            for sel in ['button:has-text("除草")', 'a:has-text("除草")', '[class*=\"remove\"]:has-text(\"除草\")']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible():
                        await btn.click()
                        removed = True
                        break
                except Exception:
                    continue
            if not removed:
                print("[预知] 未找到『除草』按钮（读取失败分支），直接重新尝试生草。")
            triggered = False
            continue

        # 筛选优先级：草种(已判) > 连号(已判) > 草精数 > 生草数 > 沼气倍率；任一不达标则除草重试
        # 生草数：配置了 --yield-grass-min 时，若成功读取且低于阈值，或读取失败(None)，均按“不达标”处理（强制除草重试）
        # 沼气：配置了 --yield-biogas-min 时，若成功读取且不大于阈值，或读取失败(None)，均按“不达标”处理（强制除草重试）
        need_remove = False
        reason = ""
        if yield_threshold is not None and (predicted is None or predicted < yield_threshold):
            need_remove = True
            reason = f"预知草精={predicted}，阈值={yield_threshold}"
        elif yield_grass_min is not None:
            if grass_value is None or grass_value < yield_grass_min:
                need_remove = True
                reason = f"预知生草数={grass_value}，阈值={yield_grass_min}（读不到或低于阈值均视为未达标）"
        elif yield_biogas_min is not None:
            biogas_value = await _read_biogas_multiplier()
            if biogas_value is None:
                need_remove = True
                reason = f"沼气倍率读取失败，阈值={yield_biogas_min}（按未达标处理）"
            elif biogas_value <= yield_biogas_min:
                need_remove = True
                reason = f"沼气倍率={biogas_value}，阈值={yield_biogas_min}（需大于阈值才保留）"
        if not need_remove:
            print("[预知] 草精、生草数与沼气倍率均达标，本轮保留当前草，进入正常生长等待。")
            break

        print(f"[预知] 未达标（{reason}），点击除草并重新生草。")
        removed = False
        for sel in ['button:has-text("除草")', 'a:has-text("除草")', '[class*=\"remove\"]:has-text(\"除草\")']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible():
                    await btn.click()
                    removed = True
                    break
            except Exception:
                continue
        if not removed:
            print("[预知] 未找到『除草』按钮，无法进行实验性多次重生，本轮将按当前结果继续生长。")
            break

        # 等待页面状态回到可再次触发的状态，然后在下一轮 while 中重新点击触发按钮
        await asyncio.sleep(0.5)
        triggered = False

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
        write_done_signal(done_selector, trigger_mode)
        for sel in RESTORE_SELECTORS:
            try:
                if await page.locator(sel).first.is_visible():
                    print("[爬虫] 点击:", sel)
                    await page.click(sel)
                    await asyncio.sleep(0.5)
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
        await page.goto(game_url, wait_until=GOTO_WAIT, timeout=50000)
        await asyncio.sleep(GOTO_STABLE_SLEEP)
    except Exception as e:
        print("检查按钮可用性时打开页面失败:", e)
        await browser.close()
        return False

    try:
        qq_input = page.locator('input[placeholder="请输入QQ号"]').first
        login_button = page.locator('button:has-text("登录")').first
        if await qq_input.is_visible() and await login_button.is_visible():
            await qq_input.fill(qq)
            await login_button.click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(0.5)
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


async def main_loop(
    headless: bool,
    game_url: str,
    kusa_type: str = "巨草",
    trigger_mode: str = "过载生草",
    wait_sec: int = 300,
    poll_interval_sec: int = 60,
    retry_sec: float = 60,
    max_poll_count: int = 10,
    buling_fast: bool = False,
    skip_lingxing_check: bool = False,
    yield_threshold: Optional[float] = None,
    yield_max_retry: int = 0,
    yield_protect_kusa: Optional[Set[str]] = None,
    yield_lianhao_min: int = 3,
    yield_grass_min: Optional[float] = None,
    yield_biogas_min: Optional[float] = None,
    cycle_types: Optional[List[str]] = None,
    login_qq: str = "",
):
    special_cycle = bool(cycle_types and cycle_types == ["不灵草", "灵灵草"])
    # effective_special_cycle 为「真正会做灵性检测」的 special cycle，允许通过参数关闭检测
    effective_special_cycle = bool(special_cycle and not skip_lingxing_check)
    if cycle_types:
        if special_cycle:
            if effective_special_cycle:
                print(
                    f"爬虫循环模式：草种轮换={cycle_types}（特殊模式：不灵草/灵灵草，按每轮灵性标签决定），触发={trigger_mode}，"
                    f"先等待 {wait_sec} 秒再每 {poll_interval_sec} 秒轮询（最多 {max_poll_count} 次），未完成时 {retry_sec:.0f} 秒后重试，Ctrl+C 退出"
                )
            else:
                print(
                    f"爬虫循环模式：草种轮换={cycle_types}（特殊模式：不灵草/灵灵草，已关闭灵性检测，按顺序轮换草种），触发={trigger_mode}，"
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
        cycle_idx = 0
        while True:
            if cycle_types:
                if special_cycle:
                    if effective_special_cycle:
                        # 特殊模式 + 开启灵性检测：每一轮都会在登录并进入生草页后，基于当前「灵性」标签自动决定本轮草种（不灵草 / 灵灵草）
                        current_type = kusa_type
                        print("[special cycle] 本轮将进入生草页后，根据灵性标签自动选择不灵草/灵灵草。")
                    else:
                        # 特殊模式但关闭灵性检测：退化为按给定列表顺序轮换不灵草/灵灵草
                        current_type = cycle_types[cycle_idx % len(cycle_types)]
                        cycle_idx += 1
                        print(f"[special cycle] 已关闭灵性检测，本轮草种按顺序轮换为 {current_type}")
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
            # effective_special_cycle 标记传入 run_once，便于其在同一页面流程中完成「登录 → 生草页 → 检测灵性 → 选草种 → 生草」
            state["special_cycle"] = effective_special_cycle
            ok = await run_once(
                p,
                headless,
                game_url,
                current_type,
                trigger_mode,
                login_qq,
                yield_threshold=yield_threshold,
                yield_max_retry=yield_max_retry,
                yield_protect_kusa=yield_protect_kusa,
                yield_lianhao_min=yield_lianhao_min,
                yield_grass_min=yield_grass_min,
                yield_biogas_min=yield_biogas_min,
                browser=browser, page=page, state=state,
            )
            if not ok:
                print(f"本轮未检测到完成，{retry_sec:.0f} 秒后同一浏览器内重试...")
                await asyncio.sleep(retry_sec)
                continue

            browser = None
            # special cycle 中的不灵草速生模块：若开启 buling_fast 且本轮是不灵草，则跳过 wait_sec，直接开始轮询
            used_type = state.get("kusa_type_used", current_type)
            if special_cycle and buling_fast and used_type == "不灵草":
                print(
                    "[special cycle] 实验性不灵草速生已启用：本轮为不灵草，跳过 "
                    f"{wait_sec} 秒等待，直接开始轮询按钮刷新（理论上有 50% 概率在一分钟内收获）。"
                )
            else:
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
    yield_threshold: Optional[float] = None,
    yield_max_retry: int = 3,
    yield_protect_kusa: Optional[Set[str]] = None,
    yield_lianhao_min: int = 3,
    yield_grass_min: Optional[float] = None,
    yield_biogas_min: Optional[float] = None,
):
    async with async_playwright() as p:
        clear_signal()
        ok = await run_once(
            p,
            headless,
            game_url,
            kusa_type,
            trigger_mode,
            login_qq,
            yield_threshold=yield_threshold,
            yield_max_retry=yield_max_retry,
            yield_protect_kusa=yield_protect_kusa,
            yield_lianhao_min=yield_lianhao_min,
            yield_grass_min=yield_grass_min,
            yield_biogas_min=yield_biogas_min,
        )
        if not ok:
            print("本轮未在限定时间内检测到完成")
        sys.exit(0 if ok else 1)


def _parse_yield_grass_min(s: str):
    """解析 --yield-grass-min：支持纯数字或带 k（千）、m（百万）后缀，如 100、100k、1.5m。"""
    if s is None or (isinstance(s, str) and not s.strip()):
        return None
    s = str(s).strip().lower()
    mult = 1
    if s.endswith("k"):
        mult = 1000
        s = s[:-1].strip()
    elif s.endswith("m"):
        mult = 1_000_000
        s = s[:-1].strip()
    try:
        return float(s) * mult
    except ValueError:
        raise argparse.ArgumentTypeError(
            "预知生草数应为数字或带 k/m 单位，如 100、100k、1.5m"
        )


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
    parser.add_argument(
        "--buling-fast",
        action="store_true",
        help="在 special cycle 不灵草,灵灵草 模式下，为不灵草启用实验性催生装置（生不灵草时跳过 wait-min，直接开始轮询检查）",
    )
    parser.add_argument(
        "--skip-lingxing-check",
        action="store_true",
        help="在 special cycle 不灵草,灵灵草 模式下，关闭灵性标签检测，退化为按 不灵草,灵灵草 顺序轮换草种",
    )
    parser.add_argument(
        "--yield-threshold",
        type=float,
        default=None,
        metavar="N",
        help="启用预知草精模式：在单次模式下，若检测到预知草精低于该阈值，则自动点击「除草」并重新生草，直到大于等于该值或达到最大尝试次数",
    )
    parser.add_argument(
        "--yield-max-retry",
        type=int,
        default=3,
        metavar="N",
        help="预知草精模式下，除草重生的最大尝试次数（默认 3）",
    )
    parser.add_argument(
        "--yield-protect-kusa",
        default=None,
        metavar="TYPES",
        help="预知草精模式下的保护草种列表，逗号分隔；若当前实际生长草种（出现在「正在生长中」前的名称）在该列表中，则忽略草精阈值与除草逻辑，直接保留",
    )
    parser.add_argument(
        "--yield-lianhao-min",
        type=int,
        default=3,
        metavar="N",
        help="预知草精模式下，草数字连续相同位数达到 N 时视为连号并忽略阈值；设为 0 则不考虑连号，始终按阈值筛选（默认 3）",
    )
    parser.add_argument(
        "--yield-grass-min",
        type=_parse_yield_grass_min,
        default=None,
        metavar="N",
        help="预知生草数下限：预知产量中「草 N」低于 N 时除草重试；支持 k（千）、m（百万），如 100k、1.5m；筛选优先级为 草种 > 草精数 > 生草数",
    )
    parser.add_argument(
        "--yield-biogas-min",
        type=float,
        default=None,
        metavar="N",
        help="沼气倍率下限：页面中「沼气×1.n」格式，仅当倍率大于该值且其他条件满足时才保留，否则除草重试；如 1.5 表示需大于 1.5",
    )
    args = parser.parse_args()
    headless = not args.no_headless
    game_url = (args.url or "").strip() or os.environ.get("KUSA_GAME_URL") or _DEFAULT_GAME_URL
    login_qq = (args.qq or "").strip() or os.environ.get("KUSA_QQ") or ""
    wait_sec = args.wait_min * 60
    poll_interval_sec = args.poll_min * 60
    retry_sec = max(1, int(round(args.retry_min * 60)))
    max_poll_count = max(1, args.max_poll)
    yield_protect_kusa: Optional[Set[str]] = None
    cycle_types = None
    if args.cycle_kusa and args.cycle_kusa.strip():
        cycle_types = [x.strip() for x in args.cycle_kusa.split(",") if x.strip()]
    if args.yield_protect_kusa and args.yield_protect_kusa.strip():
        yield_protect_kusa = {x.strip() for x in args.yield_protect_kusa.split(",") if x.strip()}
    yield_lianhao_min = max(0, args.yield_lianhao_min)
    yield_grass_min = args.yield_grass_min
    yield_biogas_min = args.yield_biogas_min
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
                buling_fast=args.buling_fast,
                skip_lingxing_check=args.skip_lingxing_check,
                yield_threshold=args.yield_threshold,
                yield_max_retry=max(0, args.yield_max_retry),
                yield_protect_kusa=yield_protect_kusa,
                yield_lianhao_min=yield_lianhao_min,
                yield_grass_min=yield_grass_min,
                yield_biogas_min=yield_biogas_min,
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
                yield_threshold=args.yield_threshold,
                yield_max_retry=max(0, args.yield_max_retry),
                yield_protect_kusa=yield_protect_kusa,
                yield_lianhao_min=yield_lianhao_min,
                yield_grass_min=yield_grass_min,
                yield_biogas_min=yield_biogas_min,
            )
        )
