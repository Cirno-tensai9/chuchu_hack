## 生草系统爬虫（可配置版 `kusa_auto_config.py`）

`kusa_auto_config.py` 是一个使用 Playwright 的自动化脚本，用于在浏览器中自动完成「生草」流程：登录 → 进入生草页 → 恢复承载力 → 选择草种 → 点击「过载生草」或「开始生草」→ 监控完成 → 写入信号文件 `kusa_done.json`。

本 README 只介绍 **`kusa_auto_config.py` 的用法**。

### 环境准备

- **Python**: 3.8+
- **依赖安装**：

```bash
pip install -r requirements.txt
playwright install chromium
```

### 基本用法

- **单次执行（默认模式）**：

```bash
python kusa_auto_config.py
```

含义：使用默认草种「巨草」，点击「过载生草」，等待直到检测到页面进入「正在生长中 / 生完 / 完成 / 巨草 / 半灵草」等状态后，写入 `kusa_done.json` 并退出。

### 常用参数（全部针对 `kusa_auto_config.py`）

- **草种 / 触发方式**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--kusa-type` | 巨草 | 草种，例如 `巨草`、`草`、`半灵草` 等；会强制改写 `plantKusa` 的草种实参 |
| `--trigger` | 过载生草 | 点击的按钮：`过载生草` 或 `开始生草` |

- **循环相关（`--loop` 模式）**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--wait-min` | 5 | 每轮完成后，先等待的分钟数（例如 5 分钟后再开始轮询按钮是否刷新） |
| `--poll-min` | 1 | 在等待结束后，每隔多少分钟检查一次按钮是否重新出现（支持小数，如 `0.5` 表示 30 秒） |
| `--retry-min` | 1 | 本轮未在限定时间内检测到完成时，多少分钟后在同一浏览器中重试整轮流程（支持小数） |
| `--max-poll` | 10 | 轮询按钮的最多次数，超过仍未出现则认为本轮失败，重新跑一轮 |

- **登录与页面**：

| 参数 / 环境变量 | 默认值 / 说明 |
|----------------|---------------|
| `--qq` / `KUSA_QQ` | 登录用 QQ 号；优先级：命令行 `--qq` > 环境变量 `KUSA_QQ` > 代码中的 `LOGIN_QQ`（默认为 `530958461`） |
| `--url` / `KUSA_GAME_URL` | 游戏页面地址；优先级：命令行 `--url` > 环境变量 `KUSA_GAME_URL` > 内置默认 `http://110.41.149.62/kusa` |

- **浏览器显示**：

| 方式 | 说明 |
|------|------|
| 默认 | 无头模式（不弹出浏览器窗口） |
| `--no-headless` | 显示浏览器窗口，方便调试 |
| 环境变量 `HEADLESS=true` | 也可以通过设置该变量强制开启无头模式 |

### 运行示例

- **默认：巨草 + 过载生草，单次执行**：

```bash
python kusa_auto_config.py
```

- **指定草种为「草」，点击「开始生草」**：

```bash
python kusa_auto_config.py --kusa-type 草 --trigger 开始生草
```

- **循环模式：先等 5 分钟，再每 1 分钟检查按钮（最多 10 次）**：

```bash
python kusa_auto_config.py --loop
```

- **循环模式：先等 3 分钟，再每 0.5 分钟检查按钮，且使用「开始生草」**：

```bash
python kusa_auto_config.py --loop --wait-min 3 --poll-min 0.5 --trigger 开始生草
```

- **指定 QQ 与 URL（命令行方式）**：

```bash
python kusa_auto_config.py --qq 123456789 --url http://example.com/kusa
```

- **通过环境变量指定 QQ 与 URL**：

```bash
export KUSA_QQ=123456789
export KUSA_GAME_URL=http://example.com/kusa
python kusa_auto_config.py
```

### 信号文件 `kusa_done.json`

每当脚本认为本轮「生草」完成时，会：

1. 在控制台打印完成日志；
2. 在脚本所在目录写入/更新 `kusa_done.json`，格式类似：

```json
{
  "done": true,
  "at": "2025-02-27T12:00:00",
  "message": "过载生草已完成",
  "detected_by": "text=完成"
}
```

字段含义：

- **`done`**: 是否完成（恒为 `true`）；
- **`at`**: 服务器当前时间的 ISO 字符串；
- **`message`**: 本轮的提示信息（会根据触发方式有所不同）；
- **`detected_by`**: 是通过哪个页面元素/文本判断为完成的。

你可以：

- **直接查看文件**：生完草时 `kusa_done.json` 会被创建或覆盖；
- **编写监听脚本**：定时读取 `kusa_done.json` 或监控其变化，用于弹窗提醒、系统通知等。

### 游戏地址（默认）

脚本的默认游戏地址为：

`http://110.41.149.62/kusa`

可通过前文的 `--url` 或环境变量 `KUSA_GAME_URL` 覆盖。
