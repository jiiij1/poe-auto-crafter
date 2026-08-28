#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
POE 剪贴板匹配与自定义动作工具 - 简化动作脚本版 (优化重构修复版 + 前后缀独立判断)
"""

import os
import json
import time
import queue
import uuid
import threading
import ctypes
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog

try:
    _user32 = ctypes.windll.user32
except Exception:
    _user32 = None

try:
    import pyautogui
    pyautogui.PAUSE = 0.005
except Exception:
    pyautogui = None

try:
    import pyperclip
except Exception:
    pyperclip = None

try:
    import keyboard
except Exception:
    keyboard = None


CONFIG_FILE = "actions_config.json"
RULES_FILE = "rules_config.json"
WORKFLOW_FILE = "workflows_config.json"

META_KEY = "__selected_rule__"

DEFAULT_CONFIG = {
    "hotkey": "F10",
    "mouse_position_hotkey": "F9",
    "copy_hotkey": ["ctrl", "c"], 

    "copy_backend": "keyboard",       
    "copy_timeout": 0.1,              
    "copy_poll_interval": 0.005,      
    "copy_retry": 3,                  
    "post_action_delay": 0.06,        
    "post_drop_delay": 0.01,          
    "read_failure_threshold": 0.1,    

    "actions": {
        "改造准备": [
            {"type": "moveTo", "x": 110, "y": 270, "duration": 0},
            {"type": "sleep", "time": 0.02},
            {"type": "click", "button": "right"},
            {"type": "sleep", "time": 0.03},
            {"type": "moveTo", "x": 330, "y": 440, "duration": 0},
            {"type": "sleep", "time": 0.03},
            {"type": "keyDown", "key": "shift"}
        ],
        "单次点击": [
            {"type": "click", "button": "left"},
            {"type": "sleep", "time": 0.03}
        ],
        "结束释放": [
            {"type": "keyUp", "key": "shift"}
        ]
    }
}

DEFAULT_RULES_DATA = {
    META_KEY: "默认规则",
    "默认规则": {
        "name": "默认规则",
        "blue_rules": [],
        "red_rules": [],
        "min_blue_match": 0,
        "max_attempts": 200,
        "action_name": "改造准备",
        "dry_run": False,
        "copy_hotkey": ["ctrl", "c"],
        "affix_count_enabled": False,
        "affix_count": 3,
        "prefix_count_enabled": False,
        "prefix_count": 0,
        "suffix_count_enabled": False,
        "suffix_count": 0
    }
}

DEFAULT_WORKFLOW_DATA = {
    "__selected_workflow__": "",
    "workflows": {}
}


# ==================== JSON & 格式辅助 ====================

def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def load_json_file(path, default):
    try:
        if not os.path.exists(path):
            save_json_file(path, default)
            return json.loads(json.dumps(default))
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(default))

def is_number(value):
    try:
        float(value)
        return True
    except Exception:
        return False

def to_number(value):
    f = float(value)
    return int(f) if f.is_integer() else f

def format_number(value):
    try:
        f = float(value)
        return str(int(f)) if f.is_integer() else format(f, "g")
    except Exception:
        return str(value)

# ==================== 简化动作脚本解析 ====================

def action_steps_to_simple(steps, show_duration=False):
    if not isinstance(steps, list):
        raise ValueError("动作必须是 JSON 数组")
    lines = []
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError(f"第 {i} 步必须是 JSON 对象")
        step_type = step.get("type")

        if step_type == "moveTo":
            x, y = format_number(step.get("x")), format_number(step.get("y"))
            duration = step.get("duration", 0)
            if show_duration and is_number(duration) and float(duration) > 0:
                lines.append(f"moveTo {x},{y},{format_number(duration)}")
            else:
                lines.append(f"moveTo {x},{y}")
        elif step_type == "click":
            button = step.get("button", "left")
            line = f"click {button}"
            clicks, interval = step.get("clicks"), step.get("interval")
            if clicks is not None or interval is not None:
                line += f" {int(clicks) if clicks is not None else 1}"
                if interval is not None:
                    line += f" {format_number(interval)}"
            lines.append(line)
        elif step_type == "sleep":
            lines.append(f"sleep {format_number(step.get('time', 0))}")
        elif step_type == "hotkey":
            keys = step.get("keys", "")
            if isinstance(keys, list): keys = "+".join(str(x) for x in keys)
            lines.append(f"hotkey {keys}")
        elif step_type in ("press", "keyDown", "keyUp"):
            lines.append(f"{step_type} {step.get('key', '')}")
        else:
            raise ValueError(f"第 {i} 步不支持的动作类型: {step_type}")
    return "\n".join(lines)

def parse_simple_action_to_steps(text):
    steps = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("#", "//")): continue
        if "#" in line: line = line.split("#", 1)[0].strip()
        if "//" in line: line = line.split("//", 1)[0].strip()
        if not line: continue

        line = line.replace("，", ",").replace("；", ";").rstrip(";").strip()
        parts = line.split()
        cmd = parts[0].lower()

        if cmd == "moveto":
            args = [x.strip() for x in " ".join(parts[1:]).split(",") if x.strip()] if "," in " ".join(parts[1:]) else parts[1:]
            if len(args) < 2: raise ValueError(f"第 {line_no} 行 moveTo 缺少坐标")
            step = {"type": "moveTo", "x": to_number(args[0]), "y": to_number(args[1]), "duration": 0}
            if len(args) >= 3: step["duration"] = to_number(args[2])
            steps.append(step)
        elif cmd == "click":
            button = parts[1].lower() if len(parts) >= 2 else "left"
            if button not in {"left", "right", "middle"}: raise ValueError(f"第 {line_no} 行 click 支持 left/right/middle")
            step = {"type": "click", "button": button}
            if len(parts) >= 3: step["clicks"] = int(parts[2])
            if len(parts) >= 4: step["interval"] = float(parts[3])
            steps.append(step)
        elif cmd == "sleep":
            steps.append({"type": "sleep", "time": to_number(parts[1])})
        elif cmd == "hotkey":
            steps.append({"type": "hotkey", "keys": normalize_hotkey_keys(" ".join(parts[1:]))})
        elif cmd in ("press", "keydown", "keyup"):
            steps.append({"type": {"keydown": "keyDown", "keyup": "keyUp", "press": "press"}[cmd], "key": parts[1]})
        else:
            raise ValueError(f"第 {line_no} 行 不支持的指令: {cmd}")
    return steps

def parse_action_text(text):
    stripped = text.strip()
    return json.loads(stripped) if stripped.startswith("[") else parse_simple_action_to_steps(text)


# ==================== 多套规则辅助 ====================

def get_rule_names(data):
    return [k for k, v in data.items() if k != META_KEY and isinstance(v, dict)] if isinstance(data, dict) else []

def normalize_rules_data(data):
    if not isinstance(data, dict): return {META_KEY: ""}
    if "rules" in data and isinstance(data.get("rules"), dict):
        normalized = {META_KEY: data.get("selected_rule") or data.get(META_KEY) or ""}
        normalized.update({k: v for k, v in data["rules"].items() if isinstance(v, dict)})
        names = get_rule_names(normalized)
        if normalized[META_KEY] not in names: normalized[META_KEY] = names[0] if names else ""
        return normalized
    if "blue_rules" in data or "red_rules" in data:
        return {META_KEY: data.get("name", "默认规则"), data.get("name", "默认规则"): data}
    
    normalized = {META_KEY: data.get(META_KEY) or data.get("selected_rule") or ""}
    normalized.update({k: v for k, v in data.items() if k not in (META_KEY, "selected_rule", "rules") and isinstance(v, dict)})
    names = get_rule_names(normalized)
    if normalized[META_KEY] not in names: normalized[META_KEY] = names[0] if names else ""
    return normalized

def normalize_imported_rules(data, fallback_name="导入规则"):
    if not isinstance(data, dict): raise ValueError("规则文件根节点必须是 JSON 对象")
    if "rules" in data and isinstance(data.get("rules"), dict):
        rules = {k: v for k, v in data["rules"].items() if isinstance(v, dict)}
        selected = data.get("selected_rule") or data.get(META_KEY) or (next(iter(rules), "") if rules else "")
        return rules, selected
    if "blue_rules" in data or "red_rules" in data:
        name = data.get("name") or fallback_name
        return {name: data}, name

    rules = {k: v for k, v in data.items() if k not in {META_KEY, "selected_rule", "rules"} and isinstance(v, dict)}
    selected = data.get(META_KEY) or data.get("selected_rule") or (next(iter(rules), "") if rules else "")
    return rules, selected

def load_rules_config():
    return normalize_rules_data(load_json_file(RULES_FILE, DEFAULT_RULES_DATA))

# ==================== 动作 & 剪贴板 ====================

def validate_action_steps(steps):
    allowed_types = {"moveTo", "click", "sleep", "hotkey", "press", "keyDown", "keyUp"}
    if not isinstance(steps, list): raise ValueError("动作必须是 JSON 数组")
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict): raise ValueError(f"第 {i} 步必须是 JSON 对象")
        if step.get("type") not in allowed_types: raise ValueError(f"第 {i} 步 type 不支持")

def normalize_hotkey_keys(keys):
    if isinstance(keys, str):
        return [x.strip() for x in (keys.split("+") if "+" in keys else keys.split()) if x.strip()]
    return [str(x).strip() for x in keys if str(x).strip()]

def get_clipboard_sequence_number():
    return _user32.GetClipboardSequenceNumber() if _user32 else None

def safe_clipboard_paste(retries=3, delay=0.005):
    if pyperclip:
        for _ in range(max(1, int(retries))):
            try: return pyperclip.paste()
            except Exception: time.sleep(delay)
    return None

def send_copy_hotkey(copy_hotkey=None, backend="keyboard"):
    keys = normalize_hotkey_keys(copy_hotkey or DEFAULT_CONFIG.get("copy_hotkey", ["ctrl", "c"]))
    if not keys: return False
    if backend.lower() == "keyboard" and keyboard:
        try:
            keyboard.send("+".join(keys))
            return True
        except Exception: pass
    if pyautogui:
        try:
            pyautogui.hotkey(*keys)
            return True
        except Exception: pass
    return False

def copy_item_text(copy_hotkey=None, timeout=0.6, poll_interval=0.005, retries=3, backend="keyboard"):
    if not pyperclip: return None
    for attempt in range(max(1, int(retries))):
        seq_before = get_clipboard_sequence_number()
        if not send_copy_hotkey(copy_hotkey, backend=backend):
            time.sleep(0.01)
            continue

        start = time.perf_counter()
        seq_changed = False
        while time.perf_counter() - start < timeout:
            current_seq = get_clipboard_sequence_number()
            if current_seq is not None and seq_before is not None:
                if current_seq != seq_before:
                    seq_changed = True
                    break
            else:
                text = safe_clipboard_paste(retries=1, delay=0)
                if text and text.strip(): return text
            time.sleep(poll_interval)
            
        if not seq_changed:
            time.sleep(0.01 + attempt * 0.01)
            continue

        text = safe_clipboard_paste(retries=2, delay=0.002)
        if text and text.strip(): return text
        time.sleep(0.01 + attempt * 0.01)
    return None

def match_rules(clipboard_text, rules):
    lines = [line.strip() for line in (clipboard_text or "").splitlines() if line.strip()]
    blue_rules = [x for x in rules.get("blue_rules", []) if x]
    red_rules = [x for x in rules.get("red_rules", []) if x]
    min_blue_match = int(rules.get("min_blue_match", 0))

    blue_matched = [r for r in blue_rules if any(r in line for line in lines)]
    red_missing = [r for r in red_rules if not any(r in line for line in lines)]

    blue_ok = len(blue_matched) >= min_blue_match
    red_ok = len(red_missing) == 0

    prefix_count = (clipboard_text or "").count("前缀")
    suffix_count = (clipboard_text or "").count("后缀")
    affix_total = prefix_count + suffix_count

    # 总词缀判断
    affix_count_enabled = bool(rules.get("affix_count_enabled", False))
    affix_count_target = int(rules.get("affix_count", 3))
    affix_ok = (affix_total == affix_count_target) if affix_count_enabled else True

    # 前缀判断
    prefix_enabled = bool(rules.get("prefix_count_enabled", False))
    prefix_target = int(rules.get("prefix_count", 0))
    prefix_ok = (prefix_count == prefix_target) if prefix_enabled else True

    # 后缀判断
    suffix_enabled = bool(rules.get("suffix_count_enabled", False))
    suffix_target = int(rules.get("suffix_count", 0))
    suffix_ok = (suffix_count == suffix_target) if suffix_enabled else True

    return {
        "success": blue_ok and red_ok and affix_ok and prefix_ok and suffix_ok,
        "blue_ok": blue_ok, "red_ok": red_ok, "affix_ok": affix_ok, "prefix_ok": prefix_ok, "suffix_ok": suffix_ok,
        "blue_matched": blue_matched, "red_missing": red_missing,
        "blue_count": len(blue_matched), "min_blue_match": min_blue_match,
        "affix_count_enabled": affix_count_enabled, "affix_count": affix_total, "affix_count_target": affix_count_target,
        "prefix_enabled": prefix_enabled, "prefix_count_val": prefix_count, "prefix_target": prefix_target,
        "suffix_enabled": suffix_enabled, "suffix_count_val": suffix_count, "suffix_target": suffix_target,
    }


# ==================== 主程序 (类) ====================

class PoeClipboardMakerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("POE 剪贴板匹配与自定义动作工具 - 优化重构修复版")
        self.root.geometry("1200x920")

        self.ui_queue = queue.Queue()
        self.running_lock = threading.Lock()
        self.running = False

        self.config = load_json_file(CONFIG_FILE, DEFAULT_CONFIG)
        self.rules_data = load_rules_config()
        save_json_file(RULES_FILE, self.rules_data)

        self.current_loaded_rule = ""
        self.blue_entries = []
        self.red_entries = []

        self._build_ui()
        self._refresh_action_selector()
        self._refresh_rule_selector()
        self._register_hotkeys()
        self.root.after(100, self._process_ui_queue)

    # ==================== 核心封装 Helper ====================
    
    def _make_btn(self, parent, text, command, side="left", padx=3, width=None, **kwargs):
        """统一封装按钮创建与布局"""
        btn = tk.Button(parent, text=text, command=command, width=width, **kwargs) if "bg" in kwargs else ttk.Button(parent, text=text, command=command, width=width, **kwargs)
        btn.pack(side=side, padx=padx)
        return btn

    def _make_labeled_entry(self, parent, label_text, width=8, default_val="", side="left", padx=(10, 2)):
        """统一封装标签 + 输入框"""
        ttk.Label(parent, text=label_text).pack(side=side, padx=padx)
        entry = ttk.Entry(parent, width=width)
        entry.pack(side=side, padx=2)
        if default_val: entry.insert(0, str(default_val))
        return entry

    def _set_entry(self, entry_widget, value):
        """一键赋值文本框"""
        entry_widget.delete(0, tk.END)
        entry_widget.insert(0, str(value))

    def _safe_int(self, value, default=0):
        """安全转换整型"""
        try: return int(str(value).strip() or default)
        except ValueError: return default

    def _draw_port(self, sx, sy, port_r, fill_color, text, text_offset, tag_prefix, nid, idx=""):
        """统一绘制节点圆形端口"""
        z = self.wf_zoom_level
        tag = f"{tag_prefix}_{nid}_{idx}" if idx != "" else f"{tag_prefix}_{nid}"
        self.wf_canvas.create_oval(sx - port_r, sy - port_r, sx + port_r, sy + port_r, fill=fill_color, outline="#333", tags=("port", tag))
        if text:
            self.wf_canvas.create_text(sx - port_r - text_offset, sy, text=text, font=("Arial", max(6, int(7 * z))), fill=fill_color, anchor="e", tags=("port_label", tag))

    # ==================== UI 布局 ====================

    def _build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=4, pady=4)

        self.tab_rule_mode = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_rule_mode, text="  规则模式  ")

        self.tab_workflow = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_workflow, text="  流程编辑  ")

        self._build_rule_mode_tab(self.tab_rule_mode)
        self._build_workflow_tab(self.tab_workflow)

    def _build_rule_mode_tab(self, parent):
        # 运行控制
        top = ttk.LabelFrame(parent, text="运行控制")
        top.pack(fill="x", padx=8, pady=6)
        self.status_label = ttk.Label(top, text="状态: 停止", foreground="red")
        self.status_label.pack(side="left", padx=8)

        self.toggle_button = self._make_btn(top, "开始 / 停止 (F10)", self.toggle_start_stop, padx=6)
        self._make_btn(top, "复制并测试", self.test_copy_clipboard, padx=6)
        self._make_btn(top, "测试当前剪贴板", self.test_current_clipboard, padx=6)
        
        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="试运行(不执行点击)", variable=self.dry_run_var).pack(side="left", padx=8)

        # 规则方案
        scheme_frame = ttk.LabelFrame(parent, text="规则方案（多套规则切换）")
        scheme_frame.pack(fill="x", padx=8, pady=6)
        
        row1 = ttk.Frame(scheme_frame)
        row1.pack(fill="x", padx=6, pady=2)
        ttk.Label(row1, text="选择规则:").pack(side="left")
        self.rule_selector = ttk.Combobox(row1, state="readonly", width=28)
        self.rule_selector.pack(side="left", padx=4)
        self.rule_selector.bind("<<ComboboxSelected>>", self.on_rule_selected)
        self.rule_name_entry = self._make_labeled_entry(row1, "规则名:", width=26)

        row2 = ttk.Frame(scheme_frame)
        row2.pack(fill="x", padx=6, pady=2)
        self._make_btn(row2, "新建规则", self.new_rule)
        self._make_btn(row2, "保存规则", self._save_and_reload_rules, bg="#d4edda", activebackground="#c3e6cb")
        self._make_btn(row2, "删除规则", self.delete_current_rule)
        self._make_btn(row2, "导入规则", self.import_rules_file)
        self._make_btn(row2, "导出当前规则", self.export_current_rule)
        ttk.Label(scheme_frame, text="所有规则保存在 rules_config.json 中。", foreground="#555").pack(anchor="w", padx=8, pady=2)

        # 匹配规则
        rule_frame = ttk.LabelFrame(parent, text="匹配规则")
        rule_frame.pack(fill="both", expand=True, padx=8, pady=6)
        left, right = ttk.Frame(rule_frame), ttk.Frame(rule_frame)
        left.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        right.pack(side="left", fill="both", expand=True, padx=6, pady=6)

        blue_box, red_box = ttk.LabelFrame(left, text="蓝色词条：至少匹配 X 条"), ttk.LabelFrame(right, text="红色词条：必须全部包含")
        blue_box.pack(fill="both", expand=True)
        red_box.pack(fill="both", expand=True)
        
        self.blue_list_frame, blue_buttons = ttk.Frame(blue_box), ttk.Frame(blue_box)
        self.red_list_frame, red_buttons = ttk.Frame(red_box), ttk.Frame(red_box)
        self.blue_list_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self.red_list_frame.pack(fill="both", expand=True, padx=4, pady=4)
        blue_buttons.pack(fill="x", padx=4, pady=4)
        red_buttons.pack(fill="x", padx=4, pady=4)

        self._make_btn(blue_buttons, "添加蓝色词条", lambda: self.add_rule_entry("blue"), padx=2)
        self._make_btn(blue_buttons, "清空蓝色", lambda: self.clear_entries("blue"), padx=2)
        self._make_btn(red_buttons, "添加红色词条", lambda: self.add_rule_entry("red"), padx=2)
        self._make_btn(red_buttons, "清空红色", lambda: self.clear_entries("red"), padx=2)

        # ------ 修改部分：前后缀分离 UI ------
        affix_frame = ttk.Frame(rule_frame)
        affix_frame.pack(side="bottom", fill="x", padx=6, pady=2)
        
        ttk.Label(affix_frame, text="总词缀数:").pack(side="left", padx=(4, 2))
        self.affix_count_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(affix_frame, text="启用", variable=self.affix_count_var).pack(side="left")
        self.affix_count_entry = ttk.Entry(affix_frame, width=4)
        self.affix_count_entry.insert(0, "3")
        self.affix_count_entry.pack(side="left", padx=2)

        ttk.Label(affix_frame, text="前缀数:").pack(side="left", padx=(16, 2))
        self.prefix_count_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(affix_frame, text="启用", variable=self.prefix_count_var).pack(side="left")
        self.prefix_count_entry = ttk.Entry(affix_frame, width=4)
        self.prefix_count_entry.insert(0, "0")
        self.prefix_count_entry.pack(side="left", padx=2)

        ttk.Label(affix_frame, text="后缀数:").pack(side="left", padx=(16, 2))
        self.suffix_count_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(affix_frame, text="启用", variable=self.suffix_count_var).pack(side="left")
        self.suffix_count_entry = ttk.Entry(affix_frame, width=4)
        self.suffix_count_entry.insert(0, "0")
        self.suffix_count_entry.pack(side="left", padx=2)

        x_frame = ttk.Frame(rule_frame)
        x_frame.pack(side="bottom", fill="x", padx=6, pady=2)
        self.min_blue_entry = self._make_labeled_entry(x_frame, "至少匹配 X 条蓝色词条:", padx=(4, 2))
        self.max_attempts_entry = self._make_labeled_entry(x_frame, "最大尝试次数:", padx=(16, 4))
        # ------------------------------------

        # 动作配置
        action_frame = ttk.LabelFrame(parent, text="动作配置（简化脚本编辑）")
        action_frame.pack(fill="both", expand=True, padx=8, pady=6)
        
        action_top = ttk.Frame(action_frame)
        action_top.pack(fill="x", padx=6, pady=4)
        ttk.Label(action_top, text="选择动作:").pack(side="left")
        self.action_selector = ttk.Combobox(action_top, state="readonly", width=20)
        self.action_selector.pack(side="left", padx=4)
        self.action_selector.bind("<<ComboboxSelected>>", self.on_action_selected)
        
        self.action_name_entry = self._make_labeled_entry(action_top, "动作名:", width=18, padx=(12, 2))
        self._make_btn(action_top, "保存", self._save_and_reload_action, bg="#d4edda", activebackground="#c3e6cb")
        self._make_btn(action_top, "删除", self.delete_current_action)

        # 动作快捷输入面板
        mouse_frame = ttk.Frame(action_frame)
        mouse_frame.pack(fill="x", padx=6, pady=2)
        self.mouse_x_entry = self._make_labeled_entry(mouse_frame, "鼠标取点:   X:", padx=(0, 2))
        self.mouse_y_entry = self._make_labeled_entry(mouse_frame, "Y:")
        self._make_btn(mouse_frame, "读取鼠标位置 (F9)", self.capture_mouse_position, padx=6)
        self._make_btn(mouse_frame, "插入 moveTo", self.insert_moveto_from_mouse)
        self._make_btn(mouse_frame, "插入 moveTo + 右键", self.insert_moveto_right_click)
        self._make_btn(mouse_frame, "插入 moveTo + 左键", self.insert_moveto_left_click)

        click_sleep_frame = ttk.Frame(action_frame)
        click_sleep_frame.pack(fill="x", padx=6, pady=2)
        ttk.Label(click_sleep_frame, text="插入点击:").pack(side="left")
        self._make_btn(click_sleep_frame, "click left", self.insert_click_left)
        self._make_btn(click_sleep_frame, "click right", self.insert_click_right)
        self._make_btn(click_sleep_frame, "click middle", self.insert_click_middle)
        self.sleep_entry = self._make_labeled_entry(click_sleep_frame, "等待时间:", default_val="0.1", padx=(16, 4))
        self._make_btn(click_sleep_frame, "插入 sleep", self.insert_sleep_from_entry)

        key_frame = ttk.Frame(action_frame)
        key_frame.pack(fill="x", padx=6, pady=2)
        self.key_entry = self._make_labeled_entry(key_frame, "按键:", width=20, padx=(0, 2))
        self._make_btn(key_frame, "插入 hotkey", lambda: self.insert_key_step("hotkey"))
        self._make_btn(key_frame, "插入 press", lambda: self.insert_key_step("press"))
        self._make_btn(key_frame, "插入 keyDown", lambda: self.insert_key_step("keyDown"))
        self._make_btn(key_frame, "插入 keyUp", lambda: self.insert_key_step("keyUp"))

        convert_frame = ttk.Frame(action_frame)
        convert_frame.pack(fill="x", padx=6, pady=2)
        self._make_btn(convert_frame, "插入模板", self.insert_template)
        self._make_btn(convert_frame, "转译为简化", self.convert_action_text_to_simple)
        self._make_btn(convert_frame, "转译为 JSON", self.convert_action_text_to_json)
        self._make_btn(convert_frame, "清空动作", self.clear_action_text)

        self.action_text = scrolledtext.ScrolledText(action_frame, height=10, wrap="none", font=("Consolas", 11))
        self.action_text.pack(fill="both", expand=True, padx=6, pady=4)

    def _build_workflow_tab(self, parent):
        left_panel = ttk.Frame(parent, width=280)
        left_panel.pack(side="left", fill="y", padx=4, pady=4)
        left_panel.pack_propagate(False)

        wf_list_frame = ttk.LabelFrame(left_panel, text="流程列表")
        wf_list_frame.pack(fill="both", expand=True, padx=4, pady=4)

        self.wf_selector = ttk.Combobox(wf_list_frame, state="readonly", width=24)
        self.wf_selector.pack(fill="x", padx=4, pady=4)
        self.wf_selector.bind("<<ComboboxSelected>>", self._on_workflow_selected)

        wf_btn_row = ttk.Frame(wf_list_frame)
        wf_btn_row.pack(fill="x", padx=4, pady=2)
        self._make_btn(wf_btn_row, "新建", self._wf_new, width=8, padx=2)
        self._make_btn(wf_btn_row, "保存", self._save_and_reload_workflow, width=8, padx=2, bg="#d4edda", activebackground="#c3e6cb")
        self._make_btn(wf_btn_row, "删除", self._wf_delete, width=8, padx=2)

        wf_btn_row2 = ttk.Frame(wf_list_frame)
        wf_btn_row2.pack(fill="x", padx=4, pady=2)
        self._make_btn(wf_btn_row2, "导入", self._wf_import, width=8, padx=2)
        self._make_btn(wf_btn_row2, "导出", self._wf_export, width=8, padx=2)

        wf_name_frame = ttk.LabelFrame(left_panel, text="流程名称")
        wf_name_frame.pack(fill="x", padx=4, pady=4)
        self.wf_name_entry = ttk.Entry(wf_name_frame, width=28)
        self.wf_name_entry.pack(fill="x", padx=4, pady=4)

        node_ops_frame = ttk.LabelFrame(left_panel, text="节点操作")
        node_ops_frame.pack(fill="x", padx=4, pady=4)
        ttk.Button(node_ops_frame, text="添加动作节点", command=self._wf_add_action_node).pack(fill="x", padx=4, pady=2)
        ttk.Button(node_ops_frame, text="添加结束节点", command=self._wf_add_end_node).pack(fill="x", padx=4, pady=2)
        ttk.Button(node_ops_frame, text="自动整理节点", command=self._wf_auto_layout).pack(fill="x", padx=4, pady=2)
        ttk.Button(node_ops_frame, text="验证流程", command=self._wf_validate).pack(fill="x", padx=4, pady=2)

        wf_run_frame = ttk.LabelFrame(left_panel, text="流程运行")
        wf_run_frame.pack(fill="x", padx=4, pady=4)
        self.wf_status_label = ttk.Label(wf_run_frame, text="状态: 停止", foreground="red")
        self.wf_status_label.pack(anchor="w", padx=4, pady=2)
        self.wf_run_button = ttk.Button(wf_run_frame, text="运行流程 (F10)", command=self._wf_run_toggle)
        self.wf_run_button.pack(fill="x", padx=4, pady=2)
        ttk.Button(wf_run_frame, text="停止流程", command=self._wf_stop).pack(fill="x", padx=4, pady=2)

        # Canvas
        right_panel = ttk.Frame(parent)
        right_panel.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        
        canvas_toolbar = ttk.Frame(right_panel)
        canvas_toolbar.pack(fill="x", padx=2, pady=2)
        self._make_btn(canvas_toolbar, "+", lambda: self._wf_zoom(1.2), width=3, padx=1)
        self._make_btn(canvas_toolbar, "-", lambda: self._wf_zoom(1 / 1.2), width=3, padx=1)
        self._make_btn(canvas_toolbar, "重置", self._wf_zoom_reset, width=4, padx=4)
        ttk.Label(canvas_toolbar, text="提示: 滚轮缩放, 中键拖动画布, 左键拖拽节点, 右键菜单").pack(side="left", padx=8)

        canvas_frame = ttk.Frame(right_panel, relief="sunken", borderwidth=2)
        canvas_frame.pack(fill="both", expand=True, padx=2, pady=2)
        self.wf_canvas = tk.Canvas(canvas_frame, bg="#f0f0f0", highlightthickness=0)
        self.wf_canvas.pack(fill="both", expand=True)

        self.wf_zoom_level = 1.0
        self.wf_pan_x, self.wf_pan_y = 0, 0
        self.wf_dragging_node, self.wf_selected_node = None, None
        self.wf_connecting_from, self.wf_connecting_port = None, None
        self.wf_panning = False
        
        self.wf_workflows_data = load_json_file(WORKFLOW_FILE, DEFAULT_WORKFLOW_DATA)
        self.wf_current_name = ""
        self.wf_nodes = {}
        self.wf_start_node = ""

        self.wf_canvas.bind("<Button-1>", self._wf_canvas_click)
        self.wf_canvas.bind("<B1-Motion>", self._wf_canvas_drag)
        self.wf_canvas.bind("<ButtonRelease-1>", self._wf_canvas_release)
        self.wf_canvas.bind("<Button-3>", self._wf_canvas_right_click)
        self.wf_canvas.bind("<MouseWheel>", self._wf_canvas_mousewheel)
        self.wf_canvas.bind("<Button-2>", self._wf_canvas_mid_press)
        self.wf_canvas.bind("<B2-Motion>", self._wf_canvas_mid_drag)
        self.wf_canvas.bind("<ButtonRelease-2>", self._wf_canvas_mid_release)
        self.wf_canvas.bind("<Double-Button-1>", self._wf_canvas_double_click)

        self.wf_context_menu = tk.Menu(self.wf_canvas, tearoff=0)
        self._refresh_workflow_list()

    # ==================== 后台管理与控制 ====================

    def _process_ui_queue(self):
        try:
            while True:
                msg_type, payload = self.ui_queue.get_nowait()
                if msg_type == "success":
                    self._set_status(False)
                    messagebox.showinfo("提示", f"制作成功：\n\n{payload[:1000]}")
                elif msg_type == "error":
                    messagebox.showerror("错误", payload)
                elif msg_type == "status":
                    self._set_status(payload == "running")
        except queue.Empty: pass
        self.root.after(100, self._process_ui_queue)

    def _set_status(self, running):
        state_text, btn_text, color = ("运行中", "停止 (F10)", "green") if running else ("停止", "开始 / 停止 (F10)", "red")
        self.status_label.config(text=f"状态: {state_text}", foreground=color)
        self.toggle_button.config(text=btn_text)
        self.wf_status_label.config(text=f"状态: {state_text}", foreground=color)
        self.wf_run_button.config(text=btn_text.replace("开始 / ", "运行流程 "))

    def add_rule_entry(self, kind, text=""):
        parent, store, color = (self.blue_list_frame, self.blue_entries, "#cfe8ff") if kind == "blue" else (self.red_list_frame, self.red_entries, "#ffd6d6")
        row = tk.Frame(parent)
        row.pack(fill="x", padx=2, pady=2)
        entry = tk.Entry(row, bg=color)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        if text: entry.insert(0, text)
        
        item = {"row": row, "entry": entry}
        tk.Button(row, text="删除", width=6, command=lambda: (store.remove(item) if item in store else None, row.destroy())).pack(side="right")
        store.append(item)

    def clear_entries(self, kind):
        store = self.blue_entries if kind == "blue" else self.red_entries
        for item in store[:]: item["row"].destroy()
        store.clear()

    def get_entry_values(self, store):
        return [item["entry"].get().strip() for item in store if item["entry"].get().strip()]

    def collect_ui_rules(self):
        return {
            "blue_rules": self.get_entry_values(self.blue_entries),
            "red_rules": self.get_entry_values(self.red_entries),
            "min_blue_match": self._safe_int(self.min_blue_entry.get(), 0),
            "max_attempts": self._safe_int(self.max_attempts_entry.get(), 200),
            "action_name": self.action_selector.get().strip(),
            "dry_run": bool(self.dry_run_var.get()),
            "copy_hotkey": self.config.get("copy_hotkey", DEFAULT_CONFIG["copy_hotkey"]),
            
            # 前后缀开关
            "affix_count_enabled": bool(self.affix_count_var.get()),
            "affix_count": self._safe_int(self.affix_count_entry.get(), 3),
            "prefix_count_enabled": bool(self.prefix_count_var.get()),
            "prefix_count": self._safe_int(self.prefix_count_entry.get(), 0),
            "suffix_count_enabled": bool(self.suffix_count_var.get()),
            "suffix_count": self._safe_int(self.suffix_count_entry.get(), 0),
        }

    # ==================== 规则状态与更新 ====================

    def _refresh_rule_selector(self, select=None):
        names = get_rule_names(self.rules_data)
        self.rule_selector["values"] = names
        chosen = select if select in names else self.rule_selector.get().strip() if self.rule_selector.get().strip() in names else self.rules_data.get(META_KEY) if self.rules_data.get(META_KEY) in names else names[0] if names else ""
        self.rule_selector.set(chosen)
        if chosen:
            self.rules_data[META_KEY] = chosen
            self.load_rule_to_ui(chosen)
        else:
            self.load_empty_rule_to_ui("")

    def on_rule_selected(self, event=None):
        name = self.rule_selector.get().strip()
        if name in get_rule_names(self.rules_data):
            self.rules_data[META_KEY] = name
            self.load_rule_to_ui(name)

    def load_empty_rule_to_ui(self, name=""):
        self.current_loaded_rule = ""
        self._set_entry(self.rule_name_entry, name)
        self.clear_entries("blue"); self.clear_entries("red")
        self.add_rule_entry("blue"); self.add_rule_entry("red")
        
        self._set_entry(self.min_blue_entry, "0")
        self._set_entry(self.max_attempts_entry, "200")
        self.dry_run_var.set(False)

        # 清空前后缀
        self.affix_count_var.set(False)
        self._set_entry(self.affix_count_entry, "3")
        self.prefix_count_var.set(False)
        self._set_entry(self.prefix_count_entry, "0")
        self.suffix_count_var.set(False)
        self._set_entry(self.suffix_count_entry, "0")

        action_names = list(self.config.get("actions", {}).keys())
        if action_names:
            self.action_selector.set(action_names[0])
            self.on_action_selected()

    def load_rule_to_ui(self, name):
        rule = self.rules_data.get(name, {})
        self.current_loaded_rule = name
        self._set_entry(self.rule_name_entry, name)
        
        self.clear_entries("blue"); self.clear_entries("red")
        for text in rule.get("blue_rules", []): self.add_rule_entry("blue", text)
        for text in rule.get("red_rules", []): self.add_rule_entry("red", text)
        if not self.blue_entries: self.add_rule_entry("blue")
        if not self.red_entries: self.add_rule_entry("red")

        self._set_entry(self.min_blue_entry, rule.get("min_blue_match", 0))
        self._set_entry(self.max_attempts_entry, rule.get("max_attempts", 200))
        self.dry_run_var.set(bool(rule.get("dry_run", False)))

        # 加载前后缀
        self.affix_count_var.set(bool(rule.get("affix_count_enabled", False)))
        self._set_entry(self.affix_count_entry, rule.get("affix_count", 3))
        self.prefix_count_var.set(bool(rule.get("prefix_count_enabled", False)))
        self._set_entry(self.prefix_count_entry, rule.get("prefix_count", 0))
        self.suffix_count_var.set(bool(rule.get("suffix_count_enabled", False)))
        self._set_entry(self.suffix_count_entry, rule.get("suffix_count", 0))

        if rule.get("action_name", "") in self.config.get("actions", {}):
            self.action_selector.set(rule.get("action_name", ""))
            self.on_action_selected()

    def new_rule(self):
        names = get_rule_names(self.rules_data)
        name, index = "新规则", 1
        while name in names:
            index += 1
            name = f"新规则_{index}"
        self.load_empty_rule_to_ui(name)
        self.rule_selector.set("")

    def save_current_rule(self):
        name = self.rule_name_entry.get().strip()
        if not name or name in {META_KEY, "rules", "selected_rule"}: return messagebox.showerror("错误", "规则名无效")
        old_name = self.current_loaded_rule
        rule = self.collect_ui_rules()
        rule["name"] = name
        
        if old_name and old_name != name and old_name in self.rules_data:
            if messagebox.askyesno("重命名确认", f"是否删除原规则：{old_name}，保留新规则：{name}？"):
                self.rules_data.pop(old_name, None)
        
        self.rules_data[name] = rule
        self.rules_data[META_KEY] = name
        if save_json_file(RULES_FILE, self.rules_data): self._refresh_rule_selector(select=name)

    def _save_and_reload_rules(self):
        self.save_current_rule()
        self.rules_data = load_rules_config()
        save_json_file(RULES_FILE, self.rules_data)
        self._refresh_rule_selector()

    def delete_current_rule(self):
        name = self.rule_selector.get().strip() or self.current_loaded_rule or self.rule_name_entry.get().strip()
        if not name or name not in get_rule_names(self.rules_data): return messagebox.showwarning("提示", "请选择删除规则")
        if not messagebox.askyesno("确认删除", f"确定删除规则：{name} ?"): return
        self.rules_data.pop(name, None)
        self.rules_data[META_KEY] = get_rule_names(self.rules_data)[0] if get_rule_names(self.rules_data) else ""
        save_json_file(RULES_FILE, self.rules_data)
        self._refresh_rule_selector()

    def import_rules_file(self):
        path = filedialog.askopenfilename(filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f: imported, selected = normalize_imported_rules(json.load(f), os.path.splitext(os.path.basename(path))[0])
        except Exception as e: return messagebox.showerror("导入失败", str(e))
        
        if not imported: return messagebox.showwarning("导入失败", "没有发现可导入规则")
        conflicts = [n for n in imported if n in get_rule_names(self.rules_data)]
        if conflicts and not messagebox.askyesno("冲突", f"以下存在：\n\n{chr(10).join(conflicts[:20])}\n\n覆盖？"): return
        
        self.rules_data.update(imported)
        self.rules_data[META_KEY] = selected if selected in imported else (get_rule_names(self.rules_data)[0] if get_rule_names(self.rules_data) else "")
        save_json_file(RULES_FILE, self.rules_data)
        self._refresh_rule_selector(select=self.rules_data[META_KEY])

    def export_current_rule(self):
        name = self.rule_selector.get().strip() or self.current_loaded_rule or "当前规则"
        rule = dict(self.rules_data.get(name) or self.collect_ui_rules())
        rule["name"] = name
        path = filedialog.asksaveasfilename(initialfile=f"{name}.json", defaultextension=".json", filetypes=[("JSON 文件", "*.json")])
        if path: save_json_file(path, rule)

    # ==================== 文本编辑器控制 ====================

    def _get_action_text(self): return self.action_text.get("1.0", tk.END).rstrip("\n")
    def _set_action_text(self, text):
        self.action_text.delete("1.0", tk.END)
        if text: self.action_text.insert("1.0", text.rstrip("\n") + "\n")

    def _append_simple_lines(self, lines):
        current = self._get_action_text()
        try: current = action_steps_to_simple(parse_action_text(current))
        except Exception: pass
        current = current.rstrip("\n")
        addition = "\n".join(lines) if isinstance(lines, list) else lines
        self._set_action_text(f"{current}\n{addition}\n" if current else f"{addition}\n")

    def capture_mouse_position(self):
        if not pyautogui: return messagebox.showerror("错误", "需安装 pyautogui")
        try:
            x, y = map(int, pyautogui.position())
            self._set_entry(self.mouse_x_entry, x)
            self._set_entry(self.mouse_y_entry, y)
            return x, y
        except Exception as e: return messagebox.showerror("错误", str(e))

    def get_mouse_xy_from_inputs(self):
        x, y = self.mouse_x_entry.get().strip(), self.mouse_y_entry.get().strip()
        if not x or not y: return self.capture_mouse_position()
        try: return to_number(x), to_number(y)
        except Exception: return messagebox.showerror("错误", "坐标须为数字")

    def insert_moveto_from_mouse(self):
        if pos := self.get_mouse_xy_from_inputs(): self._append_simple_lines(f"moveTo {format_number(pos[0])},{format_number(pos[1])}")
    def insert_moveto_right_click(self):
        if pos := self.get_mouse_xy_from_inputs(): self._append_simple_lines([f"moveTo {format_number(pos[0])},{format_number(pos[1])}", "sleep 0.02", "click right"])
    def insert_moveto_left_click(self):
        if pos := self.get_mouse_xy_from_inputs(): self._append_simple_lines([f"moveTo {format_number(pos[0])},{format_number(pos[1])}", "sleep 0.02", "click left"])
    
    def insert_click_left(self): self._append_simple_lines("click left")
    def insert_click_right(self): self._append_simple_lines("click right")
    def insert_click_middle(self): self._append_simple_lines("click middle")
    
    def insert_sleep_from_entry(self):
        try: self._append_simple_lines(f"sleep {format_number(max(0.0, float(self.sleep_entry.get().strip() or '0.1')))}")
        except ValueError: messagebox.showerror("错误", "sleep 时间非法")

    def insert_key_step(self, cmd):
        key = self.key_entry.get().strip()
        if not key: return messagebox.showerror("错误", "填写按键")
        self._append_simple_lines(f"hotkey {'+'.join(normalize_hotkey_keys(key))}" if cmd == "hotkey" else f"{cmd} {key}")

    def convert_action_text_to_simple(self):
        try: self._set_action_text(action_steps_to_simple(parse_action_text(self._get_action_text())))
        except Exception as e: messagebox.showerror("转译失败", str(e))
    def convert_action_text_to_json(self):
        try:
            steps = parse_action_text(self._get_action_text())
            validate_action_steps(steps)
            self._set_action_text(json.dumps(steps, ensure_ascii=False, indent=2))
        except Exception as e: messagebox.showerror("转译失败", str(e))
    def clear_action_text(self):
        if self._get_action_text().strip() and not messagebox.askyesno("清空", "清空编辑框？"): return
        self.action_text.delete("1.0", tk.END)
    def insert_template(self): self._set_action_text("\n".join(["moveTo 100,200", "click right", "sleep 0.034", "moveTo 300,400", "click left", "sleep 0.034"]))

    # ==================== 动作配置 ====================
    
    def _refresh_action_selector(self, select=None):
        names = list(self.config.get("actions", {}).keys())
        self.action_selector["values"] = names
        self.action_selector.set(select if select in names else self.action_selector.get() if self.action_selector.get() in names else names[0] if names else "")
        self.on_action_selected()

    def on_action_selected(self, event=None):
        name = self.action_selector.get().strip()
        self._set_entry(self.action_name_entry, name)
        steps = self.config.get("actions", {}).get(name, [])
        try: self._set_action_text(action_steps_to_simple(steps))
        except Exception: self._set_action_text(json.dumps(steps, ensure_ascii=False, indent=2))

    def _save_and_reload_action(self):
        name = self.action_name_entry.get().strip()
        if not name: return messagebox.showerror("错误", "动作名空")
        if name in self.config.get("actions", {}) and not messagebox.askyesno("覆盖", f"动作 {name} 已存在，覆盖？"): return
        try:
            steps = parse_action_text(self._get_action_text().strip())
            validate_action_steps(steps)
            self.config.setdefault("actions", {})[name] = steps
            save_json_file(CONFIG_FILE, self.config)
            self._refresh_action_selector(select=name)
        except Exception as e: messagebox.showerror("配置错误", str(e))

    def delete_current_action(self):
        name = self.action_selector.get().strip()
        if not name or not messagebox.askyesno("删除", f"删除动作: {name} ?"): return
        self.config.get("actions", {}).pop(name, None)
        save_json_file(CONFIG_FILE, self.config)
        self._refresh_action_selector()
        self.action_text.delete("1.0", tk.END)

    # ==================== 自动化核心 ====================

    def _register_hotkeys(self):
        if not keyboard: return
        try:
            keyboard.on_release_key(self.config.get("hotkey", "F10"), lambda e: self.root.after(0, self.toggle_start_stop))
            keyboard.on_release_key(self.config.get("mouse_position_hotkey", "F9"), lambda e: self.root.after(0, self.capture_mouse_position))
        except Exception: pass

    def is_running(self):
        with self.running_lock: return self.running
    def set_running(self, value):
        with self.running_lock: self.running = value

    def toggle_start_stop(self):
        if self.is_running(): self.set_running(False)
        else:
            if str(self.notebook.select()) == str(self.tab_workflow): self._wf_start()
            else: self.start_automation()

    def start_automation(self):
        if not pyautogui: return messagebox.showerror("错误", "需 pyautogui")
        rules = self.collect_ui_rules()
        if not rules["dry_run"] and rules["action_name"] not in self.config.get("actions", {}): return messagebox.showerror("错误", "动作不存在")
        self.set_running(True)
        self.ui_queue.put(("status", "running"))
        threading.Thread(target=self.automation_loop, args=(rules,), daemon=True).start()

    def automation_loop(self, rules):
        attempt, max_attempts = 0, max(1, int(rules.get("max_attempts", 200)))
        cp_hk, cp_tm, cp_pi, cp_rt, cp_be = rules.get("copy_hotkey", DEFAULT_CONFIG["copy_hotkey"]), float(self.config.get("copy_timeout", 0.1)), float(self.config.get("copy_poll_interval", 0.005)), int(self.config.get("copy_retry", 2)), self.config.get("copy_backend", "keyboard")
        pad, pdd, rft = float(self.config.get("post_action_delay", 0.06)), float(self.config.get("post_drop_delay", 0.01)), float(self.config.get("read_failure_threshold", 0.1))

        try:
            while self.is_running() and attempt < max_attempts:
                attempt += 1
                start_time = time.perf_counter()
                clip_text = copy_item_text(cp_hk, cp_tm, cp_pi, cp_rt, cp_be)

                if clip_text is None and time.perf_counter() - start_time >= rft and pyautogui:
                    try: pyautogui.click(button="left")
                    except Exception: pass
                    if pdd > 0: time.sleep(pdd)
                    clip_text = copy_item_text(cp_hk, cp_tm, cp_pi, max(1, cp_rt - 1), cp_be)
                
                if clip_text is None:
                    time.sleep(0.02)
                    continue

                if match_rules(clip_text, rules)["success"]:
                    self.ui_queue.put(("success", clip_text))
                    break
                if rules.get("dry_run"):
                    time.sleep(0.01)
                    continue
                if not self.execute_action(rules["action_name"]): break
                if pad > 0: time.sleep(pad)
        except Exception as e: self.ui_queue.put(("error", str(e)))
        finally:
            self.set_running(False)
            self.ui_queue.put(("status", "stopped"))

    def _interruptible_sleep(self, seconds, interval=0.005):
        end = time.perf_counter() + max(0.0, float(seconds))
        while self.is_running() and end > time.perf_counter(): time.sleep(min(interval, end - time.perf_counter()))
        return self.is_running()

    def execute_action(self, action_name):
        steps = self.config.get("actions", {}).get(action_name)
        if steps is None or not pyautogui: return False
        
        for step in steps:
            if not self.is_running(): return False
            try:
                stype = step.get("type")
                if stype == "moveTo": pyautogui.moveTo(float(step.get("x")), float(step.get("y")), duration=float(step.get("duration", 0)))
                elif stype == "click": pyautogui.click(button=step.get("button", "left"), clicks=int(step.get("clicks", 1)), interval=float(step.get("interval", 0)))
                elif stype == "sleep": 
                    if not self._interruptible_sleep(step.get("time", 0)): return False
                elif stype == "hotkey": pyautogui.hotkey(*normalize_hotkey_keys(step.get("keys")))
                elif stype in ("press", "keyDown", "keyUp"): getattr(pyautogui, stype)(step.get("key"))
            except Exception as e:
                self.ui_queue.put(("error", f"动作执行失败: {e}"))
                return False
            if not self._interruptible_sleep(0.01): return False
        return True

    def test_current_clipboard(self):
        if not pyperclip: return messagebox.showerror("错误", "需 pyperclip")
        self._show_match_result(pyperclip.paste() or "", "当前剪贴板")

    def test_copy_clipboard(self):
        if text := copy_item_text(self.collect_ui_rules().get("copy_hotkey"), timeout=1.2):
            self._show_match_result(text, "复制并测试")
        else: messagebox.showwarning("提示", "复制失败")

    def _show_match_result(self, text, src):
        res = match_rules(text, self.collect_ui_rules())
        
        affix_str = f"总词缀: {res['affix_count']}/{res['affix_count_target']} {'✓' if res['affix_ok'] else '✗'}"
        if res.get('prefix_enabled'): affix_str += f" | 前缀: {res['prefix_count_val']}/{res['prefix_target']} {'✓' if res['prefix_ok'] else '✗'}"
        if res.get('suffix_enabled'): affix_str += f" | 后缀: {res['suffix_count_val']}/{res['suffix_target']} {'✓' if res['suffix_ok'] else '✗'}"

        messagebox.showinfo("测试", f"{src}\n\n结果: {'成功' if res['success'] else '失败'}\n蓝色: {res['blue_count']}/{res['min_blue_match']}\n"
                            f"{affix_str}\n文本预览:\n{text[:500]}")

    # ==================== 工作流编辑器Canvas ====================

    NODE_W, NODE_H_ACTION, NODE_H_END, PORT_R, NODE_RULE_H = 200, 120, 50, 6, 25

    def _wf_get_rules(self, nd):
        return nd["rules"] if "rules" in nd and isinstance(nd["rules"], list) else ([{"rule": nd["rule"], "success": nd.get("success", "")}] if nd.get("rule") else [])

    def _wf_node_height(self, nd):
        return self.NODE_H_END if nd.get("type") == "end" else self.NODE_H_ACTION + max(0, len(self._wf_get_rules(nd)) - 1) * self.NODE_RULE_H

    def _wf_redraw_canvas(self):
        self.wf_canvas.delete("all")
        z, cw, ch = self.wf_zoom_level, int(self.wf_canvas.winfo_width() or 800), int(self.wf_canvas.winfo_height() or 600)
        step = max(20, int(40 * z))
        for x in range(int(self.wf_pan_x) % step, cw, step): self.wf_canvas.create_line(x, 0, x, ch, fill="#e0e0e0", tags="grid")
        for y in range(int(self.wf_pan_y) % step, ch, step): self.wf_canvas.create_line(0, y, cw, y, fill="#e0e0e0", tags="grid")

        for nid, nd in self.wf_nodes.items():
            if nd.get("type") == "end": continue
            for i, r in enumerate(self._wf_get_rules(nd)):
                if r.get("success") in self.wf_nodes: self._wf_draw_connection(nid, f"success_{i}", r["success"])
            for port in ["failure", "max_reached"]:
                if nd.get(port) in self.wf_nodes: self._wf_draw_connection(nid, port, nd[port])

        for nid, nd in self.wf_nodes.items(): self._wf_draw_node(nid, nd)

    def _wf_draw_node(self, nid, nd):
        z, sx, sy = self.wf_zoom_level, *self._wf_world_to_screen(nd["x"], nd["y"])
        w, h = int(self.NODE_W * z), int(self._wf_node_height(nd) * z)
        is_end = nd.get("type") == "end"
        
        self.wf_canvas.create_rectangle(sx, sy, sx + w, sy + h, fill="#ffcccc" if is_end else ("#c8e6c9" if nid == self.wf_start_node else "#e3f2fd"),
                                        outline="#2196F3" if nid == self.wf_selected_node else "#333", width=3 if nid == self.wf_selected_node else 1, tags=("node", f"node_{nid}"))
        self.wf_canvas.create_text(sx + w // 2, sy + int(14 * z), text=f"{'▶ ' if nid == self.wf_start_node else '■ ' if is_end else ''}{nd.get('name', nid)}", font=("Arial", max(8, int(10 * z)), "bold"), tags=("node", f"node_{nid}"))

        if not is_end:
            self.wf_canvas.create_text(sx + w // 2, sy + int(36 * z), text=f"动作: {nd.get('action', '-')}", font=("Arial", max(7, int(9 * z))), tags=("node", f"node_{nid}"))
            rules = self._wf_get_rules(nd)
            pr = int(self.PORT_R * z)
            
            for i, rule in enumerate(rules):
                ry = sy + int((52 + i * self.NODE_RULE_H) * z)
                self.wf_canvas.create_text(sx + w // 2, ry, text=f"规则{i+1}: {rule.get('rule', '-')}", font=("Arial", max(7, int(9 * z))), tags=("node", f"node_{nid}"))
                self._draw_port(sx + w, ry, pr, "#4CAF50", f"✓{i+1}", int(4 * z), "success", nid, i)

            fp_y = sy + int((52 + len(rules) * self.NODE_RULE_H) * z)
            self._draw_port(sx + w, fp_y, pr, "#f44336", "失败", int(16 * z), "failure", nid)
            self._draw_port(sx + w, fp_y + int(self.NODE_RULE_H * z), pr, "#FF9800", "耗尽", int(16 * z), "max_reached", nid)
            self._draw_port(sx, sy + h // 2, pr, "#2196F3", "", 0, "input", nid)

    def _wf_draw_connection(self, from_nid, port, to_nid):
        from_nd, to_nd = self.wf_nodes[from_nid], self.wf_nodes[to_nid]
        rules = self._wf_get_rules(from_nd)
        fx = from_nd["x"] + self.NODE_W
        fy = from_nd["y"] + 52 + (int(port.split("_")[1]) if port.startswith("success_") else len(rules)) * self.NODE_RULE_H + (self.NODE_RULE_H if port == "max_reached" else 0)
        
        fsx, fsy = self._wf_world_to_screen(fx, fy)
        ftx, fty = self._wf_world_to_screen(to_nd["x"], to_nd["y"] + self._wf_node_height(to_nd) / 2)
        
        color, lbl = ("#4CAF50", f"规则{int(port.split('_')[1])+1}") if port.startswith("success_") else ("#f44336", "失败") if port == "failure" else ("#FF9800", "耗尽")
        self.wf_canvas.create_line(fsx, fsy, fsx + abs(ftx - fsx) * 0.5, fsy, ftx - abs(ftx - fsx) * 0.5, fty, ftx, fty, smooth=True, fill=color, width=max(1, int(2 * self.wf_zoom_level)), arrow="last", tags="connection")
        self.wf_canvas.create_text((fsx + ftx) / 2, (fsy + fty) / 2 - 10, text=lbl, fill=color, font=("Arial", max(7, int(8 * self.wf_zoom_level))), tags="connection_label")

    def _wf_screen_to_world(self, sx, sy): return (sx - self.wf_pan_x) / self.wf_zoom_level, (sy - self.wf_pan_y) / self.wf_zoom_level
    def _wf_world_to_screen(self, wx, wy): return wx * self.wf_zoom_level + self.wf_pan_x, wy * self.wf_zoom_level + self.wf_pan_y

    def _wf_find_node_at(self, wx, wy):
        for nid, nd in reversed(list(self.wf_nodes.items())):
            if nd["x"] <= wx <= nd["x"] + self.NODE_W and nd["y"] <= wy <= nd["y"] + self._wf_node_height(nd): return nid
        return None

    def _wf_find_port_at(self, wx, wy):
        pr = self.PORT_R * 1.5
        for nid, nd in self.wf_nodes.items():
            if nd.get("type") == "end": continue
            x, y, rules = nd["x"], nd["y"], self._wf_get_rules(nd)
            for i in range(len(rules)):
                if abs(wx - (x + self.NODE_W)) <= pr and abs(wy - (y + 52 + i * self.NODE_RULE_H)) <= pr: return nid, f"success_{i}"
            if abs(wx - (x + self.NODE_W)) <= pr and abs(wy - (y + 52 + len(rules) * self.NODE_RULE_H)) <= pr: return nid, "failure"
            if abs(wx - (x + self.NODE_W)) <= pr and abs(wy - (y + 52 + (len(rules) + 1) * self.NODE_RULE_H)) <= pr: return nid, "max_reached"
        return None, None

    # ==================== Canvas 事件 ====================

    def _wf_canvas_click(self, event):
        wx, wy = self._wf_screen_to_world(event.x, event.y)
        port_nid, port_type = self._wf_find_port_at(wx, wy)
        if port_nid:
            self.wf_connecting_from, self.wf_connecting_port = port_nid, port_type
            return
        if nid := self._wf_find_node_at(wx, wy):
            self.wf_selected_node = self.wf_dragging_node = nid
            self.wf_drag_offset_x, self.wf_drag_offset_y = wx - self.wf_nodes[nid]["x"], wy - self.wf_nodes[nid]["y"]
        else: self.wf_selected_node = None
        self._wf_redraw_canvas()

    def _wf_canvas_drag(self, event):
        if self.wf_dragging_node:
            wx, wy = self._wf_screen_to_world(event.x, event.y)
            self.wf_nodes[self.wf_dragging_node]["x"] = wx - self.wf_drag_offset_x
            self.wf_nodes[self.wf_dragging_node]["y"] = wy - self.wf_drag_offset_y
            self._wf_redraw_canvas()

    def _wf_canvas_release(self, event):
        if self.wf_connecting_from:
            target = self._wf_find_node_at(*self._wf_screen_to_world(event.x, event.y))
            if target and target != self.wf_connecting_from:
                nd, port = self.wf_nodes[self.wf_connecting_from], self.wf_connecting_port
                if port.startswith("success_"): nd["rules"][int(port.split("_")[1])]["success"] = target
                else: nd[port] = target
                self._wf_redraw_canvas()
            self.wf_connecting_from, self.wf_connecting_port = None, None
        self.wf_dragging_node = None

    def _wf_canvas_right_click(self, event):
        self.wf_selected_node = self._wf_find_node_at(*self._wf_screen_to_world(event.x, event.y))
        self._wf_redraw_canvas()
        self.wf_context_menu.delete(0, tk.END)
        state = "normal" if self.wf_selected_node else "disabled"
        for label, cmd in [("编辑节点", self._wf_ctx_edit), ("复制节点", self._wf_ctx_copy), ("删除节点", self._wf_ctx_delete)]:
            self.wf_context_menu.add_command(label=label, command=cmd, state=state)
        self.wf_context_menu.add_separator()
        self.wf_context_menu.add_command(label="设为开始节点", command=self._wf_ctx_set_start, state=state)
        if self.wf_selected_node and self.wf_nodes[self.wf_selected_node].get("type") != "end":
            self.wf_context_menu.add_separator()
            for i, r in enumerate(self._wf_get_rules(self.wf_nodes[self.wf_selected_node])):
                self.wf_context_menu.add_command(label=f"从规则{i+1}连接 ({r.get('rule','')})", command=lambda idx=i: self._wf_ctx_connect(f"success_{idx}"))
            self.wf_context_menu.add_command(label="从失败连接", command=lambda: self._wf_ctx_connect("failure"))
            self.wf_context_menu.add_command(label="从耗尽连接", command=lambda: self._wf_ctx_connect("max_reached"))
        self.wf_context_menu.tk_popup(event.x_root, event.y_root)

    def _wf_canvas_double_click(self, event):
        if nid := self._wf_find_node_at(*self._wf_screen_to_world(event.x, event.y)):
            self.wf_selected_node = nid
            self._wf_edit_node(nid)

    def _wf_canvas_mousewheel(self, event): self._wf_zoom_at(1.1 if event.delta > 0 else 1/1.1, event.x, event.y)
    def _wf_canvas_mid_press(self, event): self.wf_panning, self.wf_pan_start_x, self.wf_pan_start_y = True, event.x, event.y
    def _wf_canvas_mid_drag(self, event):
        if self.wf_panning:
            self.wf_pan_x += event.x - self.wf_pan_start_x; self.wf_pan_y += event.y - self.wf_pan_start_y
            self.wf_pan_start_x, self.wf_pan_start_y = event.x, event.y; self._wf_redraw_canvas()
    def _wf_canvas_mid_release(self, event): self.wf_panning = False

    def _wf_zoom_at(self, factor, cx, cy):
        actual = max(0.2, min(3.0, self.wf_zoom_level * factor)) / self.wf_zoom_level
        self.wf_pan_x, self.wf_pan_y = cx - (cx - self.wf_pan_x) * actual, cy - (cy - self.wf_pan_y) * actual
        self.wf_zoom_level *= actual
        self._wf_redraw_canvas()
    def _wf_zoom(self, factor): self._wf_zoom_at(factor, int(self.wf_canvas.winfo_width() or 800) // 2, int(self.wf_canvas.winfo_height() or 600) // 2)
    def _wf_zoom_reset(self): self.wf_zoom_level, self.wf_pan_x, self.wf_pan_y = 1.0, 0, 0; self._wf_redraw_canvas()

    def _wf_ctx_edit(self): self._wf_edit_node(self.wf_selected_node)
    def _wf_ctx_copy(self):
        if not self.wf_selected_node: return
        src = self.wf_nodes[self.wf_selected_node]
        self.wf_node_counter += 1
        new_nd = {k: v for k, v in src.items()}
        new_nd.update({"x": src["x"]+30, "y": src["y"]+30, "name": src.get("name", "") + " (副本)"})
        if new_nd.get("type") != "end":
            new_nd["rules"] = [{"rule": r["rule"], "success": ""} for r in self._wf_get_rules(src)]
            new_nd.update({"failure": "", "max_reached": "end"})
        self.wf_nodes[f"node_{self.wf_node_counter}"] = new_nd
        self._wf_redraw_canvas()
    def _wf_ctx_delete(self):
        if not self.wf_selected_node or not messagebox.askyesno("删除", "删除节点？"): return
        for nd in self.wf_nodes.values():
            if nd.get("success") == self.wf_selected_node: nd["success"] = ""
            if nd.get("failure") == self.wf_selected_node: nd["failure"] = ""
        if self.wf_start_node == self.wf_selected_node: self.wf_start_node = ""
        del self.wf_nodes[self.wf_selected_node]
        self.wf_selected_node = None
        self._wf_redraw_canvas()
    def _wf_ctx_set_start(self):
        if self.wf_selected_node and self.wf_nodes[self.wf_selected_node].get("type") != "end":
            self.wf_start_node = self.wf_selected_node; self._wf_redraw_canvas()
    def _wf_ctx_connect(self, port):
        self.wf_connecting_from, self.wf_connecting_port = self.wf_selected_node, port

    # ==================== 工作流表单操作 ====================

    def _refresh_workflow_list(self, select=None):
        names = list(self.wf_workflows_data.get("workflows", {}).keys())
        self.wf_selector["values"] = names
        chosen = select if select in names else self.wf_workflows_data.get("__selected_workflow__") if self.wf_workflows_data.get("__selected_workflow__") in names else names[0] if names else ""
        self.wf_selector.set(chosen)
        if chosen:
            self.wf_workflows_data["__selected_workflow__"] = chosen
            self._load_workflow_to_canvas(chosen)

    def _on_workflow_selected(self, event=None):
        if name := self.wf_selector.get().strip():
            self.wf_workflows_data["__selected_workflow__"] = name
            self._load_workflow_to_canvas(name)

    def _load_workflow_to_canvas(self, name):
        wf = self.wf_workflows_data.get("workflows", {}).get(name, {})
        self.wf_current_name, self.wf_start_node, self.wf_nodes = name, wf.get("start_node", ""), {}
        self._set_entry(self.wf_name_entry, name)
        
        for nid, nd in wf.get("nodes", {}).items():
            self.wf_nodes[nid] = {"type": "end", "name": nd.get("name", "结束"), "x": nd.get("x", 100), "y": nd.get("y", 100)} if nd.get("type") == "end" else {
                "name": nd.get("name", nid), "action": nd.get("action", ""), "rules": nd.get("rules", [{"rule": nd.get("rule", ""), "success": nd.get("success", "")}] if nd.get("rule") else []),
                "failure": nd.get("failure", ""), "max_attempts": int(nd.get("max_attempts", 9999)), "max_reached": nd.get("max_reached", "end"), "x": nd.get("x", 100), "y": nd.get("y", 100)
            }
        self.wf_node_counter = max([int(nid.split("_")[1]) for nid in self.wf_nodes if nid.startswith("node_")] + [0])
        self._wf_redraw_canvas()

    def _wf_new(self):
        names, name, idx = list(self.wf_workflows_data.get("workflows", {}).keys()), "新流程", 1
        while name in names: idx += 1; name = f"新流程_{idx}"
        self.wf_workflows_data.setdefault("workflows", {})[name] = {"start_node": "", "nodes": {}}
        save_json_file(WORKFLOW_FILE, self.wf_workflows_data)
        self._refresh_workflow_list(select=name)

    def _wf_save(self):
        name = self.wf_name_entry.get().strip()
        if not name: return messagebox.showerror("错误", "名称空")
        workflows = self.wf_workflows_data.setdefault("workflows", {})
        if self.wf_current_name and self.wf_current_name != name and self.wf_current_name in workflows and messagebox.askyesno("重命名", f"删除「{self.wf_current_name}」？"):
            workflows.pop(self.wf_current_name, None)
            
        workflows[name] = {"start_node": self.wf_start_node, "nodes": {k: {**v} for k, v in self.wf_nodes.items()}}
        self.wf_workflows_data["__selected_workflow__"], self.wf_current_name = name, name
        save_json_file(WORKFLOW_FILE, self.wf_workflows_data)
        self._refresh_workflow_list(select=name)

    def _save_and_reload_workflow(self): self._wf_save(); self.wf_workflows_data = load_json_file(WORKFLOW_FILE, DEFAULT_WORKFLOW_DATA); self._refresh_workflow_list()
    def _wf_delete(self):
        if not self.wf_selector.get().strip() or not messagebox.askyesno("删除", "确定删除？"): return
        self.wf_workflows_data.get("workflows", {}).pop(self.wf_selector.get().strip(), None)
        save_json_file(WORKFLOW_FILE, self.wf_workflows_data)
        self._refresh_workflow_list()

    def _wf_import(self):
        if path := filedialog.askopenfilename(filetypes=[("JSON", "*.json")]):
            try: imported = json.load(open(path, "r", encoding="utf-8"))
            except Exception: return messagebox.showerror("错误", "无法读取")
            imported = imported.get("workflows", {os.path.splitext(os.path.basename(path))[0]: imported}) if "nodes" in imported else imported.get("workflows", imported)
            self.wf_workflows_data.setdefault("workflows", {}).update(imported)
            save_json_file(WORKFLOW_FILE, self.wf_workflows_data)
            self._refresh_workflow_list(select=list(imported.keys())[0] if imported else None)

    def _wf_export(self):
        name = self.wf_selector.get().strip() or self.wf_current_name
        if not name or name not in self.wf_workflows_data.get("workflows", {}): return
        if path := filedialog.asksaveasfilename(initialfile=f"{name}.json", defaultextension=".json"): save_json_file(path, {"workflows": {name: self.wf_workflows_data["workflows"][name]}})

    def _wf_add_action_node(self):
        self.wf_node_counter += 1
        wx, wy = self._wf_screen_to_world(int(self.wf_canvas.winfo_width() or 800) // 2, int(self.wf_canvas.winfo_height() or 600) // 2)
        wx, wy = wx - self.NODE_W // 2, wy - self.NODE_H_ACTION // 2
        for nd in self.wf_nodes.values():
            if abs(nd["x"] - wx) < 20 and abs(nd["y"] - wy) < 20: wx += 30; wy += 30
        
        nid = f"node_{self.wf_node_counter}"
        self.wf_nodes[nid] = {"name": f"节点{self.wf_node_counter}", "action": list(self.config.get("actions", {}).keys())[0] if self.config.get("actions", {}) else "", "rules": [{"rule": get_rule_names(self.rules_data)[0] if get_rule_names(self.rules_data) else "", "success": ""}], "failure": "", "max_attempts": 9999, "max_reached": "end", "x": wx, "y": wy}
        if not self.wf_start_node: self.wf_start_node = nid
        self.wf_selected_node = nid; self._wf_redraw_canvas(); self._wf_edit_node(nid)

    def _wf_add_end_node(self):
        self.wf_node_counter += 1
        wx, wy = self._wf_screen_to_world(int(self.wf_canvas.winfo_width() or 800) // 2, int(self.wf_canvas.winfo_height() or 600) // 2)
        nid = f"node_{self.wf_node_counter}"
        self.wf_nodes[nid] = {"type": "end", "name": "结束", "x": wx - self.NODE_W // 2, "y": wy - self.NODE_H_END // 2}
        self.wf_selected_node = nid; self._wf_redraw_canvas()

    def _wf_auto_layout(self):
        if not self.wf_nodes: return
        queue_nodes = [self.wf_start_node] if self.wf_start_node in self.wf_nodes else []
        queue_nodes.extend([n for n in self.wf_nodes if n not in queue_nodes])
        for i, nid in enumerate(queue_nodes):
            self.wf_nodes[nid]["x"] = 50 + (i % 4) * 280
            self.wf_nodes[nid]["y"] = 50 + (i // 4) * 150
        self._wf_redraw_canvas()

    def _wf_edit_node(self, nid):
        if not (nd := self.wf_nodes.get(nid)): return
        dlg = tk.Toplevel(self.root)
        dlg.title(f"编辑节点 - {nd.get('name', nid)}")
        dlg.geometry("500x520")
        dlg.transient(self.root); dlg.grab_set()

        is_end = nd.get("type") == "end"
        id_map = {"end": "结束 (end)", "": ""}
        name_map = {"结束 (end)": "end", "自身 (self)": "self", "": ""}
        target_displays = []
        for k, v in self.wf_nodes.items():
            if k != nid:
                disp = f"{v.get('name', k)} ({k})"
                id_map[k] = disp; name_map[disp] = k; target_displays.append(disp)
        target_displays.append("结束 (end)")

        top_frame = ttk.Frame(dlg)
        top_frame.pack(fill="x", padx=8, pady=4)
        name_entry = ttk.Entry(top_frame, width=34); name_entry.insert(0, nd.get("name", "")); name_entry.grid(row=0, column=1, sticky="we", padx=4, pady=4)
        ttk.Label(top_frame, text="节点名称:").grid(row=0, column=0, sticky="w", padx=4, pady=4)

        if not is_end:
            ttk.Label(top_frame, text="动作:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
            action_cb = ttk.Combobox(top_frame, values=list(self.config.get("actions", {}).keys()), state="readonly", width=32)
            action_cb.set(nd.get("action", "")); action_cb.grid(row=1, column=1, sticky="we", padx=4, pady=4)
            
            rule_section = ttk.LabelFrame(dlg, text="规则列表")
            rule_section.pack(fill="x", padx=8, pady=4)
            rule_container, rule_widgets = ttk.Frame(rule_section), []
            rule_container.pack(fill="x", padx=4, pady=4)

            def add_rule_row(rule_name="", success_target=""):
                f = ttk.Frame(rule_container); f.pack(fill="x", pady=2)
                ttk.Label(f, text=f"规则{len(rule_widgets)+1}:").pack(side="left", padx=(0, 4))
                rcb = ttk.Combobox(f, values=get_rule_names(self.rules_data), state="readonly", width=18); rcb.set(rule_name); rcb.pack(side="left", padx=2)
                ttk.Label(f, text="成功→").pack(side="left", padx=(8, 2))
                scb = ttk.Combobox(f, values=target_displays, state="readonly", width=14); scb.set(id_map.get(success_target, success_target)); scb.pack(side="left", padx=2)
                ttk.Button(f, text="×", width=3, command=lambda: (rule_widgets.remove((f, rcb, scb)), f.destroy()) if len(rule_widgets)>1 else None).pack(side="left", padx=4)
                rule_widgets.append((f, rcb, scb))
            
            rules = self._wf_get_rules(nd)
            for re in rules if rules else [{}]: add_rule_row(re.get("rule", ""), re.get("success", ""))
            ttk.Button(rule_section, text="+ 添加规则", command=add_rule_row).pack(pady=4)

            bottom_frame = ttk.Frame(dlg); bottom_frame.pack(fill="x", padx=8, pady=4, side="bottom")
            failure_cb = ttk.Combobox(bottom_frame, values=target_displays + ["自身 (self)"], state="readonly", width=32)
            failure_cb.set("自身 (self)" if nd.get("failure") == nid else id_map.get(nd.get("failure", ""), ""))
            max_attempts_entry = ttk.Entry(bottom_frame, width=34); max_attempts_entry.insert(0, str(nd.get("max_attempts", 9999)))
            max_reached_cb = ttk.Combobox(bottom_frame, values=target_displays + ["自身 (self)"], state="readonly", width=32)
            max_reached_cb.set("自身 (self)" if nd.get("max_reached") == nid else id_map.get(nd.get("max_reached", "end"), "结束 (end)"))
            
            ttk.Label(bottom_frame, text="失败 →").grid(row=0, column=0, sticky="w", padx=4, pady=4); failure_cb.grid(row=0, column=1, sticky="we", padx=4, pady=4)
            ttk.Label(bottom_frame, text="最大尝试:").grid(row=1, column=0, sticky="w", padx=4, pady=4); max_attempts_entry.grid(row=1, column=1, sticky="we", padx=4, pady=4)
            ttk.Label(bottom_frame, text="次数耗尽 →").grid(row=2, column=0, sticky="w", padx=4, pady=4); max_reached_cb.grid(row=2, column=1, sticky="we", padx=4, pady=4)

        btn_frame = ttk.Frame(dlg); btn_frame.pack(side="bottom", pady=8)
        def on_ok():
            nd["name"] = name_entry.get().strip() or nid
            if not is_end:
                nd["action"] = action_cb.get().strip()
                nd["rules"] = [{"rule": rcb.get().strip(), "success": name_map.get(scb.get().strip(), scb.get().strip())} for _, rcb, scb in rule_widgets]
                nd.pop("rule", None); nd.pop("success", None)
                fv = name_map.get(failure_cb.get().strip(), failure_cb.get().strip()); nd["failure"] = nid if fv == "self" else fv
                nd["max_attempts"] = self._safe_int(max_attempts_entry.get(), 9999)
                mrv = name_map.get(max_reached_cb.get().strip(), max_reached_cb.get().strip()); nd["max_reached"] = nid if mrv == "self" else mrv
            self._wf_redraw_canvas(); dlg.destroy()
        self._make_btn(btn_frame, "确定", on_ok, width=10, padx=8)
        self._make_btn(btn_frame, "取消", dlg.destroy, width=10, padx=8)

    def _get_wf_errors(self):
        errors, rule_names = [], set(get_rule_names(self.rules_data))
        if not self.wf_start_node or self.wf_start_node not in self.wf_nodes: errors.append("开始节点无效。")
        for nid, nd in self.wf_nodes.items():
            if nd.get("type") == "end": continue
            if nd.get("action") and nd.get("action") not in self.config.get("actions", {}): errors.append(f"节点 {nid} 动作不存在。")
            has_succ = False
            for r in self._wf_get_rules(nd):
                if r.get("rule") and r.get("rule") not in rule_names: errors.append(f"节点 {nid} 规则不存在。")
                if r.get("success") and r.get("success") not in self.wf_nodes and r.get("success") != "end": errors.append(f"节点 {nid} 成功目标不存在。")
                if r.get("success"): has_succ = True
            if nd.get("failure") not in self.wf_nodes and nd.get("failure") != "end" and nd.get("failure"): errors.append(f"节点 {nid} 失败目标不存在。")
            if not has_succ and not nd.get("failure"): errors.append(f"节点 {nid} 无任何出口。")
        return errors

    def _wf_validate(self):
        errors = self._get_wf_errors()
        messagebox.showerror("验证失败", "\n".join(errors)) if errors else messagebox.showinfo("验证通过", "流程无误。")
        return errors

    def _wf_run_toggle(self): self._wf_stop() if self.is_running() else self._wf_start()
    def _wf_stop(self): self.set_running(False)
    
    def _wf_start(self):
        if not pyautogui: return messagebox.showerror("错误", "需 pyautogui")
        self._wf_save()
        errors = self._get_wf_errors()
        if errors: return messagebox.showerror("验证失败", "\n".join(errors))
        if not self.wf_start_node: return
        self.set_running(True); self.ui_queue.put(("status", "running"))
        threading.Thread(target=self._wf_run_loop, daemon=True).start()

    def _wf_run_loop(self):
        current, exec_counts = self.wf_start_node, {}
        cp_hk, cp_tm, cp_pi, cp_rt, cp_be = self.config.get("copy_hotkey", DEFAULT_CONFIG["copy_hotkey"]), float(self.config.get("copy_timeout", 0.1)), float(self.config.get("copy_poll_interval", 0.005)), int(self.config.get("copy_retry", 3)), self.config.get("copy_backend", "keyboard")
        pad, pdd = float(self.config.get("post_action_delay", 0.06)), float(self.config.get("post_drop_delay", 0.01))

        try:
            while self.is_running() and current and current != "end":
                nd = self.wf_nodes.get(current)
                if not nd or nd.get("type") == "end": break
                
                exec_counts[current] = exec_counts.get(current, 0) + 1
                if exec_counts[current] > int(nd.get("max_attempts", 9999)):
                    current = nd.get("max_reached", "end"); continue
                
                self.root.after(0, self._wf_highlight_node, current)
                if nd.get("action") and not self.execute_action(nd["action"]): break
                if pad > 0 and not self._interruptible_sleep(pad): break

                rules = self._wf_get_rules(nd)
                if not rules or not [r for r in rules if r.get("rule")]:
                    current = nd.get("failure", "end"); continue

                clip_text = copy_item_text(cp_hk, cp_tm, cp_pi, cp_rt, cp_be)
                if clip_text is None and pyautogui:
                    try: pyautogui.click(button="left")
                    except Exception: pass
                    if pdd > 0: time.sleep(pdd)
                    clip_text = copy_item_text(cp_hk, cp_tm, cp_pi, max(1, cp_rt - 1), cp_be)

                if clip_text is None:
                    current = nd.get("failure", "end"); continue

                matched = False
                for r in rules:
                    if r.get("rule") and match_rules(clip_text, self.rules_data.get(r["rule"], {}))["success"]:
                        current, matched = r.get("success", "end"), True
                        if current == "end": self.set_running(False); return
                        break
                if not matched: current = nd.get("failure", "end")
                
            if current == "end": self.root.after(0, lambda: messagebox.showinfo("流程完成", "流程正常结束。"))
        except Exception as e: self.ui_queue.put(("error", str(e)))
        finally:
            self.set_running(False); self.ui_queue.put(("status", "stopped")); self.root.after(0, lambda: (self.wf_canvas.delete("highlight"), self._wf_redraw_canvas()))

    def _wf_highlight_node(self, nid):
        self._wf_redraw_canvas(); self.wf_canvas.delete("highlight")
        if nd := self.wf_nodes.get(nid):
            sx, sy = self._wf_world_to_screen(nd["x"], nd["y"])
            self.wf_canvas.create_rectangle(sx - 3, sy - 3, sx + int(self.NODE_W * self.wf_zoom_level) + 3, sy + int(self._wf_node_height(nd) * self.wf_zoom_level) + 3, outline="#FF9800", width=3, tags="highlight")

if __name__ == "__main__":
    root = tk.Tk()
    app = PoeClipboardMakerApp(root)
    root.mainloop()