# Fund Monitor

一个个人持仓邮件监控脚本：读取 `holdings.json`，拉取股票与基金净值，发送 HTML 邮件，并记录历史与生成趋势图。

## 依赖安装

```bash
python3 -m pip install -r requirements.txt
```

`requirements.txt` 至少包含：
- `requests`
- `python-dotenv`
- `matplotlib`

## .env 配置

示例：

```env
HOLDINGS_FILE=holdings.json
TEMPLATE_FILE=email_template.html

SMTP_SERVER=smtp.qq.com
SMTP_PORT=465
EMAIL_FROM=your_sender@qq.com
EMAIL_PASS=your_smtp_auth_code
EMAIL_TO=your_receiver@example.com

HISTORY_FILE=history.csv
CHART_DIR=charts
EMAIL_FROM_NAME=持仓更新
EMAIL_SUBJECT=每日持仓更新
```

说明：
- 邮件标题使用 `EMAIL_SUBJECT`，不包含金额。
- 发件人显示名称使用 `EMAIL_FROM_NAME`，默认 `持仓更新`。

## holdings.json 配置

保持如下结构：

```json
{
  "stocks": [
    {
      "code": "sh601615",
      "name": "明阳智能",
      "shares": 500,
      "cost_price": 15.960
    }
  ],
  "funds": [
    {
      "code": "005544",
      "name": "基金名称",
      "shares": 1000.00,
      "cost_price": 1.2500
    }
  ]
}
```

关键规则：
- 股票代码必须显式带交易所前缀，只支持 `sh` / `sz`，例如 `sh601615`、`sz000001`。
- 不支持自动识别交易所，不支持只写 6 位股票代码。
- 基金 `shares` 是持有份额，不是买入金额。
- 基金 `cost_price` 是每份持仓成本。
- 基金使用最新单位净值，不使用盘中估值（`fundgz`）。

## 运行

```bash
python3 fund_monitor.py
```

## crontab 示例

```cron
30 22 * * * cd /path/to/project && /usr/bin/python3 fund_monitor.py >> monitor.log 2>&1
```

## history.csv 与 charts

- `history.csv`：每日快照（同一天会覆盖，不重复追加）。
- `charts/asset_value.png`：总市值变化图（最近 60 条）。
- `charts/profit.png`：总盈亏变化图（优先 `total_return`，否则 `total_profit`，最近 60 条）。

## 卖出处理说明

当前版本不自动处理买入卖出流水。卖出后可以先手动更新 `holdings.json` 中的 `shares` 和 `cost_price`。代码中已为后续扩展预留 `realized_profit` 与 `total_return` 字段。

## 免责声明

本项目仅供个人记录，不构成投资建议。
