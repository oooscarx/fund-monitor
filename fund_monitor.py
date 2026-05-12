import csv
import json
import os
import smtplib
from datetime import datetime
import html as html_escape_module

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests
from dotenv import load_dotenv
from email.header import Header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

load_dotenv()

HOLDINGS_FILE = os.getenv("HOLDINGS_FILE", "holdings.json")
TEMPLATE_FILE = os.getenv("TEMPLATE_FILE", "email_template.html")
HISTORY_FILE = os.getenv("HISTORY_FILE", "history.csv")
CHART_DIR = os.getenv("CHART_DIR", "charts")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "持仓更新")
EMAIL_SUBJECT = os.getenv("EMAIL_SUBJECT", "每日持仓更新")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

HISTORY_FIELDS = [
    "date",
    "total_cost",
    "total_market_value",
    "total_profit",
    "total_profit_pct",
    "stock_market_value",
    "fund_market_value",
    "realized_profit",
    "total_return",
]


def check_email_config():
    missing = []
    if not EMAIL_FROM:
        missing.append("EMAIL_FROM")
    if not EMAIL_PASS:
        missing.append("EMAIL_PASS")
    if not EMAIL_TO:
        missing.append("EMAIL_TO")
    if missing:
        raise ValueError(f".env 缺少配置项：{', '.join(missing)}")


def escape_html(x):
    return html_escape_module.escape(str(x))


def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip().replace(",", "")
            if x in ["", "--", "-"]:
                return default
        return float(x)
    except Exception:
        return default


def money(x):
    return f"{x:,.2f}"


def price4(x):
    return f"{x:,.4f}"


def pct_text(x):
    return f"{x:+.2f}%"


def color_by_value(x):
    if x > 0:
        return "#d93025"
    if x < 0:
        return "#188038"
    return "#666666"


def validate_stock_code(code):
    text = str(code).strip().lower()
    if len(text) == 6 and text.isdigit():
        raise ValueError("股票代码必须包含交易所前缀，例如 sh601615 或 sz000001")
    if len(text) != 8 or not (text.startswith("sh") or text.startswith("sz")):
        raise ValueError("股票代码必须包含交易所前缀，例如 sh601615 或 sz000001")
    if not text[2:].isdigit():
        raise ValueError("股票代码必须包含交易所前缀，例如 sh601615 或 sz000001")
    return text


def load_holdings():
    if not os.path.exists(HOLDINGS_FILE):
        raise FileNotFoundError(f"找不到持仓文件：{HOLDINGS_FILE}")

    try:
        with open(HOLDINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"holdings.json 格式错误：{e}") from e

    if not isinstance(data, dict):
        raise ValueError("holdings.json 格式错误：根节点必须是对象")

    stocks = data.get("stocks", [])
    funds = data.get("funds", [])

    if not isinstance(stocks, list) or not isinstance(funds, list):
        raise ValueError("holdings.json 格式错误：stocks 和 funds 必须是数组")

    for item in stocks:
        if not isinstance(item, dict):
            raise ValueError("holdings.json 格式错误：stocks 项必须是对象")
        if "code" not in item or "shares" not in item or "cost_price" not in item:
            raise ValueError("holdings.json 格式错误：股票项缺少 code/shares/cost_price")
        validate_stock_code(item.get("code", ""))

    for item in funds:
        if not isinstance(item, dict):
            raise ValueError("holdings.json 格式错误：funds 项必须是对象")
        if "code" not in item or "shares" not in item or "cost_price" not in item:
            raise ValueError("holdings.json 格式错误：基金项缺少 code/shares/cost_price")

    return {"stocks": stocks, "funds": funds}


def load_template():
    if not os.path.exists(TEMPLATE_FILE):
        raise FileNotFoundError(f"找不到邮件模板文件：{TEMPLATE_FILE}")
    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return f.read()


def get_stocks(stock_holdings):
    if not stock_holdings:
        return []

    code_list = [validate_stock_code(item["code"]) for item in stock_holdings]
    quote_map = {}

    try:
        url = "http://qt.gtimg.cn/q=" + ",".join(f"s_{code}" for code in code_list)
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        r.encoding = "gbk"

        for line in r.text.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            left, right = line.split("=", 1)
            raw = right.strip().strip('"')
            parts = raw.split("~")
            if len(parts) < 6:
                continue
            full_code = left.split("v_s_")[-1].strip().lower()
            quote_map[full_code] = {
                "name": parts[1],
                "price": safe_float(parts[3]),
                "change": safe_float(parts[4]),
                "daily_pct": safe_float(parts[5]),
            }
    except Exception:
        quote_map = {}

    rows = []
    for item in stock_holdings:
        code = validate_stock_code(item["code"])
        shares = safe_float(item.get("shares", 0))
        cost_price = safe_float(item.get("cost_price", 0))
        cost_value = cost_price * shares

        quote = quote_map.get(code)
        if not quote:
            rows.append(
                {
                    "name": item.get("name", code),
                    "code": code,
                    "shares": shares,
                    "cost_price": cost_price,
                    "cost_value": cost_value,
                    "data_ok": False,
                    "error": "数据获取失败",
                }
            )
            continue

        price = quote["price"]
        market_value = price * shares
        profit = market_value - cost_value
        profit_pct = profit / cost_value * 100 if cost_value > 0 else 0
        rows.append(
            {
                "name": quote.get("name") or item.get("name", code),
                "code": code,
                "shares": shares,
                "cost_price": cost_price,
                "price": price,
                "change": quote["change"],
                "daily_pct": quote["daily_pct"],
                "market_value": market_value,
                "cost_value": cost_value,
                "profit": profit,
                "profit_pct": profit_pct,
                "data_ok": True,
            }
        )

    return rows


def get_funds(fund_holdings):
    if not fund_holdings:
        return []

    rows = []
    for item in fund_holdings:
        code = str(item["code"]).strip()
        shares = safe_float(item.get("shares", 0))
        cost_price = safe_float(item.get("cost_price", 0))
        cost_value = cost_price * shares

        try:
            url = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList"
            params = {
                "FCODE": code,
                "pageIndex": 1,
                "pageSize": 1,
                "plat": "Android",
                "appType": "ttjj",
                "product": "EFund",
                "Version": "1",
                "deviceid": "python-script",
            }
            r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            r.raise_for_status()
            datas = r.json().get("Datas", [])
            if not datas:
                raise ValueError("empty data")
            latest = datas[0]

            nav = safe_float(latest.get("DWJZ", 0))
            total_nav = safe_float(latest.get("LJJZ", 0))
            daily_pct = safe_float(latest.get("JZZZL", 0))
            nav_date = latest.get("FSRQ", "")
            market_value = nav * shares
            profit = market_value - cost_value
            profit_pct = profit / cost_value * 100 if cost_value > 0 else 0

            rows.append(
                {
                    "name": item.get("name", code),
                    "code": code,
                    "shares": shares,
                    "cost_price": cost_price,
                    "nav": nav,
                    "total_nav": total_nav,
                    "daily_pct": daily_pct,
                    "nav_date": nav_date,
                    "market_value": market_value,
                    "cost_value": cost_value,
                    "profit": profit,
                    "profit_pct": profit_pct,
                    "data_ok": True,
                }
            )
        except Exception:
            rows.append(
                {
                    "name": item.get("name", code),
                    "code": code,
                    "shares": shares,
                    "cost_price": cost_price,
                    "cost_value": cost_value,
                    "data_ok": False,
                    "error": "数据获取失败",
                }
            )

    return rows


def build_empty_card(text):
    return (
        "<div style=\"padding:16px;color:#777;text-align:center;background:#fafafa;border-radius:12px;\">"
        + escape_html(text)
        + "</div>"
    )


def build_data_failed_row(label):
    return (
        f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">{label}</td>"
        "<td style=\"padding:7px 0;text-align:right;font-size:14px;color:#999;white-space:nowrap;\">数据获取失败</td></tr>"
    )


def build_stock_cards(stock_rows):
    if not stock_rows:
        return build_empty_card("未配置股票持仓")

    cards = ""
    for s in stock_rows:
        header = (
            f"<div style=\"margin-bottom:12px;\"><div style=\"font-size:17px;font-weight:700;color:#222;\">{escape_html(s['name'])}</div>"
            f"<div style=\"font-size:12px;color:#888;margin-top:4px;\">{escape_html(s['code'])}</div></div>"
        )
        base_rows = (
            f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">持股</td><td style=\"padding:7px 0;text-align:right;font-size:14px;font-weight:600;white-space:nowrap;\">{s['shares']:.0f}</td></tr>"
            f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">成本价</td><td style=\"padding:7px 0;text-align:right;font-size:14px;white-space:nowrap;\">¥ {money(s['cost_price'])}</td></tr>"
        )

        if not s.get("data_ok"):
            detail_rows = (
                build_data_failed_row("市场价")
                + build_data_failed_row("当前市值")
                + build_data_failed_row("浮动盈亏")
                + build_data_failed_row("收益率")
            )
        else:
            profit_color = color_by_value(s["profit"])
            daily_color = color_by_value(s["daily_pct"])
            detail_rows = (
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">市场价</td><td style=\"padding:7px 0;text-align:right;font-size:14px;white-space:nowrap;\">¥ {money(s['price'])}</td></tr>"
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">今日涨跌</td><td style=\"padding:7px 0;text-align:right;font-size:14px;color:{daily_color};font-weight:600;white-space:nowrap;\">{pct_text(s['daily_pct'])}</td></tr>"
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">当前市值</td><td style=\"padding:7px 0;text-align:right;font-size:14px;white-space:nowrap;\">¥ {money(s['market_value'])}</td></tr>"
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">浮动盈亏</td><td style=\"padding:7px 0;text-align:right;font-size:15px;color:{profit_color};font-weight:700;white-space:nowrap;\">¥ {money(s['profit'])}</td></tr>"
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">收益率</td><td style=\"padding:7px 0;text-align:right;font-size:15px;color:{profit_color};font-weight:700;white-space:nowrap;\">{pct_text(s['profit_pct'])}</td></tr>"
            )

        cards += (
            "<div style=\"background:#ffffff;border:1px solid #eaeaea;border-radius:14px;padding:16px;margin-bottom:12px;\">"
            + header
            + "<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;\">"
            + base_rows
            + detail_rows
            + "</table></div>"
        )

    return cards


def build_fund_cards(fund_rows):
    if not fund_rows:
        return build_empty_card("未配置基金持仓")

    cards = ""
    for f in fund_rows:
        header = (
            f"<div style=\"margin-bottom:12px;\"><div style=\"font-size:17px;font-weight:700;color:#222;\">{escape_html(f['name'])}</div>"
            f"<div style=\"font-size:12px;color:#888;margin-top:4px;\">{escape_html(f['code'])}</div></div>"
        )
        base_rows = (
            f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">持有份额</td><td style=\"padding:7px 0;text-align:right;font-size:14px;font-weight:600;white-space:nowrap;\">{f['shares']:.2f}</td></tr>"
            f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">成本净值</td><td style=\"padding:7px 0;text-align:right;font-size:14px;white-space:nowrap;\">{price4(f['cost_price'])}</td></tr>"
        )

        if not f.get("data_ok"):
            detail_rows = (
                build_data_failed_row("单位净值")
                + build_data_failed_row("当前市值")
                + build_data_failed_row("浮动盈亏")
                + build_data_failed_row("收益率")
            )
        else:
            profit_color = color_by_value(f["profit"])
            daily_color = color_by_value(f["daily_pct"])
            detail_rows = (
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">单位净值</td><td style=\"padding:7px 0;text-align:right;font-size:14px;white-space:nowrap;\">{price4(f['nav'])}</td></tr>"
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">累计净值</td><td style=\"padding:7px 0;text-align:right;font-size:14px;white-space:nowrap;\">{price4(f['total_nav'])}</td></tr>"
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">日涨幅</td><td style=\"padding:7px 0;text-align:right;font-size:14px;color:{daily_color};font-weight:600;white-space:nowrap;\">{pct_text(f['daily_pct'])}</td></tr>"
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">净值日期</td><td style=\"padding:7px 0;text-align:right;font-size:14px;white-space:nowrap;\">{escape_html(f['nav_date'])}</td></tr>"
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">当前市值</td><td style=\"padding:7px 0;text-align:right;font-size:14px;white-space:nowrap;\">¥ {money(f['market_value'])}</td></tr>"
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">浮动盈亏</td><td style=\"padding:7px 0;text-align:right;font-size:15px;color:{profit_color};font-weight:700;white-space:nowrap;\">¥ {money(f['profit'])}</td></tr>"
                f"<tr><td style=\"padding:7px 0;color:#777;font-size:13px;\">收益率</td><td style=\"padding:7px 0;text-align:right;font-size:15px;color:{profit_color};font-weight:700;white-space:nowrap;\">{pct_text(f['profit_pct'])}</td></tr>"
            )

        cards += (
            "<div style=\"background:#ffffff;border:1px solid #eaeaea;border-radius:14px;padding:16px;margin-bottom:12px;\">"
            + header
            + "<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;\">"
            + base_rows
            + detail_rows
            + "</table></div>"
        )

    return cards


def render_template(template, values):
    result = template
    for key, value in values.items():
        result = result.replace("{{ " + key + " }}", str(value))
    return result


def collect_totals(stock_rows, fund_rows):
    ok_stocks = [x for x in stock_rows if x.get("data_ok")]
    ok_funds = [x for x in fund_rows if x.get("data_ok")]
    ok_rows = ok_stocks + ok_funds

    total_cost = sum(x["cost_value"] for x in ok_rows)
    total_market = sum(x["market_value"] for x in ok_rows)
    total_profit = total_market - total_cost
    total_profit_pct = total_profit / total_cost * 100 if total_cost > 0 else 0

    realized_profit = 0.0  # 预留：后续可从交易记录累计已实现盈亏
    total_return = total_profit + realized_profit
    total_return_pct = total_return / total_cost * 100 if total_cost > 0 else 0

    stock_market = sum(x["market_value"] for x in ok_stocks)
    fund_market = sum(x["market_value"] for x in ok_funds)

    return {
        "total_cost": total_cost,
        "total_market": total_market,
        "total_profit": total_profit,
        "total_profit_pct": total_profit_pct,
        "realized_profit": realized_profit,
        "total_return": total_return,
        "total_return_pct": total_return_pct,
        "stock_market": stock_market,
        "fund_market": fund_market,
    }


def load_history_records(history_file):
    records = []
    if not os.path.exists(history_file):
        return records

    with open(history_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            records.append(row)
    return records


def save_history_snapshot(history_file, totals):
    today = datetime.now().strftime("%Y-%m-%d")
    records = load_history_records(history_file)

    snapshot = {
        "date": today,
        "total_cost": f"{totals['total_cost']:.2f}",
        "total_market_value": f"{totals['total_market']:.2f}",
        "total_profit": f"{totals['total_profit']:.2f}",
        "total_profit_pct": f"{totals['total_profit_pct']:.4f}",
        "stock_market_value": f"{totals['stock_market']:.2f}",
        "fund_market_value": f"{totals['fund_market']:.2f}",
        "realized_profit": f"{totals['realized_profit']:.2f}",
        "total_return": f"{totals['total_return']:.2f}",
    }

    replaced = False
    for i, row in enumerate(records):
        if row.get("date") == today:
            records[i] = snapshot
            replaced = True
            break
    if not replaced:
        records.append(snapshot)

    with open(history_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        for row in records:
            clean = {key: row.get(key, "") for key in HISTORY_FIELDS}
            writer.writerow(clean)


def generate_chart(history_file, chart_dir, output_filename, y_field, title):
    os.makedirs(chart_dir, exist_ok=True)
    output_path = os.path.join(chart_dir, output_filename)

    records = load_history_records(history_file)
    if not records:
        return None

    last_records = records[-60:]
    dates = [row.get("date", "") for row in last_records]
    values = []
    for row in last_records:
        if y_field == "total_return":
            value = safe_float(row.get("total_return", row.get("total_profit", 0)))
        else:
            value = safe_float(row.get(y_field, 0))
        values.append(value)

    try:
        plt.figure(figsize=(8, 4.2))
        plt.plot(dates, values, marker="o")
        plt.title(title)
        plt.xlabel("Date")
        plt.ylabel("Value")
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(output_path, dpi=120)
        plt.close()
        return output_path
    except Exception:
        try:
            plt.close()
        except Exception:
            pass
        return None


def build_chart_section(asset_chart_path, profit_chart_path):
    blocks = []

    if asset_chart_path and os.path.exists(asset_chart_path):
        blocks.append(
            "<div style=\"margin-top:12px;\"><div style=\"font-size:14px;color:#555;margin-bottom:8px;\">Asset Value Trend</div>"
            "<img src=\"cid:asset_value_chart\" style=\"display:block;width:100%;max-width:100%;border:1px solid #eeeeee;border-radius:10px;\" /></div>"
        )
    else:
        blocks.append(
            "<div style=\"margin-top:12px;font-size:13px;color:#999;\">Asset Value Trend: insufficient history</div>"
        )

    if profit_chart_path and os.path.exists(profit_chart_path):
        blocks.append(
            "<div style=\"margin-top:12px;\"><div style=\"font-size:14px;color:#555;margin-bottom:8px;\">Total Profit Trend</div>"
            "<img src=\"cid:profit_chart\" style=\"display:block;width:100%;max-width:100%;border:1px solid #eeeeee;border-radius:10px;\" /></div>"
        )
    else:
        blocks.append(
            "<div style=\"margin-top:12px;font-size:13px;color:#999;\">Total Profit Trend: insufficient history</div>"
        )

    return "".join(blocks)


def build_email_html(stock_rows, fund_rows, totals, chart_section):
    template = load_template()
    return render_template(
        template,
        {
            "TOTAL_MARKET": money(totals["total_market"]),
            "TOTAL_COST": money(totals["total_cost"]),
            "TOTAL_PROFIT": money(totals["total_profit"]),
            "TOTAL_PROFIT_PCT": pct_text(totals["total_profit_pct"]),
            "REALIZED_PROFIT": money(totals["realized_profit"]),
            "TOTAL_RETURN": money(totals["total_return"]),
            "TOTAL_RETURN_PCT": pct_text(totals["total_return_pct"]),
            "TOTAL_PROFIT_COLOR": color_by_value(totals["total_profit"]),
            "TOTAL_RETURN_COLOR": color_by_value(totals["total_return"]),
            "STOCK_MARKET": money(totals["stock_market"]),
            "FUND_MARKET": money(totals["fund_market"]),
            "STOCK_CARDS": build_stock_cards(stock_rows),
            "FUND_CARDS": build_fund_cards(fund_rows),
            "CHART_SECTION": chart_section,
        },
    )


def send_mail(subject, html_content, asset_chart_path=None, profit_chart_path=None):
    check_email_config()

    msg_root = MIMEMultipart("related")
    msg_root["Subject"] = Header(subject, "utf-8")
    msg_root["From"] = formataddr((Header(EMAIL_FROM_NAME, "utf-8").encode(), EMAIL_FROM))
    msg_root["To"] = EMAIL_TO

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_content, "html", "utf-8"))
    msg_root.attach(alt)

    if asset_chart_path and os.path.exists(asset_chart_path):
        with open(asset_chart_path, "rb") as f:
            img = MIMEImage(f.read())
            img.add_header("Content-ID", "<asset_value_chart>")
            img.add_header("Content-Disposition", "inline", filename="asset_value.png")
            msg_root.attach(img)

    if profit_chart_path and os.path.exists(profit_chart_path):
        with open(profit_chart_path, "rb") as f:
            img = MIMEImage(f.read())
            img.add_header("Content-ID", "<profit_chart>")
            img.add_header("Content-Disposition", "inline", filename="profit.png")
            msg_root.attach(img)

    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
        server.login(EMAIL_FROM, EMAIL_PASS)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg_root.as_string())


def main():
    holdings = load_holdings()
    stock_rows = get_stocks(holdings.get("stocks", []))
    fund_rows = get_funds(holdings.get("funds", []))
    totals = collect_totals(stock_rows, fund_rows)

    save_history_snapshot(HISTORY_FILE, totals)

    asset_chart_path = None
    profit_chart_path = None
    try:
        asset_chart_path = generate_chart(
            HISTORY_FILE, CHART_DIR, "asset_value.png", "total_market_value", "Asset Value Trend"
        )
        profit_chart_path = generate_chart(
            HISTORY_FILE, CHART_DIR, "profit.png", "total_return", "Total Profit Trend"
        )
    except Exception:
        asset_chart_path = None
        profit_chart_path = None

    chart_section = build_chart_section(asset_chart_path, profit_chart_path)
    email_html = build_email_html(stock_rows, fund_rows, totals, chart_section)

    send_mail(EMAIL_SUBJECT, email_html, asset_chart_path, profit_chart_path)

    print("邮件已发送")
    print(f"总成本：{money(totals['total_cost'])}")
    print(f"总市值：{money(totals['total_market'])}")
    print(f"当前浮动盈亏：{money(totals['total_profit'])}")
    print(f"已实现盈亏：{money(totals['realized_profit'])}")
    print(f"累计盈亏：{money(totals['total_return'])}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"运行失败：{e}")
        raise
