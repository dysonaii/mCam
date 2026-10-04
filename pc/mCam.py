"""mCam PC 端 — 預覽+截圖+錄影 (手機USB共享 + USB webcam + WiFi傳送給手機)."""
import ctypes
import json
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np
import PySimpleGUI as sg

MODES = {
    "USB 共享": "http://192.168.42.129:8080/video",
    "WiFi 接收端": "http://192.168.1.100:8080/video",
    "WiFi 傳送端": "0",
    "USB webcam": "0",
}
TX_PORT = 8080  # ponytail: 跟手機端同 port，手機接收照抄 IP 就能看，不另記
DOWN_SIZES = {"720p": 720, "480p": 480}  # ponytail: 只給事後降級用，存檔一律最高
BASE = Path(__file__).parent
SHOTS = BASE / "shots"
REC = BASE / "rec"
ICON = BASE / "mCam.ico"
CFG = BASE / "mCam.json"
SHOTS.mkdir(exist_ok=True)
REC.mkdir(exist_ok=True)
BLACK = cv2.imencode(".png", np.zeros((480, 640, 3), np.uint8))[1].tobytes()  # ponytail: 未連線/斷線黑屏佔位
_ovlbl = None
_ov_on = False


def set_black(window, msg: str | None) -> None:
    # ponytail: 黑屏+中央灰字只用一個 tk Label 蓋在 Image 上；中文字 tkinter 系統字型直接吃，不引 PIL
    global _ovlbl, _ov_on
    if msg:
        window["-IMG-"].update(data=BLACK)
        if _ovlbl is None:
            _ovlbl = tk.Label(window["-IMG-"].Widget, bg="black", fg="gray", font=("", 20))
        _ovlbl.config(text=msg)
        _ovlbl.place(relx=0.5, rely=0.5, anchor="center")
        _ov_on = True
    elif _ov_on:
        _ovlbl.place_forget()
        _ov_on = False


def load_cfg() -> dict:
    # ponytail: 每個 mode 各記一組 url，切換不丟；舊檔只有 url 就當該 mode 的，壞檔回預設
    # ponytail: 舊名 USB共享/WiFi/WiFi傳送順手搬到新名，免得改名吃掉使用者存的 IP
    _rename = {"USB共享": "USB 共享", "WiFi": "WiFi 接收端", "WiFi傳送": "WiFi 傳送端"}
    try:
        d = json.loads(CFG.read_text(encoding="utf-8"))
        m = d.get("mode", "USB 共享")
        m = _rename.get(m, m)
        m = m if m in MODES else "USB 共享"
        urls = dict(MODES)
        saved = {_rename.get(k, k): v for k, v in (d.get("urls") or {}).items() if _rename.get(k, k) in MODES and v}
        urls.update(saved)
        if d.get("url"):
            urls[m] = d["url"]
        return {"mode": m, "urls": urls}
    except Exception:
        return {"mode": "USB 共享", "urls": dict(MODES)}


def save_cfg(mode: str, urls: dict) -> None:
    try:
        CFG.write_text(json.dumps({"mode": mode, "urls": urls}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def open_cap(spec: str) -> cv2.VideoCapture:
    spec = spec.strip()
    if spec.isdigit():
        # ponytail: 解析度一律要最高；驅動會自動 clamp 到裝置上限，不用列舉
        cap = cv2.VideoCapture(int(spec), cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        return cap
    return cv2.VideoCapture(spec)


def rndis_gateway() -> str | None:
    # ponytail: RNDIS 網卡的閘道就是手機，各牌子網段不同(42.129/43.1/172.24.x)都靠它自動找；
    # 用 NextHop 而不用解析 ipconfig 文字，免得被中文語系編碼雷到
    try:
        ps = ("Get-NetAdapter | Where-Object {$_.InterfaceDescription -like '*Remote NDIS*'} "
              " | Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue"
              " | Select-Object -ExpandProperty NextHop")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=15).stdout
        m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", out)
        return m.group(1) if m else None
    except Exception:
        return None


def server_ok(url: str, timeout: float = 1.5) -> bool:
    # ponytail: 先用 /info 快探(1.5s)，不通就不叫 VideoCapture，UI 才不會卡死等 ffmpeg 超時
    try:
        base = url.strip().rsplit("/video", 1)[0]
        urllib.request.urlopen(base + "/info", timeout=timeout).read(256)
        return True
    except Exception:
        return False


def server_fps(url: str) -> float:
    try:
        base = url.strip().rsplit("/video", 1)[0]
        raw = urllib.request.urlopen(base + "/info", timeout=2).read(256).decode()
        fps = float(json.loads(raw).get("fps", 20))
        return fps if fps > 0 else 20.0
    except Exception:
        return 20.0


_tx_jpg: bytes | None = None
_tx_wh = (0, 0)
_tx_fps = 20.0
_tx_lock = threading.Lock()
_tx_srv: ThreadingHTTPServer | None = None


def pc_ip() -> str:
    # ponytail: 第一個非迴路 IPv4 就夠；區網直連用，不猜哪張卡
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def tx_url() -> str:
    # ponytail: 傳送端欄位顯示的是「手機要填的 URL」，真正的 cam index 另存 urls，不混一起
    return f"http://{pc_ip()}:{TX_PORT}/video"


def tx_push(frame) -> None:
    # ponytail: 主迴圈每幀壓一次 jpg 就夠，手機接收下一步直接拿 /video
    global _tx_jpg, _tx_wh
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if ok:
        with _tx_lock:
            _tx_jpg = buf.tobytes()
            _tx_wh = (frame.shape[1], frame.shape[0])


class _TxHandler(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        if self.path.startswith("/video"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            last = None
            while True:
                with _tx_lock:
                    jpg = _tx_jpg
                if jpg is None or jpg is last:
                    time.sleep(0.066)
                    continue
                last = jpg
                try:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                except Exception:
                    return
        elif self.path.startswith("/snapshot"):
            with _tx_lock:
                jpg = _tx_jpg
            if jpg is None:
                self.send_response(503)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.end_headers()
            try:
                self.wfile.write(jpg)
            except Exception:
                pass
        elif self.path.startswith("/info"):
            body = json.dumps({"w": _tx_wh[0], "h": _tx_wh[1], "fps": _tx_fps,
                               "facing": "pc", "ver": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass
        else:
            self.send_response(404)
            self.end_headers()


def tx_start(fps: float) -> bool:
    # ponytail: port 被佔就回 False，UI 顯示換 port？不做，8080 跟手機同 port 記一組就好
    global _tx_srv, _tx_fps
    if _tx_srv is not None:
        return True
    _tx_fps = fps
    try:
        _tx_srv = ThreadingHTTPServer(("0.0.0.0", TX_PORT), _TxHandler)
    except Exception:
        _tx_srv = None
        return False
    threading.Thread(target=_tx_srv.serve_forever, daemon=True).start()
    return True


def tx_stop() -> None:
    global _tx_srv
    if _tx_srv is not None:
        try:
            _tx_srv.shutdown()
            _tx_srv.server_close()
        except Exception:
            pass
        _tx_srv = None


def make_writer(path: Path, w: int, h: int, fps: float) -> cv2.VideoWriter:
    # ponytail: 沒 DLL 時 avc1 會假開啟(isOpened 照 True 卻寫廢檔)，有 DLL 才試它；檔名寫死，ffmpeg 只認 2.5.0(實測 2.6.0 改名會崩)
    dll = "openh264-2.5.0-win64.dll"
    has_h264 = (Path(sys.executable).parent / dll).exists() or (Path(cv2.__file__).parent / dll).exists()
    tags = ("avc1", "mp4v") if has_h264 else ("mp4v",)
    wr = cv2.VideoWriter()
    for tag in tags:
        wr.open(str(path), cv2.VideoWriter_fourcc(*tag), fps, (w, h))
        try:
            wr.set(cv2.VIDEOWRITER_PROP_QUALITY, 100)
        except Exception:
            pass
        if wr.isOpened():
            return wr
    return wr


def pick_reachable(spec: str, mode: str) -> str | None:
    # ponytail: 只探使用者填的；USB 才多試 RNDIS 閘道(各牌網段不同)，WiFi 同區網不猜
    cands = [spec]
    if mode == "USB 共享":
        gw = rndis_gateway()
        if gw and f"http://{gw}:" not in spec:
            cands.append(f"http://{gw}:8080/video")
    for c in cands:
        if server_ok(c):
            return c
    return None


def file_labels() -> list:
    # ponytail: 圖+影合一清單；時間戳檔名排序即時間序，不另存 metadata
    out = [f"[圖] {p.name}" for p in sorted(SHOTS.glob("*.jpg"), reverse=True)]
    return out + [f"[影] {p.name}" for p in sorted(REC.glob("*.mp4"), reverse=True)]


def label_to_path(label: str) -> Path:
    return (SHOTS if label.startswith("[圖]") else REC) / label[4:]


def shrink(frame, h: int):
    # ponytail: 只給事後降級用；存檔當下不縮
    if frame.shape[0] <= h:
        return frame
    w = int(frame.shape[1] * h / frame.shape[0])
    return cv2.resize(frame, (w, h))


def downgrade_file(path: Path, h: int) -> str:
    dst = path.parent / f"{path.stem}_{h}p{path.suffix}"
    if path.suffix.lower() == ".jpg":
        img = cv2.imread(str(path))
        if img is None:
            return ""
        cv2.imwrite(str(dst), shrink(img, h), [cv2.IMWRITE_JPEG_QUALITY, 90])
        return dst.name
    src = cv2.VideoCapture(str(path))
    ok, fr = src.read()
    if not ok:
        src.release()
        return ""
    w = int(fr.shape[1] * h / fr.shape[0]) if fr.shape[0] > h else fr.shape[1]
    hh = h if fr.shape[0] > h else fr.shape[0]
    fps = src.get(cv2.CAP_PROP_FPS) or 20.0
    wr = make_writer(dst, w, hh, fps)
    while ok:
        wr.write(fr if (w, hh) == (fr.shape[1], fr.shape[0]) else cv2.resize(fr, (w, hh)))
        ok, fr = src.read()
    src.release()
    wr.release()
    return dst.name


def ask_downgrade(name: str) -> int | None:
    win = sg.Window("降級", [[sg.Text(f"{name} 壓成？")],
                             [sg.Button("720p"), sg.Button("480p"), sg.Button("取消")]],
                    modal=True)
    ev, _ = win.read()
    win.close()
    return DOWN_SIZES.get(ev)


def thumb_bytes(path: Path, w: int = 160) -> bytes | None:
    if path.suffix.lower() == ".jpg":
        img = cv2.imread(str(path))
    else:
        c = cv2.VideoCapture(str(path))  # ponytail: 只抓第一幀就丟，片多也只是開慢一點
        ok, img = c.read()
        c.release()
        if not ok:
            return None
    if img is None:
        return None
    if img.shape[1] > w:
        img = cv2.resize(img, (w, int(img.shape[0] * w / img.shape[1])))
    return cv2.imencode(".png", img)[1].tobytes()


def play_file(path: Path) -> None:
    img = thumb_bytes(path, 640)
    if img is None:
        return
    if path.suffix.lower() == ".jpg":
        win = sg.Window(path.name, [[sg.Image(data=img)]], modal=True)
        win.read()
        win.close()
        return
    cap = cv2.VideoCapture(str(path))
    win = sg.Window(path.name, [[sg.Image(key="-P-", data=img)],
                                [sg.Button("暫停", key="-PP-"), sg.Button("關閉")]],
                    modal=True, finalize=True)
    paused = False
    while True:
        ev, _ = win.read(timeout=33)
        if ev in (sg.WIN_CLOSED, "關閉"):
            break
        if ev == "-PP-":
            paused = not paused
            win["-PP-"].update("繼續" if paused else "暫停")
        if paused:
            continue
        ok, fr = cap.read()
        if not ok:  # ponytail: 播完就關，不做進度條/重播
            break
        if fr.shape[1] > 640:
            fr = cv2.resize(fr, (640, int(fr.shape[0] * 640 / fr.shape[1])))
        win["-P-"].update(data=cv2.imencode(".png", fr)[1].tobytes())
    cap.release()
    win.close()


def ctrl_down() -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)


def gallery() -> None:
    # ponytail: 全程同一視窗(建/選/刪都不重建，重建會閃)；刪檔只隱藏該格；Column 沒 bg update，直接染 tk
    # ponytail: ctrl+點=多選(PSG 按鈕事件不帶修飾鍵，用 GetAsyncKeyState 抓)；多選時放/壓 disabled
    hl = "#FFD24D"
    bg, fg = sg.theme_background_color(), sg.theme_text_color()
    sel: set[str] = set()
    alive: set[str] = set()
    cells = []
    for lb in file_labels():
        tb = thumb_bytes(label_to_path(lb))
        if tb is None:
            continue
        alive.add(lb)
        cells.append(sg.Column([[sg.Button(image_data=tb, key=f"V:{lb}", border_width=0, pad=(8, 8))],
                                [sg.Text(lb[4:][:14], size=(14, 1), key=f"T:{lb}")]],
                               key=f"C:{lb}"))
    rows = [cells[i:i + 4] for i in range(0, len(cells), 4)]
    has_empty = bool(rows)
    rows.append([sg.Text("還沒有檔案", key="EMPTY")])
    rows.append([sg.Button("放", key="PLAY", disabled=True),
                 sg.Button("壓", key="DOWN", disabled=True),
                 sg.Button("刪", key="DEL", disabled=True)])
    win = sg.Window("圖庫", rows, modal=True, finalize=True)
    if has_empty:
        win["EMPTY"].update(visible=False)  # ponytail: 建時可見再藏(藏會存 pack 設定)，全刪光才叫得回來
    for lb in alive:
        win[f"V:{lb}"].bind("<Double-Button-1>", "+DBL")  # ponytail: 點兩下直接放；第一下選取無害，不用消抖 timer

    def paint(lb: str, color: str) -> None:
        w = win[f"C:{lb}"].Widget
        w.configure(background=color)
        for k in w.winfo_children():  # ponytail: Column 內外兩層 Frame 都要染
            k.configure(background=color)
        win[f"T:{lb}"].update(text_color=hl if color == hl else fg)

    def sync_btns() -> None:
        n = len(sel)
        win["PLAY"].update(disabled=n != 1)
        win["DOWN"].update(disabled=n != 1)
        win["DEL"].update(disabled=n == 0)

    while True:
        ev, _ = win.read()
        if ev is None or ev == sg.WIN_CLOSED:
            break
        dbl = ev.endswith("+DBL")  # ponytail: 點兩下直接放；第一下選取無害，不用消抖 timer
        if dbl:
            ev = ev[:-4]
        if ev.startswith("V:"):
            lb = ev[2:]
            if ctrl_down() and not dbl:
                if lb in sel:
                    sel.discard(lb)
                    paint(lb, bg)
                else:
                    sel.add(lb)
                    paint(lb, hl)
            else:
                for s in sel - {lb}:
                    paint(s, bg)
                sel = {lb}
                paint(lb, hl)
            sync_btns()
            if dbl:
                play_file(label_to_path(lb))
            continue
        lab = next(iter(sel))  # ponytail: 放/壓被 disabled 擋，這裡必單選
        if ev == "PLAY":
            play_file(label_to_path(lab))
        elif ev == "DOWN":
            p = label_to_path(lab)
            h = ask_downgrade(p.name)
            if h:
                got = downgrade_file(p, h)
                sg.popup_quick_message(f"已存 {got}" if got else "失敗", auto_close_duration=1.5)
        elif ev == "DEL":
            q = f"確定刪除 {label_to_path(lab).name}？" if len(sel) == 1 else f"一次刪除 {len(sel)} 個檔案？"
            if sg.popup_yes_no(q) == "Yes":
                for s in sel:
                    label_to_path(s).unlink(missing_ok=True)
                    win[f"C:{s}"].update(visible=False)  # ponytail: 隱藏不重建，重建會閃
                alive -= sel
                sel = set()
                if not alive and has_empty:
                    win["EMPTY"].update(visible=True)
                sync_btns()
    win.close()


def main() -> None:
    cfg = load_cfg()
    urls = cfg["urls"]
    cur_mode = cfg["mode"]
    layout = [
        [sg.Image(data=BLACK, key="-IMG-")],
        [sg.Combo(list(MODES), default_value=cur_mode, key="-MODE-", readonly=True,
                  enable_events=True, size=(14, 1)),
         sg.Input(tx_url() if cur_mode == "WiFi 傳送端" else urls[cur_mode], key="-URL-", size=(40, 1))],
        [sg.Button("連線"), sg.Button("中斷"),
         sg.Button("截圖"), sg.Button("●錄影", key="-REC-"),
         sg.Button("圖庫")],
        [sg.Text("USB /WiFi 填IP；webcam/傳送填0/1", key="-STATUS-", size=(60, 1))],
    ]
    window = sg.Window("mCam", layout, finalize=True)
    window.set_icon(str(ICON))  # ponytail: 建構式吃 icon 不會套用(實測)，要事後 set_icon
    cap: cv2.VideoCapture | None = None
    writer: cv2.VideoWriter | None = None
    frame = None
    fails = 0
    rec_t0, rec_last, rec_name, src_fps = 0.0, -1, "", 20.0
    tx_on = False

    while True:
        event, values = window.read(timeout=20)
        if event in (sg.WIN_CLOSED, "Exit"):
            break
        if event == "-MODE-":
            # ponytail: 離開前先把舊 mode 的手填值存下，切過去帶該 mode 上次的值
            # ponytail: 傳送端欄位是顯示用 URL 不存；切進傳送端自動帶本機 wifi IP
            if cur_mode != "WiFi 傳送端":
                urls[cur_mode] = window["-URL-"].get()
            cur_mode = values["-MODE-"]
            window["-URL-"].update(tx_url() if cur_mode == "WiFi 傳送端" else urls[cur_mode])
            save_cfg(cur_mode, urls)
        if event == "連線":
            tx_stop()
            tx_on = False
            if cap is not None:
                cap.release()
                cap = None
            spec = window["-URL-"].get()
            if values["-MODE-"] == "WiFi 傳送端":
                # ponytail: 欄位是顯示用 URL，手填 0/1 才當 cam index 收下；傳送端永不探測，壞值回 0
                typed = spec.strip()
                spec = typed if typed.isdigit() else urls.get("WiFi 傳送端", "0")
                if not spec.strip().isdigit():
                    spec = "0"
            if not spec.strip().isdigit():
                window["-STATUS-"].update("連線中(快探 server)...")
                window.refresh()
                hit = pick_reachable(spec, values["-MODE-"])
                if hit is None:
                    window["-STATUS-"].update("連不上：USB共享開了嗎？手機 App 在前景？或改填手機畫面上的 IP")
                    set_black(window, "已斷線")
                    continue
                if hit != spec:
                    window["-URL-"].update(hit)  # ponytail: 自動找到就寫回
                    spec = hit
            cap = open_cap(spec)
            fails = 0
            src_fps = 20.0 if spec.strip().isdigit() else server_fps(spec)
            if values["-MODE-"] != "WiFi 傳送端" or spec.strip().isdigit():
                urls[values["-MODE-"]] = spec
                save_cfg(values["-MODE-"], urls)
            window["-STATUS-"].update("連線中..." if cap.isOpened() else "連不上：webcam被佔用？換 0/1 試試")
            if values["-MODE-"] == "WiFi 傳送端" and cap.isOpened():
                # ponytail: 傳送=本地 webcam 邊播邊 serve，手機接收端填 STATUS 上的 IP 即可
                fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
                src_fps = fps if fps > 0 else 20.0
                if tx_start(src_fps):
                    tx_on = True
                    window["-URL-"].update(tx_url())
                    window["-STATUS-"].update(f"傳送中 {tx_url()}（手機切WIFI接收填此 IP）")
                else:
                    window["-STATUS-"].update(f"傳送失敗：port {TX_PORT} 被佔用")
        elif event == "中斷":
            tx_stop()
            tx_on = False
            if cap is not None:
                cap.release()
                cap = None
            set_black(window, "已斷線")
            window["-STATUS-"].update("已中斷")
        elif event == "截圖" and frame is not None:
            p = SHOTS / f"IMG_{datetime.now():%Y%m%d_%H%M%S}.jpg"
            cv2.imwrite(str(p), frame, [cv2.IMWRITE_JPEG_QUALITY, 100])  # ponytail: 截圖一律最高，不留選項
            window["-STATUS-"].update(f"已存 {p.name}")
        elif event == "圖庫":
            gallery()
        elif event == "-REC-":
            if writer is None:
                if frame is None:
                    continue
                h, w = frame.shape[:2]
                p = REC / f"VID_{datetime.now():%Y%m%d_%H%M%S}.mp4"
                writer = make_writer(p, w, h, src_fps)
                rec_t0, rec_last, rec_name = time.monotonic(), -1, p.name
                window["-REC-"].update("■停止")
                window["-STATUS-"].update(f"錄影中 {p.name}")
            else:
                writer.release()
                writer = None
                window["-REC-"].update("●錄影")
                window["-STATUS-"].update("錄影已存")

        if cap is not None and cap.isOpened():
            ok, frame = cap.read()
            now = time.monotonic()
            if ok:
                fails = 0
                set_black(window, None)  # ponytail: 有幀就藏灰字，不重刷圖免閃
                if tx_on:
                    tx_push(frame)
                if writer is not None:
                    writer.write(frame)
                    s = int(now - rec_t0)
                    if s != rec_last:
                        rec_last = s
                        window["-STATUS-"].update(f"錄影中 {rec_name} {s // 60:02d}:{s % 60:02d}")
                small = frame if frame.shape[1] <= 640 else cv2.resize(
                    frame, (640, int(frame.shape[0] * 640 / frame.shape[1])))
                window["-IMG-"].update(data=cv2.imencode(".png", small)[1].tobytes())
            else:
                fails += 1
                # ponytail: 連壞 ~30 次(約6秒)才重連一次；server 沒回來就等下輪，不狂刷
                if fails >= 30:
                    fails = 0
                    set_black(window, "已斷線")  # ponytail: 斷線先黑屏，有幀回來自動蓋掉
                    spec_now = window["-URL-"].get()
                    if spec_now.strip().isdigit() or server_ok(spec_now):
                        window["-STATUS-"].update("重連中...")
                        window.refresh()
                        cap.release()
                        cap = open_cap(spec_now)
                        if not cap.isOpened():
                            window["-STATUS-"].update("斷線：重連失敗，檢查手機/線")
                    else:
                        window["-STATUS-"].update("斷線重試中...")

    tx_stop()
    if writer is not None:
        writer.release()
    if cap is not None:
        cap.release()
    try:
        if window["-MODE-"].get() != "WiFi 傳送端":
            urls[cur_mode] = window["-URL-"].get()
        save_cfg(window["-MODE-"].get(), urls)
    except Exception:
        pass
    window.close()


if __name__ == "__main__":
    main()
