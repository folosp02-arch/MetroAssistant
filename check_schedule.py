#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桃園市長行程 -> 捷運關鍵字比對 -> LINE 官方帳號推播

環境變數:
  LINE_CHANNEL_ACCESS_TOKEN   LINE Messaging API 的 Channel Access Token(必填)
  KEYWORDS                    要比對的關鍵字,用逗號分隔,預設 "捷運"
  TARGET_YMD                  (選用,測試用)強制指定要檢查的日期,格式 YYYY-MM-DD
                               不填的話預設檢查「明天」
  GITHUB_REPOSITORY           GitHub Actions 會自動提供,格式 owner/repo,
                               用來組出截圖的公開網址
  GITHUB_REF_NAME              GitHub Actions 會自動提供,目前分支名稱

運作邏輯:
  1. 抓取桃園市政府「市長行程」列表頁(該頁為一般 HTML table,不需 JS 渲染)
  2. 該頁日期欄位是民國年格式,例如 115-08-06,程式會轉換成西元年比對
  3. 找出「行程日期」等於明天的所有列
  4. 只保留「出席首長」欄位剛好是「市長」的列(不含副市長、秘書長等)
  5. 若「行程內容」或「地點」欄位包含關鍵字(預設「捷運」):
     a. 組成兩則文字訊息:① 行程摘要、② 督察組通報格式(第一項自動帶入
        行程資料,二、三項的勤教/警力數字留空供人工填寫)
     b. 用 Playwright 對政府網站的行程表格直接截圖,存回本 Repo 的
        screenshots/ 資料夾並 git push(需要 Repo 為 Public,且 workflow
        要有 contents: write 權限),取得截圖的公開網址
  6. 用 LINE Messaging API 的 broadcast 端點,依序推播:行程摘要 → 督察組
     通報 → 行程表格截圖(若截圖失敗,仍會照常送出前兩則文字訊息)
     (broadcast 不需要事先知道對方的 User ID,只要有加官方帳號好友就會收到,
      很適合個人 / 小規模使用;若之後想改成只推給特定人,可以改用 push API
      並帶入你自己的 User ID)
"""

import os
import sys
import re
import time
import subprocess
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

SCHEDULE_URL = "https://www.tycg.gov.tw/NewsPage.aspx?n=9&sms=9881"
LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
SCREENSHOT_DIR = "screenshots"

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
    payload = {"messages": messages}
    resp = requests.post(LINE_BROADCAST_URL, headers=headers, json=payload, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"LINE 推播失敗: {resp.status_code} {resp.text}")


def capture_schedule_screenshot(output_path: str) -> bool:
    """用 Playwright 對政府網站的行程表格直接截圖,存到 output_path。
    成功回傳 True,失敗印出警告並回傳 False(不中斷整體流程)。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("警告: 未安裝 playwright,略過截圖。")
        return False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(SCHEDULE_URL, wait_until="networkidle", timeout=45000)
            table = page.locator("table").first
            table.screenshot(path=output_path)
            browser.close()
        print(f"已產生截圖: {output_path}")
        return True
    except Exception as e:
        print(f"警告: 截圖失敗,略過此步驟。錯誤: {e}")
        return False


def commit_and_push_screenshot(local_path: str, repo_relative_path: str) -> str:
    """把截圖 commit 並 push 回 Repo,回傳可公開存取的 raw.githubusercontent.com
    網址;若沒有 GITHUB_REPOSITORY(例如本機測試環境)或 push 失敗,回傳空字串。
    """
    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = os.environ.get("GITHUB_REF_NAME")
    if not repo or not branch:
        print("警告: 不在 GitHub Actions 環境中,略過 git push,無法取得公開網址。")
        return ""

    try:
        os.makedirs(os.path.dirname(repo_relative_path), exist_ok=True)
        os.replace(local_path, repo_relative_path)

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(
            ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
            check=True,
        )
        subprocess.run(["git", "add", repo_relative_path], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"新增行程截圖: {repo_relative_path}"], check=True
        )
        subprocess.run(["git", "push", "origin", f"HEAD:{branch}"], check=True)

        return f"https://raw.githubusercontent.com/{repo}/{branch}/{repo_relative_path}"
    except subprocess.CalledProcessError as e:
        print(f"警告: git commit/push 失敗,略過截圖網址。錯誤: {e}")
        return ""


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

    messages = [
        {"type": "text", "text": summary_message},
        {"type": "text", "text": report_message},
    ]

    local_screenshot = "schedule_screenshot.png"
    if capture_schedule_screenshot(local_screenshot):
        repo_relative_path = f"{SCREENSHOT_DIR}/{target_date}.png"
        image_url = commit_and_push_screenshot(local_screenshot, repo_relative_path)
        if image_url:
            print(f"截圖公開網址: {image_url}")
            messages.append({
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url,
            })

    send_line_broadcast(messages, token)
    print(f"已發送 LINE 通知(共 {len(messages)} 則)。")


if __name__ == "__main__":
    main()
