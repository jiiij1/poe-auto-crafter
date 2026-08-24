#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
POE 剪贴板匹配与自定义动作工具 - 多套规则 + F9 取点 + 简化动作脚本版

功能：
1. 读取游戏内复制的装备文本
2. 根据蓝色词条、红色词条进行匹配
3. 匹配失败时执行自定义动作
4. 用户可自定义类似“改造”的动作
5. 支持多套规则切换
6. 所有规则保存在同一个 rules_config.json 文件中
7. 按 F9 读取鼠标位置并写入输入框，方便编辑动作
8. 动作编辑窗口支持简化脚本显示：
   moveTo 110,270
   click right
   sleep 0.034
"""

import os
import json
import time
import queue
import uuid
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog

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

# 多套规则文件中的特殊字段，用于记录当前选中规则
META_KEY = "__selected_rule__"


DEFAULT_CONFIG = {
    "hotkey": "F10",
    "mouse_position_hotkey": "F9",
    "copy_hotkey": ["ctrl", "c"],

    # 剪贴板与性能优化配置
    "copy_backend": "keyboard",       # 发送复制快捷键的后端：keyboard / pyautogui
    "copy_timeout": 0.067,              # 单次等待剪贴板变化超时（秒）
    "copy_poll_interval": 0.005,      # 剪贴板轮询间隔（秒）
    "copy_retry": 3,                  # 复制失败重试次数
    "pre_copy_delay": 0.01,           # 发送复制快捷键前稳定等待（秒）
    "post_action_delay": 0.01,        # 动作执行后稳定等待（秒）
    "post_drop_delay": 0.02,          # 左键放下装备后稳定等待（秒）
    "read_failure_threshold": 0.167,    # 判定长时间读取不到剪贴板的阈值（秒）
    "log_every": 10,                  # 每 N 次循环输出一次非关键日志
    "verbose_log": False,             # 是否输出详细日志

    "actions": {
        "改造": [
            {"type": "moveTo", "x": 110, "y": 270, "duration": 0},
            {"type": "click", "button": "right"},
            {"type": "sleep", "time": 0.034},
            {"type": "moveTo", "x": 330, "y": 440, "duration": 0},
            {"type": "click", "button": "left"},
            {"type": "sleep", "time": 0.034}
        ],
        "重铸点金": [
            {"type": "moveTo", "x": 490, "y": 270, "duration": 0},
            {"type": "sleep", "time": 0.03},
            {"type": "click", "button": "right"},
            {"type": "sleep", "time": 0.05},
            {"type": "moveTo", "x": 329, "y": 448, "duration": 0},
            {"type": "sleep", "time": 0.03},
            {"type": "click", "button": "left"},
            {"type": "sleep", "time": 0.05},
            {"type": "moveTo", "x": 430, "y": 400, "duration": 0},
            {"type": "sleep", "time": 0.03},
            {"type": "click", "button": "right"},
            {"type": "sleep", "time": 0.05},
            {"type": "moveTo", "x": 329, "y": 448, "duration": 0},
            {"type": "sleep", "time": 0.03},
            {"type": "click", "button": "left"},
            {"type": "sleep", "time": 0.034}
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
        "action_name": "改造",
        "dry_run": False,
        "copy_hotkey": ["ctrl", "c"],
        "affix_count_enabled": False,
        "affix_count": 3
    }
}

DEFAULT_WORKFLOW_DATA = {
    "__selected_workflow__": "",
    "workflows": {}
}


# ==================== JSON 配置工具 ====================

def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存 {path} 失败: {e}")
        return False


def load_json_file(path, default):
    try:
        if not os.path.exists(path):
            save_json_file(path, default)
            return json.loads(json.dumps(default))

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"读取 {path} 失败: {e}")
        return json.loads(json.dumps(default))


# ==================== 数字格式工具 ====================

def is_number(value):
    try:
        float(value)
        return True
    except Exception:
        return False


def to_number(value):
    """
    将字符串或数字转成 int/float。
    如果是整数，则返回 int，避免保存成 110.0。
    """
    f = float(value)
    if f.is_integer():
        return int(f)
    return f


def format_number(value):
    """
    用于简化脚本显示：
    391.0 -> 391
    0.034 -> 0.034
    """
    try:
        f = float(value)
        if f.is_integer():
            return str(int(f))
        return format(f, "g")
    except Exception:
        return str(value)


# ==================== 简化动作脚本 ====================

def action_steps_to_simple(steps, show_duration=False):
    """
    将标准动作 JSON 转成简化文本。

    支持：
    moveTo x,y
    moveTo x,y,duration
    click left/right/middle
    click left 2
    click left 2 0.05
    sleep 0.1
    hotkey ctrl+shift+c
    press f1
    keyDown shift
    keyUp shift
    """
    if not isinstance(steps, list):
        raise ValueError("动作必须是 JSON 数组")

    lines = []

    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError(f"第 {i} 步必须是 JSON 对象")

        step_type = step.get("type")

        if step_type == "moveTo":
            if "x" not in step or "y" not in step:
                raise ValueError(f"第 {i} 步 moveTo 缺少 x/y")

            x = format_number(step.get("x"))
            y = format_number(step.get("y"))

            if show_duration:
                duration = step.get("duration", 0)
                if is_number(duration) and float(duration) > 0:
                    lines.append(f"moveTo {x},{y},{format_number(duration)}")
                else:
                    lines.append(f"moveTo {x},{y}")
            else:
                lines.append(f"moveTo {x},{y}")

        elif step_type == "click":
            button = step.get("button", "left")
            if button not in {"left", "right", "middle"}:
                raise ValueError(f"第 {i} 步 click button 只能是 left/right/middle")

            line = f"click {button}"

            clicks = step.get("clicks")
            interval = step.get("interval")

            if clicks is not None or interval is not None:
                clicks_value = int(clicks) if clicks is not None else 1
                line += f" {clicks_value}"

                if interval is not None:
                    line += f" {format_number(interval)}"

            lines.append(line)

        elif step_type == "sleep":
            if "time" not in step:
                raise ValueError(f"第 {i} 步 sleep 缺少 time")
            lines.append(f"sleep {format_number(step.get('time', 0))}")

        elif step_type == "hotkey":
            keys = step.get("keys", "")
            if isinstance(keys, list):
                keys = "+".join([str(x) for x in keys])
            lines.append(f"hotkey {keys}")

        elif step_type == "press":
            lines.append(f"press {step.get('key', '')}")

        elif step_type == "keyDown":
            lines.append(f"keyDown {step.get('key', '')}")

        elif step_type == "keyUp":
            lines.append(f"keyUp {step.get('key', '')}")

        else:
            raise ValueError(f"第 {i} 步不支持的动作类型: {step_type}")

    return "\n".join(lines)


def parse_simple_action_to_steps(text):
    """
    将简化动作脚本解析为标准动作 JSON 步骤。
    """
    steps = []

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#") or line.startswith("//"):
            continue

        # 去掉行尾注释
        if "#" in line:
            line = line.split("#", 1)[0].strip()

        if "//" in line:
            line = line.split("//", 1)[0].strip()

        if not line:
            continue

        # 兼容中文逗号、中文分号
        line = line.replace("，", ",").replace("；", ";")
        line = line.rstrip(";").strip()

        parts = line.split()
        cmd = parts[0].lower()

        # ==================== moveTo ====================
        if cmd == "moveto":
            if len(parts) < 2:
                raise ValueError(f"第 {line_no} 行: moveTo 缺少坐标，例如 moveTo 110,270")

            arg_text = " ".join(parts[1:])

            if "," in arg_text:
                args = [x.strip() for x in arg_text.split(",") if x.strip()]
            else:
                args = parts[1:]

            if len(args) < 2:
                raise ValueError(f"第 {line_no} 行: moveTo 缺少 x/y，例如 moveTo 110,270")

            x = to_number(args[0])
            y = to_number(args[1])

            step = {
                "type": "moveTo",
                "x": x,
                "y": y,
                "duration": 0
            }

            if len(args) >= 3:
                step["duration"] = to_number(args[2])

            steps.append(step)

        # ==================== click ====================
        elif cmd == "click":
            button = parts[1].lower() if len(parts) >= 2 else "left"

            if button not in {"left", "right", "middle"}:
                raise ValueError(f"第 {line_no} 行: click 只能是 left/right/middle")

            step = {
                "type": "click",
                "button": button
            }

            if len(parts) >= 3:
                try:
                    clicks = int(parts[2])
                    if clicks <= 0:
                        raise ValueError
                    step["clicks"] = clicks
                except Exception:
                    raise ValueError(f"第 {line_no} 行: click 次数必须是正整数")

            if len(parts) >= 4:
                try:
                    interval = float(parts[3])
                    step["interval"] = interval
                except Exception:
                    raise ValueError(f"第 {line_no} 行: click interval 必须是数字")

            steps.append(step)

        # ==================== sleep ====================
        elif cmd == "sleep":
            if len(parts) < 2:
                raise ValueError(f"第 {line_no} 行: sleep 缺少时间，例如 sleep 0.1")

            try:
                sleep_time = to_number(parts[1])
                if float(sleep_time) < 0:
                    raise ValueError
            except Exception:
                raise ValueError(f"第 {line_no} 行: sleep 时间必须是 >=0 的数字")

            steps.append({
                "type": "sleep",
                "time": sleep_time
            })

        # ==================== hotkey ====================
        elif cmd == "hotkey":
            keys_text = " ".join(parts[1:])
            keys = normalize_hotkey_keys(keys_text)

            if not keys:
                raise ValueError(f"第 {line_no} 行: hotkey 缺少按键，例如 hotkey ctrl+shift+c")

            steps.append({
                "type": "hotkey",
                "keys": keys
            })

        # ==================== press ====================
        elif cmd == "press":
            if len(parts) < 2:
                raise ValueError(f"第 {line_no} 行: press 缺少按键，例如 press f1")

            steps.append({
                "type": "press",
                "key": parts[1]
            })

        # ==================== keyDown ====================
        elif cmd == "keydown":
            if len(parts) < 2:
                raise ValueError(f"第 {line_no} 行: keyDown 缺少按键，例如 keyDown shift")

            steps.append({
                "type": "keyDown",
                "key": parts[1]
            })

        # ==================== keyUp ====================
        elif cmd == "keyup":
            if len(parts) < 2:
                raise ValueError(f"第 {line_no} 行: keyUp 缺少按键，例如 keyUp shift")

            steps.append({
                "type": "keyUp",
                "key": parts[1]
            })

        else:
            raise ValueError(f"第 {line_no} 行: 不支持的指令: {raw_line.strip()}")

    return steps


def parse_action_text(text):
    """
    自动识别动作编辑框内容：
    1. 如果是 JSON 数组，则按 JSON 解析；
    2. 否则按简化脚本解析。
    """
    stripped = text.strip()

    if not stripped:
        return []

    if stripped.startswith("["):
        return json.loads(stripped)

    return parse_simple_action_to_steps(text)


# ==================== 多套规则工具 ====================

def get_rule_names(data):
    """
    获取规则字典中所有规则名。
    排除特殊字段 __selected_rule__。
    """
    if not isinstance(data, dict):
        return []

    return [
        key
        for key, value in data.items()
        if key != META_KEY and isinstance(value, dict)
    ]


def normalize_rules_data(data):
    """
    将不同格式的规则文件统一成当前程序使用的格式：

    {
        "__selected_rule__": "规则名",
        "规则名": {
            ...
        }
    }
    """
    if not isinstance(data, dict):
        return {META_KEY: ""}

    if "rules" in data and isinstance(data.get("rules"), dict):
        selected = data.get("selected_rule") or data.get(META_KEY) or ""
        normalized = {META_KEY: selected}

        for name, rule in data["rules"].items():
            if isinstance(rule, dict):
                normalized[name] = rule

        names = get_rule_names(normalized)
        if normalized[META_KEY] not in names:
            normalized[META_KEY] = names[0] if names else ""

        return normalized

    if "blue_rules" in data or "red_rules" in data:
        name = data.get("name") or "默认规则"
        return {
            META_KEY: name,
            name: data
        }

    selected = data.get(META_KEY) or data.get("selected_rule") or ""
    normalized = {META_KEY: selected}

    for key, value in data.items():
        if key == META_KEY:
            continue
        if key in {"selected_rule", "rules"}:
            continue
        if isinstance(value, dict):
            normalized[key] = value

    names = get_rule_names(normalized)
    if normalized[META_KEY] not in names:
        normalized[META_KEY] = names[0] if names else ""

    return normalized


def normalize_imported_rules(data, fallback_name="导入规则"):
    """
    导入规则文件时，自动识别多种格式。
    """
    if not isinstance(data, dict):
        raise ValueError("规则文件根节点必须是 JSON 对象")

    if "rules" in data and isinstance(data.get("rules"), dict):
        rules = {
            key: value
            for key, value in data["rules"].items()
            if isinstance(value, dict)
        }

        selected = data.get("selected_rule") or data.get(META_KEY) or ""

        if selected not in rules:
            selected = next(iter(rules), "")

        return rules, selected

    if "blue_rules" in data or "red_rules" in data:
        name = data.get("name") or fallback_name
        return {name: data}, name

    selected = data.get(META_KEY) or data.get("selected_rule") or ""

    rules = {
        key: value
        for key, value in data.items()
        if key not in {META_KEY, "selected_rule", "rules"} and isinstance(value, dict)
    }

    if selected not in rules:
        selected = next(iter(rules), "")

    return rules, selected


def load_rules_config():
    raw = load_json_file(RULES_FILE, DEFAULT_RULES_DATA)
    return normalize_rules_data(raw)


# ==================== 动作校验 ====================

def validate_action_steps(steps):
    """
    校验动作配置是否合法。
    动作必须是 JSON 数组，每个步骤是一个对象。
    """
    allowed_types = {
        "moveTo",
        "click",
        "sleep",
        "hotkey",
        "press",
        "keyDown",
        "keyUp"
    }

    if not isinstance(steps, list):
        raise ValueError("动作必须是 JSON 数组，例如：[{...}, {...}]")

    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError(f"第 {i} 步必须是 JSON 对象")

        step_type = step.get("type")
        if step_type not in allowed_types:
            raise ValueError(f"第 {i} 步 type 不支持: {step_type}")

        if step_type == "moveTo":
            if "x" not in step or "y" not in step:
                raise ValueError(f"第 {i} 步 moveTo 缺少 x/y")
            if not is_number(step["x"]) or not is_number(step["y"]):
                raise ValueError(f"第 {i} 步 moveTo x/y 必须是数字")
            if "duration" in step and not is_number(step["duration"]):
                raise ValueError(f"第 {i} 步 duration 必须是数字")

        elif step_type == "click":
            button = step.get("button", "left")
            if button not in {"left", "right", "middle"}:
                raise ValueError(f"第 {i} 步 click button 只能是 left/right/middle")

            if "clicks" in step:
                try:
                    clicks = int(step["clicks"])
                    if clicks <= 0:
                        raise ValueError
                except Exception:
                    raise ValueError(f"第 {i} 步 clicks 必须是正整数")

            if "interval" in step and not is_number(step["interval"]):
                raise ValueError(f"第 {i} 步 interval 必须是数字")

        elif step_type == "sleep":
            if "time" not in step:
                raise ValueError(f"第 {i} 步 sleep 缺少 time")
            if not is_number(step["time"]) or float(step["time"]) < 0:
                raise ValueError(f"第 {i} 步 sleep time 必须是 >=0 的数字")

        elif step_type == "hotkey":
            keys = step.get("keys")
            if not keys:
                raise ValueError(f"第 {i} 步 hotkey 缺少 keys")

            if isinstance(keys, str):
                if not keys.strip():
                    raise ValueError(f"第 {i} 步 hotkey keys 不能为空")
            elif isinstance(keys, list):
                if not all(isinstance(x, str) and x.strip() for x in keys):
                    raise ValueError(f"第 {i} 步 hotkey keys 必须是字符串列表")
            else:
                raise ValueError(f"第 {i} 步 hotkey keys 必须是字符串或字符串列表")

        elif step_type in {"press", "keyDown", "keyUp"}:
            if not step.get("key") or not isinstance(step.get("key"), str):
                raise ValueError(f"第 {i} 步 {step_type} 缺少 key")


def normalize_hotkey_keys(keys):
    """
    支持：
    "ctrl+shift+c"
    ["ctrl", "c"]
    "ctrl shift c"
    """
    if isinstance(keys, str):
        if "+" in keys:
            return [x.strip() for x in keys.split("+") if x.strip()]
        return [x.strip() for x in keys.split() if x.strip()]

    return [str(x).strip() for x in keys if str(x).strip()]


# ==================== 剪贴板工具 ====================

def safe_clipboard_paste(retries=3, delay=0.005):
    """
    安全读取剪贴板。
    Windows 下剪贴板可能被其他程序短暂占用，所以增加重试。
    """
    if pyperclip is None:
        return None

    for _ in range(max(1, int(retries))):
        try:
            return pyperclip.paste()
        except Exception:
            time.sleep(delay)

    return None


def safe_clipboard_copy(text, retries=3, delay=0.005):
    """
    安全写入剪贴板。
    """
    if pyperclip is None:
        return False

    for _ in range(max(1, int(retries))):
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            time.sleep(delay)

    return False


def wait_clipboard_change(base_text, timeout=0.6, poll_interval=0.005):
    """
    等待剪贴板内容变成不同于 base_text 的新内容。
    使用更短轮询间隔，减少停顿感。
    """
    if pyperclip is None:
        return None

    start = time.perf_counter()

    while time.perf_counter() - start < timeout:
        text = safe_clipboard_paste(retries=1, delay=0)

        if text is not None and text != base_text:
            return text

        time.sleep(poll_interval)

    return None


def send_copy_hotkey(copy_hotkey=None, backend="keyboard"):
    """
    发送复制快捷键。
    优先使用 keyboard 库，通常比 pyautogui.hotkey 更适合游戏。
    """
    copy_hotkey = copy_hotkey or DEFAULT_CONFIG.get("copy_hotkey", ["ctrl", "c"])
    keys = normalize_hotkey_keys(copy_hotkey)

    if not keys:
        return False

    backend = (backend or "keyboard").lower()

    if backend == "keyboard" and keyboard is not None:
        try:
            keyboard.send("+".join(keys))
            return True
        except Exception:
            pass

    if pyautogui is not None:
        try:
            pyautogui.hotkey(*keys)
            return True
        except Exception:
            return False

    return False


def copy_item_text(
    copy_hotkey=None,
    timeout=0.6,
    poll_interval=0.005,
    retries=3,
    backend="keyboard"
):
    """
    向游戏发送复制快捷键，然后等待剪贴板变化。
    优化点：
    1. 使用标记文本，避免误读旧剪贴板；
    2. 使用更短轮询间隔；
    3. 支持失败快速重试；
    4. 优先使用 keyboard 发送快捷键。
    """
    if pyperclip is None:
        return None

    marker = f"__POE_CLIPBOARD_MARKER_{uuid.uuid4().hex}__"

    for attempt in range(max(1, int(retries))):
        # 每次尝试前重新写入标记，避免读到旧内容
        if not safe_clipboard_copy(marker, retries=2, delay=0.005):
            time.sleep(0.02)
            continue

        if not send_copy_hotkey(copy_hotkey, backend=backend):
            time.sleep(0.02)
            continue

        text = wait_clipboard_change(
            base_text=marker,
            timeout=timeout,
            poll_interval=poll_interval
        )

        if text is not None and text != marker and text.strip():
            return text

        # 失败后短暂重试，不要长时间等待
        time.sleep(0.02 + attempt * 0.02)

    return None


# ==================== 文本匹配 ====================

def split_lines(text):
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]


def match_rules(clipboard_text, rules):
    """
    蓝色词条：至少匹配 min_blue_match 条。
    红色词条：必须全部包含。
    词缀数量：开启时，前缀+后缀数量必须等于指定值。
    """
    lines = split_lines(clipboard_text or "")

    blue_rules = [x for x in rules.get("blue_rules", []) if x]
    red_rules = [x for x in rules.get("red_rules", []) if x]
    min_blue_match = int(rules.get("min_blue_match", 0))

    blue_matched = []
    red_missing = []

    for rule in blue_rules:
        if any(rule in line for line in lines):
            blue_matched.append(rule)

    for rule in red_rules:
        if not any(rule in line for line in lines):
            red_missing.append(rule)

    blue_ok = len(blue_matched) >= min_blue_match
    red_ok = len(red_missing) == 0

    # 词缀数量匹配
    affix_count_enabled = bool(rules.get("affix_count_enabled", False))
    affix_count_target = int(rules.get("affix_count", 3))
    full_text = clipboard_text or ""
    prefix_count = full_text.count("前缀")
    suffix_count = full_text.count("后缀")
    affix_total = prefix_count + suffix_count
    affix_ok = (affix_total == affix_count_target) if affix_count_enabled else True

    return {
        "success": blue_ok and red_ok and affix_ok,
        "blue_ok": blue_ok,
        "red_ok": red_ok,
        "affix_ok": affix_ok,
        "blue_matched": blue_matched,
        "red_missing": red_missing,
        "blue_count": len(blue_matched),
        "min_blue_match": min_blue_match,
        "affix_count_enabled": affix_count_enabled,
        "affix_count": affix_total,
        "affix_count_target": affix_count_target,
    }


# ==================== 主程序 ====================

class PoeClipboardMakerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("POE 剪贴板匹配与自定义动作工具 - 简化动作脚本版")
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

        self.append_log("初始化完成。")
        self.append_log("提示：按 F9 可读取鼠标位置到动作配置区的 X/Y 输入框。")
        self.append_log("提示：动作编辑框默认使用简化脚本，例如：moveTo 110,270")
        self.append_log("提示：保存动作时会自动将简化脚本转换为标准 JSON。")

        self.root.after(100, self._process_ui_queue)

    # ==================== UI ====================

    def _build_ui(self):
        # ==================== Notebook 主框架 ====================
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=4, pady=4)

        # Tab 1: 规则模式（原有 UI）
        self.tab_rule_mode = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_rule_mode, text="  规则模式  ")

        # Tab 2: 流程编辑
        self.tab_workflow = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_workflow, text="  流程编辑  ")

        # ---------- 构建规则模式 Tab ----------
        self._build_rule_mode_tab(self.tab_rule_mode)

        # ---------- 构建流程编辑 Tab ----------
        self._build_workflow_tab(self.tab_workflow)

    def _build_rule_mode_tab(self, parent):
        # ==================== 运行控制 ====================
        top = ttk.LabelFrame(parent, text="运行控制")
        top.pack(fill="x", padx=8, pady=6)

        self.status_label = ttk.Label(top, text="状态: 停止", foreground="red")
        self.status_label.pack(side="left", padx=8)

        self.toggle_button = ttk.Button(
            top,
            text="开始 / 停止 (F10)",
            command=self.toggle_start_stop
        )
        self.toggle_button.pack(side="left", padx=6)

        ttk.Button(
            top,
            text="复制并测试",
            command=self.test_copy_clipboard
        ).pack(side="left", padx=6)

        ttk.Button(
            top,
            text="测试当前剪贴板",
            command=self.test_current_clipboard
        ).pack(side="left", padx=6)

        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top,
            text="试运行(不执行点击)",
            variable=self.dry_run_var
        ).pack(side="left", padx=8)

        # ==================== 规则方案 ====================
        rule_scheme_frame = ttk.LabelFrame(parent, text="规则方案（多套规则切换）")
        rule_scheme_frame.pack(fill="x", padx=8, pady=6)

        row1 = ttk.Frame(rule_scheme_frame)
        row1.pack(fill="x", padx=6, pady=2)

        ttk.Label(row1, text="选择规则:").pack(side="left")
        self.rule_selector = ttk.Combobox(row1, state="readonly", width=28)
        self.rule_selector.pack(side="left", padx=4)
        self.rule_selector.bind("<<ComboboxSelected>>", self.on_rule_selected)

        ttk.Label(row1, text="规则名:").pack(side="left", padx=(12, 2))
        self.rule_name_entry = ttk.Entry(row1, width=26)
        self.rule_name_entry.pack(side="left", padx=4)

        row2 = ttk.Frame(rule_scheme_frame)
        row2.pack(fill="x", padx=6, pady=2)

        ttk.Button(row2, text="新建规则", command=self.new_rule).pack(side="left", padx=3)
        tk.Button(row2, text="保存规则", command=self._save_and_reload_rules, bg="#d4edda", activebackground="#c3e6cb").pack(side="left", padx=3)
        ttk.Button(row2, text="删除规则", command=self.delete_current_rule).pack(side="left", padx=3)
        ttk.Button(row2, text="导入规则", command=self.import_rules_file).pack(side="left", padx=3)
        ttk.Button(row2, text="导出当前规则", command=self.export_current_rule).pack(side="left", padx=3)

        ttk.Label(
            rule_scheme_frame,
            text="所有规则保存在 rules_config.json 中。导入/导出可用于备份或分享单条规则。",
            foreground="#555"
        ).pack(anchor="w", padx=8, pady=2)

        # ==================== 匹配规则 ====================
        rule_frame = ttk.LabelFrame(parent, text="匹配规则")
        rule_frame.pack(fill="both", expand=True, padx=8, pady=6)

        left = ttk.Frame(rule_frame)
        left.pack(side="left", fill="both", expand=True, padx=6, pady=6)

        right = ttk.Frame(rule_frame)
        right.pack(side="left", fill="both", expand=True, padx=6, pady=6)

        blue_box = ttk.LabelFrame(left, text="蓝色词条：至少匹配 X 条")
        blue_box.pack(fill="both", expand=True)

        self.blue_list_frame = ttk.Frame(blue_box)
        self.blue_list_frame.pack(fill="both", expand=True, padx=4, pady=4)

        blue_buttons = ttk.Frame(blue_box)
        blue_buttons.pack(fill="x", padx=4, pady=4)

        ttk.Button(
            blue_buttons,
            text="添加蓝色词条",
            command=lambda: self.add_rule_entry("blue")
        ).pack(side="left", padx=2)

        ttk.Button(
            blue_buttons,
            text="清空蓝色",
            command=lambda: self.clear_entries("blue")
        ).pack(side="left", padx=2)

        red_box = ttk.LabelFrame(right, text="红色词条：必须全部包含")
        red_box.pack(fill="both", expand=True)

        self.red_list_frame = ttk.Frame(red_box)
        self.red_list_frame.pack(fill="both", expand=True, padx=4, pady=4)

        red_buttons = ttk.Frame(red_box)
        red_buttons.pack(fill="x", padx=4, pady=4)

        ttk.Button(
            red_buttons,
            text="添加红色词条",
            command=lambda: self.add_rule_entry("red")
        ).pack(side="left", padx=2)

        ttk.Button(
            red_buttons,
            text="清空红色",
            command=lambda: self.clear_entries("red")
        ).pack(side="left", padx=2)

        x_frame = ttk.Frame(rule_frame)
        x_frame.pack(side="bottom", fill="x", padx=6, pady=2)

        ttk.Label(x_frame, text="至少匹配 X 条蓝色词条:").pack(side="left", padx=4)
        self.min_blue_entry = ttk.Entry(x_frame, width=8)
        self.min_blue_entry.pack(side="left", padx=4)

        ttk.Label(x_frame, text="最大尝试次数:").pack(side="left", padx=(16, 4))
        self.max_attempts_entry = ttk.Entry(x_frame, width=8)
        self.max_attempts_entry.pack(side="left")

        ttk.Label(x_frame, text="    词缀数量:").pack(side="left", padx=(16, 4))
        self.affix_count_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(x_frame, text="启用", variable=self.affix_count_var).pack(side="left")
        self.affix_count_entry = ttk.Entry(x_frame, width=6)
        self.affix_count_entry.insert(0, "3")
        self.affix_count_entry.pack(side="left", padx=4)

        # ==================== 动作配置 ====================
        action_frame = ttk.LabelFrame(parent, text="动作配置（简化脚本编辑）")
        action_frame.pack(fill="both", expand=True, padx=8, pady=6)

        action_top = ttk.Frame(action_frame)
        action_top.pack(fill="x", padx=6, pady=4)

        ttk.Label(action_top, text="选择动作:").pack(side="left")

        self.action_selector = ttk.Combobox(action_top, state="readonly", width=20)
        self.action_selector.pack(side="left", padx=4)
        self.action_selector.bind("<<ComboboxSelected>>", self.on_action_selected)

        ttk.Label(action_top, text="动作名:").pack(side="left", padx=(12, 2))

        self.action_name_entry = ttk.Entry(action_top, width=18)
        self.action_name_entry.pack(side="left", padx=4)

        tk.Button(action_top, text="保存", command=self._save_and_reload_action, bg="#d4edda", activebackground="#c3e6cb").pack(side="left", padx=3)
        ttk.Button(action_top, text="删除", command=self.delete_current_action).pack(side="left", padx=3)

        # ==================== 鼠标取点 ====================
        mouse_frame = ttk.Frame(action_frame)
        mouse_frame.pack(fill="x", padx=6, pady=2)

        ttk.Label(mouse_frame, text="鼠标取点:").pack(side="left")

        ttk.Label(mouse_frame, text="X:").pack(side="left", padx=(10, 2))
        self.mouse_x_entry = ttk.Entry(mouse_frame, width=8)
        self.mouse_x_entry.pack(side="left")

        ttk.Label(mouse_frame, text="Y:").pack(side="left", padx=(10, 2))
        self.mouse_y_entry = ttk.Entry(mouse_frame, width=8)
        self.mouse_y_entry.pack(side="left")

        ttk.Button(
            mouse_frame,
            text="读取鼠标位置 (F9)",
            command=self.capture_mouse_position
        ).pack(side="left", padx=6)

        ttk.Button(
            mouse_frame,
            text="插入 moveTo",
            command=self.insert_moveto_from_mouse
        ).pack(side="left", padx=3)

        ttk.Button(
            mouse_frame,
            text="插入 moveTo + 右键",
            command=self.insert_moveto_right_click
        ).pack(side="left", padx=3)

        ttk.Button(
            mouse_frame,
            text="插入 moveTo + 左键",
            command=self.insert_moveto_left_click
        ).pack(side="left", padx=3)

        # ==================== click / sleep ====================
        click_sleep_frame = ttk.Frame(action_frame)
        click_sleep_frame.pack(fill="x", padx=6, pady=2)

        ttk.Label(click_sleep_frame, text="插入点击:").pack(side="left")

        ttk.Button(
            click_sleep_frame,
            text="click left",
            command=self.insert_click_left
        ).pack(side="left", padx=3)

        ttk.Button(
            click_sleep_frame,
            text="click right",
            command=self.insert_click_right
        ).pack(side="left", padx=3)

        ttk.Button(
            click_sleep_frame,
            text="click middle",
            command=self.insert_click_middle
        ).pack(side="left", padx=3)

        ttk.Label(click_sleep_frame, text="等待时间:").pack(side="left", padx=(16, 4))
        self.sleep_entry = ttk.Entry(click_sleep_frame, width=8)
        self.sleep_entry.insert(0, "0.1")
        self.sleep_entry.pack(side="left")

        ttk.Button(
            click_sleep_frame,
            text="插入 sleep",
            command=self.insert_sleep_from_entry
        ).pack(side="left", padx=3)

        # ==================== hotkey / press / keyDown / keyUp ====================
        key_frame = ttk.Frame(action_frame)
        key_frame.pack(fill="x", padx=6, pady=2)

        ttk.Label(key_frame, text="按键:").pack(side="left")
        self.key_entry = ttk.Entry(key_frame, width=20)
        self.key_entry.pack(side="left", padx=4)

        ttk.Button(
            key_frame,
            text="插入 hotkey",
            command=lambda: self.insert_key_step("hotkey")
        ).pack(side="left", padx=3)

        ttk.Button(
            key_frame,
            text="插入 press",
            command=lambda: self.insert_key_step("press")
        ).pack(side="left", padx=3)

        ttk.Button(
            key_frame,
            text="插入 keyDown",
            command=lambda: self.insert_key_step("keyDown")
        ).pack(side="left", padx=3)

        ttk.Button(
            key_frame,
            text="插入 keyUp",
            command=lambda: self.insert_key_step("keyUp")
        ).pack(side="left", padx=3)

        # ==================== 转换按钮 ====================
        convert_frame = ttk.Frame(action_frame)
        convert_frame.pack(fill="x", padx=6, pady=2)

        ttk.Button(
            convert_frame,
            text="插入模板",
            command=self.insert_template
        ).pack(side="left", padx=3)

        ttk.Button(
            convert_frame,
            text="转译为简化",
            command=self.convert_action_text_to_simple
        ).pack(side="left", padx=3)

        ttk.Button(
            convert_frame,
            text="转译为 JSON",
            command=self.convert_action_text_to_json
        ).pack(side="left", padx=3)

        ttk.Button(
            convert_frame,
            text="清空动作",
            command=self.clear_action_text
        ).pack(side="left", padx=3)

        hint = (
            "简化脚本格式:\n"
            "moveTo 110,270\n"
            "moveTo 110,270,0.02\n"
            "click left / click right / click middle\n"
            "click left 2 0.05\n"
            "sleep 0.1\n"
            "hotkey ctrl+shift+c\n"
            "press f1\n"
            "keyDown shift\n"
            "keyUp shift\n"
            "# 这一行是注释"
        )

        tk.Label(
            action_frame,
            text=hint,
            fg="#555",
            justify="left",
            anchor="w"
        ).pack(anchor="w", padx=8)

        self.action_text = scrolledtext.ScrolledText(
            action_frame,
            height=10,
            wrap="none",
            font=("Consolas", 11)
        )
        self.action_text.pack(fill="both", expand=True, padx=6, pady=4)

        # ==================== 日志 ====================
        log_frame = ttk.LabelFrame(parent, text="日志")
        log_frame.pack(fill="both", expand=True, padx=8, pady=6)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=10,
            state="disabled",
            wrap="word"
        )
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

    # ==================== 流程编辑器 Tab ====================

    def _build_workflow_tab(self, parent):
        # ---------- 左侧：流程管理 ----------
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
        ttk.Button(wf_btn_row, text="新建", width=8, command=self._wf_new).pack(side="left", padx=2)
        tk.Button(wf_btn_row, text="保存", width=8, command=self._save_and_reload_workflow, bg="#d4edda", activebackground="#c3e6cb").pack(side="left", padx=2)
        ttk.Button(wf_btn_row, text="删除", width=8, command=self._wf_delete).pack(side="left", padx=2)

        wf_btn_row2 = ttk.Frame(wf_list_frame)
        wf_btn_row2.pack(fill="x", padx=4, pady=2)
        ttk.Button(wf_btn_row2, text="导入", width=8, command=self._wf_import).pack(side="left", padx=2)
        ttk.Button(wf_btn_row2, text="导出", width=8, command=self._wf_export).pack(side="left", padx=2)

        # 流程名称
        wf_name_frame = ttk.LabelFrame(left_panel, text="流程名称")
        wf_name_frame.pack(fill="x", padx=4, pady=4)
        self.wf_name_entry = ttk.Entry(wf_name_frame, width=28)
        self.wf_name_entry.pack(fill="x", padx=4, pady=4)

        # 节点操作
        node_ops_frame = ttk.LabelFrame(left_panel, text="节点操作")
        node_ops_frame.pack(fill="x", padx=4, pady=4)
        ttk.Button(node_ops_frame, text="添加动作节点", command=self._wf_add_action_node).pack(fill="x", padx=4, pady=2)
        ttk.Button(node_ops_frame, text="添加结束节点", command=self._wf_add_end_node).pack(fill="x", padx=4, pady=2)
        ttk.Button(node_ops_frame, text="自动整理节点", command=self._wf_auto_layout).pack(fill="x", padx=4, pady=2)
        ttk.Button(node_ops_frame, text="验证流程", command=self._wf_validate).pack(fill="x", padx=4, pady=2)

        # 运行控制
        wf_run_frame = ttk.LabelFrame(left_panel, text="流程运行")
        wf_run_frame.pack(fill="x", padx=4, pady=4)

        self.wf_status_label = ttk.Label(wf_run_frame, text="状态: 停止", foreground="red")
        self.wf_status_label.pack(anchor="w", padx=4, pady=2)

        self.wf_run_button = ttk.Button(wf_run_frame, text="运行流程 (F10)", command=self._wf_run_toggle)
        self.wf_run_button.pack(fill="x", padx=4, pady=2)

        ttk.Button(wf_run_frame, text="停止流程", command=self._wf_stop).pack(fill="x", padx=4, pady=2)

        # 流程日志
        wf_log_frame = ttk.LabelFrame(left_panel, text="流程日志")
        wf_log_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self.wf_log_text = scrolledtext.ScrolledText(
            wf_log_frame, height=8, state="disabled", wrap="word"
        )
        self.wf_log_text.pack(fill="both", expand=True, padx=4, pady=4)

        # ---------- 右侧：Canvas 流程图画布 ----------
        right_panel = ttk.Frame(parent)
        right_panel.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        canvas_toolbar = ttk.Frame(right_panel)
        canvas_toolbar.pack(fill="x", padx=2, pady=2)
        ttk.Label(canvas_toolbar, text="缩放:").pack(side="left", padx=4)
        ttk.Button(canvas_toolbar, text="+", width=3, command=lambda: self._wf_zoom(1.2)).pack(side="left", padx=1)
        ttk.Button(canvas_toolbar, text="-", width=3, command=lambda: self._wf_zoom(1 / 1.2)).pack(side="left", padx=1)
        ttk.Button(canvas_toolbar, text="重置", width=4, command=self._wf_zoom_reset).pack(side="left", padx=4)
        ttk.Label(canvas_toolbar, text="提示: 滚轮缩放, 中键拖动画布, 左键拖拽节点, 右键菜单").pack(side="left", padx=8)

        canvas_frame = ttk.Frame(right_panel, relief="sunken", borderwidth=2)
        canvas_frame.pack(fill="both", expand=True, padx=2, pady=2)

        self.wf_canvas = tk.Canvas(canvas_frame, bg="#f0f0f0", highlightthickness=0)
        self.wf_canvas.pack(fill="both", expand=True)

        # Canvas 状态
        self.wf_zoom_level = 1.0
        self.wf_pan_x = 0
        self.wf_pan_y = 0
        self.wf_dragging_node = None
        self.wf_drag_offset_x = 0
        self.wf_drag_offset_y = 0
        self.wf_connecting_from = None
        self.wf_connecting_port = None
        self.wf_panning = False
        self.wf_pan_start_x = 0
        self.wf_pan_start_y = 0
        self.wf_selected_node = None
        self.wf_node_counter = 0

        # 流程数据
        self.wf_workflows_data = load_json_file(WORKFLOW_FILE, DEFAULT_WORKFLOW_DATA)
        self.wf_current_name = ""
        self.wf_nodes = {}
        self.wf_start_node = ""
        self.wf_max_node_exec = 200

        # Canvas 事件绑定
        self.wf_canvas.bind("<Button-1>", self._wf_canvas_click)
        self.wf_canvas.bind("<B1-Motion>", self._wf_canvas_drag)
        self.wf_canvas.bind("<ButtonRelease-1>", self._wf_canvas_release)
        self.wf_canvas.bind("<Button-3>", self._wf_canvas_right_click)
        self.wf_canvas.bind("<MouseWheel>", self._wf_canvas_mousewheel)
        self.wf_canvas.bind("<Button-2>", self._wf_canvas_mid_press)
        self.wf_canvas.bind("<B2-Motion>", self._wf_canvas_mid_drag)
        self.wf_canvas.bind("<ButtonRelease-2>", self._wf_canvas_mid_release)
        self.wf_canvas.bind("<Double-Button-1>", self._wf_canvas_double_click)

        # 右键菜单
        self.wf_context_menu = tk.Menu(self.wf_canvas, tearoff=0)

        # 初始化流程列表
        self._refresh_workflow_list()

    # ==================== UI 辅助 ====================

    def append_log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _process_ui_queue(self):
        """
        主线程定时处理子线程发来的消息。
        避免子线程直接操作 Tkinter。
        """
        try:
            while True:
                msg_type, payload = self.ui_queue.get_nowait()

                # 判断当前是否在流程 Tab
                is_wf_tab = False
                try:
                    is_wf_tab = (self.notebook.select() == str(self.tab_workflow))
                except Exception:
                    pass

                if msg_type == "log":
                    if is_wf_tab:
                        self._wf_append_log(payload)
                    else:
                        self.append_log(payload)

                elif msg_type == "success":
                    self._set_status(False)
                    if is_wf_tab:
                        self._wf_append_log("匹配成功，已停止。")
                    else:
                        self.append_log("匹配成功，已停止。")
                    messagebox.showinfo(
                        "提示",
                        f"制作成功：\n\n{payload[:1000]}"
                    )

                elif msg_type == "error":
                    if is_wf_tab:
                        self._wf_append_log(f"错误: {payload}")
                    else:
                        self.append_log(f"错误: {payload}")
                    messagebox.showerror("错误", payload)

                elif msg_type == "status":
                    if payload == "stopped":
                        self._set_status(False)
                        self._set_wf_status(False)
                    elif payload == "running":
                        self._set_status(True)
                        self._set_wf_status(True)

        except queue.Empty:
            pass

        self.root.after(100, self._process_ui_queue)

    def _set_status(self, running):
        if running:
            self.status_label.config(text="状态: 运行中", foreground="green")
            self.toggle_button.config(text="停止 (F10)")
        else:
            self.status_label.config(text="状态: 停止", foreground="red")
            self.toggle_button.config(text="开始 / 停止 (F10)")

    def _set_wf_status(self, running):
        if running:
            self.wf_status_label.config(text="状态: 运行中", foreground="green")
            self.wf_run_button.config(text="停止 (F10)")
        else:
            self.wf_status_label.config(text="状态: 停止", foreground="red")
            self.wf_run_button.config(text="运行流程 (F10)")

    # ==================== 匹配规则输入框 ====================

    def add_rule_entry(self, kind, text=""):
        if kind == "blue":
            parent = self.blue_list_frame
            store = self.blue_entries
            color = "#cfe8ff"
        else:
            parent = self.red_list_frame
            store = self.red_entries
            color = "#ffd6d6"

        row = tk.Frame(parent)
        row.pack(fill="x", padx=2, pady=2)

        entry = tk.Entry(row, bg=color)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        if text:
            entry.insert(0, text)

        item = {"row": row, "entry": entry}

        def remove():
            if item in store:
                store.remove(item)
            row.destroy()

        tk.Button(
            row,
            text="删除",
            width=6,
            command=remove
        ).pack(side="right")

        store.append(item)

    def clear_entries(self, kind):
        store = self.blue_entries if kind == "blue" else self.red_entries

        for item in store[:]:
            item["row"].destroy()

        store.clear()

    def get_entry_values(self, store):
        values = []

        for item in store:
            value = item["entry"].get().strip()
            if value:
                values.append(value)

        return values

    def collect_ui_rules(self):
        try:
            min_blue_match = int(self.min_blue_entry.get().strip() or 0)
        except ValueError:
            min_blue_match = 0

        try:
            max_attempts = int(self.max_attempts_entry.get().strip() or 200)
        except ValueError:
            max_attempts = 200

        try:
            affix_count = int(self.affix_count_entry.get().strip() or 3)
        except ValueError:
            affix_count = 3

        return {
            "blue_rules": self.get_entry_values(self.blue_entries),
            "red_rules": self.get_entry_values(self.red_entries),
            "min_blue_match": min_blue_match,
            "max_attempts": max_attempts,
            "action_name": self.action_selector.get().strip(),
            "dry_run": bool(self.dry_run_var.get()),
            "copy_hotkey": self.config.get("copy_hotkey", DEFAULT_CONFIG["copy_hotkey"]),
            "affix_count_enabled": bool(self.affix_count_var.get()),
            "affix_count": affix_count,
        }

    # ==================== 多套规则方案 ====================

    def _refresh_rule_selector(self, select=None):
        names = get_rule_names(self.rules_data)
        self.rule_selector["values"] = names

        current = self.rule_selector.get().strip()

        chosen = ""

        if select and select in names:
            chosen = select
        elif current and current in names:
            chosen = current
        elif self.rules_data.get(META_KEY) in names:
            chosen = self.rules_data.get(META_KEY)
        elif names:
            chosen = names[0]

        self.rule_selector.set(chosen)

        if chosen:
            self.rules_data[META_KEY] = chosen
            self.load_rule_to_ui(chosen)
        else:
            self.load_empty_rule_to_ui("")

    def on_rule_selected(self, event=None):
        name = self.rule_selector.get().strip()

        if name and name in get_rule_names(self.rules_data):
            self.rules_data[META_KEY] = name
            self.load_rule_to_ui(name)

    def load_empty_rule_to_ui(self, name=""):
        self.current_loaded_rule = ""

        self.rule_name_entry.delete(0, tk.END)
        if name:
            self.rule_name_entry.insert(0, name)

        self.clear_entries("blue")
        self.clear_entries("red")

        self.add_rule_entry("blue")
        self.add_rule_entry("red")

        self.min_blue_entry.delete(0, tk.END)
        self.min_blue_entry.insert(0, "0")

        self.max_attempts_entry.delete(0, tk.END)
        self.max_attempts_entry.insert(0, "200")

        self.dry_run_var.set(False)

        action_names = list(self.config.get("actions", {}).keys())
        if action_names:
            self.action_selector.set(action_names[0])
            self.on_action_selected()

    def load_rule_to_ui(self, name):
        rule = self.rules_data.get(name, {})

        self.current_loaded_rule = name

        self.rule_name_entry.delete(0, tk.END)
        self.rule_name_entry.insert(0, name)

        self.clear_entries("blue")
        self.clear_entries("red")

        for text in rule.get("blue_rules", []):
            self.add_rule_entry("blue", text)

        for text in rule.get("red_rules", []):
            self.add_rule_entry("red", text)

        if not self.blue_entries:
            self.add_rule_entry("blue")

        if not self.red_entries:
            self.add_rule_entry("red")

        self.min_blue_entry.delete(0, tk.END)
        self.min_blue_entry.insert(0, str(rule.get("min_blue_match", 0)))

        self.max_attempts_entry.delete(0, tk.END)
        self.max_attempts_entry.insert(0, str(rule.get("max_attempts", 200)))

        self.dry_run_var.set(bool(rule.get("dry_run", False)))

        self.affix_count_var.set(bool(rule.get("affix_count_enabled", False)))
        self.affix_count_entry.delete(0, tk.END)
        self.affix_count_entry.insert(0, str(rule.get("affix_count", 3)))

        action_name = rule.get("action_name", "")
        if action_name in self.config.get("actions", {}):
            self.action_selector.set(action_name)
            self.on_action_selected()

        self.append_log(f"已加载规则: {name}")

    def new_rule(self):
        names = get_rule_names(self.rules_data)

        base_name = "新规则"
        name = base_name
        index = 1

        while name in names:
            index += 1
            name = f"{base_name}_{index}"

        self.load_empty_rule_to_ui(name)
        self.rule_selector.set("")
        self.append_log(f"新建规则: {name}，填写后点击“保存规则”。")

    def save_current_rule(self):
        name = self.rule_name_entry.get().strip()

        if not name:
            messagebox.showerror("错误", "规则名不能为空")
            return

        if name in {META_KEY, "rules", "selected_rule"}:
            messagebox.showerror("错误", f"规则名不能使用保留字段: {name}")
            return

        old_name = self.current_loaded_rule

        rule = self.collect_ui_rules()
        rule["name"] = name

        if old_name and old_name != name and old_name in self.rules_data:
            if messagebox.askyesno(
                "重命名确认",
                f"检测到原规则：{old_name}\n\n"
                f"是否删除原规则，只保留新规则：{name}？\n\n"
                f"点击“是”：重命名（删除旧规则）\n"
                f"点击“否”：另存为（保留旧规则）"
            ):
                self.rules_data.pop(old_name, None)

        self.rules_data[name] = rule
        self.rules_data[META_KEY] = name

        if save_json_file(RULES_FILE, self.rules_data):
            self._refresh_rule_selector(select=name)
            self.append_log(f"规则已保存: {name}")
        else:
            messagebox.showerror("错误", "保存规则失败")

    def _save_and_reload_rules(self):
        self.save_current_rule()
        self.reload_rules_file()

    def delete_current_rule(self):
        name = (
            self.rule_selector.get().strip()
            or self.current_loaded_rule
            or self.rule_name_entry.get().strip()
        )

        names = get_rule_names(self.rules_data)

        if not name or name not in names:
            messagebox.showwarning("提示", "请先选择要删除的规则")
            return

        if not messagebox.askyesno("确认删除", f"确定删除规则：{name} ?"):
            return

        self.rules_data.pop(name, None)

        remaining_names = get_rule_names(self.rules_data)
        if remaining_names:
            self.rules_data[META_KEY] = remaining_names[0]
        else:
            self.rules_data[META_KEY] = ""

        save_json_file(RULES_FILE, self.rules_data)
        self._refresh_rule_selector(select=self.rules_data.get(META_KEY) or None)
        self.append_log(f"规则已删除: {name}")

    def reload_rules_file(self):
        self.rules_data = load_rules_config()
        save_json_file(RULES_FILE, self.rules_data)
        self._refresh_rule_selector()
        self.append_log("已重新读取规则文件: rules_config.json")

    def import_rules_file(self):
        path = filedialog.askopenfilename(
            title="选择规则文件",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")]
        )

        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("导入失败", f"无法读取规则文件:\n{path}\n\n{e}")
            return

        fallback_name = os.path.splitext(os.path.basename(path))[0]

        try:
            imported_rules, selected = normalize_imported_rules(data, fallback_name)
        except Exception as e:
            messagebox.showerror("导入失败", str(e))
            return

        if not imported_rules:
            messagebox.showwarning("导入失败", "文件中没有发现可导入的规则。")
            return

        conflicts = [
            name
            for name in imported_rules
            if name in get_rule_names(self.rules_data)
        ]

        if conflicts:
            preview = "\n".join(conflicts[:20])
            if len(conflicts) > 20:
                preview += "\n..."

            if not messagebox.askyesno(
                "导入冲突",
                f"以下规则已经存在：\n\n{preview}\n\n是否覆盖这些规则？"
            ):
                return

        self.rules_data.update(imported_rules)

        if selected and selected in imported_rules:
            self.rules_data[META_KEY] = selected
        else:
            names = get_rule_names(self.rules_data)
            if names and self.rules_data.get(META_KEY) not in names:
                self.rules_data[META_KEY] = names[0]

        if save_json_file(RULES_FILE, self.rules_data):
            self._refresh_rule_selector(select=self.rules_data.get(META_KEY))
            self.append_log(f"已导入规则文件: {path}")
        else:
            messagebox.showerror("导入失败", "保存导入后的规则文件失败。")

    def export_current_rule(self):
        name = (
            self.rule_selector.get().strip()
            or self.current_loaded_rule
            or self.rule_name_entry.get().strip()
            or "当前规则"
        )

        rule = self.rules_data.get(name)

        if rule is None:
            rule = self.collect_ui_rules()

        rule = dict(rule)
        rule["name"] = name

        path = filedialog.asksaveasfilename(
            title="导出当前规则",
            initialfile=f"{name}.json",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")]
        )

        if not path:
            return

        if save_json_file(path, rule):
            self.append_log(f"当前规则已导出: {path}")
        else:
            messagebox.showerror("导出失败", "导出当前规则失败。")

    # ==================== 动作编辑框文本辅助 ====================

    def _get_action_text(self):
        return self.action_text.get("1.0", tk.END).rstrip("\n")

    def _set_action_text(self, text):
        self.action_text.delete("1.0", tk.END)
        if text:
            self.action_text.insert("1.0", text.rstrip("\n") + "\n")

    def _ensure_action_text_is_simple(self):
        """
        如果当前动作编辑框是合法 JSON 或合法简化脚本，
        则转换成简化脚本显示。
        """
        text = self._get_action_text()

        if not text.strip():
            return ""

        try:
            steps = parse_action_text(text)
            simple = action_steps_to_simple(steps, show_duration=False)
            self._set_action_text(simple)
            return simple
        except Exception:
            return text

    def _append_simple_lines(self, lines):
        if isinstance(lines, str):
            lines = [lines]

        current = self._ensure_action_text_is_simple()
        current = current.rstrip("\n")

        addition = "\n".join(lines)

        if current:
            new_text = current + "\n" + addition + "\n"
        else:
            new_text = addition + "\n"

        self._set_action_text(new_text)

    # ==================== 鼠标取点 ====================

    def capture_mouse_position(self):
        """
        读取当前鼠标位置，并写入 X/Y 输入框。
        """
        if pyautogui is None:
            self.append_log("pyautogui 不可用，无法读取鼠标位置。")
            messagebox.showerror(
                "错误",
                "pyautogui 不可用，请先安装: pip install pyautogui"
            )
            return None

        try:
            x, y = pyautogui.position()
            x = int(x)
            y = int(y)
        except Exception as e:
            self.append_log(f"读取鼠标位置失败: {e}")
            messagebox.showerror("错误", f"读取鼠标位置失败:\n{e}")
            return None

        self.mouse_x_entry.delete(0, tk.END)
        self.mouse_x_entry.insert(0, str(x))

        self.mouse_y_entry.delete(0, tk.END)
        self.mouse_y_entry.insert(0, str(y))

        self.append_log(f"已读取鼠标位置: ({x}, {y})")
        return x, y

    def get_mouse_xy_from_inputs(self):
        """
        从 X/Y 输入框读取坐标。
        如果为空，则自动读取一次鼠标位置。
        """
        x_str = self.mouse_x_entry.get().strip()
        y_str = self.mouse_y_entry.get().strip()

        if not x_str or not y_str:
            return self.capture_mouse_position()

        try:
            x = to_number(x_str)
            y = to_number(y_str)
            return x, y
        except Exception:
            messagebox.showerror("错误", "X/Y 坐标必须是数字。请先按 F9 读取鼠标位置。")
            return None

    # ==================== 插入简化动作 ====================

    def insert_moveto_from_mouse(self):
        pos = self.get_mouse_xy_from_inputs()
        if pos is None:
            return

        x, y = pos
        line = f"moveTo {format_number(x)},{format_number(y)}"
        self._append_simple_lines(line)
        self.append_log(f"已插入: {line}")

    def insert_moveto_right_click(self):
        pos = self.get_mouse_xy_from_inputs()
        if pos is None:
            return

        x, y = pos

        lines = [
            f"moveTo {format_number(x)},{format_number(y)}",
            "sleep 0.05",
            "click right"
        ]

        self._append_simple_lines(lines)
        self.append_log(f"已插入 moveTo + 右键: ({format_number(x)}, {format_number(y)})")

    def insert_moveto_left_click(self):
        pos = self.get_mouse_xy_from_inputs()
        if pos is None:
            return

        x, y = pos

        lines = [
            f"moveTo {format_number(x)},{format_number(y)}",
            "sleep 0.05",
            "click left"
        ]

        self._append_simple_lines(lines)
        self.append_log(f"已插入 moveTo + 左键: ({format_number(x)}, {format_number(y)})")

    def insert_click_left(self):
        self._append_simple_lines("click left")
        self.append_log("已插入: click left")

    def insert_click_right(self):
        self._append_simple_lines("click right")
        self.append_log("已插入: click right")

    def insert_click_middle(self):
        self._append_simple_lines("click middle")
        self.append_log("已插入: click middle")

    def insert_sleep_from_entry(self):
        value = self.sleep_entry.get().strip()

        if not value:
            value = "0.1"

        try:
            sleep_time = to_number(value)
            if float(sleep_time) < 0:
                raise ValueError
        except Exception:
            messagebox.showerror("错误", "sleep 时间必须是 >=0 的数字")
            return

        line = f"sleep {format_number(sleep_time)}"
        self._append_simple_lines(line)
        self.append_log(f"已插入: {line}")

    def insert_key_step(self, cmd):
        key = self.key_entry.get().strip()

        if not key:
            messagebox.showerror("错误", "请先在“按键”输入框填写按键。")
            return

        if cmd == "hotkey":
            keys = normalize_hotkey_keys(key)
            if not keys:
                messagebox.showerror("错误", "hotkey 按键不能为空。")
                return

            line = f"hotkey {'+'.join(keys)}"
        else:
            line = f"{cmd} {key}"

        self._append_simple_lines(line)
        self.append_log(f"已插入: {line}")

    def convert_action_text_to_simple(self):
        text = self._get_action_text()

        try:
            steps = parse_action_text(text)
            simple = action_steps_to_simple(steps, show_duration=False)
            self._set_action_text(simple)
            self.append_log("已转译为简化动作脚本。")
        except Exception as e:
            messagebox.showerror("转译失败", str(e))

    def convert_action_text_to_json(self):
        text = self._get_action_text()

        try:
            steps = parse_action_text(text)
            validate_action_steps(steps)
            json_text = json.dumps(steps, ensure_ascii=False, indent=2)
            self._set_action_text(json_text)
            self.append_log("已转译为 JSON。")
        except Exception as e:
            messagebox.showerror("转译失败", str(e))

    def clear_action_text(self):
        current = self._get_action_text().strip()

        if current:
            if not messagebox.askyesno("确认清空", "确定清空当前动作编辑框吗？"):
                return

        self.action_text.delete("1.0", tk.END)
        self.append_log("动作编辑框已清空。")

    # ==================== 动作编辑器 ====================

    def _refresh_action_selector(self, select=None):
        names = list(self.config.get("actions", {}).keys())
        self.action_selector["values"] = names

        if select and select in names:
            self.action_selector.set(select)
        elif names:
            current = self.action_selector.get()
            if current in names:
                self.action_selector.set(current)
            else:
                self.action_selector.set(names[0])
        else:
            self.action_selector.set("")

        self.on_action_selected()

    def on_action_selected(self, event=None):
        name = self.action_selector.get().strip()

        self.action_name_entry.delete(0, tk.END)
        self.action_name_entry.insert(0, name)

        steps = self.config.get("actions", {}).get(name, [])

        try:
            simple = action_steps_to_simple(steps, show_duration=False)
        except Exception:
            simple = json.dumps(steps, ensure_ascii=False, indent=2)

        self._set_action_text(simple)

    def insert_template(self):
        template = [
            "moveTo 100,200",
            "click right",
            "sleep 0.034",
            "moveTo 300,400",
            "click left",
            "sleep 0.034"
        ]

        self._set_action_text("\n".join(template))
        self.append_log("已插入简化动作模板，可修改后保存。")

    def new_action(self):
        name = self.action_name_entry.get().strip()

        if not name:
            name = f"新动作_{len(self.config.get('actions', {})) + 1}"

        if name in self.config.get("actions", {}):
            messagebox.showwarning(
                "提示",
                f"动作 {name} 已存在。请换一个名称，或直接修改后保存覆盖。"
            )
            return

        self.action_name_entry.delete(0, tk.END)
        self.action_name_entry.insert(0, name)

        self.insert_template()
        self.append_log(f"新建动作: {name}。请修改模板后点击“保存”。")

    def save_current_action(self):
        name = self.action_name_entry.get().strip()

        if not name:
            messagebox.showerror("错误", "动作名不能为空")
            return

        if name in self.config.get("actions", {}):
            if not messagebox.askyesno("确认保存", f"动作 {name} 已存在，是否覆盖？"):
                return

        raw = self._get_action_text().strip()

        try:
            steps = parse_action_text(raw)
        except Exception as e:
            messagebox.showerror("动作解析失败", str(e))
            return

        try:
            validate_action_steps(steps)
        except Exception as e:
            messagebox.showerror("动作配置错误", str(e))
            return

        self.config.setdefault("actions", {})[name] = steps

        if save_json_file(CONFIG_FILE, self.config):
            self._refresh_action_selector(select=name)
            self.append_log(f"动作已保存: {name}")
        else:
            messagebox.showerror("错误", "保存动作配置失败")

    def _save_and_reload_action(self):
        self.save_current_action()
        self.reload_config()

    def delete_current_action(self):
        name = self.action_selector.get().strip()

        if not name:
            messagebox.showwarning("提示", "请先选择要删除的动作")
            return

        if not messagebox.askyesno("确认删除", f"确定删除动作: {name} ?"):
            return

        self.config.get("actions", {}).pop(name, None)

        if save_json_file(CONFIG_FILE, self.config):
            self._refresh_action_selector()
            self.action_name_entry.delete(0, tk.END)
            self.action_text.delete("1.0", tk.END)
            self.append_log(f"动作已删除: {name}")

    def reload_config(self):
        self.config = load_json_file(CONFIG_FILE, DEFAULT_CONFIG)
        self._refresh_action_selector()
        self.append_log("动作配置已重新加载")

    # ==================== 热键 ====================

    def _register_hotkeys(self):
        start_stop_hotkey = self.config.get("hotkey", "F10")
        mouse_position_hotkey = self.config.get("mouse_position_hotkey", "F9")

        if keyboard is None:
            self.append_log("keyboard 未安装，全局热键不可用；请使用按钮操作。")
            return

        try:
            keyboard.on_release_key(
                start_stop_hotkey,
                lambda e: self.root.after(0, self.toggle_start_stop)
            )
            self.append_log(f"全局开始/停止热键已注册: {start_stop_hotkey}")
        except Exception as e:
            self.append_log(f"开始/停止热键注册失败: {e}")

        try:
            keyboard.on_release_key(
                mouse_position_hotkey,
                lambda e: self.root.after(0, self.capture_mouse_position)
            )
            self.append_log(f"全局读取鼠标位置热键已注册: {mouse_position_hotkey}")
        except Exception as e:
            self.append_log(f"读取鼠标位置热键注册失败: {e}")

    # ==================== 运行控制 ====================

    def set_running(self, value):
        with self.running_lock:
            self.running = value

    def is_running(self):
        with self.running_lock:
            return self.running

    def toggle_start_stop(self):
        if self.is_running():
            self.stop_automation()
        else:
            # 根据当前 Tab 决定启动哪种模式
            try:
                current_tab = self.notebook.select()
                if current_tab == str(self.tab_workflow):
                    self._wf_start()
                    return
            except Exception:
                pass
            self.start_automation()

    def stop_automation(self):
        self.set_running(False)
        # 根据当前 Tab 决定日志输出位置
        try:
            current_tab = self.notebook.select()
            if current_tab == str(self.tab_workflow):
                self._wf_append_log("请求停止...")
                return
        except Exception:
            pass
        self.append_log("请求停止...")

    def start_automation(self):
        if pyautogui is None:
            messagebox.showerror(
                "错误",
                "pyautogui 不可用，请先安装: pip install pyautogui"
            )
            return

        rules = self.collect_ui_rules()

        if not rules["blue_rules"] and not rules["red_rules"]:
            if not messagebox.askyesno(
                "提示",
                "当前没有填写任何匹配词条，程序会立即判断成功。仍要继续吗？"
            ):
                return

        if not rules["dry_run"]:
            if not rules["action_name"]:
                messagebox.showerror(
                    "错误",
                    "未选择动作。请先选择动作，或勾选试运行。"
                )
                return

            if rules["action_name"] not in self.config.get("actions", {}):
                messagebox.showerror(
                    "错误",
                    f"动作不存在: {rules['action_name']}"
                )
                return

        self.set_running(True)
        self.ui_queue.put(("status", "running"))
        self.ui_queue.put(("log", "开始自动化循环"))

        threading.Thread(
            target=self.automation_loop,
            args=(rules,),
            daemon=True
        ).start()

    # ==================== 核心循环 ====================

    def automation_loop(self, rules):
        attempt = 0
        max_attempts = max(1, int(rules.get("max_attempts", 200)))

        copy_hotkey = rules.get(
            "copy_hotkey",
            self.config.get("copy_hotkey", DEFAULT_CONFIG["copy_hotkey"])
        )

        # 读取优化配置（向后兼容，旧配置没有这些字段时使用默认值）
        copy_timeout = float(self.config.get("copy_timeout", 0.067))
        copy_poll_interval = float(self.config.get("copy_poll_interval", 0.005))
        copy_retry = int(self.config.get("copy_retry", 3))
        copy_backend = self.config.get("copy_backend", "keyboard")

        pre_copy_delay = float(self.config.get("pre_copy_delay", 0.02))
        post_action_delay = float(self.config.get("post_action_delay", 0.034))
        post_drop_delay = float(self.config.get("post_drop_delay", 0.034))

        log_every = int(self.config.get("log_every", 10))
        verbose_log = bool(self.config.get("verbose_log", False))
        read_failure_threshold = float(self.config.get("read_failure_threshold", 0.167))

        try:
            while self.is_running():
                attempt += 1

                if attempt > max_attempts:
                    self.ui_queue.put((
                        "log",
                        f"已达到最大尝试次数: {max_attempts}，自动停止"
                    ))
                    break

                # 动作执行后，游戏可能需要一点时间稳定
                if pre_copy_delay > 0:
                    time.sleep(pre_copy_delay)

                # 读取剪贴板，并记录耗时，方便判断瓶颈
                start_time = time.perf_counter()

                clipboard_text = copy_item_text(
                    copy_hotkey=copy_hotkey,
                    timeout=copy_timeout,
                    poll_interval=copy_poll_interval,
                    retries=copy_retry,
                    backend=copy_backend
                )

                copy_cost = time.perf_counter() - start_time

                if clipboard_text is None:
                    # 长时间读取不到剪贴板：可能拾起了装备，尝试左键放下后重试
                    if copy_cost >= read_failure_threshold and pyautogui is not None:
                        self.ui_queue.put((
                            "log",
                            f"第 {attempt} 次剪贴板读取耗时 {copy_cost:.3f}s，"
                            f"可能拾起了装备，尝试左键放下后重试"
                        ))
                        try:
                            pyautogui.click(button="left")
                        except Exception as e:
                            self.ui_queue.put(("log", f"左键放下装备失败: {e}"))

                        if post_drop_delay > 0:
                            time.sleep(post_drop_delay)

                        # 放下后再试一次
                        clipboard_text = copy_item_text(
                            copy_hotkey=copy_hotkey,
                            timeout=copy_timeout,
                            poll_interval=copy_poll_interval,
                            retries=max(1, copy_retry - 1),
                            backend=copy_backend
                        )

                        if clipboard_text is not None:
                            copy_cost = time.perf_counter() - start_time

                    if clipboard_text is None:
                        if verbose_log or attempt == 1 or attempt % log_every == 0:
                            self.ui_queue.put((
                                "log",
                                f"第 {attempt} 次剪贴板读取失败，耗时 {copy_cost:.3f}s"
                            ))
                        # 失败后不要长等待，快速重试
                        time.sleep(0.02)
                        continue

                if verbose_log or attempt == 1 or attempt % log_every == 0:
                    self.ui_queue.put((
                        "log",
                        f"第 {attempt} 次读取成功，剪贴板耗时 {copy_cost:.3f}s"
                    ))

                # 匹配规则
                match_start = time.perf_counter()
                result = match_rules(clipboard_text, rules)
                match_cost = time.perf_counter() - match_start

                if result["success"]:
                    self.ui_queue.put((
                        "log",
                        f"匹配成功: 蓝色 {result['blue_count']}/{result['min_blue_match']}，"
                        f"红色全部满足"
                        + (f"，词缀 {result['affix_count']}/{result['affix_count_target']}" if result['affix_count_enabled'] else "")
                        + f"，匹配耗时 {match_cost:.4f}s"
                    ))
                    self.ui_queue.put(("success", clipboard_text))
                    break

                if verbose_log or attempt == 1 or attempt % log_every == 0:
                    affix_log = f"，词缀 {result['affix_count']}/{result['affix_count_target']}" if result['affix_count_enabled'] else ""
                    self.ui_queue.put((
                        "log",
                        f"第 {attempt} 次匹配失败: "
                        f"蓝色 {result['blue_count']}/{result['min_blue_match']}，"
                        f"红色缺失={result['red_missing']}{affix_log}，"
                        f"剪贴板耗时 {copy_cost:.3f}s，匹配耗时 {match_cost:.4f}s"
                    ))

                if rules.get("dry_run"):
                    self.ui_queue.put(("log", "试运行模式：跳过动作执行"))
                    time.sleep(0.02)
                    continue

                ok = self.execute_action(rules["action_name"])

                if not ok:
                    self.ui_queue.put(("log", "动作执行失败或已停止"))
                    break

                # 动作执行完毕后，给游戏一点稳定时间
                if post_action_delay > 0:
                    time.sleep(post_action_delay)

        except Exception as e:
            self.ui_queue.put(("error", f"自动化循环异常: {e}"))

        finally:
            self.set_running(False)
            self.ui_queue.put(("status", "stopped"))

    # ==================== 动作执行器 ====================

    def execute_action(self, action_name):
        steps = self.config.get("actions", {}).get(action_name)

        if steps is None:
            self.ui_queue.put(("error", f"动作不存在: {action_name}"))
            return False

        if pyautogui is None:
            self.ui_queue.put(("error", "pyautogui 不可用"))
            return False

        try:
            validate_action_steps(steps)
        except Exception as e:
            self.ui_queue.put(("error", f"动作配置错误: {e}"))
            return False

        self.ui_queue.put(("log", f"执行动作: {action_name}"))

        for idx, step in enumerate(steps, start=1):
            if not self.is_running():
                return False

            step_type = step.get("type")

            try:
                if step_type == "moveTo":
                    x = float(step.get("x"))
                    y = float(step.get("y"))
                    duration = float(step.get("duration", 0))
                    pyautogui.moveTo(x, y, duration=duration)

                elif step_type == "click":
                    button = step.get("button", "left")
                    clicks = int(step.get("clicks", 1))
                    interval = float(step.get("interval", 0))
                    pyautogui.click(button=button, clicks=clicks, interval=interval)

                elif step_type == "sleep":
                    sleep_time = max(0.0, float(step.get("time", 0)))
                    time.sleep(sleep_time)

                elif step_type == "hotkey":
                    keys = normalize_hotkey_keys(step.get("keys"))
                    if not keys:
                        raise ValueError("hotkey keys 为空")
                    pyautogui.hotkey(*keys)

                elif step_type == "press":
                    pyautogui.press(step.get("key"))

                elif step_type == "keyDown":
                    pyautogui.keyDown(step.get("key"))

                elif step_type == "keyUp":
                    pyautogui.keyUp(step.get("key"))

                else:
                    raise ValueError(f"未知动作类型: {step_type}")

            except Exception as e:
                self.ui_queue.put((
                    "error",
                    f"动作 {action_name} 第 {idx} 步执行失败: {e}"
                ))
                return False

            time.sleep(0.01)

        return True

    # ==================== 匹配测试 ====================

    def _show_match_result(self, text, source_name):
        rules = self.collect_ui_rules()
        result = match_rules(text, rules)

        self.append_log(
            f"{source_name} 匹配结果: {'成功' if result['success'] else '失败'}"
        )
        self.append_log(f"蓝色命中: {result['blue_matched']}")
        self.append_log(f"红色缺失: {result['red_missing']}")
        if result['affix_count_enabled']:
            self.append_log(f"词缀数量: {result['affix_count']}/{result['affix_count_target']} {'✓' if result['affix_ok'] else '✗'}")

        affix_info = ""
        if result['affix_count_enabled']:
            affix_info = f"词缀数量: {result['affix_count']}/{result['affix_count_target']} {'✓' if result['affix_ok'] else '✗'}\n"

        messagebox.showinfo(
            "匹配测试",
            f"{source_name}\n\n"
            f"结果: {'成功' if result['success'] else '失败'}\n"
            f"蓝色: {result['blue_count']}/{result['min_blue_match']}\n"
            f"蓝色命中: {', '.join(result['blue_matched']) or '无'}\n"
            f"红色缺失: {', '.join(result['red_missing']) or '无'}\n"
            f"{affix_info}\n"
            f"剪贴板预览:\n{text[:500]}"
        )

    def test_current_clipboard(self):
        if pyperclip is None:
            messagebox.showerror(
                "错误",
                "pyperclip 不可用，请先安装: pip install pyperclip"
            )
            return

        try:
            text = pyperclip.paste() or ""
        except Exception as e:
            messagebox.showerror("错误", f"读取剪贴板失败: {e}")
            return

        self._show_match_result(text, "当前剪贴板")

    def test_copy_clipboard(self):
        rules = self.collect_ui_rules()

        text = copy_item_text(
            copy_hotkey=rules.get("copy_hotkey"),
            timeout=1.2
        )

        if text is None:
            messagebox.showwarning(
                "提示",
                "复制失败：剪贴板没有变化。请确认游戏窗口有焦点，且复制快捷键有效。"
            )
            return

        self._show_match_result(text, "复制并测试")

    # ==================== 流程管理 ====================

    def _wf_append_log(self, message):
        self.wf_log_text.configure(state="normal")
        self.wf_log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.wf_log_text.see(tk.END)
        self.wf_log_text.configure(state="disabled")

    def _refresh_workflow_list(self, select=None):
        wf_data = self.wf_workflows_data
        names = list(wf_data.get("workflows", {}).keys())
        self.wf_selector["values"] = names

        chosen = ""
        if select and select in names:
            chosen = select
        elif wf_data.get("__selected_workflow__") in names:
            chosen = wf_data["__selected_workflow__"]
        elif names:
            chosen = names[0]

        self.wf_selector.set(chosen)
        if chosen:
            self.wf_workflows_data["__selected_workflow__"] = chosen
            self._load_workflow_to_canvas(chosen)

    def _on_workflow_selected(self, event=None):
        name = self.wf_selector.get().strip()
        if name:
            self.wf_workflows_data["__selected_workflow__"] = name
            self._load_workflow_to_canvas(name)

    def _load_workflow_to_canvas(self, name):
        wf = self.wf_workflows_data.get("workflows", {}).get(name, {})
        self.wf_current_name = name
        self.wf_name_entry.delete(0, tk.END)
        self.wf_name_entry.insert(0, name)

        self.wf_start_node = wf.get("start_node", "")
        self.wf_nodes = {}
        for nid, ndata in wf.get("nodes", {}).items():
            if ndata.get("type") == "end":
                self.wf_nodes[nid] = {
                    "type": "end",
                    "name": ndata.get("name", "结束"),
                    "x": ndata.get("x", 100),
                    "y": ndata.get("y", 100),
                }
            else:
                node = {
                    "name": ndata.get("name", nid),
                    "action": ndata.get("action", ""),
                    "failure": ndata.get("failure", ""),
                    "max_attempts": int(ndata.get("max_attempts", 9999)),
                    "max_reached": ndata.get("max_reached", "end"),
                    "x": ndata.get("x", 100),
                    "y": ndata.get("y", 100),
                }
                # 新格式：rules 列表
                if "rules" in ndata and isinstance(ndata["rules"], list):
                    node["rules"] = ndata["rules"]
                else:
                    # 旧格式兼容：单 rule 转为 rules 列表
                    rule = ndata.get("rule", "")
                    success = ndata.get("success", "")
                    node["rules"] = [{"rule": rule, "success": success}] if rule else []
                self.wf_nodes[nid] = node

        self.wf_node_counter = 0
        for nid in self.wf_nodes:
            if nid.startswith("node_"):
                try:
                    num = int(nid.split("_", 1)[1])
                    self.wf_node_counter = max(self.wf_node_counter, num)
                except ValueError:
                    pass

        self._wf_redraw_canvas()
        self._wf_append_log(f"已加载流程: {name}")

    def _wf_new(self):
        names = list(self.wf_workflows_data.get("workflows", {}).keys())
        base = "新流程"
        name = base
        idx = 1
        while name in names:
            idx += 1
            name = f"{base}_{idx}"

        self.wf_workflows_data.setdefault("workflows", {})[name] = {
            "start_node": "",
            "nodes": {}
        }
        self.wf_workflows_data["__selected_workflow__"] = name
        save_json_file(WORKFLOW_FILE, self.wf_workflows_data)
        self._refresh_workflow_list(select=name)
        self._wf_append_log(f"新建流程: {name}")

    def _wf_save(self):
        name = self.wf_name_entry.get().strip()
        if not name:
            messagebox.showerror("错误", "流程名称不能为空")
            return

        old_name = self.wf_current_name
        nodes_data = {}
        for nid, nd in self.wf_nodes.items():
            entry = {"x": nd["x"], "y": nd["y"]}
            if nd.get("type") == "end":
                entry["type"] = "end"
                entry["name"] = nd.get("name", "结束")
            else:
                entry["name"] = nd.get("name", nid)
                entry["action"] = nd.get("action", "")
                entry["rules"] = self._wf_get_rules(nd)
                entry["failure"] = nd.get("failure", "")
                entry["max_attempts"] = int(nd.get("max_attempts", 9999))
                entry["max_reached"] = nd.get("max_reached", "end")
            nodes_data[nid] = entry

        wf_obj = {
            "start_node": self.wf_start_node,
            "nodes": nodes_data
        }

        workflows = self.wf_workflows_data.setdefault("workflows", {})
        if old_name and old_name != name and old_name in workflows:
            if messagebox.askyesno("重命名确认", f"是否删除原流程「{old_name}」，只保留「{name}」？"):
                workflows.pop(old_name, None)

        workflows[name] = wf_obj
        self.wf_workflows_data["__selected_workflow__"] = name
        self.wf_current_name = name

        if save_json_file(WORKFLOW_FILE, self.wf_workflows_data):
            self._refresh_workflow_list(select=name)
            self._wf_append_log(f"流程已保存: {name}")
        else:
            messagebox.showerror("错误", "保存流程失败")

    def _save_and_reload_workflow(self):
        self._wf_save()
        self._wf_reload()

    def _wf_delete(self):
        name = self.wf_selector.get().strip()
        if not name:
            messagebox.showwarning("提示", "请先选择要删除的流程")
            return
        if not messagebox.askyesno("确认删除", f"确定删除流程「{name}」？"):
            return

        self.wf_workflows_data.get("workflows", {}).pop(name, None)
        if self.wf_workflows_data.get("__selected_workflow__") == name:
            remaining = list(self.wf_workflows_data.get("workflows", {}).keys())
            self.wf_workflows_data["__selected_workflow__"] = remaining[0] if remaining else ""

        save_json_file(WORKFLOW_FILE, self.wf_workflows_data)
        self._refresh_workflow_list()
        self._wf_append_log(f"流程已删除: {name}")

    def _wf_import(self):
        path = filedialog.askopenfilename(
            title="导入流程文件",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("导入失败", f"无法读取文件:\n{e}")
            return

        imported = data.get("workflows", data) if isinstance(data, dict) else {}
        if not isinstance(imported, dict):
            messagebox.showerror("导入失败", "文件格式不正确")
            return

        # 如果导入的是单个流程（有 nodes 字段）
        if "nodes" in imported and isinstance(imported.get("nodes"), dict):
            fname = os.path.splitext(os.path.basename(path))[0]
            imported = {fname: imported}

        workflows = self.wf_workflows_data.setdefault("workflows", {})
        conflicts = [n for n in imported if n in workflows]
        if conflicts:
            preview = "\n".join(conflicts[:20])
            if not messagebox.askyesno("导入冲突", f"以下流程已存在:\n\n{preview}\n\n是否覆盖？"):
                return

        workflows.update(imported)
        save_json_file(WORKFLOW_FILE, self.wf_workflows_data)
        self._refresh_workflow_list(select=list(imported.keys())[0])
        self._wf_append_log(f"已导入流程: {path}")

    def _wf_export(self):
        name = self.wf_selector.get().strip() or self.wf_current_name
        if not name:
            messagebox.showwarning("提示", "请先选择要导出的流程")
            return

        wf = self.wf_workflows_data.get("workflows", {}).get(name)
        if wf is None:
            messagebox.showwarning("提示", "流程数据不存在")
            return

        path = filedialog.asksaveasfilename(
            title="导出流程",
            initialfile=f"{name}.json",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")]
        )
        if not path:
            return

        export_data = {"workflows": {name: wf}}
        if save_json_file(path, export_data):
            self._wf_append_log(f"流程已导出: {path}")
        else:
            messagebox.showerror("导出失败", "导出流程失败")

    def _wf_reload(self):
        self.wf_workflows_data = load_json_file(WORKFLOW_FILE, DEFAULT_WORKFLOW_DATA)
        self._refresh_workflow_list()
        self._wf_append_log("已重新加载流程配置")

    # ==================== Canvas 坐标转换 ====================

    def _wf_screen_to_world(self, sx, sy):
        wx = (sx - self.wf_pan_x) / self.wf_zoom_level
        wy = (sy - self.wf_pan_y) / self.wf_zoom_level
        return wx, wy

    def _wf_world_to_screen(self, wx, wy):
        sx = wx * self.wf_zoom_level + self.wf_pan_x
        sy = wy * self.wf_zoom_level + self.wf_pan_y
        return sx, sy

    # ==================== Canvas 绘制 ====================

    NODE_W = 200
    NODE_H_ACTION = 120
    NODE_H_END = 50
    PORT_R = 6
    NODE_RULE_H = 25  # 每条规则额外高度

    def _wf_get_rules(self, nd):
        """获取节点的规则列表，兼容旧格式"""
        if "rules" in nd and isinstance(nd["rules"], list):
            return nd["rules"]
        # 旧格式兼容：单 rule 字段转为 rules 列表
        rule = nd.get("rule", "")
        success = nd.get("success", "")
        if rule:
            return [{"rule": rule, "success": success}]
        return []

    def _wf_node_height(self, nd):
        """根据规则数量计算节点高度"""
        if nd.get("type") == "end":
            return self.NODE_H_END
        rules = self._wf_get_rules(nd)
        extra = max(0, len(rules) - 1) * self.NODE_RULE_H
        return self.NODE_H_ACTION + extra

    def _wf_redraw_canvas(self):
        self.wf_canvas.delete("all")
        z = self.wf_zoom_level

        # 绘制网格
        cwidth = int(self.wf_canvas.winfo_width() or 800)
        cheight = int(self.wf_canvas.winfo_height() or 600)
        step = max(20, int(40 * z))
        ox = int(self.wf_pan_x) % step
        oy = int(self.wf_pan_y) % step
        for x in range(ox, cwidth, step):
            self.wf_canvas.create_line(x, 0, x, cheight, fill="#e0e0e0", tags="grid")
        for y in range(oy, cheight, step):
            self.wf_canvas.create_line(0, y, cwidth, y, fill="#e0e0e0", tags="grid")

        # 绘制连接线
        for nid, nd in self.wf_nodes.items():
            if nd.get("type") == "end":
                continue
            # 每条规则的成功连接
            rules = self._wf_get_rules(nd)
            for i, rule_entry in enumerate(rules):
                target = rule_entry.get("success", "")
                if target and target in self.wf_nodes:
                    self._wf_draw_connection(nid, f"success_{i}", target)
            # 失败和次数耗尽连接
            for port in ["failure", "max_reached"]:
                target = nd.get(port, "")
                if target and target in self.wf_nodes:
                    self._wf_draw_connection(nid, port, target)

        # 绘制节点
        for nid, nd in self.wf_nodes.items():
            self._wf_draw_node(nid, nd)

    def _wf_draw_node(self, nid, nd):
        z = self.wf_zoom_level
        x, y = nd["x"], nd["y"]
        sx, sy = self._wf_world_to_screen(x, y)
        w = int(self.NODE_W * z)
        is_end = nd.get("type") == "end"
        h = int(self._wf_node_height(nd) * z)

        # 选中高亮
        outline = "#2196F3" if nid == self.wf_selected_node else "#333"
        width = 3 if nid == self.wf_selected_node else 1

        # 开始节点标记
        is_start = (nid == self.wf_start_node)

        # 背景色
        if is_end:
            fill = "#ffcccc"
        elif is_start:
            fill = "#c8e6c9"
        else:
            fill = "#e3f2fd"

        self.wf_canvas.create_rectangle(
            sx, sy, sx + w, sy + h,
            fill=fill, outline=outline, width=width, tags=("node", f"node_{nid}")
        )

        # 节点编号和名称
        fs = max(8, int(10 * z))
        label = nd.get("name", nid)
        if is_start:
            label = f"▶ {label}"
        if is_end:
            label = f"■ {label}"

        self.wf_canvas.create_text(
            sx + w // 2, sy + int(14 * z),
            text=label, font=("Arial", fs, "bold"), tags=("node", f"node_{nid}")
        )

        if not is_end:
            action = nd.get("action", "-")
            rules = self._wf_get_rules(nd)
            info_fs = max(7, int(9 * z))
            pr = int(self.PORT_R * z)

            # 动作信息
            self.wf_canvas.create_text(
                sx + w // 2, sy + int(36 * z),
                text=f"动作: {action}", font=("Arial", info_fs), tags=("node", f"node_{nid}")
            )

            # 每条规则 + 对应的成功端口
            for i, rule_entry in enumerate(rules):
                rule_name = rule_entry.get("rule", "-")
                ry = sy + int((52 + i * self.NODE_RULE_H) * z)
                self.wf_canvas.create_text(
                    sx + w // 2, ry,
                    text=f"规则{i+1}: {rule_name}", font=("Arial", info_fs), tags=("node", f"node_{nid}")
                )
                # 该规则的成功端口
                sp_x = sx + w
                sp_y = ry
                self.wf_canvas.create_oval(
                    sp_x - pr, sp_y - pr, sp_x + pr, sp_y + pr,
                    fill="#4CAF50", outline="#333", tags=("port", f"success_{nid}_{i}")
                )
                self.wf_canvas.create_text(
                    sp_x - pr - int(4 * z), sp_y,
                    text=f"✓{i+1}", font=("Arial", max(6, int(7 * z))), fill="#4CAF50",
                    anchor="e", tags=("port_label", f"success_{nid}_{i}")
                )

            # 失败端口 - 规则下方
            fp_y = sy + int((52 + len(rules) * self.NODE_RULE_H) * z)
            fp_x = sx + w
            self.wf_canvas.create_oval(
                fp_x - pr, fp_y - pr, fp_x + pr, fp_y + pr,
                fill="#f44336", outline="#333", tags=("port", f"failure_{nid}")
            )
            self.wf_canvas.create_text(
                fp_x - pr - int(16 * z), fp_y,
                text="失败", font=("Arial", max(7, int(8 * z))), fill="#f44336",
                anchor="e", tags=("port_label", f"failure_{nid}")
            )

            # 次数耗尽端口 - 失败端口下方
            mp_y = fp_y + int(self.NODE_RULE_H * z)
            mp_x = sx + w
            self.wf_canvas.create_oval(
                mp_x - pr, mp_y - pr, mp_x + pr, mp_y + pr,
                fill="#FF9800", outline="#333", tags=("port", f"max_reached_{nid}")
            )
            self.wf_canvas.create_text(
                mp_x - pr - int(16 * z), mp_y,
                text="耗尽", font=("Arial", max(7, int(8 * z))), fill="#FF9800",
                anchor="e", tags=("port_label", f"max_reached_{nid}")
            )

            # 输入端口 - 左侧中间
            ip_x = sx
            ip_y = sy + h // 2
            self.wf_canvas.create_oval(
                ip_x - pr, ip_y - pr, ip_x + pr, ip_y + pr,
                fill="#2196F3", outline="#333", tags=("port", f"input_{nid}")
            )

    def _wf_draw_connection(self, from_nid, port, to_nid):
        z = self.wf_zoom_level
        from_nd = self.wf_nodes[from_nid]
        to_nd = self.wf_nodes[to_nid]

        # 起点：from 节点的输出端口
        fx = from_nd["x"] + self.NODE_W
        rules = self._wf_get_rules(from_nd)
        if port.startswith("success_"):
            # success_0, success_1, ...
            idx = int(port.split("_")[1])
            fy = from_nd["y"] + 52 + idx * self.NODE_RULE_H
        elif port == "failure":
            fy = from_nd["y"] + 52 + len(rules) * self.NODE_RULE_H
        else:  # max_reached
            fy = from_nd["y"] + 52 + len(rules) * self.NODE_RULE_H + self.NODE_RULE_H

        # 终点：to 节点的输入端口
        h = self._wf_node_height(to_nd)
        tx = to_nd["x"]
        ty = to_nd["y"] + h / 2

        fsx, fsy = self._wf_world_to_screen(fx, fy)
        ftx, fty = self._wf_world_to_screen(tx, ty)

        # 贝塞尔曲线控制点
        dx = abs(ftx - fsx) * 0.5
        cp1x = fsx + dx
        cp1y = fsy
        cp2x = ftx - dx
        cp2y = fty

        if port.startswith("success_"):
            color = "#4CAF50"
        elif port == "failure":
            color = "#f44336"
        else:
            color = "#FF9800"
        lw = max(1, int(2 * z))

        self.wf_canvas.create_line(
            fsx, fsy, cp1x, cp1y, cp2x, cp2y, ftx, fty,
            smooth=True, fill=color, width=lw, arrow="last",
            arrowshape=(10, 12, 5), tags="connection"
        )

        # 连接线标签
        mx = (fsx + ftx) / 2
        my = (fsy + fty) / 2 - 10
        if port.startswith("success_"):
            idx = int(port.split("_")[1])
            label = f"规则{idx+1}"
        elif port == "failure":
            label = "失败"
        else:
            label = "耗尽"
        self.wf_canvas.create_text(
            mx, my, text=label, fill=color,
            font=("Arial", max(7, int(8 * z))),
            tags="connection_label"
        )

    # ==================== Canvas 事件 ====================

    def _wf_find_node_at(self, wx, wy):
        for nid, nd in reversed(list(self.wf_nodes.items())):
            x, y = nd["x"], nd["y"]
            h = self._wf_node_height(nd)
            if x <= wx <= x + self.NODE_W and y <= wy <= y + h:
                return nid
        return None

    def _wf_find_port_at(self, wx, wy):
        z = self.wf_zoom_level
        pr = self.PORT_R * 1.5
        for nid, nd in self.wf_nodes.items():
            if nd.get("type") == "end":
                continue
            x, y = nd["x"], nd["y"]
            rules = self._wf_get_rules(nd)
            # 每条规则的成功端口
            for i in range(len(rules)):
                sp_x, sp_y = x + self.NODE_W, y + 52 + i * self.NODE_RULE_H
                if abs(wx - sp_x) <= pr and abs(wy - sp_y) <= pr:
                    return nid, f"success_{i}"
            # 失败端口
            fp_x, fp_y = x + self.NODE_W, y + 52 + len(rules) * self.NODE_RULE_H
            if abs(wx - fp_x) <= pr and abs(wy - fp_y) <= pr:
                return nid, "failure"
            # 次数耗尽端口
            mp_x, mp_y = x + self.NODE_W, y + 52 + len(rules) * self.NODE_RULE_H + self.NODE_RULE_H
            if abs(wx - mp_x) <= pr and abs(wy - mp_y) <= pr:
                return nid, "max_reached"
        return None, None

    def _wf_canvas_click(self, event):
        wx, wy = self._wf_screen_to_world(event.x, event.y)

        # 检查是否点击端口
        port_nid, port_type = self._wf_find_port_at(wx, wy)
        if port_nid:
            self.wf_connecting_from = port_nid
            self.wf_connecting_port = port_type
            return

        # 检查是否点击节点
        nid = self._wf_find_node_at(wx, wy)
        if nid:
            self.wf_selected_node = nid
            self.wf_dragging_node = nid
            nd = self.wf_nodes[nid]
            self.wf_drag_offset_x = wx - nd["x"]
            self.wf_drag_offset_y = wy - nd["y"]
            self._wf_redraw_canvas()
            return

        # 点击空白区域 - 取消选择
        self.wf_selected_node = None
        self._wf_redraw_canvas()

    def _wf_canvas_drag(self, event):
        if self.wf_dragging_node:
            wx, wy = self._wf_screen_to_world(event.x, event.y)
            nd = self.wf_nodes[self.wf_dragging_node]
            nd["x"] = wx - self.wf_drag_offset_x
            nd["y"] = wy - self.wf_drag_offset_y
            self._wf_redraw_canvas()

    def _wf_canvas_release(self, event):
        if self.wf_connecting_from:
            wx, wy = self._wf_screen_to_world(event.x, event.y)
            target = self._wf_find_node_at(wx, wy)
            if target and target != self.wf_connecting_from:
                nd = self.wf_nodes[self.wf_connecting_from]
                port = self.wf_connecting_port
                if port.startswith("success_"):
                    idx = int(port.split("_")[1])
                    rules = self._wf_get_rules(nd)
                    if idx < len(rules):
                        rules[idx]["success"] = target
                        nd["rules"] = rules
                    port_name = f"规则{idx+1}成功"
                else:
                    nd[port] = target
                    port_name = "失败" if port == "failure" else "次数耗尽"
                self._wf_redraw_canvas()
                self._wf_append_log(f"连接: {self.wf_connecting_from} [{port_name}] → {target}")
            self.wf_connecting_from = None
            self.wf_connecting_port = None

        self.wf_dragging_node = None

    def _wf_canvas_right_click(self, event):
        wx, wy = self._wf_screen_to_world(event.x, event.y)
        nid = self._wf_find_node_at(wx, wy)
        self.wf_selected_node = nid
        self._wf_redraw_canvas()

        # 动态重建右键菜单
        menu = self.wf_context_menu
        menu.delete(0, tk.END)

        state = "normal" if nid else "disabled"
        menu.add_command(label="编辑节点", command=self._wf_ctx_edit, state=state)
        menu.add_command(label="复制节点", command=self._wf_ctx_copy, state=state)
        menu.add_command(label="删除节点", command=self._wf_ctx_delete, state=state)
        menu.add_separator()
        menu.add_command(label="设为开始节点", command=self._wf_ctx_set_start, state=state)

        if nid and nid in self.wf_nodes:
            nd = self.wf_nodes[nid]
            if nd.get("type") != "end":
                rules = self._wf_get_rules(nd)
                menu.add_separator()
                for i, rule_entry in enumerate(rules):
                    rule_name = rule_entry.get("rule", f"规则{i+1}")
                    menu.add_command(
                        label=f"从规则{i+1}成功端口连接 ({rule_name})",
                        command=lambda idx=i: self._wf_ctx_connect(f"success_{idx}")
                    )
                menu.add_command(label="从失败端口连接", command=lambda: self._wf_ctx_connect("failure"))
                menu.add_command(label="从次数耗尽端口连接", command=lambda: self._wf_ctx_connect("max_reached"))

        menu.tk_popup(event.x_root, event.y_root)

    def _wf_canvas_double_click(self, event):
        wx, wy = self._wf_screen_to_world(event.x, event.y)
        nid = self._wf_find_node_at(wx, wy)
        if nid:
            self.wf_selected_node = nid
            self._wf_edit_node(nid)

    def _wf_canvas_mousewheel(self, event):
        factor = 1.1 if event.delta > 0 else 1 / 1.1
        self._wf_zoom_at(factor, event.x, event.y)

    def _wf_canvas_mid_press(self, event):
        self.wf_panning = True
        self.wf_pan_start_x = event.x
        self.wf_pan_start_y = event.y

    def _wf_canvas_mid_drag(self, event):
        if self.wf_panning:
            dx = event.x - self.wf_pan_start_x
            dy = event.y - self.wf_pan_start_y
            self.wf_pan_x += dx
            self.wf_pan_y += dy
            self.wf_pan_start_x = event.x
            self.wf_pan_start_y = event.y
            self._wf_redraw_canvas()

    def _wf_canvas_mid_release(self, event):
        self.wf_panning = False

    # ==================== 缩放 ====================

    def _wf_zoom_at(self, factor, cx, cy):
        new_zoom = self.wf_zoom_level * factor
        new_zoom = max(0.2, min(3.0, new_zoom))
        actual = new_zoom / self.wf_zoom_level
        self.wf_pan_x = cx - (cx - self.wf_pan_x) * actual
        self.wf_pan_y = cy - (cy - self.wf_pan_y) * actual
        self.wf_zoom_level = new_zoom
        self._wf_redraw_canvas()

    def _wf_zoom(self, factor):
        cw = int(self.wf_canvas.winfo_width() or 800) // 2
        ch = int(self.wf_canvas.winfo_height() or 600) // 2
        self._wf_zoom_at(factor, cw, ch)

    def _wf_zoom_reset(self):
        self.wf_zoom_level = 1.0
        self.wf_pan_x = 0
        self.wf_pan_y = 0
        self._wf_redraw_canvas()

    # ==================== 右键菜单操作 ====================

    def _wf_ctx_edit(self):
        if self.wf_selected_node:
            self._wf_edit_node(self.wf_selected_node)

    def _wf_ctx_copy(self):
        if not self.wf_selected_node:
            return
        src = self.wf_nodes[self.wf_selected_node]
        self.wf_node_counter += 1
        new_id = f"node_{self.wf_node_counter}"
        import copy
        new_nd = copy.deepcopy(src)
        new_nd["x"] = src["x"] + 30
        new_nd["y"] = src["y"] + 30
        new_nd["name"] = src.get("name", "") + " (副本)"
        if new_nd.get("type") != "end":
            # 清空规则的成功分支，保留规则名
            rules = self._wf_get_rules(new_nd)
            for r in rules:
                r["success"] = ""
            new_nd["rules"] = rules
            new_nd.pop("rule", None)
            new_nd.pop("success", None)
            new_nd["failure"] = ""
            new_nd["max_reached"] = "end"
        self.wf_nodes[new_id] = new_nd
        self.wf_selected_node = new_id
        self._wf_redraw_canvas()
        self._wf_append_log(f"已复制节点: {new_id}")

    def _wf_ctx_delete(self):
        if not self.wf_selected_node:
            return
        nid = self.wf_selected_node
        if not messagebox.askyesno("确认删除", f"确定删除节点「{self.wf_nodes[nid].get('name', nid)}」？"):
            return

        # 清除指向该节点的连接
        for nd in self.wf_nodes.values():
            if nd.get("success") == nid:
                nd["success"] = ""
            if nd.get("failure") == nid:
                nd["failure"] = ""

        if self.wf_start_node == nid:
            self.wf_start_node = ""

        del self.wf_nodes[nid]
        self.wf_selected_node = None
        self._wf_redraw_canvas()
        self._wf_append_log(f"已删除节点: {nid}")

    def _wf_ctx_set_start(self):
        if not self.wf_selected_node:
            return
        nd = self.wf_nodes[self.wf_selected_node]
        if nd.get("type") == "end":
            messagebox.showwarning("提示", "结束节点不能设为开始节点")
            return
        self.wf_start_node = self.wf_selected_node
        self._wf_redraw_canvas()
        self._wf_append_log(f"开始节点已设为: {self.wf_selected_node}")

    def _wf_ctx_connect(self, port):
        if not self.wf_selected_node:
            return
        nd = self.wf_nodes[self.wf_selected_node]
        if nd.get("type") == "end":
            messagebox.showwarning("提示", "结束节点没有输出端口")
            return
        self.wf_connecting_from = self.wf_selected_node
        self.wf_connecting_port = port
        if port.startswith("success_"):
            idx = int(port.split("_")[1])
            port_name = f"规则{idx+1}成功"
        elif port == "failure":
            port_name = "失败"
        else:
            port_name = "次数耗尽"
        self._wf_append_log(f"请左键点击目标节点完成 {port_name} 连接")

    # ==================== 节点编辑 ====================

    def _wf_edit_node(self, nid):
        nd = self.wf_nodes.get(nid)
        if not nd:
            return

        dlg = tk.Toplevel(self.root)
        dlg.title(f"编辑节点 - {nd.get('name', nid)}")
        dlg.geometry("500x520")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        is_end = nd.get("type") == "end"
        rule_names = get_rule_names(self.rules_data)
        target_names = [n for n in self.wf_nodes if n != nid] + ["end"]

        rule_widgets = []

        # ===== 顶部：名称 + 动作 =====
        top_frame = ttk.Frame(dlg)
        top_frame.pack(fill="x", padx=8, pady=4)

        ttk.Label(top_frame, text="节点名称:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        name_entry = ttk.Entry(top_frame, width=34)
        name_entry.insert(0, nd.get("name", ""))
        name_entry.grid(row=0, column=1, sticky="we", padx=4, pady=4)

        action_cb = None
        if not is_end:
            ttk.Label(top_frame, text="动作:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
            action_names = list(self.config.get("actions", {}).keys())
            action_cb = ttk.Combobox(top_frame, values=action_names, state="readonly", width=32)
            action_cb.set(nd.get("action", ""))
            action_cb.grid(row=1, column=1, sticky="we", padx=4, pady=4)

        top_frame.columnconfigure(1, weight=1)

        # ===== 中部：规则列表（可动态增删） =====
        if not is_end:
            rule_section = ttk.LabelFrame(dlg, text="规则列表（按顺序匹配，先命中先走）")
            rule_section.pack(fill="x", padx=8, pady=4)

            rule_container = ttk.Frame(rule_section)
            rule_container.pack(fill="x", padx=4, pady=4)

            def renumber_rules():
                for i, (f, _, _) in enumerate(rule_widgets):
                    for child in f.winfo_children():
                        if isinstance(child, ttk.Label) and child.cget("text").startswith("规则"):
                            child.config(text=f"规则{i+1}:")

            def add_rule_row(rule_name="", success_target=""):
                frame = ttk.Frame(rule_container)
                frame.pack(fill="x", pady=2)

                ttk.Label(frame, text=f"规则{len(rule_widgets)+1}:").pack(side="left", padx=(0, 4))
                rcb = ttk.Combobox(frame, values=rule_names, state="readonly", width=18)
                rcb.set(rule_name)
                rcb.pack(side="left", padx=2)

                ttk.Label(frame, text="成功→").pack(side="left", padx=(8, 2))
                scb = ttk.Combobox(frame, values=target_names, state="readonly", width=14)
                scb.set(success_target)
                scb.pack(side="left", padx=2)

                def remove_this():
                    if len(rule_widgets) <= 1:
                        messagebox.showwarning("提示", "至少保留一条规则")
                        return
                    rule_widgets.remove((frame, rcb, scb))
                    frame.destroy()
                    renumber_rules()

                ttk.Button(frame, text="×", width=3, command=remove_this).pack(side="left", padx=4)
                rule_widgets.append((frame, rcb, scb))

            # 加载已有规则
            rules = self._wf_get_rules(nd)
            if rules:
                for re in rules:
                    add_rule_row(re.get("rule", ""), re.get("success", ""))
            else:
                add_rule_row()

            ttk.Button(rule_section, text="+ 添加规则", command=add_rule_row).pack(pady=4)

        # ===== 底部：失败、最大次数、耗尽、按钮 =====
        bottom_frame = ttk.Frame(dlg)
        bottom_frame.pack(fill="x", padx=8, pady=4, side="bottom")

        failure_cb = None
        max_attempts_entry = None
        max_reached_cb = None

        if not is_end:
            ttk.Label(bottom_frame, text="失败 →").grid(row=0, column=0, sticky="w", padx=4, pady=4)
            failure_cb = ttk.Combobox(bottom_frame, values=target_names + ["self"], state="readonly", width=32)
            cur_failure = nd.get("failure", "")
            if cur_failure == nid:
                cur_failure = "self"
            failure_cb.set(cur_failure)
            failure_cb.grid(row=0, column=1, sticky="we", padx=4, pady=4)

            ttk.Label(bottom_frame, text="最大动作次数:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
            max_attempts_entry = ttk.Entry(bottom_frame, width=34)
            max_attempts_entry.insert(0, str(nd.get("max_attempts", 9999)))
            max_attempts_entry.grid(row=1, column=1, sticky="we", padx=4, pady=4)

            ttk.Label(bottom_frame, text="次数耗尽 →").grid(row=2, column=0, sticky="w", padx=4, pady=4)
            max_reached_cb = ttk.Combobox(bottom_frame, values=target_names + ["self"], state="readonly", width=32)
            cur_max_reached = nd.get("max_reached", "end")
            if cur_max_reached == nid:
                cur_max_reached = "self"
            max_reached_cb.set(cur_max_reached)
            max_reached_cb.grid(row=2, column=1, sticky="we", padx=4, pady=4)

            bottom_frame.columnconfigure(1, weight=1)

        # 确定/取消按钮
        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(side="bottom", pady=8)

        def on_ok():
            nd["name"] = name_entry.get().strip() or nid
            if not is_end:
                nd["action"] = action_cb.get().strip()
                new_rules = []
                for _, rcb, scb in rule_widgets:
                    r = rcb.get().strip()
                    s = scb.get().strip()
                    new_rules.append({"rule": r, "success": s if s != "end" else "end"})
                nd["rules"] = new_rules
                nd.pop("rule", None)
                nd.pop("success", None)

                fv = failure_cb.get().strip()
                if fv == "self":
                    nd["failure"] = nid
                elif fv == "end":
                    nd["failure"] = "end"
                else:
                    nd["failure"] = fv
                try:
                    nd["max_attempts"] = int(max_attempts_entry.get().strip() or 9999)
                except ValueError:
                    nd["max_attempts"] = 9999
                mrv = max_reached_cb.get().strip()
                if mrv == "self":
                    nd["max_reached"] = nid
                elif mrv == "end":
                    nd["max_reached"] = "end"
                else:
                    nd["max_reached"] = mrv
            self._wf_redraw_canvas()
            dlg.destroy()

        ttk.Button(btn_frame, text="确定", command=on_ok, width=10).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="取消", command=dlg.destroy, width=10).pack(side="left", padx=8)

    # ==================== 添加节点 ====================

    def _wf_add_action_node(self):
        self.wf_node_counter += 1
        nid = f"node_{self.wf_node_counter}"

        # 计算放置位置（Canvas 中央）
        cw = int(self.wf_canvas.winfo_width() or 800)
        ch = int(self.wf_canvas.winfo_height() or 600)
        wx, wy = self._wf_screen_to_world(cw // 2, ch // 2)
        wx -= self.NODE_W // 2
        wy -= self.NODE_H_ACTION // 2

        # 避免重叠：偏移
        for existing in self.wf_nodes.values():
            if abs(existing["x"] - wx) < 20 and abs(existing["y"] - wy) < 20:
                wx += 30
                wy += 30

        action_names = list(self.config.get("actions", {}).keys())
        rule_names = get_rule_names(self.rules_data)

        self.wf_nodes[nid] = {
            "name": f"节点{self.wf_node_counter}",
            "action": action_names[0] if action_names else "",
            "rules": [{"rule": rule_names[0] if rule_names else "", "success": ""}],
            "failure": "",
            "max_attempts": 9999,
            "max_reached": "end",
            "x": wx,
            "y": wy,
        }

        if not self.wf_start_node:
            self.wf_start_node = nid

        self.wf_selected_node = nid
        self._wf_redraw_canvas()
        self._wf_append_log(f"已添加动作节点: {nid}")
        self._wf_edit_node(nid)

    def _wf_add_end_node(self):
        self.wf_node_counter += 1
        nid = f"node_{self.wf_node_counter}"

        cw = int(self.wf_canvas.winfo_width() or 800)
        ch = int(self.wf_canvas.winfo_height() or 600)
        wx, wy = self._wf_screen_to_world(cw // 2, ch // 2)
        wx -= self.NODE_W // 2
        wy -= self.NODE_H_END // 2

        self.wf_nodes[nid] = {
            "type": "end",
            "name": "结束",
            "x": wx,
            "y": wy,
        }

        self.wf_selected_node = nid
        self._wf_redraw_canvas()
        self._wf_append_log(f"已添加结束节点: {nid}")

    # ==================== 自动整理 ====================

    def _wf_auto_layout(self):
        if not self.wf_nodes:
            return

        # 按 start_node 开始 BFS 排列
        visited = set()
        levels = []
        queue_nodes = []

        if self.wf_start_node and self.wf_start_node in self.wf_nodes:
            queue_nodes.append(self.wf_start_node)

        # 添加未连接的节点
        for nid in self.wf_nodes:
            if nid not in queue_nodes:
                queue_nodes.append(nid)

        x_spacing = 280
        y_spacing = 150
        x_start = 50
        y_start = 50

        # 简单网格排列
        col = 0
        row = 0
        for nid in queue_nodes:
            nd = self.wf_nodes[nid]
            nd["x"] = x_start + col * x_spacing
            nd["y"] = y_start + row * y_spacing
            row += 1
            if row >= 4:
                row = 0
                col += 1

        self._wf_redraw_canvas()
        self._wf_append_log("已自动整理节点布局")

    # ==================== 流程验证 ====================

    def _wf_validate(self):
        errors = self._wf_validate_workflow()
        if errors:
            messagebox.showerror("流程验证失败", "\n".join(errors))
        else:
            messagebox.showinfo("验证通过", "流程验证通过，没有发现问题。")

    def _wf_validate_workflow(self):
        errors = []
        action_names = set(self.config.get("actions", {}).keys())
        rule_names = set(get_rule_names(self.rules_data))

        if not self.wf_start_node:
            errors.append("没有设置开始节点。")
        elif self.wf_start_node not in self.wf_nodes:
            errors.append(f"开始节点「{self.wf_start_node}」不存在。")

        for nid, nd in self.wf_nodes.items():
            name = nd.get("name", nid)

            if nd.get("type") == "end":
                continue

            # 检查 action 引用
            action = nd.get("action", "")
            if action and action not in action_names:
                errors.append(f"节点「{name}」引用的动作「{action}」不存在。")

            # 检查规则列表
            rules = self._wf_get_rules(nd)
            has_success_target = False
            for i, rule_entry in enumerate(rules):
                rule = rule_entry.get("rule", "")
                if rule and rule not in rule_names:
                    errors.append(f"节点「{name}」的规则{i+1}「{rule}」不存在。")
                success = rule_entry.get("success", "")
                if success and success != "end" and success not in self.wf_nodes:
                    errors.append(f"节点「{name}」的规则{i+1}成功分支指向不存在的节点「{success}」。")
                if success:
                    has_success_target = True

            # 检查 failure 目标
            failure = nd.get("failure", "")
            if failure and failure != "end" and failure not in self.wf_nodes:
                errors.append(f"节点「{name}」的失败分支指向不存在的节点「{failure}」。")

            # 检查 max_reached 目标
            max_reached = nd.get("max_reached", "end")
            if max_reached and max_reached != "end" and max_reached not in self.wf_nodes:
                errors.append(f"节点「{name}」的次数耗尽分支指向不存在的节点「{max_reached}」。")

            # 检查是否有出口
            if not has_success_target and not failure:
                errors.append(f"节点「{name}」没有设置任何出口（规则成功分支和失败分支均为空）。")

        # 检查是否有结束节点
        has_end = any(nd.get("type") == "end" for nd in self.wf_nodes.values())
        has_end_ref = False
        for nd in self.wf_nodes.values():
            if nd.get("type") == "end":
                continue
            if nd.get("failure") == "end" or nd.get("max_reached") == "end":
                has_end_ref = True
                break
            for r in self._wf_get_rules(nd):
                if r.get("success") == "end":
                    has_end_ref = True
                    break
            if has_end_ref:
                break
        if not has_end and not has_end_ref:
            errors.append("流程中没有结束节点，也没有任何节点指向结束。")

        # 检查不可达节点（从 start_node 开始 BFS）
        if self.wf_start_node and self.wf_start_node in self.wf_nodes:
            reachable = set()
            queue_bfs = [self.wf_start_node]
            while queue_bfs:
                cur = queue_bfs.pop(0)
                if cur in reachable:
                    continue
                reachable.add(cur)
                cnd = self.wf_nodes.get(cur, {})
                if cnd.get("type") == "end":
                    continue
                targets = [cnd.get("failure", ""), cnd.get("max_reached", "")]
                for r in self._wf_get_rules(cnd):
                    targets.append(r.get("success", ""))
                for target in targets:
                    if target and target != "end" and target in self.wf_nodes and target not in reachable:
                        queue_bfs.append(target)

            for nid in self.wf_nodes:
                if nid not in reachable:
                    errors.append(f"节点「{self.wf_nodes[nid].get('name', nid)}」无法从开始节点到达。")

        return errors

    # ==================== 流程运行 ====================

    def _wf_run_toggle(self):
        if self.is_running():
            self.stop_automation()
        else:
            self._wf_start()

    def _wf_stop(self):
        self.stop_automation()

    def _wf_start(self):
        if pyautogui is None:
            messagebox.showerror("错误", "pyautogui 不可用，请先安装: pip install pyautogui")
            return

        # 先保存当前流程
        self._wf_save()

        errors = self._wf_validate_workflow()
        if errors:
            messagebox.showerror("流程验证失败", "\n".join(errors))
            return

        if not self.wf_start_node or self.wf_start_node not in self.wf_nodes:
            messagebox.showerror("错误", "开始节点无效")
            return

        self.set_running(True)
        self.ui_queue.put(("status", "running"))
        self._wf_append_log("流程开始运行...")

        threading.Thread(
            target=self._wf_run_loop,
            daemon=True
        ).start()

    def _wf_run_loop(self):
        try:
            current = self.wf_start_node

            # 每个节点的执行计数器
            node_exec_count = {}

            copy_hotkey = self.config.get("copy_hotkey", DEFAULT_CONFIG["copy_hotkey"])
            copy_timeout = float(self.config.get("copy_timeout", 0.067))
            copy_poll_interval = float(self.config.get("copy_poll_interval", 0.005))
            copy_retry = int(self.config.get("copy_retry", 3))
            copy_backend = self.config.get("copy_backend", "keyboard")
            pre_copy_delay = float(self.config.get("pre_copy_delay", 0.02))
            post_action_delay = float(self.config.get("post_action_delay", 0.034))
            post_drop_delay = float(self.config.get("post_drop_delay", 0.034))

            while self.is_running() and current and current != "end":
                nd = self.wf_nodes.get(current)
                if not nd:
                    self.ui_queue.put(("log", f"流程错误：节点 {current} 不存在"))
                    break

                if nd.get("type") == "end":
                    self.ui_queue.put(("log", "流程到达结束节点"))
                    break

                # 节点执行计数
                node_exec_count[current] = node_exec_count.get(current, 0) + 1
                max_attempts = int(nd.get("max_attempts", 9999))
                node_name = nd.get("name", current)
                action_name = nd.get("action", "")
                rules = self._wf_get_rules(nd)

                # 检查是否超过该节点的最大次数
                if node_exec_count[current] > max_attempts:
                    self.ui_queue.put(("log", f"节点「{node_name}」达到最大动作次数 ({max_attempts})，走次数耗尽分支"))
                    current = nd.get("max_reached", "end")
                    continue

                # 高亮当前节点
                self.root.after(0, self._wf_highlight_node, current)
                self.ui_queue.put(("log", f"执行节点: {node_name} (第 {node_exec_count[current]}/{max_attempts} 次)"))

                # 执行动作
                if action_name:
                    self.ui_queue.put(("log", f"  执行动作: {action_name}"))
                    ok = self.execute_action(action_name)
                    if not ok:
                        self.ui_queue.put(("log", f"  动作执行失败，停止流程"))
                        break
                    if post_action_delay > 0:
                        time.sleep(post_action_delay)

                # 读取剪贴板并按顺序匹配规则
                if rules:
                    # 收集所有需要匹配的规则名称
                    rule_names_to_check = [r.get("rule", "") for r in rules if r.get("rule")]
                    if not rule_names_to_check:
                        # 没有有效规则，走失败分支
                        current = nd.get("failure", "end")
                        continue

                    if pre_copy_delay > 0:
                        time.sleep(pre_copy_delay)

                    clipboard_text = copy_item_text(
                        copy_hotkey=copy_hotkey,
                        timeout=copy_timeout,
                        poll_interval=copy_poll_interval,
                        retries=copy_retry,
                        backend=copy_backend
                    )

                    if clipboard_text is None:
                        # 重试放下装备
                        if pyautogui is not None:
                            try:
                                pyautogui.click(button="left")
                            except Exception:
                                pass
                            if post_drop_delay > 0:
                                time.sleep(post_drop_delay)
                            clipboard_text = copy_item_text(
                                copy_hotkey=copy_hotkey,
                                timeout=copy_timeout,
                                poll_interval=copy_poll_interval,
                                retries=max(1, copy_retry - 1),
                                backend=copy_backend
                            )

                    if clipboard_text is None:
                        self.ui_queue.put(("log", f"  剪贴板读取失败，按失败分支处理"))
                        current = nd.get("failure", "end")
                        continue

                    # 按顺序匹配规则，先命中先走
                    matched = False
                    for i, rule_entry in enumerate(rules):
                        rule_name = rule_entry.get("rule", "")
                        if not rule_name:
                            continue
                        rule = self.rules_data.get(rule_name, {})
                        if not rule:
                            self.ui_queue.put(("log", f"  规则不存在: {rule_name}，跳过"))
                            continue
                        result = match_rules(clipboard_text, rule)
                        if result["success"]:
                            affix_log = f"，词缀 {result['affix_count']}/{result['affix_count_target']}" if result['affix_count_enabled'] else ""
                            self.ui_queue.put(("log", f"  规则{i+1}「{rule_name}」匹配成功: 蓝色 {result['blue_count']}/{result['min_blue_match']}{affix_log}"))
                            current = rule_entry.get("success", "end")
                            matched = True
                            break
                        else:
                            affix_log = f"，词缀 {result['affix_count']}/{result['affix_count_target']}" if result['affix_count_enabled'] else ""
                            self.ui_queue.put(("log", f"  规则{i+1}「{rule_name}」匹配失败: 蓝色 {result['blue_count']}/{result['min_blue_match']}，红色缺失={result['red_missing']}{affix_log}"))

                    if not matched:
                        self.ui_queue.put(("log", f"  所有规则均未匹配，走失败分支"))
                        current = nd.get("failure", "end")
                else:
                    # 没有规则，直接走失败分支
                    current = nd.get("failure", "end")

            if current == "end":
                self.ui_queue.put(("log", "流程正常结束"))
                self.root.after(0, lambda: messagebox.showinfo("流程完成", "流程已正常结束。"))

        except Exception as e:
            self.ui_queue.put(("error", f"流程运行异常: {e}"))

        finally:
            self.set_running(False)
            self.ui_queue.put(("status", "stopped"))
            self.root.after(0, self._wf_clear_highlights)

    def _wf_highlight_node(self, nid):
        self._wf_redraw_canvas()
        if nid in self.wf_nodes:
            nd = self.wf_nodes[nid]
            x, y = nd["x"], nd["y"]
            h = self._wf_node_height(nd)
            sx, sy = self._wf_world_to_screen(x, y)
            w = int(self.NODE_W * self.wf_zoom_level)
            hh = int(h * self.wf_zoom_level)
            self.wf_canvas.create_rectangle(
                sx - 3, sy - 3, sx + w + 3, sy + hh + 3,
                outline="#FF9800", width=3, tags="highlight"
            )

    def _wf_clear_highlights(self):
        self.wf_canvas.delete("highlight")
        self._wf_redraw_canvas()


if __name__ == "__main__":
    root = tk.Tk()
    app = PoeClipboardMakerApp(root)
    root.mainloop()