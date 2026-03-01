# chuchu_hack meow

生草系统 **爬虫式** 自动化：自动登录 → 进入生草页 → 恢复承载力 → 选择草种（默认半灵草/巨草）→ 过载生草或开始生草，生完后写入**信号文件**，不依赖 QQ。

## 环境

- Python 3.8+
- 安装依赖并安装浏览器：

```bash
pip install -r requirements.txt
playwright install chromium
```

## 运行方式

### 单次爬取（默认）

```bash
python kusa_auto.py
```

流程：打开页面 → 自动输入 QQ 登录 → 进入生草 → 恢复承载力 → 强制草种为半灵草 → 点击「过载生草」→ 轮询直到检测到完成 → 写入 `kusa_done.json` 并退出。

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

### 循环爬取（自动多轮）

```bash
python kusa_auto.py --loop
```

- 每轮：登录 → 生草入口 → 恢复承载力 → 半灵草 → 点击「过载生草」→ 等完成 → 写信号。
- **未检测到完成**（如未找到按钮）：不关浏览器，等 `--retry-min` 分钟后在同一浏览器内重新打开页面再试。
- **检测到完成**：先固定等 **3 分钟**，再轮询「过载生草」按钮是否出现（同一浏览器内刷新检查，间隔 `--poll-min`，默认 0.5 分钟即 30 秒）；**最多轮询 `--max-poll` 次**（默认 10），超过仍未发现则重试整轮。

常用参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--retry-min` | 1 | 未检测到完成时，多少分钟后在同一浏览器内重试（支持小数如 0.1、0.5） |
| `--poll-min` | 0.5 | 完成后等待 3 分钟，每隔多少分钟检查一次按钮（默认 30 秒） |
| `--max-poll` | 10 | 轮询按钮最多次数，超过则重试整轮 |
| `--qq` | 见上 | 登录 QQ 号 |
| `--url` | 见上 | 游戏 URL |

### 可配置版（草种 / 等待 / 轮询 / 过载或开始生草）

`kusa_auto_config.py` 在原有流程基础上支持通过命令行自定义草种、等待时间、轮询间隔、触发按钮等；**未检测到完成时同样在同一浏览器内重试**，不重启浏览器。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--kusa-type` | 巨草 | 草种（如 巨草、草、半灵草 等） |
| `--wait-min` | 5 | 循环时每轮完成后先等待的**分钟**数 |
| `--poll-min` | 1 | 等待结束后，每隔多少**分钟**检查一次按钮（支持小数如 0.5） |
| `--retry-min` | 1 | 未检测到完成时，多少分钟后在同一浏览器内重试 |
| `--max-poll` | 10 | 轮询按钮最多次数，超过则重试整轮 |
| `--trigger` | 过载生草 | 点击的按钮：`过载生草` 或 `开始生草` |
| `--qq` | 同 `KUSA_QQ` / 代码默认 | 登录用 QQ 号 |

同时支持 `--url`、`--loop`、`--no-headless`。

示例：

```bash
# 默认：巨草 + 过载生草，单次
python kusa_auto_config.py

# 草种「草」，点击「开始生草」
python kusa_auto_config.py --kusa-type 草 --trigger 开始生草

# 循环：先等 5 分钟，再每 0.5 分钟检查，最多 10 次
python kusa_auto_config.py --loop

# 循环：先等 3 分钟，再每 0.5 分钟检查，使用「开始生草」
python kusa_auto_config.py --loop --wait-min 3 --poll-min 0.5 --trigger 开始生草
```

## 接收「生完草」信号（不用 QQ）

生完后脚本会：

1. **在控制台** 打印完成提示；
2. **写入信号文件** `kusa_done.json`，例如：

```json
{
  "done": true,
  "at": "2025-02-27T12:00:00",
  "message": "过载生半灵草已完成",
  "detected_by": "text=完成"
}
```

（`kusa_auto.py` 固定半灵草时 message 为「过载生半灵草已完成」；`kusa_auto_config.py` 按所选草种/触发方式可能不同。）

你可以：

- **直接看文件**：生完就会多出/更新 `kusa_done.json`；
- **用监听脚本**：另开一个终端运行 `python watch_signal.py`，会一直等这个文件出现，出现就打印并退出；
- **自己写脚本**：定时读 `kusa_done.json` 或监控文件变化，做后续提醒（如弹窗、声音等）。

## 页面状态说明

- **无「生完」信号时**：以「**正在生长中**」出现为本轮回合结束。点击「过载生草」或「开始生草」后，轮询直到页面上出现「正在生长中」（或「生完」「完成」等），即写信号、点「恢复承载力」，然后进入「等 3 分钟（或 wait-min）→ 按 poll-min 轮询「过载生草」按钮」阶段。
- **完成标志**（任一项即可，见 `DONE_INDICATORS`）：文字「正在生长中」「生完」「完成」「半灵草」「巨草」，或 class 含 `done`/`complete` 的元素。

## 若页面结构不同

若游戏改版或选择器对不上（或按钮文字变化）：

1. 若未找到按钮，请根据当前页面结构检查并修改 `kusa_auto.py` 里的：
   - `TAB_SELECTORS`：进入生草页面的入口（侧边栏「生草」等）；
   - `TRIGGER_SELECTORS`：触发过载生草的按钮（目前只点「过载生草」）；
   - `DONE_INDICATORS`：认为「生完草」的页面元素或文字（完成时出现的文案，不是「正在生长中」）。
2. 选择器语法见 [Playwright 文档](https://playwright.dev/python/docs/selectors)。

## 游戏链接

http://110.41.149.62/kusa
