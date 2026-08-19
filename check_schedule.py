#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桃園市長行程 -> 捷運關鍵字比對 -> LINE 官方帳號推播

環境變數:
  LINE_CHANNEL_ACCESS_TOKEN   LINE Messaging API 的 Channel Access Token(必填)
  KEYWORDS                    要比對的關鍵字,用逗號分隔,預設 "捷運"
  TARGET_YMD                  (選用,測試用)強制指定要檢查的日期,格式 YYYY-MM-DD
                               不填的話預設檢查「明天」

運作邏輯:
  1. 抓取桃園市政府「市長行程」列表頁(該頁為一般 HTML table,不需 JS 渲染)
  2. 該頁日期欄位是民國年格式,例如 115-08-06,程式會轉換成西元年比對
  3. 找出「行程日期」等於明天的所有列
  4. 只保留「出席首長」欄位剛好是「市長」的列(不含副市長、秘書長等)
  5. 若「行程內容」或「地點」欄位包含關鍵字(預設「捷運」),就依序組成
     兩則訊息:① 行程摘要、② 督察組通報格式(第一項自動帶入行程資料,
     二、三項的勤教/警力數字留空供人工填寫)
  6. 用 LINE Messaging API 的 broadcast 端點,把上述兩則訊息依序一次
     推播給所有加官方帳號好友的人
     (broadcast 不需要事先知道對方的 User ID,只要有加官方帳號好友就會收到,
      很適合個人 / 小規模使用;若之後想改成只推給特定人,可以改用 push API
      並帶入你自己的 User ID)
"""

import os
import sys
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

SCHEDULE_URL = "https://www.tycg.gov.tw/NewsPage.aspx?n=9&sms=9881"
LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"

TW_TZ = timezone(timedelta(hours=8))


def roc_to_western(date_str: str) -> str:
    """115-08-06 -> 2026-08-06"""
    m = re.match(r"(\d{2,3})-(\d{2})-(\d{2})", date_str.strip())
    if not m:
        return ""
    roc_year, month, day = m.groups()
    western_year = int(roc_year) + 1911
    return f"{western_year}-{month}-{day}"


def get_target_date() -> str:
    override = os.environ.get("TARGET_YMD")
    if override:
        return override
    now_tw = datetime.now(TW_TZ)
    tomorrow = now_tw + timedelta(days=1)
    return tomorrow.strftime("%Y-%m-%d")


def fetch_schedule_rows(max_retries: int = 4):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9",
    }

    proxy_url = os.environ.get("PROXY_URL")  # 例如 http://user:pass@host:port
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    if proxy_url:
        print("已設定代理伺服器,將透過代理連線。")

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(
                SCHEDULE_URL,
                timeout=(15, 45),  # (連線逾時, 讀取逾時) 秒 - 讀取拉長因應跨國連線慢
                headers=headers,
                proxies=proxies,
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"
            break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            wait = attempt * 5
            print(f"第 {attempt} 次抓取失敗({e.__class__.__name__}),"
                  f"{wait} 秒後重試...")
            if attempt < max_retries:
                time.sleep(wait)
    else:
        raise RuntimeError(
            f"抓取行程頁面連續失敗 {max_retries} 次,放棄。最後錯誤: {last_error}"
        )

    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table")
    if table is None:
        raise RuntimeError("找不到行程表格,網站版面可能已改版")

    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 4:
            continue
        raw_date, speaker, content, location = cells[0], cells[1], cells[2], cells[3]
        western_date = roc_to_western(raw_date)
        rows.append({
            "raw_date": raw_date,
            "date": western_date,
            "speaker": speaker,
            "content": content,
            "location": location,
        })
    return rows


def roc_date_with_weekday(western_date: str) -> str:
    """2026-06-01 -> 115年6月1日(星期一)"""
    dt = datetime.strptime(western_date, "%Y-%m-%d")
    roc_year = dt.year - 1911
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    weekday = weekday_names[dt.weekday()]
    return f"{roc_year}年{dt.month}月{dt.day}日(星期{weekday})"


def format_time_hm(time_str: str) -> str:
    """14:00 -> 14時 ; 14:30 -> 14時30分"""
    m = re.match(r"(\d{1,2}):(\d{2})", time_str.strip())
    if not m:
        return "＿＿時＿＿分"
    hour, minute = m.groups()
    hour = str(int(hour))
    if minute == "00":
        return f"{hour}時"
    return f"{hour}時{int(minute)}分"


def build_summary_message(matched_rows, target_date, keywords):
    lines = [f"📢 桃園市長隔日行程提醒 ({target_date})",
             f"偵測到與「{ '、'.join(keywords) }」相關的行程:", ""]
    for r in matched_rows:
        time_part = r["raw_date"].split(" ")[1] if " " in r["raw_date"] else ""
        lines.append(f"🕒 {time_part}｜{r['speaker']}")
        lines.append(f"　{r['content']}")
        lines.append(f"　📍 {r['location']}")
        lines.append("")
    return "\n".join(lines).strip()


def build_report_message(matched_rows, target_date, keywords):
    date_disp = roc_date_with_weekday(target_date)

    blocks = []
    for r in matched_rows:
        time_part = r["raw_date"].split(" ")[1] if " " in r["raw_date"] else ""
        time_disp = format_time_hm(time_part) if time_part else "＿＿時＿＿分"

        block = (
            "督察組通報:\n"
            f"一、市長預計於{date_disp}{time_disp}蒞臨{r['location']}，"
            f"參加「{r['content']}」活動,預計停留時間＿＿分鐘。\n"
            "二、預訂勤務規劃如下:\n"
            f"(一){date_disp}　　時　　分,於＿＿＿＿現地勤教。\n"
            f"(二)勤務時段:{date_disp}　　時至　　時。\n"
            "(三)警力規劃:督察組便衣　　名,刑事組便衣　　名,"
            "勤控中心制服警力　　名,第三分隊制服警力　　名(含幹部帶班)。\n"
            "三、請相關單位落實執行。"
        )
        blocks.append(block)

    return "\n\n".join(blocks)


def send_line_broadcast(messages: list, token: str):
    if len(messages) > 5:
        raise ValueError("LINE 單次 broadcast 最多只能放 5 則訊息")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {"messages": [{"type": "text", "text": m} for m in messages]}
    resp = requests.post(LINE_BROADCAST_URL, headers=headers, json=payload, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"LINE 推播失敗: {resp.status_code} {resp.text}")


def main():
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("錯誤: 缺少環境變數 LINE_CHANNEL_ACCESS_TOKEN", file=sys.stderr)
        sys.exit(1)

    keywords = [k.strip() for k in os.environ.get("KEYWORDS", "捷運").split(",") if k.strip()]
    target_date = get_target_date()

    print(f"檢查目標日期: {target_date}")
    print(f"比對關鍵字: {keywords}")

    rows = fetch_schedule_rows()
    print(f"共抓到 {len(rows)} 筆行程列(僅第一頁 20 筆)")

    todays_rows = [r for r in rows if r["date"] == target_date]
    print(f"其中 {len(todays_rows)} 筆屬於目標日期")

    # 只保留「出席首長」剛好是「市長」的列,排除副市長、秘書長等
    mayor_rows = [r for r in todays_rows if r["speaker"] == "市長"]
    print(f"其中 {len(mayor_rows)} 筆是市長本人的行程")

    matched = [
        r for r in mayor_rows
        if any(
            k.lower() in r["content"].lower() or k.lower() in r["location"].lower()
            for k in keywords
        )
    ]

    if not matched:
        print("沒有符合關鍵字的行程,不發送通知。")
        return

    summary_message = build_summary_message(matched, target_date, keywords)
    report_message = build_report_message(matched, target_date, keywords)

    print("即將發送的訊息 1(行程摘要):\n" + summary_message)
    print("即將發送的訊息 2(督察組通報):\n" + report_message)

    send_line_broadcast([summary_message, report_message], token)
    print("已發送 LINE 通知(共 2 則)。")


if __name__ == "__main__":
    main()
