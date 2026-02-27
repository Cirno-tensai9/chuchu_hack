# chuchu_hack meow

生草系统 **爬虫式** 自动化：自动过载生巨草，生完后写入**信号文件**，不依赖 QQ。

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

打开页面 → 点「过载/生巨草」→ 轮询直到检测到完成 → 写入 `kusa_done.json` 并退出。

显示浏览器窗口（方便调试）：

```bash
python kusa_auto.py --no-headless
```

无头模式（不弹窗）：

```bash
set HEADLESS=true
python kusa_auto.py
```

### 循环爬取（类似定时爬虫）

每隔一段时间自动执行一轮，生完就写一次信号文件，然后继续下一轮：

```bash
python kusa_auto.py --loop
```

默认间隔 300 秒，可通过环境变量改：

```bash
set KUSA_LOOP_SEC=180
python kusa_auto.py --loop
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

若游戏改版或选择器对不上：

1. 运行一次后若未找到按钮，会生成 `kusa_debug.html`，根据其内容改 `kusa_auto.py` 里的：
   - `TRIGGER_SELECTORS`：触发过载/生巨草的按钮；
   - `DONE_INDICATORS`：认为「生完草」的页面元素或文字。
2. 选择器语法见 [Playwright 文档](https://playwright.dev/python/docs/selectors)。

## 游戏链接

http://110.41.149.62/kusa
