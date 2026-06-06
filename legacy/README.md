# Legacy Tools

这些文件从主目录移入归档区，仅用于追溯历史方案或临时救援，不作为日常入口。

## google_drive

旧 Google Drive 同步与 0603 恢复脚本。日常跨设备同步已经切到 Git，不要用这些脚本覆盖当前数据。

## historical

早期历史回补、NOW/FUTU 案例验证、option flow 原型和旧安全快照入口。除非 Richard 明确要求历史验证或恢复旧实验，不要运行这里的脚本。

日常入口仍是项目根目录中的：

```cmd
START_HERE_期权监控.cmd
run_daily_report.cmd
run_intraday_report.cmd
git_sync_update.cmd
```
