"""scope.py - DPO2014B config dialog (minimal)."""
import json
import threading
import time
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")
warnings.filterwarnings("ignore", category=UserWarning)

import PySimpleGUI as sg
try:
    sg.set_options(suppress_raise_key_errors=False, suppress_error_popups=False, suppress_key_guessing=False)
except Exception:
    pass


SCOPE_CFG = Path(__file__).parent / "scope_last.json"


def _num_to_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        s = v.strip()
        return s
    try:
        f = float(v)
        if abs(f) >= 1e-12 and f.is_integer():
            return str(int(f))
        s = f"{f:g}"
        return s
    except Exception:
        return str(v).strip()


def _parse_time_to_sec(s: str) -> str:
    if s is None:
        return ""
    t = str(s).strip().replace(" ", "")
    if not t:
        return ""
    tl = t.lower()
    try:
        if tl.endswith("ns"):
            v = float(tl[:-2]) * 1e-9
            return f"{v:g}"
        if tl.endswith("us") or tl.endswith("µs"):
            v = float(tl[:-2]) * 1e-6
            return f"{v:g}"
        if tl.endswith("ms"):
            v = float(tl[:-2]) * 1e-3
            return f"{v:g}"
        if tl.endswith("s") and not tl.endswith("ms") and not tl.endswith("us") and not tl.endswith("ns") and not tl.endswith("µs"):
            v = float(tl[:-1])
            return f"{v:g}"
        v = float(tl)
        return f"{v:g}"
    except Exception:
        return t


def _val_with_unit_to_sec(val: str, unit: str) -> str:
    if val is None:
        return ""
    v = str(val).strip().replace(" ", "")
    if not v:
        return ""
    u = (unit or "s").strip().lower()
    try:
        vl = v.lower()
        if vl.endswith(("ns", "us", "µs", "ms", "s")):
            return _parse_time_to_sec(v)
        f = float(v)
        if u == "ns":
            f = f * 1e-9
        elif u == "us" or u == "µs":
            f = f * 1e-6
        elif u == "ms":
            f = f * 1e-3
        elif u == "s":
            pass
        return f"{f:g}"
    except Exception:
        return _parse_time_to_sec(v)


def _load_scope_cfg() -> dict:
    try:
        if SCOPE_CFG.exists():
            return json.loads(SCOPE_CFG.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def _save_scope_cfg(d: dict) -> None:
    try:
        SCOPE_CFG.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


class DpoScope:
    """Thin wrapper around TekCap instance to serialize SCPI."""
    def __init__(self, tek):
        self.tek = tek
        self.lock = getattr(tek, "_scpi_lock", None) or threading.RLock()
        try:
            setattr(tek, "_scpi_lock", self.lock)
        except Exception:
            pass

    def write(self, cmd: str) -> bool:
        inst = getattr(self.tek, "_inst", None)
        if inst is None:
            return False
        try:
            with self.lock:
                inst.write(cmd)
            return True
        except Exception:
            return False

    def query(self, cmd: str) -> str:
        inst = getattr(self.tek, "_inst", None)
        if inst is None:
            return ""
        try:
            with self.lock:
                try:
                    r = inst.query(cmd)
                except Exception:
                    # try write+read_raw not expected; fallback
                    inst.write(cmd)
                    r = inst.read_raw()
                    if isinstance(r, bytes):
                        r = r.decode("latin-1", errors="ignore")
                    else:
                        r = str(r)
            if isinstance(r, bytes):
                r = r.decode("latin-1", errors="ignore")
            s = str(r).strip()
            return s
        except Exception:
            return ""

    def idn(self) -> str:
        return self.query("*IDN?")

    def acq_state(self) -> str:
        s = self.query("ACQuire:STATE?")
        return s.strip().upper()

    def run(self) -> bool:
        return self.write("ACQuire:STATE RUN")

    def stop(self) -> bool:
        return self.write("ACQuire:STATE STOP")

    def single(self) -> bool:
        # try common forms
        ok = self.write("ACQuire:STOPAfter SEQuence")
        if ok:
            ok = self.write("ACQuire:STATE RUN")
        if not ok:
            # fallback
            ok = self.write("FPANEL:PRESS SINGLESEQ")
        return ok

    # channel
    def ch_disp(self, ch: int) -> str:
        return self.query(f"CH{ch}:DISPlay?").strip().upper()

    def ch_set_disp(self, ch: int, on: bool) -> bool:
        return self.write(f"CH{ch}:DISPlay {'ON' if on else 'OFF'}")

    def ch_cpl(self, ch: int) -> str:
        return self.query(f"CH{ch}:COUPling?").strip().upper()

    def ch_set_cpl(self, ch: int, cpl: str) -> bool:
        cpl = (cpl or "").strip().upper()
        if cpl not in ("AC", "DC", "GND"):
            return False
        return self.write(f"CH{ch}:COUPling {cpl}")

    def ch_scale(self, ch: int) -> str:
        return self.query(f"CH{ch}:SCAle?").strip()

    def ch_set_scale(self, ch: int, val: str) -> bool:
        val = (val or "").strip()
        if not val:
            return False
        return self.write(f"CH{ch}:SCAle {val}")

    def ch_pos(self, ch: int) -> str:
        return self.query(f"CH{ch}:POSition?").strip()

    def ch_set_pos(self, ch: int, val: str) -> bool:
        val = (val or "").strip()
        if not val:
            return False
        return self.write(f"CH{ch}:POSition {val}")

    # timebase
    def tb_scale(self) -> str:
        for cmd in ("HORizontal:MAIn:SCAle?", "HOR:SCAle?", "HORIZONTAL:MAIN:SCALE?", "HORIZONTAL:SCALE?"):
            try:
                s = self.query(cmd)
                if s:
                    return s.strip()
            except Exception:
                continue
        return ""

    def tb_set_scale(self, val: str) -> bool:
        val = (val or "").strip()
        if not val:
            return False
        v = _parse_time_to_sec(val)
        for cmd in (
            f"HORizontal:MAIn:SCAle {val}",
            f"HOR:SCAle {val}",
            f"HORIZONTAL:MAIN:SCALE {val}",
            f"HORIZONTAL:SCALE {val}",
            f"HORizontal:MAIn:SCAle {v}",
            f"HOR:SCAle {v}",
            f"HORIZONTAL:MAIN:SCALE {v}",
            f"HORIZONTAL:SCALE {v}",
            f":HORizontal:MAIn:SCAle {val}",
            f":HORizontal:MAIn:SCAle {v}",
        ):
            try:
                if self.write(cmd):
                    return True
            except Exception:
                continue
        return False

    def tb_pos(self) -> str:
        for cmd in (
            "HORizontal:MAIn:POSition?",
            "HORizontal:POSition?",
            "HOR:POSition?",
            "HORIZONTAL:MAIN:POSITION?",
            "HORIZONTAL:POSITION?",
            "HORizontal:MAIn:DELay:POSition?",
            "HORizontal:DELay:POSition?",
            "HORIZONTAL:MAIN:DELAY:POSITION?",
            ":HORizontal:MAIn:POSition?",
            ":HORizontal:POSition?",
        ):
            try:
                s = self.query(cmd)
                if s:
                    return s.strip()
            except Exception:
                continue
        return ""

    def tb_set_pos(self, val: str) -> bool:
        val = (val or "").strip()
        if not val:
            return False
        v = _parse_time_to_sec(val)
        # try common forms for DPO/Tek
        for cmd in (
            f"HORizontal:MAIn:POSition {v}",
            f"HORizontal:POSition {v}",
            f"HOR:POSition {v}",
            f"HORIZONTAL:MAIN:POSITION {v}",
            f"HORIZONTAL:POSITION {v}",
            f"HORizontal:MAIn:DELay:POSition {v}",
            f"HORizontal:DELay:POSition {v}",
            f"HORIZONTAL:MAIN:DELAY:POSITION {v}",
            f":HORizontal:MAIn:POSition {v}",
            f":HORizontal:POSition {v}",
            f":HOR:POSition {v}",
            f"HORizontal:MAIn:POSition {val}",
            f"HORizontal:POSition {val}",
            f"HOR:POSition {val}",
        ):
            try:
                if self.write(cmd):
                    return True
            except Exception:
                continue
        return False

    def tb_center(self) -> bool:
        for cmd in (
            "HORizontal:MAIn:POSition 0",
            "HORizontal:POSition 0",
            "HOR:POSition 0",
            "HORIZONTAL:MAIN:POSITION 0",
            "HORIZONTAL:POSITION 0",
            "HORizontal:MAIn:DELay:POSition 0",
            "HORizontal:DELay:POSition 0",
            "HORIZONTAL:MAIN:DELAY:POSITION 0",
            ":HORizontal:MAIn:POSition 0",
            ":HORizontal:POSition 0",
            ":HOR:POSition 0",
            "HORizontal:MAIn:POSition 0.0",
            "HORizontal:POSition 0.0",
            "HOR:POSition 0.0",
        ):
            try:
                if self.write(cmd):
                    return True
            except Exception:
                continue
        return False

    # trigger
    def trig_mode(self) -> str:
        s = self.query("TRIGger:A:MODe?")
        if s:
            return s.strip().upper()
        s = self.query("TRIGger:MODe?")
        return s.strip().upper()

    def trig_set_mode(self, mode: str) -> bool:
        mode = (mode or "").strip().upper()
        if mode not in ("AUTO", "NORMAL"):
            return False
        ok = self.write(f"TRIGger:A:MODe {mode}")
        if ok:
            return True
        ok = self.write(f"TRIGger:MODe {mode}")
        if ok:
            return True
        return self.write(f"TRIGGER:A:MODE {mode}")

    def trig_type(self) -> str:
        s = self.query("TRIGger:A:TYPe?")
        return s.strip().upper()

    def trig_set_edge(self, source: str, slope: str, level: str) -> bool:
        source = (source or "").strip().upper()
        slope = (slope or "").strip().upper()
        level = (level or "").strip()
        if source not in ("CH1", "CH2", "CH3", "CH4", "EXT", "LINE"):
            return False
        if slope not in ("RISE", "FALL"):
            # also accept RISING/FALLING
            if slope == "RISING":
                slope = "RISE"
            if slope == "FALLING":
                slope = "FALL"
        if slope not in ("RISE", "FALL"):
            return False
        ok = self.write("TRIGger:A:TYPe EDGE")
        if not ok:
            return False
        ok = self.write(f"TRIGger:A:EDGE:SOURce {source}")
        if not ok:
            return False
        ok = self.write(f"TRIGger:A:EDGE:SLOpe {slope}")
        if not ok:
            return False
        if level:
            ok = self.write(f"TRIGger:A:LEVel {level}")
        return ok

    def trig_src(self) -> str:
        s = self.query("TRIGger:A:EDGE:SOURce?")
        if s:
            return s.strip().upper()
        s = self.query("TRIGger:A:SOURce?")
        return s.strip().upper()

    def trig_slope(self) -> str:
        s = self.query("TRIGger:A:EDGE:SLOpe?")
        return s.strip().upper()

    def trig_level(self) -> str:
        s = self.query("TRIGger:A:LEVel?")
        return s.strip()


def tek_config_dialog(tek, preview_update=None) -> None:
    # only dpo
    model = getattr(tek, "_model", None)
    if model != "dpo":
        try:
            sg.popup_error("目前僅支援 DPO2014B 設定")
        except Exception:
            pass
        return
    try:
        if not getattr(tek, "isOpened", lambda: False)():
            try:
                sg.popup_error("尚未連線")
            except Exception:
                pass
            return
    except Exception:
        pass

    try:
        d = DpoScope(tek)
    except Exception:
        try:
            sg.popup_error("無法建立設定物件")
        except Exception:
            pass
        return
    try:
        cfg = _load_scope_cfg()
    except Exception:
        cfg = {}
    # build UI first (fast), fill in background
    ch_list = ["CH1", "CH2", "CH3", "CH4"]
    cpl_list = ["DC", "AC", "GND"]
    src_list = ["CH1", "CH2", "CH3", "CH4", "EXT", "LINE"]
    slope_list = ["RISE", "FALL"]
    mode_list = ["AUTO", "NORMAL"]

    ch_cur = cfg.get("ch_cur", "CH1")
    if ch_cur not in ch_list:
        ch_cur = "CH1"
    chon = bool(cfg.get("ch_on", True))
    chcpl = cfg.get("ch_cpl", "DC")
    if chcpl not in cpl_list:
        chcpl = "DC"
    chs = cfg.get("ch_scale", "")
    chp = cfg.get("ch_pos", "")
    tbs = cfg.get("tb_scale", "")
    tbp = cfg.get("tb_pos", "")
    trsrc = cfg.get("tr_src", "CH1")
    if trsrc not in src_list:
        trsrc = "CH1"
    trslo = cfg.get("tr_slope", "RISE")
    if trslo not in slope_list:
        trslo = "RISE"
    trlv = cfg.get("tr_level", "")
    trmode = cfg.get("tr_mode", "AUTO")
    if trmode not in mode_list:
        trmode = "AUTO"

    try:
        layout = [
            [sg.Frame("Channel config", [
                [sg.Text("Channel"), sg.Combo(ch_list, default_value=ch_cur, key="-CH-", readonly=True, enable_events=True, size=(8,1)),
                 sg.Checkbox("On", default=chon, key="-CHON-")],
                [sg.Text("Coupling"), sg.Combo(cpl_list, default_value=chcpl, key="-CHCPL-", readonly=True, size=(8,1))],
                [sg.Text("Scale (V/div)"), sg.Input(chs, key="-CHS-", size=(12,1))],
                [sg.Text("Position (div)"), sg.Input(chp, key="-CHP-", size=(12,1))],
                [sg.Button("套用 Channel", key="-CHAPPLY-")]
            ])],
            [sg.Frame("Timebase", [
                [sg.Text("Time/div (Scale)"), sg.Input(tbs, key="-TBS-", size=(10,1)), sg.Combo(["s", "ms", "us", "ns"], default_value=cfg.get("tb_scale_u", "ms"), key="-TBSU-", readonly=True, size=(5,1))],
                [sg.Text("H-Pos (time)"), sg.Input(tbp, key="-TBP-", size=(10,1)), sg.Combo(["s", "ms", "us", "ns"], default_value=cfg.get("tb_pos_u", "ms"), key="-TBPU-", readonly=True, size=(5,1))],
                [sg.Button("套用 Timebase", key="-TBAPPLY-"), sg.Button("置中", key="-TBCENTER-")]
            ])],
            [sg.Frame("Trigger condition", [
                [sg.Text("Source"), sg.Combo(src_list, default_value=trsrc, key="-TRSRC-", readonly=True, size=(8,1))],
                [sg.Text("Type"), sg.Text("EDGE")],
                [sg.Text("Slope"), sg.Combo(slope_list, default_value=trslo, key="-TRSLO-", readonly=True, size=(8,1))],
                [sg.Text("Level (V)"), sg.Input(trlv, key="-TRLV-", size=(12,1))],
                [sg.Text("Mode"), sg.Combo(mode_list, default_value=trmode, key="-TRMODE-", readonly=True, size=(8,1))],
                [sg.Button("套用 Trigger", key="-TRAPPLY-")]
            ])],
            [sg.Frame("Start/Stop", [
                [sg.Text("State:"), sg.Text("-", key="-ACQ-")],
                [sg.Button("Run"), sg.Button("Stop"), sg.Button("Single")],
                [sg.Button("關閉", key="-CLOSE-")]
            ])],
            [sg.Text("就緒", key="-STAT-", size=(40,1))]
        ]
    except Exception:
        return

    # ponytail: sg.Window 必須在 main thread 建立，呼叫端(mCam.py -CFG- 事件)已確保在 main thread
    try:
        win = sg.Window("Config 示波器 (DPO2014B)", layout, finalize=True)
    except Exception:
        return

    try:
        win["-TRAPPLY-"].update(disabled=True)
    except Exception:
        pass
    try:
        win.refresh()
    except Exception:
        pass
    try:
        _sync_trig_en()
    except Exception:
        pass

    def _refresh_ch(ch: int):
        try:
            win["-CHON-"].update(d.ch_disp(ch) == "ON")
            c = d.ch_cpl(ch)
            if c in cpl_list:
                win["-CHCPL-"].update(c)
            win["-CHS-"].update(d.ch_scale(ch))
            win["-CHP-"].update(d.ch_pos(ch))
        except Exception:
            pass

    def _set_stat(msg: str):
        try:
            win["-STAT-"].update(msg)
            win.refresh()
        except Exception:
            pass

    def _loader():
        try:
            try:
                ast = d.acq_state()
            except Exception:
                ast = ""
            try:
                win.write_event_value("-LOADAST-", ast or "-")
            except Exception:
                pass
            try:
                win.write_event_value("-SYNC-", "")
            except Exception:
                pass
        except Exception:
            pass

    try:
        threading.Thread(target=_loader, daemon=True).start()
    except Exception:
        pass
    try:
        ccfg = _load_scope_cfg()
        ccur = ccfg.get("ch_cur") or "CH1"
        c = int(str(ccur)[2:]) if str(ccur).startswith("CH") else 1
        try:
            win["-CH-"].update(f"CH{c}")
        except Exception:
            pass
        try:
            win["-CHON-"].update(bool(ccfg.get(f"ch{c}_on", True)))
        except Exception:
            pass
        if ccfg.get(f"ch{c}_cpl") in cpl_list:
            try:
                win["-CHCPL-"].update(ccfg.get(f"ch{c}_cpl"))
            except Exception:
                pass
        try:
            win["-CHS-"].update(ccfg.get(f"ch{c}_scale") or ccfg.get("ch_scale") or "")
        except Exception:
            pass
        try:
            win["-CHP-"].update(ccfg.get(f"ch{c}_pos") or ccfg.get("ch_pos") or "")
        except Exception:
            pass
        try:
            win["-TRLV-"].update(ccfg.get("tr_level_cur") or ccfg.get("tr_level") or "")
        except Exception:
            pass
    except Exception:
        pass

    def _sync_trig_en():
        try:
            try:
                st = d.acq_state()
            except Exception:
                st = ""
            su = str(st).strip().upper().replace(" ", "")
            if su in ("RUN", "1", "RUNNING", "ON", "TRUE"):
                try:
                    win["-TRAPPLY-"].update(disabled=True)
                except Exception:
                    pass
                return
            if su in ("STOP", "0", "STOPPED", "OFF", "FALSE"):
                try:
                    win["-TRAPPLY-"].update(disabled=False)
                except Exception:
                    pass
                return
            try:
                win["-TRAPPLY-"].update(disabled=True)
            except Exception:
                pass
        except Exception:
            try:
                win["-TRAPPLY-"].update(disabled=False)
            except Exception:
                pass

    try:
        _sync_trig_en()
    except Exception:
        pass

    while True:
        if preview_update is not None:
            try:
                preview_update()
            except Exception:
                pass
        ev, vals = win.read(timeout=200)
        if ev in (sg.WIN_CLOSED, "-CLOSE-"):
            break
        if ev == "-SYNC-":
            try:
                _sync_trig_en()
            except Exception:
                pass
            continue
        if ev == "-LOADAST-":
            try:
                s = vals.get("-LOADAST-") or "-"
                try:
                    win["-ACQ-"].update(s)
                except Exception:
                    pass
                try:
                    _sync_trig_en()
                except Exception:
                    pass
            except Exception:
                pass
            continue
        if ev == "-LOAD-":
            try:
                data = vals.get("-LOAD-")
                if isinstance(data, (tuple, list)) and len(data) == 12:
                    _, on0, cpl0, sc0, pos0, tbs, tbp, tsrc, tslo, tlv, tmode, ast = data
                    try:
                        win["-CHON-"].update(bool(on0))
                    except Exception:
                        pass
                    if cpl0 in cpl_list:
                        try:
                            win["-CHCPL-"].update(cpl0)
                        except Exception:
                            pass
                    try:
                        win["-CHS-"].update(sc0 or cfg.get("ch_scale") or cfg.get("ch_scale_cur") or "")
                    except Exception:
                        pass
                    try:
                        win["-CHP-"].update(pos0 or cfg.get("ch_pos") or cfg.get("ch_pos_cur") or "")
                    except Exception:
                        pass
                    try:
                        win["-TBS-"].update(tbs or cfg.get("tb_scale") or "")
                    except Exception:
                        pass
                    try:
                        win["-TBP-"].update(tbp or cfg.get("tb_pos") or "")
                    except Exception:
                        pass
                    if tsrc in src_list:
                        try:
                            win["-TRSRC-"].update(tsrc or cfg.get("tr_src") or "CH1")
                        except Exception:
                            pass
                    if tslo in slope_list:
                        try:
                            win["-TRSLO-"].update(tslo or cfg.get("tr_slope") or "RISE")
                        except Exception:
                            pass
                    try:
                        win["-TRLV-"].update(tlv or cfg.get("tr_level") or cfg.get("tr_level_cur") or "")
                    except Exception:
                        pass
                    if tmode in mode_list:
                        try:
                            win["-TRMODE-"].update(tmode or cfg.get("tr_mode") or "AUTO")
                        except Exception:
                            pass
                    try:
                        win["-ACQ-"].update(ast or "-")
                    except Exception:
                        pass
                    try:
                        _sync_trig_en()
                    except Exception:
                        pass
                    try:
                        win["-STAT-"].update("")
                    except Exception:
                        pass
            except Exception:
                pass
            continue
        if ev == "-CH-":
            try:
                c = int(vals["-CH-"][2:])
                try:
                    ccfg = _load_scope_cfg()
                    try:
                        win["-CHON-"].update(bool(ccfg.get(f"ch{c}_on", True)))
                    except Exception:
                        pass
                    if ccfg.get(f"ch{c}_cpl") in cpl_list:
                        try:
                            win["-CHCPL-"].update(ccfg.get(f"ch{c}_cpl"))
                        except Exception:
                            pass
                    try:
                        win["-CHS-"].update(ccfg.get(f"ch{c}_scale") or "")
                    except Exception:
                        pass
                    try:
                        win["-CHP-"].update(ccfg.get(f"ch{c}_pos") or "")
                    except Exception:
                        pass
                except Exception:
                    pass
                _refresh_ch(c)
                try:
                    _save_scope_cfg({**_load_scope_cfg(), "ch_cur": f"CH{c}"})
                except Exception:
                    pass
            except Exception:
                pass
            continue
        if ev == "-CHAPPLY-":
            try:
                c = int(vals["-CH-"][2:])
                d.ch_set_disp(c, bool(vals.get("-CHON-")))
                d.ch_set_cpl(c, vals.get("-CHCPL-") or "DC")
                d.ch_set_scale(c, vals.get("-CHS-") or "")
                d.ch_set_pos(c, vals.get("-CHP-") or "")
                try:
                    _save_scope_cfg({**_load_scope_cfg(), "ch_cur": f"CH{c}", "ch_on": bool(vals.get("-CHON-")), "ch_cpl": vals.get("-CHCPL-") or "DC", "ch_scale": vals.get("-CHS-") or "", "ch_pos": vals.get("-CHP-") or "", f"ch{c}_scale": vals.get("-CHS-") or "", f"ch{c}_pos": vals.get("-CHP-") or "", f"ch{c}_cpl": vals.get("-CHCPL-") or "DC", f"ch{c}_on": bool(vals.get("-CHON-"))})
                except Exception:
                    pass
                _set_stat("Channel 已套用")
            except Exception as e:
                _set_stat(f"Channel 套用失敗: {e}")
            continue
        if ev == "-TBAPPLY-":
            try:
                vs = _val_with_unit_to_sec(vals.get("-TBS-") or "", vals.get("-TBSU-") or "s")
                vp = _val_with_unit_to_sec(vals.get("-TBP-") or "", vals.get("-TBPU-") or "s")
                d.tb_set_scale(vs or vals.get("-TBS-") or "")
                d.tb_set_pos(vp or vals.get("-TBP-") or "")
                try:
                    _save_scope_cfg({**_load_scope_cfg(), "tb_scale": vals.get("-TBS-") or "", "tb_pos": vals.get("-TBP-") or "", "tb_scale_u": vals.get("-TBSU-") or "ms", "tb_pos_u": vals.get("-TBPU-") or "ms"})
                except Exception:
                    pass
                _set_stat("Timebase 已套用")
            except Exception as e:
                _set_stat(f"Timebase 套用失敗: {e}")
            continue
        if ev == "-TBCENTER-":
            try:
                ok = d.tb_center()
                if ok:
                    try:
                        win["-TBP-"].update("0")
                    except Exception:
                        pass
                    # try read back
                    try:
                        p = d.tb_pos()
                        if p:
                            win["-TBP-"].update(p)
                    except Exception:
                        pass
                    _set_stat("水平置中(0)")
                else:
                    _set_stat("水平置中失敗")
            except Exception as e:
                _set_stat(f"水平置中失敗: {e}")
            continue
        if ev == "-TRAPPLY-":
            try:
                # DPO often needs stop to change trigger
                try:
                    st = d.acq_state()
                    if str(st).strip().upper() == "RUN":
                        d.stop()
                        time.sleep(0.25)
                except Exception:
                    pass
                tm = vals.get("-TRMODE-") or "AUTO"
                if tm not in ("AUTO", "NORMAL"):
                    tm = "AUTO"
                    try:
                        win["-TRMODE-"].update("AUTO")
                    except Exception:
                        pass
                d.trig_set_edge(vals.get("-TRSRC-") or "CH1", vals.get("-TRSLO-") or "RISE", vals.get("-TRLV-") or "")
                d.trig_set_mode(tm)
                try:
                    _save_scope_cfg({**_load_scope_cfg(), "tr_src": vals.get("-TRSRC-") or "CH1", "tr_slope": vals.get("-TRSLO-") or "RISE", "tr_level": vals.get("-TRLV-") or "", "tr_mode": tm, "tr_level_cur": vals.get("-TRLV-") or ""})
                except Exception:
                    pass
                try:
                    _sync_trig_en()
                except Exception:
                    pass
                _set_stat("Trigger 已套用")
            except Exception as e:
                _set_stat(f"Trigger 套用失敗: {e}")
            continue
        if ev == "Run":
            d.run()
            time.sleep(0.15)
            try:
                win["-ACQ-"].update(d.acq_state() or "-")
            except Exception:
                pass
            try:
                _sync_trig_en()
            except Exception:
                pass
            _set_stat("Run")
            continue
        if ev == "Stop":
            d.stop()
            time.sleep(0.15)
            try:
                win["-ACQ-"].update(d.acq_state() or "-")
            except Exception:
                pass
            try:
                _sync_trig_en()
            except Exception:
                pass
            _set_stat("Stop")
            continue
        if ev == "Single":
            try:
                try:
                    d.trig_set_mode("NORMAL")
                except Exception:
                    pass
                try:
                    win["-TRMODE-"].update("NORMAL")
                except Exception:
                    pass
                try:
                    d.write("ACQuire:STOPAfter SEQuence")
                except Exception:
                    pass
                try:
                    d.write("ACQuire:STATE RUN")
                except Exception:
                    pass
                try:
                    d.single()
                except Exception:
                    pass
                time.sleep(0.15)
                try:
                    win["-ACQ-"].update(d.acq_state() or "-")
                except Exception:
                    pass
                try:
                    _sync_trig_en()
                except Exception:
                    pass
                try:
                    _save_scope_cfg({**_load_scope_cfg(), "tr_mode": "NORMAL"})
                except Exception:
                    pass
                _set_stat("Single (Trigger Mode -> NORMAL)")
            except Exception as e:
                _set_stat(f"Single 失敗: {e}")
            continue
        # periodic state update
        try:
            s = d.acq_state()
            if s:
                win["-ACQ-"].update(s)
            try:
                _sync_trig_en()
            except Exception:
                pass
        except Exception:
            pass

    try:
        win.close()
    except Exception:
        pass