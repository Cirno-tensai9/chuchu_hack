# chuchu_hack meow

生草系统 **爬虫式** 自动化：自动登录 → 进入生草页 → 选择巨草 → 过载生草，生完后写入**信号文件**，不依赖 QQ。

## 环境

- Python 3.8+
- 安装依赖并安装浏览器：

```bash
pip install -r requirements.txt
playwright install chromium
```

## 运行方式

### 单次爬取（默认，自动巨草）

```bash
python kusa_auto.py
```

流程：打开页面 → 自动输入 QQ 登录 → 进入生草 → 恢复承载力 → 选择巨草 → 点击「过载生草」→ 轮询直到检测到完成 → 写入 `kusa_done.json` 并退出。

登录 QQ 号可通过以下方式指定（优先级从高到低）：命令行 `--qq`、环境变量 `KUSA_QQ`、代码内常量 `LOGIN_QQ`（默认 `530958461`）。例如：

```bash
python kusa_auto.py --qq 123456789
# 或
set KUSA_QQ=123456789
python kusa_auto.py
```

默认游戏 URL 为 `http://110.41.149.62/kusa`，可通过：

- 环境变量：`KUSA_GAME_URL`
- 或命令行参数：`--url`

覆盖，例如：

```bash
set KUSA_GAME_URL=http://example.com/kusa
python kusa_auto.py
```

或：

```bash
python kusa_auto.py --url http://example.com/kusa
```

显示浏览器窗口（方便调试）：

```bash
python kusa_auto.py --no-headless
```

无头模式（不弹窗）：

```bash
set HEADLESS=true
python kusa_auto.py
```

### 循环爬取（自动多轮过载生巨草）

自动执行多轮过载生巨草：每轮按照「登录 → 巨草 → 过载生草」流程执行一次，生完写一次信号文件，然后等待下一轮可用时继续。

```bash
python kusa_auto.py --loop
```

当前逻辑（基于实际冷却时间约 5–7 分钟）：

1. 完成一轮过载生草后，先**固定等待 5 分钟**；
2. 之后每隔 **1 分钟** 重新打开页面检查一次「过载生草」按钮是否重新出现；
3. 一旦检测到按钮出现，就立即开始下一轮完整流程。

### 可配置版（草种 / 等待时间 / 轮询间隔 / 过载或开始生草）

`kusa_auto_config.py` 在原有流程基础上支持通过命令行自定义：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--kusa-type` | 巨草 | 草种（如 巨草、草 等） |
| `--wait-min` | 5 | 循环时每轮完成后先等待的**分钟**数 |
| `--poll-min` | 1 | 等待结束后，每隔多少**分钟**检查一次按钮是否出现 |
| `--trigger` | 过载生草 | 点击的按钮：`过载生草` 或 `开始生草` |
| `--qq` | 同 `KUSA_QQ` / 代码默认 | 登录用 QQ 号 |

同时支持 `--url`、`--loop`、`--no-headless`（与 `kusa_auto.py` 相同）。

示例：

```bash
# 默认：巨草 + 过载生草，单次
python kusa_auto_config.py

# 草种「草」，点击「开始生草」
python kusa_auto_config.py --kusa-type 草 --trigger 开始生草

# 循环：先等 5 分钟，再每 1 分钟检查（等同默认）
python kusa_auto_config.py --loop

# 循环：先等 3 分钟，再每 2 分钟检查，使用「开始生草」
python kusa_auto_config.py --loop --wait-min 3 --poll-min 2 --trigger 开始生草
```

## 接收「生完草」信号（不用 QQ）

生完后脚本会：

1. **在控制台** 打印完成提示；
2. **写入信号文件** `kusa_done.json`，例如：

```json
{
  "done": true,
  "at": "2025-02-27T12:00:00",
  "message": "过载生巨草已完成",
  "detected_by": "text=完成"
}
```

你可以：

- **直接看文件**：生完就会多出/更新 `kusa_done.json`；
- **用监听脚本**：另开一个终端运行 `python watch_signal.py`，会一直等这个文件出现，出现就打印并退出；
- **自己写脚本**：定时读 `kusa_done.json` 或监控文件变化，做后续提醒（如弹窗、声音等）。

## 若页面结构不同

若游戏改版或选择器对不上（或按钮文字变化）：

1. 运行一次后若未找到按钮，会生成 `kusa_debug.html`，根据其内容改 `kusa_auto.py` 里的：
   - `TAB_SELECTORS`：进入生草页面的入口（侧边栏「生草」等）；
   - `TRIGGER_SELECTORS`：触发过载生草的按钮（目前只点「过载生草」）；
   - `DONE_INDICATORS`：认为「生完草」的页面元素或文字。
2. 选择器语法见 [Playwright 文档](https://playwright.dev/python/docs/selectors)。

## 游戏链接

http://110.41.149.62/kusa
