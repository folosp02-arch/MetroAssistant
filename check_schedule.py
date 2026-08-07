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
  5. 若「行程內容」或「地點」欄位包含關鍵字(預設「捷運」),就組成訊息
  5. 用 LINE Messaging API 的 broadcast 端點推播給所有加官方帳號好友的人
     (broadcast 不需要事先知道對方的 User ID,只要有加官方帳號好友就會收到,
      很適合個人 / 小規模使用;若之後想改成只推給特定人,可以改用 push API
      並帶入你自己的 User ID)
"""

import os
import sys
import re
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


def fetch_schedule_rows():
    resp = requests.get(SCHEDULE_URL, timeout=20, headers={
        "User-Agent": "Mozilla/5.0 (compatible; TaoyuanScheduleBot/1.0)"
    })
    resp.raise_for_status()
    resp.encoding = "utf-8"
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


def build_message(matched_rows, target_date, keywords):
    lines = [f"📢 桃園市長隔日行程提醒 ({target_date})",
             f"偵測到與「{ '、'.join(keywords) }」相關的行程:", ""]
    for r in matched_rows:
        time_part = r["raw_date"].split(" ")[1] if " " in r["raw_date"] else ""
        lines.append(f"🕒 {time_part}｜{r['speaker']}")
        lines.append(f"　{r['content']}")
        lines.append(f"　📍 {r['location']}")
        lines.append("")
    return "\n".join(lines).strip()


def send_line_broadcast(message: str, token: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {"messages": [{"type": "text", "text": message}]}
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
        if any(k in r["content"] or k in r["location"] for k in keywords)
    ]

    if not matched:
        print("沒有符合關鍵字的行程,不發送通知。")
        return

    message = build_message(matched, target_date, keywords)
    print("即將發送的訊息:\n" + message)
    send_line_broadcast(message, token)
    print("已發送 LINE 通知。")


if __name__ == "__main__":
    main()
