# 桃園市長行程・捷運關鍵字 LINE 提醒

自動抓取桃園市政府「市長行程」頁面,若隔天行程中出現「捷運」關鍵字,
就透過 LINE 官方帳號推播通知你。

## 部署步驟(GitHub Actions,全免費)

1. **建立一個新的 GitHub Repository**(可以設成 Private)。

2. 把這個資料夾裡的三個檔案上傳上去,保持原本的路徑結構:
   ```
   check_schedule.py
   requirements.txt
   .github/workflows/check_schedule.yml
   ```

3. 到 Repository 的 **Settings → Secrets and variables → Actions**,
   新增一個 secret:
   - Name: `LINE_CHANNEL_ACCESS_TOKEN`
   - Value: 你申請好的 LINE Messaging API Channel Access Token

4. 用手機 LINE **加你自己申請的官方帳號為好友**(broadcast 訊息只會發給
   已加好友的人,你自己也要加)。

5. 到 GitHub 網頁的 **Actions** 分頁,選這個 workflow,點
   **Run workflow** 手動觸發一次,測試是否成功收到 LINE 通知。

6. 之後就會照排程(台灣時間每天 18:00)自動執行,不用再管它。

## 為什麼需要代理伺服器?

實測發現桃園市政府網站對 GitHub Actions 的連線會持續 Read Timeout(逾時、
重試都沒用),這是台灣政府網站常見的防火牆設定:直接封鎖已知的雲端機房
IP 段(GitHub Actions 底層是 Azure),不分請求來源國家。要解決這個問題,
必須讓程式改用**非雲端機房、且最好是台灣節點**的出口 IP 連線,也就是
透過代理伺服器(proxy)轉發請求。

### 設定步驟

1. 申請一個**付費代理服務**,選擇**台灣(TW)節點**,常見服務例如
   Webshare.io、IPRoyal、Smartproxy 等(免費公開代理不建議,通常不穩定
   或也在封鎖名單內)。申請後你會拿到一組連線字串,格式類似:
   ```
   http://使用者名稱:密碼@proxy主機:port
   ```
2. 到 GitHub Repo 的 **Settings → Secrets and variables → Actions**,
   新增一個 secret:
   - Name: `PROXY_URL`
   - Value: 上面那組完整的代理連線字串
3. 不用改程式碼,`check_schedule.py` 已經支援:只要偵測到
   `PROXY_URL` 這個環境變數存在,就會自動透過代理連線;沒設定的話行為
   跟原本一樣(直接連線)。
4. 重新手動觸發一次 workflow 測試。

## 想調整的地方

- **關鍵字**:改 `.github/workflows/check_schedule.yml` 裡的 `KEYWORDS`
  環境變數,可用逗號分隔多個關鍵字,例如 `"捷運,高鐵"`。
- **只抓市長本人**:程式已經內建只保留「出席首長」欄位剛好是「市長」
  的行程,副市長、秘書長的行程不會拿來比對關鍵字。若之後想連副市長
  的行程也一起檢查,可以到 `check_schedule.py` 裡搜尋
  `r["speaker"] == "市長"` 這一行修改判斷條件。
- **執行時間**:改 workflow 檔裡的 `cron`,記得 GitHub Actions 的 cron
  是 UTC 時間,要自己 -8 小時換算成台灣時間。
- **只推給自己而不是所有好友**:目前用的是 `broadcast`(推播給所有加
  官方帳號好友的人),如果之後想只推給特定人,可以改用 LINE 的
  `push` API,並在程式裡帶入你自己的 User ID(需要另外架 webhook 或
  用 LINE 官方帳號後台的「加入好友的歡迎訊息」流程取得)。

## 本機測試(不透過 GitHub Actions)

```bash
pip install -r requirements.txt
export LINE_CHANNEL_ACCESS_TOKEN="你的token"
# 可選:強制指定要檢查的日期,方便測試(不加的話預設檢查「明天」)
export TARGET_YMD="2026-08-07"
python check_schedule.py
```

## 注意事項

- 市府網站的「行程日期」欄位有時要到當天傍晚以後才會補上隔天的行程,
  目前排在台灣時間 18:00 檢查,若市府更新得比這個時間晚,當天可能會
  抓不到隔天的行程,這點沒有辦法從程式端解決,只能視情況調整檢查時間。
- 網站目前抓的是第一頁(最新 20 筆),正常情況下隔天的行程一定會在
  最新的 20 筆之內,不會抓不到。
- 如果桃園市政府網站改版導致抓不到表格,程式會直接報錯並讓 GitHub
  Actions 顯示失敗,方便你及早發現。
