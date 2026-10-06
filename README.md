# mCam

手機/PC 互當相機：傳送端播相機，接收端預覽、截圖、錄影。含 Android 端（`android/`）與 PC 端（`pc/`，Python + PySimpleGUI）。

## 功能

- **雙向互傳**：手機可傳可收，PC 可收可傳（組合見下表）
- **手機傳送**：USB 共享 / WiFi，MJPEG server 跑 `:8080`，路徑只有 `/v` 串流、`/s/` 截圖、`/i/` 資訊；預設空碼，有約好才自加 1~6 碼（如 `/v/140023`，兩邊打一樣就能看）
- **手機接收**：切 `WiFi 接收`，輸入 `IP`（空碼直連）或 `IP＋空格＋1~6碼`（如 `10.35.9.108 140023`）或完整 URL；有記憶
- **PC 接收**：`USB` / `WiFi` / `WebCam` / `DPO2014B`（使用 USB，欄位填 `USB`）；斷線自動重連，斷線黑屏灰字 `已斷線`
- **PC 轉播**：任何來源勾 `轉播` 即再 serve `:8080`（同規 `/v` 空碼），區網 browser 或手機開勾選框上的 URL 即看；各來源分開記，不互蓋；開啟後 URL 直接顯示在 `轉播` 勾選框上，右鍵點一下即複製；多網卡跳選單挑 IP，單張靜默自動
- **手機預覽開關**：`關預覽` 只停本地顯示（黑屏+`預覽已關閉`），推流不斷；接收中黑屏顯示 `接收中...`
- **凍結/解凍**：傳送中按 `凍結`，手機本地和 PC 同幀靜止（USB 共享 / WiFi 傳送通用，PC 端零操作）；按 `解凍` 恢復；接收模式停用；凍結中截圖/錄影拿到的就是凍結幀
- **截圖**：一律最高畫質 JPG，存 `pc/shots/`
- **錄影**：一律最高畫質 MP4，存 `pc/rec/`（有 openh264 DLL 即 H.264，否則退回 mp4v，見安裝）；畫面顯示錄影秒數
- **圖庫**：縮圖一覽（5 欄+捲軸）；點一下選取（黃底），ctrl+點多選，點兩下直接放
- **看圖**：滾輪縮放（0.2x~8x）；主畫面**資料夾**鈕直接開 `pc/`（`shots/`+`rec/`）
  - 單選才能**放**（預覽）/**壓**（降級存 720p/480p）
  - **刪**可一次刪多個；刪後自動重排不閃爍
  - **全選**（ctrl-a）/**全不選**兩個快選鈕

## 操作手冊

配對只有一種規則：**一端傳送，另一端接收**。

| 手機 | PC | 說明 |
| --- | --- | --- |
| WiFi 傳送 | WiFi | 同一 WiFi，PC 填手機畫面的 IP（有加碼就貼完整 URL） |
| WiFi 接收 | WebCam（勾轉播） | 同一 WiFi，手機輸 `IP` 或 `IP＋空格＋1~6碼` |
| USB 共享傳送 | USB | 手機開 USB 數據共享插 PC，PC 按連線（自動找 RNDIS 閘道） |
| — | WebCam | PC 本機鏡頭，欄位填 `0/1`；畫面左右鏡像（照鏡子） |
| — | DPO2014B | 示波器 USB 直連 PC，PC 欄位填 `USB` |

手機按鍵同一排：`[USB 共享] [WiFi 傳送/接收] [關預覽] [凍結]`，四顆等高，下面兩行是 USB / WiFi 的 URL（預設空碼 `/v`）。傳送模式點 WiFi 那行改自家連線碼（留空=空碼，或 1~6 碼，對方照打就能看，附複製鈕）；接收模式點 WiFi 那行改對方：`IP` 直連，有約碼就 `IP＋空格＋碼`（如 `10.35.9.108 140023`），完整 URL 也能貼。接收報錯分三種：`HTTP 404`＝路徑不對、`連線逾時`＝IP 錯或防火牆、其他＝連不上。

PC 欄位：左上 `接收`/`傳送` 鈕切方向（接收＝`USB`/`WiFi`/`DPO2014B`，傳送＝`WebCam`＋勾轉播），再從 combo 選來源；`USB` 填對方 IP（自動找 RNDIS 閘道）；`WiFi` 填對方 IP（有加碼才貼完整 URL；欄位無 IP 按連線跳輸入框，預帶上次值）；`WebCam` 填 `0/1`；`DPO2014B` 填 `USB`。勾 `轉播` 按連線（或連線中勾上）多網卡跳選單挑 IP，選完記住；連線成功才記住欄位，下次開啟預帶上次值。

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
| `pyvisa` | 示波器抓圖（DPO 走 USBTMC） | 只缺示波器源，其他功能正常 |

手動裝的東西（pip 裝不到），缺了也能跑、只是降級：

1. **H.264 錄影 DLL**（錄影檔小 2–4 倍）：
   1. 下載 `openh264-2.5.0-win64.dll`（Cisco 官方：http://ciscobinary.openh264.org/openh264-2.5.0-win64.dll.bz2，解壓）
   2. 放到 `python.exe` 旁（或 `site-packages\cv2\` 旁，任一即可）
   3. 沒放自動退回 mp4v（檔較大）；console 出現 `Failed to load OpenH264` 就是缺它
2. **VISA runtime**（DPO2014B 才需要，二選一：NI-VISA 或 TekVISA；示波器附的光碟有，或 Tek/NI 官網下載）：
   1. DPO2014B：後面 USB device 口用 USB 線接 PC（走 USBTMC，裝置管理員會出現 `USB Test and Measurement Device (IVI)`）；PC 端切 `DPO2014B`，欄位填 `USB`（自動找示波器）→ 按連線
   2. 沒裝 VISA 按連線會顯示要裝 pyvisa+NI-VISA，其他來源不受影響
   3. DPO 截圖原生只有 480×234（面板物理解析度），會 2x 放大顯示/存檔；真要高清得改抓波形重畫（未做）

## 執行

```bat
python pc\mCam.py
```

手機 App 保持前景（亮屏保活）。USB 共享流程：手機開 USB 數據共享 → 手機 App 保持前景 → PC 按「連線」。

## 發版（手機 App）

`android\release_build.bat` 雙擊：自動產生簽名 keystore（只做一次，放 `android/` 本機、不進版控）→ `assembleRelease` → APK 存到 `android\release\mCam-v版本.apk`。需先裝 Android Studio（JDK/keytool/SDK 都用它的）。

## 連不上排查

1. 同一 WiFi 嗎？手機 IP 和 PC IP 前三段要一樣（`192.168.50.x`）。行動數據互連不到
2. Windows 防火牆擋 inbound（最常見）：手機瀏覽器開 `http://PC_IP:8080/i`，看不到 JSON 就是被擋 → 防火牆把 `python.exe` 放行（私人+公用）
3. 公司/公共 WiFi 可能有 AP 隔離（設備互不可見），換手機熱點或自家 WiFi
4. PC 勾轉播後顯示 `port 8080 被佔用`：port 被別的程式佔了
5. 手機切接收後 PC 顯示斷線：正常（手機 server 關了），手機切回傳送 PC 會自動重連
6. 手機 App 先關（或斷網）：PC 最多頓約 3 秒後顯示 `已斷線`，`中斷` 隨時可按，不會卡死（讀流有 3 秒超時）

## 備註

- `pc/shots/`、`pc/rec/`、`pc/mCam.json` 自動建立，不進版控
- 改名過的模式（`USB` / `WiFi` / `WebCam`，含舊名 `USB 共享` / `WiFi 接收端` / `WiFi 傳送端` / `USB webcam`）舊設定自動搬家，不用重填
- console 若出現 `Stream ends prematurely`：ffmpeg 讀手機串流的警告，無害，不影響預覽/錄影
