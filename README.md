# mCam

手機/PC 互當相機：傳送端播相機，接收端預覽、截圖、錄影。含 Android 端（`android/`）與 PC 端（`pc/`，Python + PySimpleGUI）。

## 功能

- **雙向互傳**：手機可傳可收，PC 可收可傳（四種組合，見下表）
- **手機傳送**：USB 共享 / WiFi，MJPEG server 跑 `:8080`（`/video` `/snapshot` `/info`）
- **手機接收**：切 `WiFi 接收端`，輸對方 IP 就播（只輸 IP，前後綴自動補，有記憶）
- **PC 接收**：`USB 共享` / `WiFi 接收端` / `USB webcam` / `DPO2014B`（只吃 USB，欄位填 `USB`）/ `TDS3014B`（只吃 LAN，填示波器 IP；約幾秒一張）；斷線自動重連，斷線黑屏灰字 `已斷線`
- **PC 傳送**：`WiFi 傳送端`，播本地 webcam，欄位自動帶 `http://本機IP:8080/video` 給手機填
- **手機預覽開關**：`關預覽` 只停本地顯示（黑屏+`預覽已關閉`），推流不斷；接收中黑屏顯示 `接收中...`
- **截圖**：一律最高畫質 JPG，存 `pc/shots/`
- **錄影**：一律最高畫質 MP4（H.264），存 `pc/rec/`；畫面顯示錄影秒數
- **圖庫**：縮圖一覽（5 欄+捲軸）；點一下選取（黃底），ctrl+點多選，點兩下直接放
- **看圖**：滾輪縮放（0.2x~8x）；主畫面**資料夾**鈕直接開 `pc/`（`shots/`+`rec/`）
  - 單選才能**放**（預覽）/**壓**（降級存 720p/480p）
  - **刪**可一次刪多個；刪後自動重排不閃爍
  - **全選**（ctrl-a）/**全不選**兩個快選鈕

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

`requirements.txt` 內容與用途：

| 套件 | 用途 | 沒裝會怎樣 |
| --- | --- | --- |
| `PySimpleGUI>=6` | 主視窗/圖庫 UI | 不能跑（必需） |
| `opencv-python` | 預覽/截圖/錄影/轉檔 | 不能跑（必需） |
| `pyvisa` | DPO2014B 用 USB 抓圖（USBTMC） | 只缺示波器源，其他功能正常 |

手動裝的東西（pip 裝不到），缺了也能跑、只是降級：

1. **H.264 錄影 DLL**（錄影檔小 2–4 倍）：
   1. 下載 `openh264-2.5.0-win64.dll`（Cisco 官方：http://ciscobinary.openh264.org/openh264-2.5.0-win64.dll.bz2，解壓）
   2. 放到 `python.exe` 旁（或 `site-packages\cv2\` 旁，任一即可）
   3. 沒放自動退回 mp4v（檔較大）；console 出現 `Failed to load OpenH264` 就是缺它
2. **VISA runtime**（示波器才需要，二選一：NI-VISA 或 TekVISA；示波器附的光碟有，或 Tek/NI 官網下載）：
   1. DPO2014B：後面 USB device 口用 USB 線接 PC（走 USBTMC，裝置管理員會出現 `USB Test and Measurement Device (IVI)`）；PC 端切 `DPO2014B`，欄位填 `USB`（自動找示波器）→ 按連線
   2. TDS3014B：網路線接 LAN（內建 10Base-T），示波器 Utility → I/O 看 IP；PC 端切 `TDS3014B`，欄位填該 IP → 按連線（走 VXI-11，不用加裝任何模組）
   3. 沒裝 VISA 按連線會顯示要裝 pyvisa+NI-VISA，其他來源不受影響
   4. DPO 截圖原生只有 480×234、TDS 只有 640×480（面板物理解析度）；DPO 會 2x 放大顯示/存檔；真要高清得改抓波形重畫（未做）

## 執行

```bat
python pc\mCam.py
```

手機 App 保持前景（亮屏保活）。USB 共享流程：手機開 USB 數據共享 → 手機 App 保持前景 → PC 按「連線」。

## 發版（手機 App）

`android\release_build.bat` 雙擊：自動產生簽名 keystore（只做一次，放 `android/` 本機、不進版控）→ `assembleRelease` → APK 存到 `android\release\mCam-v版本.apk`。需先裝 Android Studio（JDK/keytool/SDK 都用它的）。

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
