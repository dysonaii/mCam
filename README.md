# mCam

手機當相機，PC 預覽、截圖、錄影。含 Android 端（`android/`）與 PC 端（`pc/`，Python + PySimpleGUI）。

## 功能

- **三種訊源**：USB 共享（免 adb，自動找 RNDIS 閘道）、WiFi（照手機畫面改 IP）、USB webcam（填 0/1）
- **預覽**：最高解析度（webcam 要 1080p，不夠驅動自動 clamp）；斷線自動重連
- **截圖**：一律最高畫質 JPG，存 `pc/shots/`
- **錄影**：一律最高畫質 MP4（H.264），存 `pc/rec/`；畫面顯示錄影秒數
- **圖庫**：縮圖一覽；點一下選取（黃框），ctrl+點多選
  - 單選才能**放**（預覽）/**壓**（降級存 720p/480p）
  - **刪**可一次刪多個；刪檔不閃爍（隱藏不重建）

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

USB 共享流程：手機開 USB 數據共享 → 手機 App 保持前景 → PC 按「連線」（連不上會自動試 RNDIS 閘道 IP）。

## 備註

- `pc/shots/`、`pc/rec/` 自動建立，不進版控
- console 若出現 `Stream ends prematurely`：ffmpeg 讀手機串流的警告，無害，不影響預覽/錄影
