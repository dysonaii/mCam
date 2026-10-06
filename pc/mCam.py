"""mCam PC 端 — 預覽+截圖+錄影 (手機USB共享 + WebCam + 轉播給手機)."""
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
    "USB": "http://192.168.42.129:8080/v",
    "WiFi": "http://192.168.1.100:8080/v",
    "WebCam": "0",
    "DPO2014B": "USB",
    #"TDS3014B": "192.168.1.60",
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
    # ponytail: 新名 USB/WiFi/WebCam，舊檔所有寫法順手搬家，免得改名吃掉存的 IP
    _rename = {"USB共享": "USB", "USB 共享": "USB", "WiFi": "WiFi",
               "WiFi 接收端": "WiFi", "WiFi傳送": "WebCam", "WiFi 傳送端": "WebCam",
               "USB webcam": "WebCam"}
    try:
        d = json.loads(CFG.read_text(encoding="utf-8"))
        m = d.get("mode", "USB")
        m = _rename.get(m, m)
        m = m if m in MODES else "USB"
        urls = dict(MODES)
        saved = {_rename.get(k, k): v for k, v in (d.get("urls") or {}).items() if _rename.get(k, k) in MODES and v}
        urls.update(saved)
        if d.get("url"):
            urls[m] = d["url"]
        return {"mode": m, "urls": urls, "tx_ip": d.get("tx_ip")}
    except Exception:
        return {"mode": "USB", "urls": dict(MODES), "tx_ip": None}


def save_cfg(mode: str, urls: dict, tx_ip=None) -> None:
    try:
        # ponytail: mode 非法就不寫檔，免得 null 蓋掉上次記憶，下次開回預設
        if mode not in MODES:
            return
        CFG.write_text(json.dumps({"mode": mode, "urls": urls, "tx_ip": tx_ip}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def mode_url(urls: dict, mode: str) -> str:
    # ponytail: TDS 還沒設過 IP 就帶本機 IP 當起點（同網段，改尾段即可）；設過就用設過的
    v = urls.get(mode, MODES.get(mode, ""))
    if mode == "TDS3014B" and v == MODES["TDS3014B"]:
        v = pc_ip()
    return v
    try:
        CFG.write_text(json.dumps({"mode": mode, "urls": urls}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


TEK_MODES = ("DPO2014B", "TDS3014B")  # ponytail: 示波器兩台走同一套，訊息/提示不用寫兩份


def tek_ip(spec: str) -> str | None:
    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", spec)
    return m.group(1) if m else None


def tek_visa_resource(spec: str) -> str | None:
    # ponytail: USB 走 VISA(USBTMC)；欄位填 USB/auto 就自動找第一台 Tek(0x0699)，填完整 resource 就直用
    s = spec.strip()
    if "::" in s:
        return s
    try:
        import pyvisa
        rcs = pyvisa.ResourceManager().list_resources()
    except Exception:
        return None
    usb = [r for r in rcs if "USB" in r.upper()]
    if not usb:
        return None
    for r in usb:
        if "0X0699" in r.upper():
            return r
    return usb[0]


def tek_hint(mode: str) -> str:
    # ponytail: 範例 IP 用本機同網段，猜中機率高；猜不中就去示波器 Utility>I/O 看
    if mode == "TDS3014B":
        return f"TDS使用LAN，填示波器IP(如{pc_ip().rsplit('.', 1)[0]}.60)"
    return "DPO使用USB，欄位填USB"


def tek_probe(spec: str, mode: str) -> bool:
    # ponytail: DPO只看 VISA 有沒有 USB 貨；TDS先探 VXI-11 portmapper(111)，1.5s 不通就不叫 VISA，UI 不凍結
    if mode == "TDS3014B":
        host = tek_ip(spec)
        if not host:
            return False
        try:
            with socket.create_connection((host, 111), timeout=1.5):
                return True
        except Exception:
            return False
    if tek_ip(spec):
        return False
    return tek_visa_resource(spec) is not None


def tek_png(raw: bytes):
    # ponytail: 先剝 IEEE488.2 binary block 頭(#<x><len>)再 imdecode(自動判 PNG/BMP)；剝失敗才用 PNG 頭尾硬切
    data = raw
    if raw[:1] == b"#" and raw[1:2].isdigit():
        n = int(raw[1:2])
        if raw[2:2 + n].isdigit():
            ln = int(raw[2:2 + n] or 0)
            data = raw[2 + n:2 + n + ln]
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is not None:
        return img
    i, j = raw.find(b"\x89PNG"), raw.find(b"IEND")
    if i >= 0 and j >= 0:
        return cv2.imdecode(np.frombuffer(raw[i:j + 8], np.uint8), cv2.IMREAD_COLOR)
    return None


class TekCap:
    # ponytail: 介面跟 cv2.VideoCapture 對齊(read/isOpened/release)，主迴圈不用分支；
    # 示波器一張要幾秒，背景 thread 慢慢抓，主迴圈拿最新幀即可，不阻塞預覽
    def __init__(self, spec: str, model: str = "dpo"):
        self._frame = None
        self._ok = False
        self._run = True
        self._inst = None
        self._err = ""
        try:
            import pyvisa
            if model == "tds":
                # ponytail: TDS3000 只認舊指令集；PORT 要指明 ETHERNET（新系列才會自動）
                host = tek_ip(spec)
                inst = pyvisa.ResourceManager().open_resource(f"TCPIP::{host}::INSTR")
                inst.timeout = 20000  # ponytail: 10Base-T+老 CPU，截圖慢，timeout 給寬
                inst.write("HARDCOPY:FORMAT PNG")
                inst.write("HARDCOPY:PORT ETHERNET")
            else:
                if tek_ip(spec):
                    self._err = "DPO使用USB，欄位填USB"
                    return  # ponytail: DPO 使用 USB，填 IP 直接拒，免得靜默連到別台
                res = tek_visa_resource(spec)
                inst = pyvisa.ResourceManager().open_resource(res)
                inst.timeout = 8000
                inst.write("SAVe:IMAGe:FILEFormat PNG")
            self._inst = inst
            self._ok = True
        except Exception as e:
            self._err = str(e)  # ponytail: 開不起來的原因留著，UI 直接顯示，不用猜
            self._run = False
            return
        threading.Thread(target=self._poll, daemon=True).start()

    def _grab(self):
        self._inst.write("HARDCopy STARt")
        return tek_png(bytes(self._inst.read_raw()))

    def _poll(self):
        while self._run:
            try:
                img = self._grab()
            except Exception:
                time.sleep(2)
                continue
            if img is not None:
                if img.shape[1] < 640:
                    # ponytail: DPO 原生 480x234 才 2x 放大；TDS 640x480 不動
                    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                self._frame = img
            time.sleep(1)

    def isOpened(self) -> bool:
        return self._ok

    def read(self):
        if self._frame is not None:
            return True, self._frame.copy()
        time.sleep(0.1)
        return False, None

    def release(self) -> None:
        self._run = False
        try:
            if self._inst is not None:
                self._inst.close()
        except Exception:
            pass

    def get(self, _id) -> float:
        return 0.0


def open_cap(spec: str, mode: str = ""):
    spec = spec.strip()
    if mode == "DPO2014B":
        return TekCap(spec, "dpo")
    if mode == "TDS3014B":
        return TekCap(spec, "tds")
    if spec.isdigit():
        # ponytail: 解析度一律要最高；驅動會自動 clamp 到裝置上限，不用列舉
        cap = cv2.VideoCapture(int(spec), cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        return cap
    # ponytail: 手機先關會剩半開連線(不斷也不送資料)，ffmpeg 預設等到天荒地老、
    # UI 卡到中斷都按不了；3s 超時逼 read 回來走斷線重連
    return cv2.VideoCapture(spec, cv2.CAP_FFMPEG,
                            [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000,
                             cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000])


def rndis_gateway() -> str | None:
    # ponytail: USB 流程不改，RNDIS 閘道就是手機，自動找免手填；WiFi 填IP，加碼才貼URL
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


def info_url(url: str) -> str:
    # ponytail: /v/token→/i/token(WiFi)，尾端 /v→/i(USB 無 token)；舊 /video 相容
    u = url.strip()
    if "/v/" in u:
        return u.replace("/v/", "/i/", 1)
    if u.endswith("/v"):
        return u[:-2] + "/i"
    return u.rsplit("/video", 1)[0] + "/info"


def server_ok(url: str, timeout: float = 1.5) -> bool:
    # ponytail: 先用 /i 快探(1.5s)，不通就不叫 VideoCapture，UI 才不會卡死等 ffmpeg 超時
    try:
        urllib.request.urlopen(info_url(url), timeout=timeout).read(256)
        return True
    except Exception:
        return False


def server_fps(url: str) -> float:
    try:
        raw = urllib.request.urlopen(info_url(url), timeout=2).read(256).decode()
        fps = float(json.loads(raw).get("fps", 20))
        return fps if fps > 0 else 20.0
    except Exception:
        return 20.0


_tx_jpg: bytes | None = None
_tx_wh = (0, 0)
_tx_fps = 20.0
_tx_lock = threading.Lock()
_tx_srv: ThreadingHTTPServer | None = None


def _ip_rank(ip: str) -> int:
    # ponytail: 私網優先(192.168/10/172.16-31)，公網最後；pc_ip 和選單共用同一套
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("10."):
        return 1
    if re.match(r"172\.(1[6-9]|2\d|3[01])\.", ip):
        return 2
    return 3


def pc_ip() -> str:
    # ponytail: 只要 IPv4(不用 IPv6，URL 不用加括號)；多網卡時私網優先，不只看預設路由
    cands: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        cands.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and ip not in cands:
                cands.append(ip)
    except Exception:
        pass
    cands = [c for c in cands if c and not c.startswith("127.") and "." in c and ":" not in c]
    cands.sort(key=_ip_rank)
    return cands[0] if cands else "127.0.0.1"


_tx_ip: str | None = None


def lan_ips() -> list:
    # ponytail: 轉播選單用，(ip, 網卡名) 照私網優先排；powershell 掛了回空，外面退回自動
    try:
        ps = ("Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | ForEach-Object {"
              " $d=$_.InterfaceDescription; $i=$_.ifIndex;"
              " Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 -ErrorAction SilentlyContinue"
              " | ForEach-Object { \"$i|$d|$($_.IPAddress)\" } }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=15).stdout
        seen: dict[str, str] = {}
        for line in out.splitlines():
            p = line.split("|")
            if len(p) == 3:
                ip = p[2].strip()
                if ip and not ip.startswith("127.") and ip not in seen:
                    seen[ip] = p[1].strip()
        return sorted(seen.items(), key=lambda kv: _ip_rank(kv[0]))
    except Exception:
        return []


def pick_tx_ip(force: bool = False) -> str | None:
    # ponytail: 單張靜默自動，多張跳選單；force=空 IP 按連線時必跳選單(單張也選)
    global _tx_ip
    cands = lan_ips()
    if not force and _tx_ip and any(ip == _tx_ip for ip, _ in cands):
        return _tx_ip  # ponytail: 上次選的還在，不打擾；force(框被清空)則重選
    if not force and len(cands) <= 1:
        _tx_ip = cands[0][0] if cands else None
        return _tx_ip
    if not cands:
        cands = [(pc_ip(), "本機(IP 列表抓不到)")]
    opts = [f"{desc} — {ip}" for ip, desc in cands]
    cur = [o for o in opts if _tx_ip and o.endswith(_tx_ip)]
    lay = [[sg.Text("轉播用哪個 IP？(手機跟這台同網才連得到)")],
           [sg.Listbox(opts, size=(56, min(len(opts), 6)), key="-IP-",
                       default_values=cur or opts[:1])],
           [sg.Button("確定"), sg.Button("自動"), sg.Button("取消")]]
    w = sg.Window("選擇轉播 IP", lay, modal=True, finalize=True)
    while True:
        ev, vals = w.read()
        if ev in (sg.WIN_CLOSED, "取消"):
            break
        if ev == "自動":
            _tx_ip = None
            break
        if ev == "確定":
            sel = vals["-IP-"] or opts[:1]
            _tx_ip = sel[0].rsplit("—", 1)[1].strip()
            break
    w.close()
    return _tx_ip


def tx_url() -> str:
    # ponytail: 轉播勾上顯示的「手機要填的 URL」，寫在 checkbox 本體上
    return f"http://{_tx_ip or pc_ip()}:{TX_PORT}/v"


def ask_rx_url(last: str) -> str | None:
    # ponytail: 接收端空 IP 才跳輸入框，預帶上次記憶值；IP 直轉 URL，有碼貼完整 URL
    tip = sg.popup_get_text("手機 IP？(空碼填 IP，有加碼貼完整 URL)", title="輸入手機 IP",
                            default_text=tek_ip(last or "") or "")
    if not tip or not tip.strip():
        return None
    tip = tip.strip()
    if "://" in tip:
        return tip
    p = tip.split()
    if len(p) == 2 and tek_ip(p[0]) and p[1].isdigit():
        return f"http://{tek_ip(p[0])}:8080/v/{p[1]}"
    if tek_ip(tip):
        return f"http://{tek_ip(tip)}:8080/v"
    return None


def tx_push(frame) -> None:
    # ponytail: 主迴圈每幀壓一次 jpg 就夠，手機接收下一步直接拿 /v 串流
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
        # ponytail: /v 空碼或自加 1~6 碼都放行，7 碼以上當沒這頁
        if self.path == "/v" or re.fullmatch(r"/v/\d{1,6}", self.path or ""):
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
        elif self.path == "/s" or re.fullmatch(r"/s/\d{1,6}", self.path or ""):
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
        elif self.path == "/i" or re.fullmatch(r"/i/\d{1,6}", self.path or ""):
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
    # ponytail: 先探框裡的字；USB 才多試 RNDIS 閘道，WiFi 同區網不猜
    if mode in TEK_MODES:
        return spec if tek_probe(spec, mode) else None
    cands = [spec]
    if mode == "USB":
        gw = rndis_gateway()
        if gw and f"http://{gw}:" not in spec:
            cands.append(f"http://{gw}:8080/v")
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
        full = cv2.imread(str(path))
        if full is None:
            return
        h0, w0 = full.shape[:2]
        base = min(1.0, 900 / w0, 700 / h0)
        z = 1.0

        def render():
            w, h = max(1, int(w0 * base * z)), max(1, int(h0 * base * z))
            sm = full if (w, h) == (w0, h0) else cv2.resize(
                full, (w, h), interpolation=cv2.INTER_CUBIC if z >= 1 else cv2.INTER_AREA)
            return cv2.imencode(".png", sm)[1].tobytes()

        win = sg.Window(path.name, [[sg.Image(data=render(), key="-P-")]], modal=True, finalize=True)

        def wheel(ev):
            # ponytail: PSG 事件拿不到 delta，直接綁 tk；同 thread 回呼，update 安全
            nonlocal z
            z = min(8.0, max(0.2, z * (1.25 if ev.delta > 0 else 0.8)))
            win["-P-"].update(data=render())

        win["-P-"].Widget.bind("<MouseWheel>", wheel)
        win.read()
        win.close()
        return
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0  # ponytail: 秒數用幀數/fps 算，不另存 metadata
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    dur = n / fps if fps > 0 and n > 0 else 0
    fmt = lambda s: f"{int(s) // 60:02d}:{int(s) % 60:02d}"
    win = sg.Window(path.name, [[sg.Image(key="-P-", data=img)],
                                [sg.Button("暫停", key="-PP-"), sg.Button("關閉"),
                                 sg.Text(f"00:00/{fmt(dur)}" if dur else "00:00", key="-T-")]],
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
        el = cap.get(cv2.CAP_PROP_POS_FRAMES) / fps if fps > 0 else 0
        win["-T-"].update(f"{fmt(el)}/{fmt(dur)}" if dur else fmt(el))
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
    thumbs: dict[str, bytes] = {}
    for lb in file_labels():
        tb = thumb_bytes(label_to_path(lb))
        if tb is not None:
            thumbs[lb] = tb
    order: list[str] = list(thumbs)  # ponytail: 位置鍵(V:0..)綁圖，刪檔只換圖不動格，重排又不閃
    cells = []
    for i, lb in enumerate(order):
        cells.append(sg.Column([[sg.Button(image_data=thumbs[lb], key=f"V:{i}", border_width=0, pad=(8, 8))],
                                [sg.Text(lb[4:][:14], size=(14, 1), key=f"T:{i}")]],
                               key=f"C:{i}"))
    rows = [cells[i:i + 5] for i in range(0, len(cells), 5)]
    has_empty = bool(rows)
    rows.append([sg.Text("還沒有檔案", key="EMPTY")])
    layout = [[sg.Column(rows, scrollable=True, vertical_scroll_only=True,
                         size=(950, 500), key="-GRID-")],
              [sg.Button("放", key="PLAY", disabled=True),
               sg.Button("壓", key="DOWN", disabled=True),
               sg.Button("刪", key="DEL", disabled=True),
               sg.Button("全選", key="ALL"),
               sg.Button("全不選", key="NONE")]]
    win = sg.Window("圖庫", layout, modal=True, finalize=True)
    win.bind("<Control-a>", "ALL")  # ponytail: 共用 ALL 鍵，不另分支
    win.bind("<Control-A>", "ALL")
    if has_empty:
        win["EMPTY"].update(visible=False)  # ponytail: 建時可見再藏(藏會存 pack 設定)，全刪光才叫得回來
    for i in range(len(order)):
        win[f"V:{i}"].bind("<Double-Button-1>", "+DBL")  # ponytail: 點兩下直接放；第一下選取無害，不用消抖 timer

    def idx_of(lb: str) -> int:
        return order.index(lb)

    def paint(lb: str, color: str) -> None:
        w = win[f"C:{idx_of(lb)}"].Widget
        w.configure(background=color)
        for k in w.winfo_children():  # ponytail: Column 內外兩層 Frame 都要染
            k.configure(background=color)
        win[f"T:{idx_of(lb)}"].update(text_color=hl if color == hl else fg)

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
            lb = order[int(ev[2:])]
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
        lab = next(iter(sel), "")  # ponytail: 放/壓被 disabled 擋，這裡必單選；ALL/NONE 經此不取也無害
        if ev == "ALL":
            sel = set(order)
            for s in sel:
                paint(s, hl)
            sync_btns()
        elif ev == "NONE":
            for s in sel:
                paint(s, bg)
            sel = set()
            sync_btns()
        elif ev == "PLAY":
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
                    del thumbs[s]
                order = [lb for lb in order if lb in thumbs]
                for i in range(len(cells)):  # ponytail: 前段換圖、尾段隱藏，格子不動所以不閃
                    if i < len(order):
                        win[f"V:{i}"].update(image_data=thumbs[order[i]])
                        win[f"T:{i}"].update(order[i][4:][:14])
                        win[f"C:{i}"].update(visible=True)
                        w = win[f"C:{i}"].Widget
                        w.configure(background=bg)
                        for k in w.winfo_children():
                            k.configure(background=bg)
                        win[f"T:{i}"].update(text_color=fg)
                    else:
                        win[f"C:{i}"].update(visible=False)
                sel = set()
                if not order and has_empty:
                    win["EMPTY"].update(visible=True)
                sync_btns()
    win.close()


def main() -> None:
    global _tx_ip
    cfg = load_cfg()
    urls = cfg["urls"]
    cur_mode = cfg["mode"]
    _tx_ip = cfg.get("tx_ip")
    if _tx_ip and not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", _tx_ip):
        _tx_ip = None
    if _tx_ip:  # ponytail: 存的 IP 換網卡就沒了，有列表才驗，沒列表(斷網)就留著
        _ips = [ip for ip, _ in lan_ips()]
        if _ips and _tx_ip not in _ips:
            _tx_ip = None
    layout = [
        [sg.Image(data=BLACK, key="-IMG-")],
        [sg.Combo(list(MODES), default_value=cur_mode, key="-MODE-", readonly=True,
                  enable_events=True, size=(14, 1)),
         sg.Input(urls[cur_mode], key="-URL-", size=(40, 1))],
        [sg.Button("連線"), sg.Button("中斷"),
         sg.Button("截圖"), sg.Button("●錄影", key="-REC-"),
         sg.Button("圖庫"), sg.Button("開資料夾"),
           sg.Checkbox("轉播", default=False, key="-TX-", enable_events=True,
                       tooltip="勾=區網 browser 可開勾選框上的轉播 URL 看")],
         [sg.Text("USB/WiFi填IP，加碼貼URL；WebCam填0/1；DPO填USB；TDS填IP", key="-STATUS-", size=(60, 1))],
    ]
    window = sg.Window("mCam", layout, finalize=True)
    window.set_icon(str(ICON))  # ponytail: 建構式吃 icon 不會套用(實測)，要事後 set_icon

    def tx_show() -> None:
        # ponytail: IP 直接寫在 checkbox 本體上，不另加一行
        window["-TX-"].update(text=f"轉播 {tx_url()}")

    def tx_hide() -> None:
        window["-TX-"].update(text="轉播")

    def tx_copy(_ev=None) -> None:
        # ponytail: checkbox 上右鍵點一下即複製轉播 URL
        try:
            window.TKroot.clipboard_clear()
            window.TKroot.clipboard_append(tx_url())
            window["-STATUS-"].update(f"已複製 {tx_url()}")
        except Exception:
            pass

    window["-TX-"].Widget.bind("<Button-3>", tx_copy)
    if cur_mode in TEK_MODES:
        window["-STATUS-"].update(tek_hint(cur_mode))
    cap = None
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
            urls[cur_mode] = window["-URL-"].get()
            cur_mode = values["-MODE-"]
            window["-URL-"].update(urls[cur_mode])
            if cur_mode in TEK_MODES:
                window["-STATUS-"].update(tek_hint(cur_mode))
            save_cfg(cur_mode, urls, _tx_ip)
        if event == "連線":
            tx_stop()
            tx_on = False
            tx_hide()
            if cap is not None:
                cap.release()
                cap = None
            spec = window["-URL-"].get()
            mode_now = values["-MODE-"]
            if mode_now == "WiFi" and not tek_ip(spec):
                hit_ip = ask_rx_url(spec or urls.get(mode_now, ""))
                if not hit_ip:
                    window["-STATUS-"].update("未填 IP，已取消連線")
                    continue
                spec = hit_ip
                window["-URL-"].update(spec)
            if not spec.strip().isdigit():
                window["-STATUS-"].update("連線中(快探 server)...")
                window.refresh()
                hit = pick_reachable(spec, mode_now)
                if hit is None:
                    if mode_now == "DPO2014B":
                        msg = "連不上：USB線接了嗎？要裝 pyvisa+NI-VISA(見 README)"
                    elif mode_now == "TDS3014B":
                        msg = "連不上：TDS的IP對嗎？同網段嗎？(Utility>I/O 看)"
                    else:
                        msg = ("連不上：USB共享開了嗎？手機 App 在前景？"
                               if mode_now == "USB" else
                               "連不上：手機 App 在前景？WiFi 填IP或貼URL(加碼才加)")
                    window["-STATUS-"].update(msg)
                    set_black(window, "已斷線")
                    continue
                conn = hit  # ponytail: 連線只認框裡的字；閘道命中只拿來連，不回寫框、不存檔
            else:
                conn = spec
            cap = open_cap(conn, mode_now)
            fails = 0
            # ponytail: 示波器幾秒一張，錄影 fps 寫 2.0/1.0 就夠，不猜 server fps
            src_fps = {"DPO2014B": 2.0, "TDS3014B": 1.0}.get(
                mode_now, 20.0 if conn.strip().isdigit() else server_fps(conn))
            if cap.isOpened():
                # ponytail: 連線成功才記住，下次開啟預帶上次記憶值
                urls[mode_now] = spec
                save_cfg(mode_now, urls, _tx_ip)
                window["-STATUS-"].update("連線中...")
            elif mode_now in TEK_MODES:
                # ponytail: 示波器開不起來多半是 VISA/驅動層(看得到打不開)，直接秀原因；重插 USB/重開示波器多半就好
                window["-STATUS-"].update(f"連不上：{getattr(cap, '_err', '') or 'VISA打不開'}（試重插USB/重開示波器/關NI-MAX）")
            else:
                window["-STATUS-"].update("連不上：webcam被佔用？換 0/1 試試")
            if cap.isOpened() and values.get("-TX-"):
                # ponytail: 勾轉播=任何來源都 serve，區網 browser 開勾選框上的 URL 即看
                pick_tx_ip()  # ponytail: 單網卡靜默，多張跳選單；記住的還在就不打擾
                if tx_start(src_fps):
                    tx_on = True
                    tx_show()
                    window["-STATUS-"].update(f"已連線+轉播中 {tx_url()}")
                else:
                    window["-TX-"].update(value=False)
                    tx_hide()
                    window["-STATUS-"].update(f"已連線但轉播失敗：port {TX_PORT} 被佔用")
        elif event == "中斷":
            tx_stop()
            tx_on = False
            tx_hide()
            if cap is not None:
                cap.release()
                cap = None
            set_black(window, "已斷線")
            window["-STATUS-"].update("已中斷")
        elif event == "-TX-":
            # ponytail: 勾=有畫面就即開 serve，沒連線就等連線時開；取消勾即停
            if values.get("-TX-"):
                if cap is not None and cap.isOpened():
                    pick_tx_ip()  # ponytail: 單網卡靜默，多張跳選單；記住的還在就不打擾
                    if tx_start(src_fps):
                        tx_on = True
                        tx_show()
                        window["-STATUS-"].update(f"轉播中 {tx_url()}")
                    else:
                        window["-TX-"].update(value=False)
                        tx_hide()
                        window["-STATUS-"].update(f"轉播失敗：port {TX_PORT} 被佔用")
                else:
                    tx_hide()
                    window["-STATUS-"].update("已勾轉播，按連線後生效")
            else:
                tx_stop()
                tx_on = False
                tx_hide()
                if cap is not None and cap.isOpened():
                    window["-STATUS-"].update("已連線（轉播已關）")
        elif event == "截圖" and frame is not None:
            p = SHOTS / f"IMG_{datetime.now():%Y%m%d_%H%M%S}.jpg"
            cv2.imwrite(str(p), frame, [cv2.IMWRITE_JPEG_QUALITY, 100])  # ponytail: 截圖一律最高，不留選項
            window["-STATUS-"].update(f"已存 {p.name}")
        elif event == "圖庫":
            gallery()
        elif event == "資料夾":
            # ponytail: 開 pc/ 目錄(shots+rec 都在裡面)，一顆按鈕涵蓋兩個輸出；subprocess 已有
            subprocess.Popen(["explorer", str(BASE)])
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
            try:
                ok, frame = cap.read()
            except Exception:
                ok, frame = False, None  # ponytail: read 中 release 會丟 C++ 例外，當斷幀走重連
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
                    mode_now = cur_mode
                    ok_now = (spec_now.strip().isdigit() or tek_probe(spec_now, mode_now)
                              if mode_now in TEK_MODES else
                              (spec_now.strip().isdigit() or server_ok(spec_now)))
                    if ok_now:
                        window["-STATUS-"].update("重連中...")
                        window.refresh()
                        cap.release()
                        cap = open_cap(spec_now, mode_now)
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
        # ponytail: 用 cur_mode 變數存檔，不用 widget .get()(曾回傳 None 寫壞 mode)
        urls[cur_mode] = window["-URL-"].get()
        save_cfg(cur_mode, urls, _tx_ip)
    except Exception:
        pass
    window.close()


if __name__ == "__main__":
    main()
