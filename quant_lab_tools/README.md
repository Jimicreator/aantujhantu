# Quant Lab Tools

Shared offline and process-management utilities for the XAUUSD bots.

## Commands

Validate lab files:

```powershell
python E:\tradingggg\quant_lab_tools\validate_quant_lab_data.py
```

Build the offline Excel report:

```powershell
python E:\tradingggg\quant_lab_tools\build_ml_excel_report.py
```

Run a bot with session and heartbeat logging:

```powershell
python E:\tradingggg\quant_lab_tools\run_bot_with_session.py `
  --bot-id BOT_1 `
  --source "E:\tradingggg\non ema\xauusd_m1_rapid_scalper_v5.py" `
  -- python "E:\tradingggg\non ema\xauusd_m1_rapid_scalper_v5.py"
```

All outputs use `C:\Shared_Quant_Lab` by default. Override with `QUANT_LAB_ROOT` if needed.

## Automatic Offline Research

Both core bots automatically start one shared `research_monitor.py` process when they start. The monitor is single-instance, uses no MT5 connection, places no orders, and writes research output under:

```text
C:\Shared_Quant_Lab\Reports\research\
C:\Shared_Quant_Lab\Models\candidates\
C:\Shared_Quant_Lab\State\RESEARCH_MONITOR_STATE.json
C:\Shared_Quant_Lab\Logs\Research_Monitor.log
```

It waits until enough closed trades exist, generates research reports, then trains candidate-only models after new data batches. It never promotes a model or changes live bot behavior automatically.
