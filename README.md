# Options Anomaly Dashboard

这是一个基于 Futu OpenAPI 的期权异动看板。它每天沉淀本地期权筛选器快照，并生成 `reports/options_anomaly_report.html`，用于观察标的在最近交易日里的 Call/Put 方向、成交量放大、P/C 变化、混合 Top10 合约和匹配到的期权异动记录。

## 日常入口

先打开并登录 Futu OpenD，然后使用：

```cmd
START_HERE_期权监控.cmd
```

浏览器页面：

```text
http://127.0.0.1:8765/
```

日常刷新必须走 Git 事务包装器：

```cmd
run_daily_report.cmd
run_intraday_report.cmd
```

包装器会自动执行：检查 Git 状态、拉取远端、检查 OpenD、创建本地备份、刷新数据、校验输出、提交并推送到 Git。

## 当前核心口径

- `total_volume` 和 P/C 来自期权筛选器结果，不代表全市场全部期权成交。
- P/C 保持成交量口径。
- Top10 合约使用混合逻辑：成交额 Top5 + 成交量 Top10 去重后补足到 10 条。
- 异动匹配只展示能与混合 Top10 按合约代码或到期/类型/行权价匹配的主动买入/主动卖出记录。
- `option_screen_snapshot_status.json` 记录本次快照的采集口径 metadata。

## 重要文件

- `daily_option_report.py`: 主采集与报告生成入口。
- `dashboard_renderer.py`: HTML 看板渲染。
- `option_screen_monitor.py`: Futu 期权筛选器采集与聚合。
- `option_unusual_monitor.py`: 衍生品异动文本解析。
- `dashboard_analysis.py`: 只读本地 CSV 的横向分析工具，不调用 Futu。
- `git_sync_update.py`: Git 同步刷新事务。
- `data/`: 最新核心数据快照。
- `reports/options_anomaly_report.html`: 最新看板 HTML。

## 本地分析

快速看当前数据里的方向占比增强、成交量放大、P/C 跳变和集中大单：

```cmd
.venv-futu\Scripts\python.exe dashboard_analysis.py --top 20
```

该工具只读本地文件，不消耗 Futu 行情额度。

## 测试

```cmd
.venv-futu\Scripts\python.exe -m unittest discover -s tests
```

测试只使用本地 fixture 和已有 `data/` 文件，不调用 Futu。

## Legacy

历史回测、Google Drive 同步和早期验证脚本已归档到 `legacy/`。它们保留用于追溯，不作为日常入口。常规跨设备同步只使用 Git。
