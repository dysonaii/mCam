# mCam

手機/PC 互當相機：傳送端播相機，接收端預覽、截圖、錄影。含 Android 端（`android/`）與 PC 端（`pc/`，Python + PySimpleGUI）。

## 功能

- **雙向互傳**：手機可傳可收，PC 可收可傳（四種組合，見下表）
- **手機傳送**：USB 共享 / WiFi，MJPEG server 跑 `:8080`（`/video` `/snapshot` `/info`）
- **手機接收**：切 `WiFi 接收端`，輸對方 IP 就播（只輸 IP，前後綴自動補，有記憶）
- **PC 接收**：`USB 共享` / `WiFi 接收端` / `USB webcam`；斷線自動重連，斷線黑屏灰字 `已斷線`
- **PC 傳送**：`WiFi 傳送端`，播本地 webcam，欄位自動帶 `http://本機IP:8080/video` 給手機填
- **手機預覽開關**：`關預覽` 只停本地顯示（黑屏+`預覽已關閉`），推流不斷；接收中黑屏顯示 `接收中...`
- **截圖**：一律最高畫質 JPG，存 `pc/shots/`
- **錄影**：一律最高畫質 MP4（H.264），存 `pc/rec/`；畫面顯示錄影秒數
- **圖庫**：縮圖一覽；點一下選取（黃框），ctrl+點多選
  - 單選才能**放**（預覽）/**壓**（降級存 720p/480p）
  - **刪**可一次刪多個；刪檔不閃爍（隱藏不重建）

## 操作手冊

配對只有一種規則：**一端傳送，另一端接收**。

| 手機 | PC | 說明 |
| --- | --- | --- |
| WiFi 傳送端 | WiFi 接收端 | 同一 WiFi，PC 填手機畫面的 IP |
| WiFi 接收端 | WiFi 傳送端 | 同一 WiFi，手機填 PC 顯示的 IP |
| （USB 共享開）傳送 | USB 共享 | 手機開 USB 數據共享插 PC，PC 按連線（自動找 RNDIS 閘道） |

手機按鍵同一排：`[USB 共享] [WiFi 傳送端/接收端] [關預覽]`，下面兩行是 USB / WiFi 的 URL，點一下複製。接收模式點 WiFi 那行改對方 IP（只打 `192.168.x.x` 即可）。

PC 欄位：`USB 共享` / `WiFi 接收端` 填對方 URL；`WiFi 傳送端` / `USB webcam` 填 `0/1`（傳送端欄位會自動帶本機 URL，那是給手機看的）。

### 不在同一區網時

- **USB 數據共享**：手機插線連 PC 就有區網（`192.168.42.x`），跟用 WiFi 還是行動數據上網無關
- **手機熱點**：一台開熱點，其他加入（通常 `192.168.43.x`），熱點自成區網
- **行動數據直連**：不行（電信商 NAT，無公網 IP，App 會顯示行動網路不能直連）
- **遠端**：兩邊都裝 Tailscale，互填 `100.x.y.z`，App 不用改

## 安裝（PC 端）

需 Python ≥ 3.10。

```bat
pip install -r pc\requirements.txt
```

錄影要 H.264（檔小 2–4 倍）另需手動放 DLL（pip 裝不到，有版本限制）：

1. 下載 `openh264-2.5.0-win64.dll`（Cisco 官方：http://ciscobinary.openh264.org/openh264-2.5.0-win64.dll.bz2，解壓）
2. 放到 `python.exe` 旁（或 `site-packages\cv2\` 旁，任一即可）
3. 沒放也能跑，自動退回 mp4v（檔較大）；console 出現 `Failed to load OpenH264` 就是缺它

## 執行

```bat
python pc\mCam.py
```

手機 App 保持前景（亮屏保活）。USB 共享流程：手機開 USB 數據共享 → 手機 App 保持前景 → PC 按「連線」。

## 連不上排查

1. 同一 WiFi 嗎？手機 IP 和 PC IP 前三段要一樣（`192.168.50.x`）。行動數據互連不到
2. Windows 防火牆擋 inbound（最常見）：手機瀏覽器開 `http://PC_IP:8080/info`，看不到 JSON 就是被擋 → 防火牆把 `python.exe` 放行（私人+公用）
3. 公司/公共 WiFi 可能有 AP 隔離（設備互不可見），換手機熱點或自家 WiFi
4. PC `WiFi 傳送端` 顯示 `port 8080 被佔用`：port 被別的程式佔了
5. 手機切接收後 PC 顯示斷線：正常（手機 server 關了），手機切回傳送 PC 會自動重連

## 備註

- `pc/shots/`、`pc/rec/`、`pc/mCam.json` 自動建立，不進版控
- 改名過的模式（`USB 共享` / `WiFi 接收端` / `WiFi 傳送端`）舊設定自動搬家，不用重填
- console 若出現 `Stream ends prematurely`：ffmpeg 讀手機串流的警告，無害，不影響預覽/錄影
