# Option Flow Monitor

一个基于 Futu OpenAPI 的期权日成交量异常监控原型。默认股票池：

- `US.NOW`
- `US.FUTU`
- `US.DELL`
- `US.QCOM`

安全版本使用 `get_option_screen` 的当日期权成交量快照，聚合到正股维度，分别统计 Call / Put 成交量和 Put/Call 成交量比。它不调用历史 K 线接口。

## 使用方式

先确保 OpenD 已运行，并且本机 `futu-api` 可以连接到默认地址 `127.0.0.1:11111`。

如果第一次运行提示缺少 `pandas` 或 `futu-api`，先运行：

```cmd
setup_deps.cmd
```

然后运行安全快照监控：

```cmd
run_safe_snapshot.cmd
```

或手动运行：

```powershell
".venv-futu\Scripts\python.exe" ".\option_screen_monitor.py" --pages 5 --page-count 200
```

输出文件：

- `data/option_screen_contract_snapshot.csv`
- `data/option_screen_underlying_snapshot.csv`

旧的历史 K 线回补脚本仍在，但默认不传 `--max-kline-requests` 会拒绝运行，避免误耗历史 K 线额度。

## 输出文件

- `data/option_contract_daily.csv`: 单个期权合约的日成交量明细
- `data/underlying_option_daily.csv`: 聚合到正股维度的 Call / Put 日成交量
- `data/option_flow_signals.csv`: 异常方向、异常分、放量倍数和是否触发 alert

## 第一版 Alert 规则

Call 异常：

- 总期权成交量大于过去 15 个样本日均值的 2 倍
- Call 占全部期权成交量至少 65%
- Call 成交量大于过去 15 个样本日均值的 3 倍

Put 异常同理。

## 注意

历史回补依赖 Futu 当前还能否返回过去到期日的期权链和合约日 K。若历史链不完整，工具仍然适合每天收盘后运行，把数据沉淀到本地，之后用自己的历史库做稳定监控。
