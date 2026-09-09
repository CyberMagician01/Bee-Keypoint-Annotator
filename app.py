"""蜜蜂关键点标注器：在已有检测框上标注 head/tail 等关键点。"""

import copy
import ctypes
from ctypes import wintypes
import json
import math
import os
import queue
import sys
import threading
import time
from pathlib import Path

# PyInstaller 的自动 Tcl/Tk 定位在微信深层中文目录中可能失效。
# 在导入 tkinter 前明确指向随程序发布的运行库。
if getattr(sys, "frozen", False):
    _runtime_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    for _variable, _folder in (
        ("TCL_LIBRARY", "_tcl_data"),
        ("TK_LIBRARY", "_tk_data"),
    ):
        _candidate = _runtime_dir / _folder
        if _candidate.is_dir():
            os.environ[_variable] = str(_candidate)

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageTk

try:
    import win32con
    import win32gui
except ImportError:
    win32con = None
    win32gui = None

from annotation_io import (
    DIRECTION_REVIEW_REQUIRED_FLAG,
    REVIEW_REQUIRED_FLAG,
    add_rectangle,
    add_or_replace_point,
    apply_transfer_plan,
    build_next_frame_transfer_plan,
    build_trackid_transfer_plan,
    build_transfer_plan,
    change_rectangle_group_id,
    collect_point_labels,
    confirm_direction_review_for_rectangle,
    confirm_keypoints_for_rectangle,
    copy_rectangle_with_keypoints,
    delete_nearest_point,
    delete_points,
    delete_rectangle_with_keypoints,
    direction_review_metadata,
    keypoints_by_rectangle,
    keypoints_for_rectangle,
    load_document,
    point_is_suggested,
    point_records,
    rectangle_records,
    rectangle_direction_review_is_pending,
    rectangle_is_direction_review_item,
    rectangle_review_is_pending,
    rename_point_label,
    review_progress,
    save_document,
    set_rectangle_bounds,
    swap_keypoint_labels,
)
from core import (
    adjust_rectangle_bounds,
    direction_angle_degrees,
    point_from_relative,
    point_in_rect,
    rect_area,
    rectangle_handle_mode,
    rectangle_drag_mode,
    relative_position,
    smallest_containing_rectangle,
    symmetric_point,
)


SOURCE_DIR = Path(__file__).resolve().parent
IS_FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else SOURCE_DIR
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
ASSETS_DIR = RESOURCE_DIR / "assets"
SETTINGS_PATH = APP_DIR / "settings.json"
APP_ICON_PATH = ASSETS_DIR / "bee_annotator_icon.png"
ASSISTANT_IMAGE_PATH = ASSETS_DIR / "bee_assistant_v1.png"
DEFAULT_FOLDER = Path.home() / "BeeAnnotationData"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
DEFAULT_LONG_PRESS_DELAY_MS = 250
MIN_LONG_PRESS_DELAY_MS = 100
MAX_LONG_PRESS_DELAY_MS = 1000
LONG_PRESS_DELAY_STEP_MS = 50
QUICK_CLICK_MAX_MOVEMENT_PX = 6.0

COLORS = {
    "background": "#F3F0E8",
    "surface": "#FFFFFF",
    "surface_alt": "#FAF7F0",
    "border": "#D9D4C8",
    "text": "#172033",
    "muted": "#667085",
    "navy": "#172033",
    "navy_hover": "#25324A",
    "gold": "#F4B942",
    "gold_hover": "#FFC95C",
    "amber_soft": "#FFF3D4",
    "green": "#1F9D75",
    "green_hover": "#27AF85",
    "orange": "#E98724",
    "danger": "#C94C4C",
    "canvas": "#151922",
}

ACTION_DEFINITIONS = [
    ("previous_image", "上一张图片", ("Z", "无")),
    ("next_image", "下一张图片", ("C", "无")),
    ("previous_rectangle", "上一个检测框", ("Q", "鼠标侧键1")),
    ("next_rectangle", "下一个检测框", ("E", "鼠标侧键2")),
    ("select_label_1", "选择第 1 个标签", ("1", "无")),
    ("select_label_2", "选择第 2 个标签", ("2", "无")),
    ("toggle_symmetry", "开启/关闭中心对称", ("Ctrl+M", "无")),
    ("save", "保存当前标注", ("Ctrl+S", "无")),
    ("undo", "撤销", ("Ctrl+Z", "无")),
    ("delete_label_point", "删除当前标签点", ("Delete", "无")),
    ("delete_all_points", "删除当前框全部点", ("Shift+Delete", "无")),
    ("open_folder", "打开文件夹", ("Ctrl+O", "无")),
    ("open_iou", "打开 IoU 传播", ("Ctrl+I", "无")),
    ("open_label_manager", "打开标签管理", ("Ctrl+L", "无")),
    ("open_shortcut_manager", "打开快捷键设置", ("Ctrl+K", "无")),
    ("open_help", "打开帮助", ("F1", "无")),
    ("toggle_label_names", "显示/隐藏标签名", ("Ctrl+T", "无")),
    ("toggle_track_ids", "显示/隐藏检测框 ID", ("B", "无")),
    ("toggle_bee_shadow_class", "切换 bee / beeshadow", ("F", "无")),
    ("toggle_box_class_labels", "显示/隐藏框类别", ("Y", "无")),
    ("toggle_other_boxes", "显示/隐藏其他框", ("Ctrl+B", "无")),
    ("previous_track_frame", "同 ID 上一次出现", ("A", "无")),
    ("next_track_frame", "同 ID 下一次出现", ("D", "无")),
    ("next_track_id", "下一个 Track ID", ("W", "无")),
    ("copy_frame_to_next", "整帧关键点复制到下一张", ("R", "无")),
    ("propagate_track", "按 Track ID 传播", ("Ctrl+P", "无")),
    ("confirm_keypoints", "确认当前关键点", ("Space", "无")),
    ("start_continuous_annotation", "进入连续补标/切换下一个 ID", ("V", "无")),
    ("clear_default_track_id", "清除默认 Track ID", ("无", "无")),
    ("toggle_continuous_other_boxes", "显示/隐藏全部检测框", ("H", "无")),
    ("swap_head_tail", "交换 head/tail", ("X", "无")),
    ("next_review_issue", "下一待审/异常", ("N", "无")),
    ("open_frame_table", "打开本帧框清单", ("G", "无")),
]

ACTION_LABELS = {action_id: label for action_id, label, _default in ACTION_DEFINITIONS}
DEFAULT_SHORTCUTS = {
    action_id: list(defaults) for action_id, _label, defaults in ACTION_DEFINITIONS
}


class TrackIdQueryDialog(simpledialog._QueryInteger):
    """与 Track ID 修正器一致：除回车外，也可按 G 确认。"""

    def body(self, master):
        entry = super().body(master)
        entry.bind("<g>", self._confirm_with_g)
        entry.bind("<G>", self._confirm_with_g)
        return entry

    def _confirm_with_g(self, _event):
        self.ok()
        return "break"


class TrackIdChangeDialog(TrackIdQueryDialog):
    """输入新 ID，并选择修改当前帧、此前或此后的同轨迹框。"""

    def body(self, master):
        entry = super().body(master)
        self.scope_var = tk.StringVar(value="current")
        ttk.Label(master, text="修改范围：").grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(10, 2)
        )
        choices = (
            ("仅修改当前帧", "current"),
            ("当前帧及此前所有相同旧 ID", "before"),
            ("当前帧及此后所有相同旧 ID", "after"),
        )
        for row, (label, value) in enumerate(choices, start=3):
            ttk.Radiobutton(
                master,
                text=label,
                variable=self.scope_var,
                value=value,
            ).grid(row=row, column=0, columnspan=2, sticky=tk.W)
        return entry

    def apply(self):
        self.selected_scope = self.scope_var.get()
        super().apply()


class BeeKeypointAnnotator:
    def __init__(self, root: tk.Tk, initial_folder: Optional[Path] = None) -> None:
        self.root = root
        self.root.title("蜜蜂关键点标注器")
        self.root.geometry("1520x900")
        self.root.minsize(1100, 700)
        try:
            self.root.state("zoomed")
        except tk.TclError:
            pass
        self.root.configure(background=COLORS["background"])
        self.app_icon = None
        if APP_ICON_PATH.exists():
            try:
                self.app_icon = tk.PhotoImage(file=str(APP_ICON_PATH))
                self.root.iconphoto(True, self.app_icon)
            except tk.TclError:
                self.app_icon = None
        self.brand_photo = self._load_ui_asset(APP_ICON_PATH, 38, 38)
        self.assistant_empty_photo = self._load_ui_asset(
            ASSISTANT_IMAGE_PATH, 190, 190
        )
        self.assistant_help_photo = self._load_ui_asset(
            ASSISTANT_IMAGE_PATH, 94, 94
        )

        self.settings = self._load_settings()
        self.shortcuts = self._load_shortcuts()
        self.long_press_delay_ms = self._normalize_long_press_delay(
            self.settings.get("long_press_delay_ms", DEFAULT_LONG_PRESS_DELAY_MS)
        )
        self.bound_shortcut_sequences = set()
        self.keyboard_poll_job = None
        self.pressed_shortcut_keys = set()
        self.keyboard_event_queue = queue.SimpleQueue()
        self.keyboard_hook_thread = None
        self.keyboard_hook_thread_id = 0
        self.keyboard_hook_handle = None
        self.keyboard_hook_callback = None
        self.keyboard_hook_active = False
        self.keyboard_hook_stop = threading.Event()
        self.keyboard_hook_keys_down = set()
        self.main_window_handle = 0
        self.native_window_hooks = {}
        self.native_window_procedure = None
        self.native_install_attempts = 0
        labels = self.settings.get("labels") or ["head", "tail"]
        self.labels: List[str] = list(dict.fromkeys(str(label) for label in labels if label))
        if not self.labels:
            self.labels = ["head", "tail"]

        self.active_label = tk.StringVar(value=self.labels[0])
        self.symmetry_enabled = tk.BooleanVar(
            value=bool(self.settings.get("symmetry_enabled", False))
        )
        self.symmetry_source_label = tk.StringVar(
            value=self.settings.get("symmetry_source_label", "head")
        )
        self.symmetry_target_label = tk.StringVar(
            value=self.settings.get("symmetry_target_label", "tail")
        )
        configured_ratio = float(self.settings.get("symmetry_ratio", 1.0))
        self.symmetry_ratio = tk.DoubleVar(
            value=min(2.0, max(0.0, configured_ratio))
        )
        self.symmetry_ratio_text = tk.StringVar(
            value=f"{self.symmetry_ratio.get() * 100:.0f}%"
        )
        self.show_label_names = tk.BooleanVar(
            value=bool(self.settings.get("show_label_names", False))
        )
        self.show_track_ids = tk.BooleanVar(
            value=bool(self.settings.get("show_track_ids", True))
        )
        self.show_box_class_labels = tk.BooleanVar(
            value=bool(self.settings.get("show_box_class_labels", False))
        )
        self.show_other_boxes = tk.BooleanVar(
            value=bool(self.settings.get("show_other_boxes", True))
        )
        self.image_name_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请选择数据文件夹")
        self.progress_var = tk.StringVar(value="")
        self.points_var = tk.StringVar(value="")
        self.direction_review_summary_var = tk.StringVar(value="")
        self.confirmation_progress = tk.DoubleVar(value=0.0)
        self.default_track_id_text = tk.StringVar(value="")
        self.default_track_id_status_var = tk.StringVar(value="默认 ID：未启用")
        self.max_track_id_var = tk.StringVar(value="任务最大 ID：—")

        self.folder: Optional[Path] = None
        self.images: List[Path] = []
        self.documents: Dict[Path, Dict] = {}
        self.dirty_images = set()
        self.undo_stacks: Dict[Path, List[List[Dict]]] = {}
        self.undo_order: List[Tuple[Path, int]] = []
        self.selected_rectangle_by_image: Dict[Path, int] = {}
        self.current_image_index = -1
        self.current_rectangle_index = -1
        self.current_pil_image: Optional[Image.Image] = None
        self.track_id_mode = False
        self.track_mode_current_id = None
        self.direction_review_mode = False
        self.direction_review_item_index = -1
        self.track_completion_dialog = None
        self.refresh_track_stats = None

        self.overview_photo = None
        self.direction_review_photo = None
        self.detail_photo = None
        self.overview_transform: Optional[Tuple[float, float, float]] = None
        self.detail_transform: Optional[Tuple[float, float, float]] = None
        self.overview_cache_key = None
        self.overview_static_key = None
        self.overview_dirty_positions: Dict[Path, set] = {}
        self.detail_cache_key = None
        self.redraw_job = None
        self.autosave_job = None
        self.rectangle_drag = None
        self.middle_pan = None
        self.detail_view_centers: Dict[Tuple[Path, int], Tuple[float, float]] = {}
        self.default_track_id = None
        self.default_track_id_explicit = False
        self.rectangle_creation = None
        self.continuous_annotation_mode = False
        self.continuous_active_track_id = None
        self.continuous_draw_box_mode = False
        self.continuous_focus_point = None
        self.continuous_zoom_factor = 3.0
        self.pointer_press = None
        self.frame_completion_state: Dict[Path, bool] = {}

        self._build_style()
        self._build_ui()
        self._bind_shortcuts()
        self._install_native_mouse_buttons()
        self.root.after(300, self._ensure_native_mouse_buttons)
        self.root.after(450, self._balance_main_pane)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        configured_folder = initial_folder or Path(
            self.settings.get("default_folder", str(DEFAULT_FOLDER))
        )
        initial_folder = configured_folder if configured_folder.exists() else DEFAULT_FOLDER
        if initial_folder.exists():
            self.load_folder(initial_folder)

    # ----------------------------- 初始化与设置 -----------------------------

    @staticmethod
    def _normalize_long_press_delay(value) -> int:
        try:
            delay = int(round(float(value) / LONG_PRESS_DELAY_STEP_MS))
            delay *= LONG_PRESS_DELAY_STEP_MS
        except (TypeError, ValueError):
            delay = DEFAULT_LONG_PRESS_DELAY_MS
        return min(MAX_LONG_PRESS_DELAY_MS, max(MIN_LONG_PRESS_DELAY_MS, delay))

    @staticmethod
    def _load_ui_asset(path: Path, width: int, height: int):
        if not path.exists():
            return None
        try:
            with Image.open(path) as image:
                rendered = image.convert("RGBA")
                rendered.thumbnail((width, height), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(rendered)
        except (OSError, tk.TclError):
            return None

    def _load_settings(self) -> Dict:
        defaults = {
            "labels": ["head", "tail"],
            "symmetry_source_label": "head",
            "symmetry_target_label": "tail",
            "symmetry_enabled": False,
            "symmetry_ratio": 1.0,
            "show_label_names": False,
            "show_track_ids": True,
            "show_box_class_labels": False,
            "show_other_boxes": True,
            "iou_threshold": 0.5,
            "long_press_delay_ms": DEFAULT_LONG_PRESS_DELAY_MS,
            "shortcuts": copy.deepcopy(DEFAULT_SHORTCUTS),
            "default_folder": str(DEFAULT_FOLDER),
        }
        try:
            with SETTINGS_PATH.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
            defaults.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
        return defaults

    def _load_shortcuts(self) -> Dict[str, List[str]]:
        configured = self.settings.get("shortcuts") or {}
        shortcuts = copy.deepcopy(DEFAULT_SHORTCUTS)
        for action_id in shortcuts:
            value = configured.get(action_id)
            if isinstance(value, str):
                value = [value, "无"]
            if isinstance(value, list):
                cleaned = [str(item) for item in value[:2]]
                while len(cleaned) < 2:
                    cleaned.append("无")
                shortcuts[action_id] = cleaned

        # v1.2.1：A/D 改为沿同一 Track ID 导航，普通图片切换改为 Z/C。
        # 只迁移旧版默认映射，不覆盖用户主动设置的其他快捷键。
        legacy_previous_image = configured.get("previous_image") in (
            "A",
            ["A"],
            ["A", "无"],
        )
        legacy_next_image = configured.get("next_image") in (
            "D",
            ["D"],
            ["D", "无"],
        )
        legacy_previous_track = configured.get("previous_track_frame") in (
            None,
            "J",
            ["J"],
            ["J", "无"],
        )
        legacy_next_track = configured.get("next_track_frame") in (
            None,
            "K",
            ["K"],
            ["K", "无"],
        )
        if (
            legacy_previous_image
            and legacy_next_image
            and legacy_previous_track
            and legacy_next_track
        ):
            shortcuts["previous_image"] = ["Z", "无"]
            shortcuts["next_image"] = ["C", "无"]
            shortcuts["previous_track_frame"] = ["A", "无"]
            shortcuts["next_track_frame"] = ["D", "无"]

        # v1.5.3：整帧关键点复制快捷键由 P 改为 R。
        # 只迁移旧版默认映射，不覆盖用户主动设置的其他快捷键。
        if configured.get("copy_frame_to_next") in (
            "P",
            ["P"],
            ["P", "无"],
        ):
            shortcuts["copy_frame_to_next"] = ["R", "无"]

        # B 现在专门控制检测框 ID；旧版清除默认 ID 仍保留按钮操作。
        if configured.get("toggle_track_ids") is None:
            shortcuts["toggle_track_ids"] = ["B", "无"]
            if configured.get("clear_default_track_id") in (
                "V",
                "B",
                ["V"],
                ["B"],
                ["V", "无"],
                ["B", "无"],
            ):
                shortcuts["clear_default_track_id"] = ["无", "无"]
        return shortcuts

    def _save_settings(self) -> None:
        self.settings.update(
            {
                "labels": self.labels,
                "symmetry_source_label": self.symmetry_source_label.get(),
                "symmetry_target_label": self.symmetry_target_label.get(),
                "symmetry_enabled": self.symmetry_enabled.get(),
                "symmetry_ratio": round(self.symmetry_ratio.get(), 4),
                "show_label_names": self.show_label_names.get(),
                "show_track_ids": self.show_track_ids.get(),
                "show_box_class_labels": self.show_box_class_labels.get(),
                "show_other_boxes": self.show_other_boxes.get(),
                "long_press_delay_ms": self.long_press_delay_ms,
                "shortcuts": self.shortcuts,
                "default_folder": str(self.folder or DEFAULT_FOLDER),
            }
        )
        with SETTINGS_PATH.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(self.settings, file, ensure_ascii=False, indent=2)
            file.write("\n")

    def _build_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        font = ("Microsoft YaHei UI", 9)
        style.configure("TFrame", background=COLORS["background"])
        style.configure(
            "TLabel",
            background=COLORS["background"],
            foreground=COLORS["text"],
            font=font,
        )
        style.configure(
            "TButton",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["surface"],
            darkcolor=COLORS["surface"],
            font=font,
            padding=(10, 6),
            relief="flat",
        )
        style.map(
            "TButton",
            background=[("active", COLORS["surface_alt"]), ("pressed", "#ECE7DC")],
        )
        style.configure(
            "Primary.TButton",
            background=COLORS["gold"],
            foreground=COLORS["navy"],
            bordercolor=COLORS["gold"],
            lightcolor=COLORS["gold"],
            darkcolor=COLORS["gold"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[
                ("active", COLORS["gold_hover"]),
                ("pressed", "#E7A92D"),
            ],
        )
        style.configure(
            "Success.TButton",
            background=COLORS["green"],
            foreground="#FFFFFF",
            bordercolor=COLORS["green"],
            lightcolor=COLORS["green"],
            darkcolor=COLORS["green"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Success.TButton",
            background=[
                ("active", COLORS["green_hover"]),
                ("pressed", "#17815F"),
            ],
        )
        style.configure(
            "Warning.TButton",
            background=COLORS["orange"],
            foreground="#FFFFFF",
            bordercolor=COLORS["orange"],
            lightcolor=COLORS["orange"],
            darkcolor=COLORS["orange"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "Danger.TButton",
            foreground=COLORS["danger"],
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Header.TFrame",
            background=COLORS["navy"],
        )
        style.configure(
            "Header.TLabel",
            background=COLORS["navy"],
            foreground="#FFFFFF",
        )
        style.configure(
            "Brand.TLabel",
            background=COLORS["navy"],
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 14, "bold"),
        )
        style.configure(
            "BrandSub.TLabel",
            background=COLORS["navy"],
            foreground="#BFC9DA",
            font=("Microsoft YaHei UI", 8),
        )
        style.configure(
            "Header.TButton",
            background=COLORS["navy_hover"],
            foreground="#FFFFFF",
            bordercolor="#394760",
            lightcolor=COLORS["navy_hover"],
            darkcolor=COLORS["navy_hover"],
            padding=(11, 6),
        )
        style.map(
            "Header.TButton",
            background=[("active", "#33415C"), ("pressed", "#111827")],
        )
        style.configure("Toolbar.TFrame", background=COLORS["surface_alt"])
        style.configure("Card.TFrame", background=COLORS["surface"])
        style.configure("Review.TFrame", background=COLORS["amber_soft"])
        style.configure(
            "Review.TLabel",
            background=COLORS["amber_soft"],
            foreground=COLORS["text"],
        )
        style.configure(
            "ReviewTitle.TLabel",
            background=COLORS["amber_soft"],
            foreground=COLORS["navy"],
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure(
            "LegendPending.TLabel",
            background=COLORS["amber_soft"],
            foreground="#B97700",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "LegendConfirmed.TLabel",
            background=COLORS["amber_soft"],
            foreground=COLORS["green"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "Title.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
            foreground=COLORS["text"],
        )
        style.configure(
            "Muted.TLabel",
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        )
        style.configure(
            "Section.TLabel",
            font=("Microsoft YaHei UI", 9, "bold"),
            foreground=COLORS["muted"],
        )
        style.configure(
            "StatusBar.TFrame",
            background=COLORS["navy"],
        )
        style.configure(
            "Status.TLabel",
            background=COLORS["navy"],
            foreground="#CFD7E6",
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "StatusStrong.TLabel",
            background=COLORS["navy"],
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "Horizontal.TProgressbar",
            background=COLORS["gold"],
            troughcolor="#344057",
            bordercolor="#344057",
            lightcolor=COLORS["gold"],
            darkcolor=COLORS["gold"],
        )
        style.configure(
            "ToolGroup.TLabelframe",
            background=COLORS["surface_alt"],
            bordercolor=COLORS["border"],
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "ToolGroup.TLabelframe.Label",
            background=COLORS["surface_alt"],
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        style.configure(
            "Workspace.TLabelframe",
            background=COLORS["canvas"],
            bordercolor=COLORS["border"],
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Workspace.TLabelframe.Label",
            background=COLORS["background"],
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "Tool.TCheckbutton",
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            font=font,
        )
        style.map(
            "Tool.TCheckbutton",
            background=[("active", COLORS["surface_alt"])],
        )
        style.configure(
            "Review.TCheckbutton",
            background=COLORS["amber_soft"],
            foreground=COLORS["text"],
            font=font,
        )
        style.map(
            "Review.TCheckbutton",
            background=[("active", COLORS["amber_soft"])],
        )
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(14, 8))
        header.pack(fill=tk.X)
        if self.brand_photo is not None:
            ttk.Label(
                header,
                image=self.brand_photo,
                style="Header.TLabel",
            ).pack(side=tk.LEFT, padx=(0, 9))
        brand = ttk.Frame(header, style="Header.TFrame")
        brand.pack(side=tk.LEFT)
        ttk.Label(
            brand,
            text="蜜蜂关键点标注器",
            style="Brand.TLabel",
        ).pack(anchor=tk.W)
        ttk.Label(
            brand,
            text="Track ID 驱动的 head / tail 高效复审工作台",
            style="BrandSub.TLabel",
        ).pack(anchor=tk.W)
        ttk.Button(
            header,
            text="帮助  F1",
            style="Header.TButton",
            command=self.open_help,
        ).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(
            header,
            text="快捷键",
            style="Header.TButton",
            command=self.open_shortcut_manager,
        ).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(
            header,
            text="IoU 备用传播",
            style="Header.TButton",
            command=self.open_iou_dialog,
        ).pack(side=tk.RIGHT, padx=(6, 0))

        toolbar = ttk.Frame(
            self.root,
            style="Toolbar.TFrame",
            padding=(12, 7),
        )
        toolbar.pack(fill=tk.X)

        file_group = ttk.LabelFrame(
            toolbar,
            text="  文件与图片  ",
            style="ToolGroup.TLabelframe",
            padding=(7, 4),
        )
        file_group.pack(side=tk.LEFT, padx=(0, 7))
        ttk.Button(
            file_group,
            text="打开任务",
            style="Primary.TButton",
            command=self.open_folder,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(file_group, text="保存", command=self.save_current).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(file_group, text="◀", command=self.previous_image).pack(
            side=tk.LEFT, padx=(7, 2)
        )
        ttk.Button(file_group, text="▶", command=self.next_image).pack(
            side=tk.LEFT, padx=2
        )
        self.image_combo = ttk.Combobox(
            file_group,
            textvariable=self.image_name_var,
            state="readonly",
            width=24,
        )
        self.image_combo.pack(side=tk.LEFT, padx=(5, 2))
        self.image_combo.bind("<<ComboboxSelected>>", self._on_image_combo_selected)

        label_group = ttk.LabelFrame(
            toolbar,
            text="  关键点标签  ",
            style="ToolGroup.TLabelframe",
            padding=(7, 4),
        )
        label_group.pack(side=tk.LEFT, padx=(0, 7))
        ttk.Label(
            label_group,
            text="当前",
            background=COLORS["surface_alt"],
        ).pack(side=tk.LEFT, padx=(0, 3))
        self.label_combo = ttk.Combobox(
            label_group,
            textvariable=self.active_label,
            values=self.labels,
            state="readonly",
            width=8,
        )
        self.label_combo.pack(side=tk.LEFT, padx=2)
        self.label_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_all())
        ttk.Button(label_group, text="管理", command=self.open_label_manager).pack(
            side=tk.LEFT, padx=3
        )

        symmetry_group = ttk.LabelFrame(
            toolbar,
            text="  中心对称辅助  ",
            style="ToolGroup.TLabelframe",
            padding=(7, 4),
        )
        symmetry_group.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Checkbutton(
            symmetry_group,
            text="启用",
            style="Tool.TCheckbutton",
            variable=self.symmetry_enabled,
            command=self._on_symmetry_toggle,
        ).pack(side=tk.LEFT, padx=2)
        self.symmetry_source_combo = ttk.Combobox(
            symmetry_group,
            textvariable=self.symmetry_source_label,
            values=self.labels,
            state="readonly",
            width=7,
        )
        self.symmetry_source_combo.pack(side=tk.LEFT, padx=2)
        ttk.Label(
            symmetry_group,
            text="→",
            background=COLORS["surface_alt"],
        ).pack(side=tk.LEFT)
        self.symmetry_target_combo = ttk.Combobox(
            symmetry_group,
            textvariable=self.symmetry_target_label,
            values=self.labels,
            state="readonly",
            width=7,
        )
        self.symmetry_target_combo.pack(side=tk.LEFT, padx=2)
        ttk.Label(
            symmetry_group,
            text="相似度",
            background=COLORS["surface_alt"],
        ).pack(side=tk.LEFT, padx=(8, 0))
        self.symmetry_ratio_scale = ttk.Scale(
            symmetry_group,
            from_=0.0,
            to=2.0,
            variable=self.symmetry_ratio,
            command=self._on_symmetry_ratio_changed,
            length=105,
        )
        self.symmetry_ratio_scale.pack(side=tk.LEFT, padx=2)
        self.symmetry_ratio_scale.bind(
            "<ButtonRelease-1>", self._on_symmetry_ratio_released
        )
        ttk.Label(
            symmetry_group,
            textvariable=self.symmetry_ratio_text,
            width=5,
            anchor=tk.E,
            background=COLORS["surface_alt"],
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.main_pane = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        self.main_pane.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 7))

        detail_frame = ttk.LabelFrame(
            self.main_pane,
            text="  01  当前检测框｜精确标注  ",
            style="Workspace.TLabelframe",
        )
        self.overview_frame = ttk.LabelFrame(
            self.main_pane,
            text="  02  整图鸟瞰｜选择目标  ",
            style="Workspace.TLabelframe",
        )
        self.main_pane.add(detail_frame, weight=11)
        self.main_pane.add(self.overview_frame, weight=10)

        self.detail_canvas = tk.Canvas(
            detail_frame,
            background=COLORS["canvas"],
            highlightthickness=0,
            cursor="crosshair",
        )
        self.detail_canvas.pack(fill=tk.BOTH, expand=True)
        self.detail_canvas.bind("<Configure>", self._schedule_redraw)
        self.detail_canvas.bind(
            "<ButtonPress-1>",
            lambda event: self._on_pointer_press(event, "detail"),
        )
        self.detail_canvas.bind(
            "<Control-ButtonPress-1>",
            lambda event: self._begin_rectangle_drag(event, "detail"),
        )
        self.detail_canvas.bind(
            "<B1-Motion>",
            lambda event: self._on_pointer_motion(event, "detail"),
        )
        self.detail_canvas.bind(
            "<ButtonRelease-1>",
            lambda event: self._on_pointer_release(event, "detail"),
        )
        self.detail_canvas.bind("<ButtonPress-2>", self._on_detail_middle_press)
        self.detail_canvas.bind("<B2-Motion>", self._on_detail_middle_motion)
        self.detail_canvas.bind("<ButtonRelease-2>", self._on_detail_middle_release)
        self.detail_canvas.bind("<Button-3>", self._on_detail_right_click)
        self.detail_canvas.bind("<MouseWheel>", self._on_detail_wheel)

        self.overview_canvas = tk.Canvas(
            self.overview_frame,
            background=COLORS["canvas"],
            highlightthickness=0,
            cursor="hand2",
        )
        self.overview_canvas.pack(fill=tk.BOTH, expand=True)
        self.overview_canvas.bind("<Configure>", self._schedule_redraw)
        self.overview_canvas.bind(
            "<ButtonPress-1>",
            lambda event: self._on_pointer_press(event, "overview"),
        )
        self.overview_canvas.bind(
            "<Control-ButtonPress-1>",
            lambda event: self._begin_rectangle_drag(event, "overview"),
        )
        self.overview_canvas.bind(
            "<B1-Motion>",
            lambda event: self._on_pointer_motion(event, "overview"),
        )
        self.overview_canvas.bind(
            "<ButtonRelease-1>",
            lambda event: self._on_pointer_release(event, "overview"),
        )
        self.overview_canvas.bind("<MouseWheel>", self._on_continuous_zoom_wheel)

        self.direction_review_panel = ttk.Frame(
            self.overview_frame,
            style="Toolbar.TFrame",
            padding=(8, 7),
        )
        review_summary = ttk.Frame(
            self.direction_review_panel,
            style="Toolbar.TFrame",
        )
        review_summary.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(
            review_summary,
            text="方向复审清单",
            style="Section.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Label(
            review_summary,
            textvariable=self.direction_review_summary_var,
            style="StatusStrong.TLabel",
        ).pack(side=tk.RIGHT)
        review_table = ttk.Frame(self.direction_review_panel)
        review_table.pack(fill=tk.BOTH, expand=True)
        self.direction_review_tree = ttk.Treeview(
            review_table,
            columns=("status", "scene", "frame", "track"),
            show="headings",
            height=6,
            selectmode="browse",
        )
        for column, title, width in (
            ("status", "状态", 72),
            ("scene", "场景", 72),
            ("frame", "帧号", 86),
            ("track", "Track ID", 88),
        ):
            self.direction_review_tree.heading(column, text=title)
            self.direction_review_tree.column(column, width=width, anchor=tk.CENTER)
        review_scrollbar = ttk.Scrollbar(
            review_table,
            orient=tk.VERTICAL,
            command=self.direction_review_tree.yview,
        )
        self.direction_review_tree.configure(yscrollcommand=review_scrollbar.set)
        self.direction_review_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        review_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.direction_review_tree.tag_configure("pending", foreground="#C97700")
        self.direction_review_tree.tag_configure("reviewed", foreground="#1F9D75")
        self.direction_review_tree.bind(
            "<Double-1>", self._on_direction_review_tree_double_click
        )

        controls = ttk.Frame(self.root, padding=(12, 0, 12, 6))
        controls.pack(fill=tk.X)
        ttk.Label(controls, text="检测框操作", style="Section.TLabel").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        self.previous_rectangle_button = ttk.Button(
            controls,
            text=self._action_button_text("◀ 上一个框", "previous_rectangle"),
            command=self.previous_rectangle,
        )
        self.previous_rectangle_button.pack(side=tk.LEFT, padx=2)
        self.next_rectangle_button = ttk.Button(
            controls,
            text=self._action_button_text("下一个框 ▶", "next_rectangle"),
            command=self.next_rectangle,
        )
        self.next_rectangle_button.pack(side=tk.LEFT, padx=2)
        ttk.Button(
            controls,
            text="删除当前标签点",
            style="Danger.TButton",
            command=self.delete_current_label_point,
        ).pack(side=tk.LEFT, padx=(10, 2))
        ttk.Button(
            controls,
            text="清空当前框",
            style="Danger.TButton",
            command=self.delete_all_current_points,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            controls,
            text="IoU 备用",
            command=self.open_iou_dialog,
        ).pack(side=tk.LEFT, padx=(10, 2))
        self.frame_table_button = ttk.Button(
            controls,
            text=self._action_button_text("本帧框清单", "open_frame_table"),
            command=self.open_frame_table,
        )
        self.frame_table_button.pack(side=tk.LEFT, padx=2)
        self.toggle_class_button = ttk.Button(
            controls,
            text=self._action_button_text(
                "bee ↔ beeshadow", "toggle_bee_shadow_class"
            ),
            style="Accent.TButton",
            command=self.toggle_current_bee_shadow_class,
        )
        self.toggle_class_button.pack(side=tk.LEFT, padx=(10, 2))
        ttk.Checkbutton(
            controls,
            text="显示标签名",
            style="Tool.TCheckbutton",
            variable=self.show_label_names,
            command=self._refresh_all,
        ).pack(side=tk.RIGHT, padx=5)
        ttk.Checkbutton(
            controls,
            text="显示框 ID（B）",
            style="Tool.TCheckbutton",
            variable=self.show_track_ids,
            command=self._refresh_all,
        ).pack(side=tk.RIGHT, padx=5)

        creation_controls = ttk.Frame(
            self.root,
            style="Toolbar.TFrame",
            padding=(12, 7),
        )
        creation_controls.pack(fill=tk.X)
        ttk.Label(
            creation_controls,
            text="新增检测框",
            style="Section.TLabel",
        ).pack(side=tk.LEFT, padx=(0, 8))
        self.continuous_mode_button = ttk.Button(
            creation_controls,
            text=self._action_button_text(
                "连续补标", "start_continuous_annotation"
            ),
            style="Primary.TButton",
            command=self.start_continuous_annotation_mode,
        )
        self.continuous_mode_button.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(creation_controls, text="默认 Track ID：").pack(side=tk.LEFT)
        self.default_track_id_combo = ttk.Combobox(
            creation_controls,
            textvariable=self.default_track_id_text,
            width=9,
            state="normal",
        )
        self.default_track_id_combo.pack(side=tk.LEFT, padx=(2, 5))
        self.default_track_id_combo.bind(
            "<Return>", self._activate_default_track_id_from_entry
        )
        self.default_track_id_combo.bind(
            "<KP_Enter>", self._activate_default_track_id_from_entry
        )
        self.default_track_id_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.activate_default_track_id()
        )
        self.activate_default_track_id_button = ttk.Button(
            creation_controls,
            text="启用 / 更新",
            style="Primary.TButton",
            command=self.activate_default_track_id,
        )
        self.activate_default_track_id_button.pack(side=tk.LEFT, padx=2)
        self.clear_default_track_id_button = ttk.Button(
            creation_controls,
            text=self._action_button_text("清除默认 ID", "clear_default_track_id"),
            command=self.clear_default_track_id,
        )
        self.clear_default_track_id_button.pack(side=tk.LEFT, padx=(2, 10))
        ttk.Label(
            creation_controls,
            textvariable=self.default_track_id_status_var,
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, padx=5)
        ttk.Label(
            creation_controls,
            textvariable=self.max_track_id_var,
            style="Section.TLabel",
        ).pack(side=tk.RIGHT, padx=5)
        ttk.Checkbutton(
            controls,
            text="显示全部框",
            style="Tool.TCheckbutton",
            variable=self.show_other_boxes,
            command=self._refresh_all,
        ).pack(side=tk.RIGHT, padx=5)

        track_controls = ttk.Frame(
            self.root,
            style="Review.TFrame",
            padding=(12, 8),
        )
        track_controls.pack(fill=tk.X)
        ttk.Label(
            track_controls,
            text="TRACK ID 复审",
            style="ReviewTitle.TLabel",
        ).pack(side=tk.LEFT, padx=(0, 10))
        self.track_mode_button = ttk.Button(
            track_controls,
            text="进入按 ID 标注模式",
            style="Primary.TButton",
            command=self.toggle_track_id_mode,
        )
        self.track_mode_button.pack(side=tk.LEFT, padx=(0, 10))
        self.direction_review_mode_button = ttk.Button(
            track_controls,
            text="进入方向复审批注",
            style="Primary.TButton",
            command=self.toggle_direction_review_mode,
        )
        self.direction_review_mode_button.pack(side=tk.LEFT, padx=(0, 10))
        self.next_track_id_button = ttk.Button(
            track_controls,
            text=self._action_button_text("下一个 ID ▶", "next_track_id"),
            command=self.next_track_id,
            state="disabled",
        )
        self.next_track_id_button.pack(side=tk.LEFT, padx=(0, 10))
        self.previous_track_button = ttk.Button(
            track_controls,
            text=self._action_button_text("◀ 同ID上一帧", "previous_track_frame"),
            command=self.previous_track_frame,
        )
        self.previous_track_button.pack(side=tk.LEFT, padx=2)
        self.next_track_button = ttk.Button(
            track_controls,
            text=self._action_button_text("同ID下一帧 ▶", "next_track_frame"),
            command=self.next_track_frame,
        )
        self.next_track_button.pack(side=tk.LEFT, padx=2)
        self.copy_frame_button = ttk.Button(
            track_controls,
            text=self._action_button_text("整帧到下一张", "copy_frame_to_next"),
            command=self.copy_current_frame_to_next,
        )
        self.copy_frame_button.pack(side=tk.LEFT, padx=(10, 2))
        self.propagate_track_button = ttk.Button(
            track_controls,
            text=self._action_button_text("ID传播", "propagate_track"),
            style="Primary.TButton",
            command=self.propagate_current_track,
        )
        self.propagate_track_button.pack(side=tk.LEFT, padx=(10, 2))
        self.confirm_keypoints_button = ttk.Button(
            track_controls,
            text=self._action_button_text("确认", "confirm_keypoints"),
            style="Success.TButton",
            command=self.confirm_current_keypoints,
        )
        self.confirm_keypoints_button.pack(side=tk.LEFT, padx=2)
        self.swap_head_tail_button = ttk.Button(
            track_controls,
            text=self._action_button_text("交换头尾", "swap_head_tail"),
            command=self.swap_current_head_tail,
        )
        self.swap_head_tail_button.pack(side=tk.LEFT, padx=2)
        self.next_review_button = ttk.Button(
            track_controls,
            text=self._action_button_text("下一待审/异常", "next_review_issue"),
            style="Warning.TButton",
            command=self.next_review_issue,
        )
        self.next_review_button.pack(side=tk.LEFT, padx=2)
        ttk.Label(
            track_controls,
            text="● 绿色=人工确认",
            style="LegendConfirmed.TLabel",
        ).pack(side=tk.RIGHT, padx=(8, 5))
        ttk.Label(
            track_controls,
            text="● 黄色=自动建议",
            style="LegendPending.TLabel",
        ).pack(side=tk.RIGHT, padx=5)

        information = ttk.Frame(
            self.root,
            style="StatusBar.TFrame",
            padding=(12, 8),
        )
        information.pack(fill=tk.X)
        ttk.Label(
            information,
            textvariable=self.progress_var,
            style="StatusStrong.TLabel",
        ).pack(
            side=tk.LEFT
        )
        ttk.Progressbar(
            information,
            variable=self.confirmation_progress,
            maximum=100.0,
            length=150,
        ).pack(side=tk.LEFT, padx=(14, 4))
        ttk.Label(
            information,
            textvariable=self.points_var,
            style="Status.TLabel",
        ).pack(
            side=tk.LEFT, padx=18
        )
        ttk.Label(
            information, textvariable=self.status_var, style="Status.TLabel"
        ).pack(side=tk.RIGHT)

    def _bind_shortcuts(self) -> None:
        for sequence in self.bound_shortcut_sequences:
            self.root.unbind(sequence)
        self.bound_shortcut_sequences.clear()
        self._stop_keyboard_polling()
        self.pressed_shortcut_keys.clear()

        # Windows 的中文输入法可能直接吞掉 Tk 的 KeyPress。这里仅读取
        # 低级键盘事件并放入队列，不在钩子线程里调用 Tk，因此不影响输入法。
        if sys.platform == "win32":
            self._start_windows_keyboard_hook()
            self._schedule_keyboard_polling()
        else:
            keyboard_sequence = "<KeyPress>"
            self.root.bind(keyboard_sequence, self._dispatch_physical_key_event)
            self.bound_shortcut_sequences.add(keyboard_sequence)

        # 原生侧键不可用时的 Tk 备用事件。
        self.root.bind(
            "<Button-4>",
            lambda event: self._dispatch_mouse_shortcut("鼠标侧键1", event),
        )
        self.root.bind(
            "<Button-5>",
            lambda event: self._dispatch_mouse_shortcut("鼠标侧键2", event),
        )

    @staticmethod
    def _physical_virtual_key(
        reported_key: int,
        ime_original_key: int = 0,
        pressed_keys=(),
    ) -> int:
        """将输入法的 VK_PROCESSKEY(229) 还原为实际物理键。"""
        reported_key = int(reported_key or 0)
        if reported_key != 229:
            return reported_key
        ime_original_key = int(ime_original_key or 0)
        if ime_original_key not in {0, 229}:
            return ime_original_key
        return next((int(key) for key in pressed_keys if int(key) > 0), reported_key)

    @staticmethod
    def _virtual_key_name(virtual_key: int) -> Optional[str]:
        virtual_key = int(virtual_key or 0)
        if 0x41 <= virtual_key <= 0x5A:
            return chr(virtual_key)
        if 0x30 <= virtual_key <= 0x39:
            return chr(virtual_key)
        if 0x70 <= virtual_key <= 0x87:
            return f"F{virtual_key - 0x6F}"
        return {
            0x08: "Backspace",
            0x09: "Tab",
            0x0D: "Enter",
            0x1B: "Escape",
            0x20: "Space",
            0x21: "PageUp",
            0x22: "PageDown",
            0x23: "End",
            0x24: "Home",
            0x25: "Left",
            0x26: "Up",
            0x27: "Right",
            0x28: "Down",
            0x2D: "Insert",
            0x2E: "Delete",
        }.get(virtual_key)

    @staticmethod
    def _shortcut_candidates_for_virtual_key(
        virtual_key: int,
        modifiers=(),
    ) -> List[str]:
        key_name = BeeKeypointAnnotator._virtual_key_name(virtual_key)
        if key_name is None:
            return []
        ordered_modifiers = [
            modifier
            for modifier in ("Ctrl", "Shift", "Alt")
            if modifier in set(modifiers)
        ]

        def compose(selected_modifiers) -> str:
            return "+".join(list(selected_modifiers) + [key_name])

        candidates = [compose(ordered_modifiers)]
        # 对字母快捷键忽略仅用于大小写的 Shift，但仍优先匹配用户
        # 显式设置的 Shift+A / Ctrl+Shift+A 等组合键。
        if len(key_name) == 1 and key_name.isalpha() and "Shift" in ordered_modifiers:
            without_shift = [
                modifier for modifier in ordered_modifiers if modifier != "Shift"
            ]
            normalized = compose(without_shift)
            if normalized not in candidates:
                candidates.append(normalized)
        return candidates

    @staticmethod
    def _tk_state_modifiers(state: int) -> Tuple[str, ...]:
        state = int(state or 0)
        modifiers = []
        if state & 0x0004:
            modifiers.append("Ctrl")
        if state & 0x0001:
            modifiers.append("Shift")
        if state & (0x0008 | 0x20000):
            modifiers.append("Alt")
        return tuple(modifiers)

    @staticmethod
    def _windows_pressed(virtual_key: int) -> bool:
        try:
            return bool(ctypes.windll.user32.GetKeyState(virtual_key) & 0x8000)
        except Exception:
            return False

    @staticmethod
    def _windows_async_pressed(virtual_key: int) -> bool:
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000)
        except Exception:
            return False

    @staticmethod
    def _shortcut_virtual_key(shortcut: str) -> Optional[int]:
        if not shortcut or shortcut in {"无", "鼠标侧键1", "鼠标侧键2"}:
            return None
        key_name = shortcut.split("+")[-1]
        if len(key_name) == 1 and key_name.isascii() and key_name.isalnum():
            return ord(key_name.upper())
        if key_name.startswith("F") and key_name[1:].isdigit():
            number = int(key_name[1:])
            if 1 <= number <= 24:
                return 0x6F + number
        return {
            "Backspace": 0x08,
            "Tab": 0x09,
            "Enter": 0x0D,
            "Escape": 0x1B,
            "Space": 0x20,
            "PageUp": 0x21,
            "PageDown": 0x22,
            "End": 0x23,
            "Home": 0x24,
            "Left": 0x25,
            "Up": 0x26,
            "Right": 0x27,
            "Down": 0x28,
            "Insert": 0x2D,
            "Delete": 0x2E,
        }.get(key_name)

    def _configured_keyboard_virtual_keys(self) -> List[int]:
        return sorted(
            {
                virtual_key
                for shortcuts in self.shortcuts.values()
                for shortcut in shortcuts
                for virtual_key in [self._shortcut_virtual_key(shortcut)]
                if virtual_key is not None
            }
        )

    @staticmethod
    def _windows_modifier_names() -> Tuple[str, ...]:
        modifiers = []
        for name, virtual_key in (("Ctrl", 0x11), ("Shift", 0x10), ("Alt", 0x12)):
            if BeeKeypointAnnotator._windows_async_pressed(virtual_key):
                modifiers.append(name)
        return tuple(modifiers)

    def _main_window_is_foreground(self) -> bool:
        try:
            user32 = ctypes.windll.user32
            foreground_handle = int(user32.GetForegroundWindow())
            if not foreground_handle:
                return False
            process_id = ctypes.c_uint32()
            user32.GetWindowThreadProcessId(
                foreground_handle,
                ctypes.byref(process_id),
            )
            return int(process_id.value) == os.getpid()
        except Exception:
            return False

    def _start_windows_keyboard_hook(self) -> None:
        if self.keyboard_hook_thread and self.keyboard_hook_thread.is_alive():
            return
        try:
            user32 = ctypes.windll.user32
            widget_handle = int(self.root.winfo_id())
            self.main_window_handle = int(
                user32.GetAncestor(widget_handle, 2) or widget_handle
            )
        except Exception:
            self.main_window_handle = 0
        self.keyboard_hook_stop.clear()
        self.keyboard_hook_thread = threading.Thread(
            target=self._windows_keyboard_hook_loop,
            name="bee-shortcut-hook",
            daemon=True,
        )
        self.keyboard_hook_thread.start()

    def _windows_keyboard_hook_loop(self) -> None:
        if sys.platform != "win32":
            return

        class KeyboardHookData(ctypes.Structure):
            _fields_ = [
                ("vk_code", ctypes.c_uint32),
                ("scan_code", ctypes.c_uint32),
                ("flags", ctypes.c_uint32),
                ("time", ctypes.c_uint32),
                ("extra_info", ctypes.c_void_p),
            ]

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hook_procedure_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_int,
            ctypes.c_size_t,
            ctypes.c_void_p,
        )
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            hook_procedure_type,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p

        def keyboard_callback(code, message, data_pointer):
            try:
                if code >= 0 and data_pointer:
                    data = ctypes.cast(
                        data_pointer,
                        ctypes.POINTER(KeyboardHookData),
                    ).contents
                    virtual_key = int(data.vk_code)
                    if virtual_key in {0, 229}:
                        mapped_key = int(user32.MapVirtualKeyW(data.scan_code, 3))
                        if mapped_key:
                            virtual_key = mapped_key
                    if message in {0x0100, 0x0104}:
                        if virtual_key not in self.keyboard_hook_keys_down:
                            self.keyboard_hook_keys_down.add(virtual_key)
                            self.keyboard_event_queue.put(
                                (virtual_key, self._windows_modifier_names())
                            )
                    elif message in {0x0101, 0x0105}:
                        self.keyboard_hook_keys_down.discard(virtual_key)
            except Exception:
                pass
            return user32.CallNextHookEx(None, code, message, data_pointer)

        self.keyboard_hook_callback = hook_procedure_type(keyboard_callback)
        module_handle = kernel32.GetModuleHandleW(None)
        hook_handle = user32.SetWindowsHookExW(
            13,
            self.keyboard_hook_callback,
            module_handle,
            0,
        )
        if not hook_handle:
            self.keyboard_hook_callback = None
            return

        self.keyboard_hook_handle = hook_handle
        self.keyboard_hook_thread_id = int(kernel32.GetCurrentThreadId())
        self.keyboard_hook_active = True
        message = wintypes.MSG()
        try:
            while not self.keyboard_hook_stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(hook_handle)
            self.keyboard_hook_active = False
            self.keyboard_hook_handle = None
            self.keyboard_hook_callback = None
            self.keyboard_hook_keys_down.clear()

    def _drain_keyboard_hook_events(self):
        handled = None
        while True:
            try:
                virtual_key, modifiers = self.keyboard_event_queue.get_nowait()
            except queue.Empty:
                break
            if not self._main_window_is_foreground():
                continue
            if self._track_completion_dialog_exists() and not modifiers:
                if virtual_key == ord("E"):
                    self._accept_track_completion()
                    handled = "break"
                    continue
                if virtual_key == ord("Q"):
                    self._return_previous_track_from_completion()
                    handled = "break"
                    continue
            candidates = self._shortcut_candidates_for_virtual_key(
                virtual_key,
                modifiers,
            )
            if self._dispatch_keyboard_candidates(candidates) == "break":
                handled = "break"
        return handled

    def _poll_keyboard_shortcuts_once(self):
        pressed_now = {
            virtual_key
            for virtual_key in self._configured_keyboard_virtual_keys()
            if self._windows_async_pressed(virtual_key)
        }
        newly_pressed = pressed_now - self.pressed_shortcut_keys
        self.pressed_shortcut_keys = pressed_now
        if (
            not newly_pressed
            or not self._main_window_is_foreground()
            or not self._main_window_has_focus()
        ):
            return None
        modifiers = self._windows_modifier_names()
        for virtual_key in sorted(newly_pressed):
            candidates = self._shortcut_candidates_for_virtual_key(
                virtual_key,
                modifiers,
            )
            if self._dispatch_keyboard_candidates(candidates) == "break":
                return "break"
        return None

    def _poll_keyboard_shortcuts(self) -> None:
        self.keyboard_poll_job = None
        try:
            self._drain_keyboard_hook_events()
            if not self.keyboard_hook_active:
                self._poll_keyboard_shortcuts_once()
        finally:
            if self.root.winfo_exists():
                self._schedule_keyboard_polling()

    def _schedule_keyboard_polling(self) -> None:
        if self.keyboard_poll_job is None and self.root.winfo_exists():
            self.keyboard_poll_job = self.root.after(
                8,
                self._poll_keyboard_shortcuts,
            )

    def _stop_keyboard_polling(self) -> None:
        job = getattr(self, "keyboard_poll_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self.keyboard_poll_job = None

    def _stop_windows_keyboard_hook(self) -> None:
        self.keyboard_hook_stop.set()
        thread_id = int(getattr(self, "keyboard_hook_thread_id", 0) or 0)
        if thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)
            except Exception:
                pass
        thread = getattr(self, "keyboard_hook_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=0.3)
        self.keyboard_hook_thread = None
        self.keyboard_hook_thread_id = 0

    def _resolve_windows_virtual_key(self, hwnd: int, reported_key: int) -> int:
        reported_key = int(reported_key or 0)
        if reported_key != 229:
            return reported_key
        ime_original = 0
        try:
            ime_original = int(
                ctypes.windll.imm32.ImmGetVirtualKeyW(ctypes.c_void_p(hwnd))
            )
        except Exception:
            ime_original = 0
        pressed = [
            virtual_key
            for virtual_key in range(0x30, 0x5B)
            if self._windows_pressed(virtual_key)
            and not 0x3A <= virtual_key <= 0x40
        ]
        return self._physical_virtual_key(reported_key, ime_original, pressed)

    def _keyboard_action_for_candidates(self, candidates) -> Optional[str]:
        for candidate in candidates:
            for action_id, shortcuts in self.shortcuts.items():
                if candidate in shortcuts:
                    return action_id
        return None

    def _dispatch_keyboard_candidates(self, candidates):
        if not self._main_window_has_focus():
            return None
        action_id = self._keyboard_action_for_candidates(candidates)
        if action_id is None:
            return None
        self._execute_shortcut_action(action_id)
        return "break"

    def _dispatch_physical_key_event(self, event):
        try:
            hwnd = event.widget.winfo_id()
        except (AttributeError, tk.TclError):
            hwnd = self.root.winfo_id()
        virtual_key = self._resolve_windows_virtual_key(
            hwnd,
            getattr(event, "keycode", 0),
        )
        candidates = self._shortcut_candidates_for_virtual_key(
            virtual_key,
            self._tk_state_modifiers(getattr(event, "state", 0)),
        )
        return self._dispatch_keyboard_candidates(candidates)

    @staticmethod
    def _shortcut_to_tk_sequence(shortcut: str) -> Optional[str]:
        if shortcut in {"", "无", "鼠标侧键1", "鼠标侧键2"}:
            return None
        parts = shortcut.split("+")
        key = parts[-1]
        modifiers = parts[:-1]
        key_names = {
            "Space": "space",
            "Tab": "Tab",
            "Enter": "Return",
            "Backspace": "BackSpace",
            "Delete": "Delete",
            "Escape": "Escape",
            "Left": "Left",
            "Right": "Right",
            "Up": "Up",
            "Down": "Down",
            "PageUp": "Prior",
            "PageDown": "Next",
            "Home": "Home",
            "End": "End",
            "Insert": "Insert",
        }
        tk_key = key_names.get(key, key.lower() if len(key) == 1 else key)
        tk_modifiers = [
            {"Ctrl": "Control", "Shift": "Shift", "Alt": "Alt"}.get(
                modifier, modifier
            )
            for modifier in modifiers
        ]
        components = tk_modifiers + [tk_key]
        return "<" + "-".join(components) + ">"

    def _main_window_has_focus(self) -> bool:
        widget = self.root.focus_get()
        if widget is None:
            return True
        if widget.winfo_toplevel() is not self.root:
            return False
        return widget.winfo_class() not in {"Entry", "TEntry", "TCombobox", "Spinbox"}

    def _dispatch_shortcut_event(self, action_id: str, _event=None):
        if not self._main_window_has_focus():
            return None
        self._execute_shortcut_action(action_id)
        return "break"

    def _dispatch_mouse_shortcut(self, shortcut: str, _event=None):
        if not self._main_window_has_focus():
            return None
        for action_id, shortcuts in self.shortcuts.items():
            if shortcut in shortcuts:
                self._execute_shortcut_action(action_id)
                return "break"
        return None

    def _execute_shortcut_action(self, action_id: str) -> None:
        callbacks = {
            "previous_image": self.previous_image,
            "next_image": self.next_image,
            "previous_rectangle": self.previous_rectangle,
            "next_rectangle": self.next_rectangle,
            "select_label_1": lambda: self._select_label_index(0),
            "select_label_2": lambda: self._select_label_index(1),
            "toggle_symmetry": self.toggle_symmetry,
            "save": self.save_current,
            "undo": self.undo,
            "delete_label_point": self.delete_current_label_point,
            "delete_all_points": self.delete_all_current_points,
            "open_folder": self.open_folder,
            "open_iou": self.open_iou_dialog,
            "open_label_manager": self.open_label_manager,
            "open_shortcut_manager": self.open_shortcut_manager,
            "open_help": self.open_help,
            "toggle_label_names": self.toggle_label_names,
            "toggle_track_ids": self.toggle_track_ids,
            "toggle_bee_shadow_class": self.toggle_current_bee_shadow_class,
            "toggle_box_class_labels": self.toggle_box_class_labels,
            "toggle_other_boxes": self.toggle_other_boxes,
            "previous_track_frame": self.previous_track_frame,
            "next_track_frame": self.next_track_frame,
            "next_track_id": self.next_track_id,
            "copy_frame_to_next": self.copy_current_frame_to_next,
            "propagate_track": self.propagate_current_track,
            "confirm_keypoints": self.confirm_current_keypoints,
            "start_continuous_annotation": self.start_continuous_annotation_mode,
            "clear_default_track_id": self.clear_default_track_id,
            "toggle_continuous_other_boxes": self.toggle_continuous_other_boxes,
            "swap_head_tail": self.swap_current_head_tail,
            "next_review_issue": self.next_review_issue,
            "open_frame_table": self.open_frame_table,
        }
        callback = callbacks.get(action_id)
        if callback:
            callback()

    def _select_label_index(self, index: int) -> None:
        if 0 <= index < len(self.labels):
            self.active_label.set(self.labels[index])
            self.status_var.set(f"当前标签已切换为 {self.labels[index]}")
            self._refresh_all()

    def _action_button_text(self, label: str, action_id: str) -> str:
        shortcuts = [
            shortcut.replace("鼠标侧键", "侧键")
            for shortcut in self.shortcuts.get(action_id, [])
            if shortcut != "无"
        ]
        return f"{label} ({' / '.join(shortcuts)})" if shortcuts else label

    def _refresh_shortcut_button_text(self) -> None:
        if hasattr(self, "previous_rectangle_button"):
            id_mode = bool(getattr(self, "track_id_mode", False))
            direction_mode = bool(getattr(self, "direction_review_mode", False))
            continuous_mode = bool(
                getattr(self, "continuous_annotation_mode", False)
            )
            self.previous_rectangle_button.configure(
                text=self._action_button_text(
                    "◀ 上一个复审对象"
                    if direction_mode
                    else ("◀ 上一个 ID" if id_mode else "◀ 上一个框"),
                    "previous_rectangle",
                )
            )
            self.next_rectangle_button.configure(
                text=self._action_button_text(
                    "确认并下一个 ▶"
                    if direction_mode
                    else (
                        "确认并下一帧 ▶"
                        if id_mode
                        else (
                            "复制对象到下一帧 ▶"
                            if continuous_mode
                            else "下一个框 ▶"
                        )
                    ),
                    "next_rectangle",
                )
            )
            self.frame_table_button.configure(
                text=self._action_button_text(
                    "方向复审清单"
                    if direction_mode
                    else ("Track ID 清单" if id_mode else "本帧框清单"),
                    "open_frame_table",
                )
            )
        if hasattr(self, "toggle_class_button"):
            self.toggle_class_button.configure(
                text=self._action_button_text(
                    "bee ↔ beeshadow", "toggle_bee_shadow_class"
                )
            )
        if hasattr(self, "previous_track_button"):
            if hasattr(self, "next_track_id_button"):
                self.next_track_id_button.configure(
                    text=self._action_button_text(
                        "下一个复审对象 ▶"
                        if getattr(self, "direction_review_mode", False)
                        else "下一个 ID ▶",
                        "next_track_id",
                    )
                )
            self.previous_track_button.configure(
                text=self._action_button_text(
                    "◀ 上一帧" if continuous_mode else "◀ 同ID上一帧",
                    "previous_track_frame",
                )
            )
            self.next_track_button.configure(
                text=self._action_button_text(
                    "下一帧 ▶" if continuous_mode else "同ID下一帧 ▶",
                    "next_track_frame",
                )
            )
            self.copy_frame_button.configure(
                text=self._action_button_text(
                    "画 / 更新检测框"
                    if getattr(self, "continuous_annotation_mode", False)
                    else "整帧到下一张",
                    "copy_frame_to_next",
                )
            )
            self.propagate_track_button.configure(
                text=self._action_button_text("ID传播", "propagate_track")
            )
            self.confirm_keypoints_button.configure(
                text=self._action_button_text("确认", "confirm_keypoints")
            )
            self.swap_head_tail_button.configure(
                text=self._action_button_text("交换头尾", "swap_head_tail")
            )
            self.next_review_button.configure(
                text=self._action_button_text("下一待审/异常", "next_review_issue")
            )
            if hasattr(self, "continuous_mode_button"):
                self.continuous_mode_button.configure(
                    text=self._action_button_text(
                        "下一个 ID｜回第一帧"
                        if getattr(self, "continuous_annotation_mode", False)
                        else "连续补标",
                        "start_continuous_annotation",
                    )
                )
            if hasattr(self, "clear_default_track_id_button"):
                self.clear_default_track_id_button.configure(
                    text=self._action_button_text(
                        "清除默认 ID", "clear_default_track_id"
                    )
                )

    def _refresh_track_mode_ui(self) -> None:
        id_mode = bool(getattr(self, "track_id_mode", False))
        direction_mode = bool(getattr(self, "direction_review_mode", False))
        continuous_mode = bool(
            getattr(self, "continuous_annotation_mode", False)
        )
        if hasattr(self, "track_mode_button"):
            self.track_mode_button.configure(
                text="退出按 ID 模式" if id_mode else "进入按 ID 标注模式",
                style="Warning.TButton" if id_mode else "Primary.TButton",
                state="disabled" if direction_mode or continuous_mode else "normal",
            )
        if hasattr(self, "direction_review_mode_button"):
            has_items = (
                bool(self._direction_review_items())
                if getattr(self, "images", [])
                and len(getattr(self, "documents", {}))
                == len(getattr(self, "images", []))
                else False
            )
            self.direction_review_mode_button.configure(
                text="退出方向复审批注" if direction_mode else "进入方向复审批注",
                style="Warning.TButton" if direction_mode else "Primary.TButton",
                state=(
                    "normal"
                    if has_items and not continuous_mode
                    else "disabled"
                ),
            )
        if hasattr(self, "continuous_mode_button"):
            self.continuous_mode_button.configure(
                text=self._action_button_text(
                    "下一个 ID｜回第一帧" if continuous_mode else "连续补标",
                    "start_continuous_annotation",
                ),
                style="Warning.TButton" if continuous_mode else "Primary.TButton",
            )
        if hasattr(self, "image_combo"):
            self.image_combo.configure(
                state=(
                    "disabled"
                    if id_mode or direction_mode or continuous_mode
                    else "readonly"
                )
            )
        if hasattr(self, "next_track_id_button"):
            self.next_track_id_button.configure(
                state="normal" if id_mode or direction_mode else "disabled"
            )
        for widget_name in (
            "propagate_track_button",
            "next_review_button",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.configure(
                    state=(
                        "disabled"
                        if id_mode or direction_mode or continuous_mode
                        else "normal"
                    )
                )
        if hasattr(self, "copy_frame_button"):
            self.copy_frame_button.configure(
                state="disabled" if id_mode or direction_mode else "normal"
            )
        for widget_name in (
            "default_track_id_combo",
            "activate_default_track_id_button",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.configure(
                    state=(
                        "disabled"
                        if id_mode or direction_mode
                        else "normal"
                    )
                )
        if hasattr(self, "clear_default_track_id_button"):
            self.clear_default_track_id_button.configure(
                state=(
                    "disabled"
                    if id_mode or direction_mode or continuous_mode
                    else "normal"
                )
            )
        if hasattr(self, "direction_review_panel"):
            if direction_mode:
                self.overview_canvas.pack_forget()
                self.direction_review_panel.pack_forget()
                self.direction_review_panel.pack(side=tk.BOTTOM, fill=tk.X)
                self.overview_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
                self.overview_frame.configure(
                    text="  02  模型预测 vs 当前标注｜方向复审  "
                )
                self.overview_canvas.configure(cursor="arrow")
            else:
                self.direction_review_panel.pack_forget()
                self.overview_canvas.pack_forget()
                self.overview_canvas.pack(fill=tk.BOTH, expand=True)
                self.overview_frame.configure(text="  02  整图鸟瞰｜选择目标  ")
                self.overview_canvas.configure(cursor="hand2")
            self.root.after_idle(self._balance_main_pane)
        self._refresh_shortcut_button_text()

    @staticmethod
    def _normalize_track_id(value) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, float):
            return int(value) if value.is_integer() and value >= 0 else None
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    def _task_track_ids(self) -> List[int]:
        track_ids = set()
        for image_path in getattr(self, "images", []):
            document = self.documents.get(image_path)
            if document is None:
                continue
            for rectangle in rectangle_records(document):
                track_id = self._normalize_track_id(rectangle.get("group_id"))
                if track_id is not None:
                    track_ids.add(track_id)
        return sorted(track_ids)

    @staticmethod
    def _next_new_track_id(track_ids: List[int]) -> int:
        return max(track_ids, default=0) + 1

    def _refresh_default_track_id_controls(self) -> None:
        track_ids = self._task_track_ids()
        maximum = max(track_ids) if track_ids else None
        if hasattr(self, "max_track_id_var"):
            self.max_track_id_var.set(
                f"任务最大 ID：{maximum}" if maximum is not None else "任务最大 ID：—"
            )
        combo = getattr(self, "default_track_id_combo", None)
        if combo is not None:
            suggestions = list(track_ids)
            next_id = self._next_new_track_id(track_ids)
            if next_id not in suggestions:
                suggestions.append(next_id)
            combo.configure(values=[str(track_id) for track_id in suggestions])
        if hasattr(self, "default_track_id_status_var"):
            active = getattr(self, "default_track_id", None)
            if active is None:
                status = "默认 ID：未启用"
            elif getattr(self, "default_track_id_explicit", False):
                status = f"手动 ID：{active}（在整图中拖动新增框）"
            elif getattr(self, "continuous_annotation_mode", False):
                status = f"连续补标新 ID：{active}"
            else:
                status = f"自动建议 ID：{active}"
            self.default_track_id_status_var.set(status)
        if (
            hasattr(self, "overview_canvas")
            and not getattr(self, "direction_review_mode", False)
        ):
            if getattr(self, "continuous_annotation_mode", False):
                cursor = (
                    "crosshair"
                    if getattr(self, "continuous_draw_box_mode", False)
                    else "hand2"
                )
            else:
                cursor = (
                    "crosshair"
                    if getattr(self, "default_track_id", None) is not None
                    else "hand2"
                )
            self.overview_canvas.configure(cursor=cursor)

    def activate_default_track_id(self) -> None:
        if getattr(self, "track_id_mode", False) or getattr(
            self, "direction_review_mode", False
        ):
            self.status_var.set("请先退出 Track ID / 方向复审模式，再新增检测框")
            return
        track_id = self._normalize_track_id(self.default_track_id_text.get())
        if track_id is None:
            self.status_var.set("请输入大于或等于 0 的整数 Track ID")
            return
        self.default_track_id = track_id
        self.default_track_id_explicit = True
        self.rectangle_creation = None
        if getattr(self, "continuous_annotation_mode", False):
            self.continuous_active_track_id = track_id
            self.continuous_draw_box_mode = False
            self._refresh_default_track_id_controls()
            self._set_image_index(0, auto_confirm_viewed=False)
            self.status_var.set(
                f"已切换到连续补标 ID {track_id}：已回到第 1 张；"
                "右侧点选区域，R 画框，E 复制到下一帧"
            )
            return
        self._refresh_default_track_id_controls()
        self.status_var.set(
            f"默认 Track ID 已设为 {track_id}；请在整图鸟瞰中拖动新增检测框"
        )

    def _activate_default_track_id_from_entry(self, event) -> str:
        """回车应用默认 ID，并立即把输入焦点交还给图像画布。"""
        track_id = self._normalize_track_id(self.default_track_id_text.get())
        self.activate_default_track_id()
        if track_id is not None and getattr(self, "default_track_id", None) == track_id:
            try:
                event.widget.selection_clear()
            except tk.TclError:
                pass
            canvas = getattr(self, "overview_canvas", self.root)
            self.root.after_idle(canvas.focus_set)
        return "break"

    def clear_default_track_id(
        self, notify: bool = True, refresh: bool = True
    ) -> None:
        if getattr(self, "continuous_annotation_mode", False):
            self.exit_continuous_annotation_mode(notify=False, refresh=False)
        self.default_track_id = None
        self.default_track_id_explicit = False
        self.rectangle_creation = None
        if hasattr(self, "default_track_id_text"):
            self.default_track_id_text.set("")
        self._refresh_default_track_id_controls()
        if notify and hasattr(self, "status_var"):
            self.status_var.set("已清除默认 Track ID；可输入下一个 ID")
        if refresh and hasattr(self, "overview_canvas"):
            self._draw_overview()

    def toggle_continuous_annotation_mode(self) -> None:
        if getattr(self, "continuous_annotation_mode", False):
            self.exit_continuous_annotation_mode()
        else:
            self.start_continuous_annotation_mode()

    def start_continuous_annotation_mode(self) -> None:
        if not getattr(self, "images", []):
            self.status_var.set("请先打开标注任务文件夹")
            return

        advancing = bool(getattr(self, "continuous_annotation_mode", False))
        view_focus, view_zoom = self._current_view_state_for_continuous_mode()
        if advancing:
            current_id = self._normalize_track_id(self.continuous_active_track_id)
            next_unused_id = self._next_new_track_id(self._task_track_ids())
            track_id = max((current_id or 0) + 1, next_unused_id)
            self.default_track_id_explicit = False
        else:
            if getattr(self, "default_track_id_explicit", False):
                track_id = self._normalize_track_id(
                    getattr(self, "default_track_id", None)
                )
            else:
                track_id = self._next_new_track_id(self._task_track_ids())

            if track_id is None:
                self.status_var.set("请先输入有效的默认 Track ID")
                return

        self._close_track_completion_dialog()
        self.track_id_mode = False
        self.track_mode_current_id = None
        self.direction_review_mode = False
        self.direction_review_item_index = -1
        self.continuous_annotation_mode = True
        self.continuous_active_track_id = int(track_id)
        self.continuous_draw_box_mode = False
        self.rectangle_creation = None
        self.continuous_focus_point = view_focus
        self.continuous_zoom_factor = view_zoom
        self.default_track_id = int(track_id)
        self.default_track_id_text.set(str(track_id))
        self._refresh_track_mode_ui()
        self._set_image_index(
            0,
            preferred_group_id=int(track_id),
            auto_confirm_viewed=False,
        )
        self.status_var.set(
            f"{'已切换到' if advancing else '连续补标'} ID {track_id}："
            "已从第 1 张开始；"
            "右侧点选区域，R 画框，E 复制到下一帧"
        )

    def _current_view_state_for_continuous_mode(
        self,
    ) -> Tuple[Optional[Tuple[float, float]], float]:
        """取得按 V 前左侧画面的中心和倍率，用于切帧后保持视野。"""
        focus = getattr(self, "continuous_focus_point", None)
        zoom = float(getattr(self, "continuous_zoom_factor", 3.0))
        if focus is not None:
            return focus, min(12.0, max(1.0, zoom))

        transform = getattr(self, "detail_transform", None)
        image = getattr(self, "current_pil_image", None)
        canvas = getattr(self, "detail_canvas", None)
        if transform is not None and image is not None and canvas is not None:
            width = max(float(canvas.winfo_width()), 1.0)
            height = max(float(canvas.winfo_height()), 1.0)
            focus = self._clamp_point_to_current_image(
                self._canvas_to_image(width / 2.0, height / 2.0, transform)
            )
            scale = max(float(transform[0]), 1e-6)
            zoom = min(
                float(image.width) * scale / width,
                float(image.height) * scale / height,
            )
            return focus, min(12.0, max(1.0, zoom))

        rectangles = (
            self._current_rectangles()
            if getattr(self, "current_image_index", -1) >= 0
            else []
        )
        current_position = getattr(self, "current_rectangle_index", -1)
        if 0 <= current_position < len(rectangles):
            x1, y1, x2, y2 = rectangles[current_position]["rect"]
            focus = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        elif image is not None:
            focus = (float(image.width) / 2.0, float(image.height) / 2.0)
        return focus, min(12.0, max(1.0, zoom))

    def exit_continuous_annotation_mode(
        self, notify: bool = True, refresh: bool = True
    ) -> None:
        self.continuous_annotation_mode = False
        self.continuous_active_track_id = None
        self.continuous_draw_box_mode = False
        self.continuous_focus_point = None
        self.rectangle_creation = None
        self.detail_cache_key = None
        self._refresh_track_mode_ui()
        if refresh:
            self._refresh_all()
        if notify:
            self.status_var.set("已退出连续补标模式")

    def _continuous_rectangle_position(
        self, document: Optional[Dict] = None
    ) -> int:
        document = document if document is not None else self._current_document()
        if document is None:
            return -1
        track_id = self._normalize_track_id(
            getattr(self, "continuous_active_track_id", None)
        )
        if track_id is None:
            return -1
        return next(
            (
                position
                for position, rectangle in enumerate(rectangle_records(document))
                if self._normalize_track_id(rectangle.get("group_id")) == track_id
            ),
            -1,
        )

    def _align_continuous_view_to_current_frame(self) -> None:
        image = self.current_pil_image
        position = self._continuous_rectangle_position()
        rectangles = self._current_rectangles()
        if 0 <= position < len(rectangles):
            self.current_rectangle_index = position
            self._remember_current_rectangle()
            x1, y1, x2, y2 = rectangles[position]["rect"]
            self.continuous_focus_point = ((x1 + x2) / 2, (y1 + y2) / 2)
        else:
            self.current_rectangle_index = -1
            self.continuous_focus_point = (
                (float(image.width) / 2, float(image.height) / 2)
                if image is not None
                else None
            )
        self.detail_cache_key = None
        self._refresh_all()

    def _on_continuous_zoom_wheel(self, event) -> str:
        if not getattr(self, "continuous_annotation_mode", False):
            return "break"
        factor = 1.25 if event.delta > 0 else 0.8
        self.continuous_zoom_factor = min(
            12.0,
            max(1.0, float(self.continuous_zoom_factor) * factor),
        )
        self.detail_cache_key = None
        self._draw_detail()
        self.status_var.set(f"左侧放大倍率：{self.continuous_zoom_factor:.1f}×")
        return "break"

    def _balance_main_pane(self) -> None:
        if not hasattr(self, "main_pane"):
            return
        width = self.main_pane.winfo_width()
        if width > 200:
            self.main_pane.sashpos(0, int(width * 0.56))

    @staticmethod
    def _tk_toplevel_window_handle(widget) -> Optional[int]:
        if win32gui is None:
            return None
        window_handle = widget.winfo_id()
        while window_handle:
            if win32gui.GetClassName(window_handle) == "TkTopLevel":
                return window_handle
            parent_handle = win32gui.GetParent(window_handle)
            if not parent_handle:
                return window_handle
            window_handle = parent_handle
        return None

    def _install_native_mouse_buttons(self) -> None:
        if (
            win32gui is None
            or win32con is None
            or self.native_window_hooks
        ):
            return
        try:
            self.root.update_idletasks()
            window_handle = self._tk_toplevel_window_handle(self.root)
            if not window_handle:
                return
            window_handles = [window_handle]
            win32gui.EnumChildWindows(
                window_handle,
                lambda child_handle, _extra: window_handles.append(child_handle),
                None,
            )
            self.native_window_procedure = self._native_window_message
            for target_handle in window_handles:
                try:
                    old_procedure = win32gui.SetWindowLong(
                        target_handle,
                        win32con.GWL_WNDPROC,
                        self.native_window_procedure,
                    )
                    if old_procedure:
                        self.native_window_hooks[target_handle] = old_procedure
                except Exception:
                    continue
        except Exception:
            self.native_window_hooks.clear()

    def _ensure_native_mouse_buttons(self) -> None:
        if not self.native_window_hooks and self.root.winfo_exists():
            self.native_install_attempts += 1
            self._install_native_mouse_buttons()
            if (
                not self.native_window_hooks
                and self.native_install_attempts < 5
            ):
                self.root.after(300, self._ensure_native_mouse_buttons)

    def _native_window_message(self, hwnd, message, w_param, l_param):
        wm_xbutton_down = 0x020B
        wm_xbutton_up = 0x020C
        if message in {wm_xbutton_down, wm_xbutton_up}:
            button = (int(w_param) >> 16) & 0xFFFF
            if message == wm_xbutton_down:
                shortcut = "鼠标侧键1" if button == 1 else "鼠标侧键2"
                self.root.after_idle(
                    lambda selected_shortcut=shortcut: self._dispatch_mouse_shortcut(
                        selected_shortcut
                    )
                )
            return 0
        old_procedure = self.native_window_hooks.get(hwnd)
        if not old_procedure:
            return win32gui.DefWindowProc(hwnd, message, w_param, l_param)
        return win32gui.CallWindowProc(
            old_procedure, hwnd, message, w_param, l_param
        )

    def _restore_native_mouse_buttons(self) -> None:
        if win32gui is not None and win32con is not None:
            for window_handle, old_procedure in reversed(
                list(self.native_window_hooks.items())
            ):
                try:
                    if win32gui.IsWindow(window_handle):
                        win32gui.SetWindowLong(
                            window_handle,
                            win32con.GWL_WNDPROC,
                            old_procedure,
                        )
                except Exception:
                    continue
        self.native_window_hooks.clear()
        self.native_window_procedure = None

    # ----------------------------- 文件与状态 -----------------------------

    def open_folder(self) -> None:
        initial = str(self.folder or DEFAULT_FOLDER.parent)
        selected = filedialog.askdirectory(title="选择图片和 JSON 所在文件夹", initialdir=initial)
        if selected:
            self.load_folder(Path(selected))

    def load_folder(self, folder: Path) -> None:
        self._cancel_pointer_press()
        self.clear_default_track_id(notify=False, refresh=False)
        folder = folder.resolve()
        images = sorted(
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )
        if not images:
            messagebox.showerror("无法打开", "该文件夹中没有支持的图片。")
            return

        self._auto_confirm_viewed_rectangle()
        self._cancel_autosave()
        self.save_all()
        self.folder = folder
        self.images = images
        self.documents.clear()
        self.dirty_images.clear()
        self.undo_stacks.clear()
        self.undo_order.clear()
        self.selected_rectangle_by_image.clear()
        self.overview_dirty_positions.clear()
        self.frame_completion_state.clear()
        self._close_track_completion_dialog()
        self.track_id_mode = False
        self.track_mode_current_id = None
        self.direction_review_mode = False
        self.direction_review_item_index = -1
        self._refresh_track_mode_ui()

        discovered_labels = []
        try:
            for image_path in self.images:
                document = load_document(image_path)
                self.documents[image_path] = document
                discovered_labels.extend(collect_point_labels(document))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            messagebox.showerror("标注读取失败", str(error))
            return

        repaired_reviews = self._repair_legacy_manual_review_flags()

        for label in discovered_labels:
            if label and label not in self.labels:
                self.labels.append(label)
        self._update_label_widgets()

        self.image_combo.configure(values=[path.name for path in self.images])
        self.current_image_index = -1
        self.current_rectangle_index = -1
        direction_items = self._direction_review_items()
        self.direction_review_mode = bool(direction_items)
        self._refresh_track_mode_ui()
        if direction_items:
            self.direction_review_item_index = self._first_pending_direction_review_index()
            target = direction_items[self.direction_review_item_index]
            self._set_image_index(
                target["image_index"],
                preferred_group_id=target["group_id"],
                preferred_rectangle_position=target["rectangle_position"],
                auto_confirm_viewed=False,
            )
        else:
            self._set_image_index(0)
        self.settings["default_folder"] = str(folder)
        repaired_text = (
            f"｜已修复 {repaired_reviews} 个旧版待确认状态"
            if repaired_reviews
            else ""
        )
        self.status_var.set(f"已打开：{folder}{repaired_text}")
        self.root.title(f"蜜蜂关键点标注器 - {folder.name}")

    def _repair_legacy_manual_review_flags(self) -> int:
        """修复旧版 E 已经人工确认、但检测框仍残留待确认标志的数据。"""
        repaired = 0
        required = self._required_keypoint_labels()
        for image_path in self.images:
            document = self._get_document(image_path)
            rectangles = rectangle_records(document)
            changed_image = False
            for position, _rectangle in enumerate(rectangles):
                if not rectangle_review_is_pending(document, position):
                    continue
                records = keypoints_for_rectangle(document, position)
                exact_pair = all(
                    len(
                        [
                            record
                            for record in records
                            if record["label"] == label
                        ]
                    )
                    == 1
                    for label in required
                )
                if not exact_pair or any(point_is_suggested(record) for record in records):
                    continue
                if confirm_keypoints_for_rectangle(document, position):
                    repaired += 1
                    changed_image = True
            if changed_image:
                self.dirty_images.add(image_path)
        if repaired:
            self.save_all()
        return repaired

    def _get_document(self, image_path: Path) -> Dict:
        if image_path not in self.documents:
            self.documents[image_path] = load_document(image_path)
        return self.documents[image_path]

    def _current_image_path(self) -> Optional[Path]:
        if 0 <= self.current_image_index < len(self.images):
            return self.images[self.current_image_index]
        return None

    def _current_document(self) -> Optional[Dict]:
        image_path = self._current_image_path()
        return self._get_document(image_path) if image_path else None

    def _current_rectangles(self) -> List[Dict]:
        document = self._current_document()
        return rectangle_records(document) if document else []

    def _direction_review_items(self) -> List[Dict]:
        items = []
        for image_index, image_path in enumerate(getattr(self, "images", [])):
            document = self._get_document(image_path)
            for position, rectangle in enumerate(rectangle_records(document)):
                if not rectangle_is_direction_review_item(document, position):
                    continue
                metadata = direction_review_metadata(rectangle)
                items.append(
                    {
                        "image_index": image_index,
                        "image_path": image_path,
                        "rectangle_position": position,
                        "group_id": rectangle.get("group_id"),
                        "scene": metadata.get("scene", ""),
                        "frame": metadata.get("frame", ""),
                        "pending": rectangle_direction_review_is_pending(
                            document, position
                        ),
                    }
                )
        return items

    def _active_direction_review_item(self) -> Optional[Dict]:
        items = self._direction_review_items()
        index = getattr(self, "direction_review_item_index", -1)
        return items[index] if 0 <= index < len(items) else None

    def _go_to_direction_review_item(self, index: int) -> bool:
        items = self._direction_review_items()
        if not items:
            self.status_var.set("当前任务没有方向复审对象")
            return False
        index = max(0, min(index, len(items) - 1))
        self.direction_review_item_index = index
        item = items[index]
        self._set_image_index(
            item["image_index"],
            preferred_group_id=item["group_id"],
            preferred_rectangle_position=item["rectangle_position"],
            auto_confirm_viewed=False,
        )
        state = "待复审" if item["pending"] else "已复审"
        self.status_var.set(
            f"方向复审 {index + 1}/{len(items)}｜"
            f"Track ID {item['group_id']}｜帧 {item['frame']}｜{state}"
        )
        return True

    def _first_pending_direction_review_index(self) -> int:
        return next(
            (
                index
                for index, item in enumerate(self._direction_review_items())
                if item["pending"]
            ),
            0,
        )

    def toggle_direction_review_mode(self) -> None:
        if getattr(self, "continuous_annotation_mode", False):
            self.status_var.set("请先退出连续补标模式")
            return
        if getattr(self, "direction_review_mode", False):
            self.direction_review_mode = False
            self.direction_review_item_index = -1
            self._refresh_track_mode_ui()
            self.status_var.set("已退出方向复审批注模式")
            self._refresh_all()
            return
        if not self._direction_review_items():
            self.status_var.set("当前任务没有方向复审批注数据")
            return
        self.track_id_mode = False
        self.track_mode_current_id = None
        self.clear_default_track_id(notify=False, refresh=False)
        self.direction_review_mode = True
        self._refresh_track_mode_ui()
        self._go_to_direction_review_item(
            self._first_pending_direction_review_index()
        )

    def previous_direction_review_item(self) -> None:
        if not self.direction_review_mode:
            return
        if self.direction_review_item_index <= 0:
            self.status_var.set("已经是第一个复审对象")
            return
        self._go_to_direction_review_item(self.direction_review_item_index - 1)

    def next_direction_review_item(self) -> None:
        if not self.direction_review_mode:
            return
        items = self._direction_review_items()
        if self.direction_review_item_index + 1 >= len(items):
            self.status_var.set("已经是最后一个复审对象")
            return
        self._go_to_direction_review_item(self.direction_review_item_index + 1)

    def confirm_direction_review_and_next(self) -> None:
        item = self._active_direction_review_item()
        if item is None:
            self.status_var.set("当前没有可确认的方向复审对象")
            return
        document = self._get_document(item["image_path"])
        records = keypoints_for_rectangle(document, item["rectangle_position"])
        if not all(
            len([record for record in records if record["label"] == label]) == 1
            for label in self._required_keypoint_labels()
        ):
            self.status_var.set("当前复审对象必须各有一个 head 和 tail")
            return

        if item["pending"]:
            self._push_undo(item["image_path"])
            confirm_direction_review_for_rectangle(
                document, item["rectangle_position"]
            )
            self.dirty_images.add(item["image_path"])
            self.save_all()

        items = self._direction_review_items()
        pending_target = next(
            (
                index
                for index in range(self.direction_review_item_index + 1, len(items))
                if items[index]["pending"]
            ),
            None,
        )
        if pending_target is None:
            pending_target = next(
                (
                    index
                    for index in range(0, self.direction_review_item_index)
                    if items[index]["pending"]
                ),
                None,
            )
        if pending_target is None:
            self._refresh_all()
            self.status_var.set(f"方向复审已完成：{len(items)}/{len(items)}")
            messagebox.showinfo(
                "方向复审完成",
                f"全部 {len(items)} 个头尾方向对象均已复审。",
                parent=self.root,
            )
            return
        self._go_to_direction_review_item(pending_target)

    def _refresh_direction_review_queue(self) -> None:
        if not getattr(self, "direction_review_mode", False):
            return
        items = self._direction_review_items()
        reviewed = sum(not item["pending"] for item in items)
        pending = len(items) - reviewed
        current = self.direction_review_item_index + 1 if items else 0
        self.direction_review_summary_var.set(
            f"当前 {current}/{len(items)}｜已复审 {reviewed}｜剩余 {pending}"
        )
        tree = self.direction_review_tree
        tree.delete(*tree.get_children())
        for index, item in enumerate(items):
            status = "待复审" if item["pending"] else "已复审"
            scene = {"outdoor": "室外", "indoor": "室内"}.get(
                item["scene"], item["scene"]
            )
            tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(status, scene, item["frame"], item["group_id"]),
                tags=("pending" if item["pending"] else "reviewed",),
            )
        if 0 <= self.direction_review_item_index < len(items):
            iid = str(self.direction_review_item_index)
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)

    def _on_direction_review_tree_double_click(self, event) -> None:
        iid = self.direction_review_tree.identify_row(event.y)
        if iid:
            self._go_to_direction_review_item(int(iid))

    def _set_image_index(
        self,
        index: int,
        preferred_group_id=None,
        preferred_rectangle_position: Optional[int] = None,
        auto_confirm_viewed: bool = True,
    ) -> None:
        if not self.images:
            return
        self._cancel_pointer_press()
        self.rectangle_creation = None
        if getattr(self, "continuous_annotation_mode", False):
            self.continuous_draw_box_mode = False
        if self.rectangle_drag is not None:
            self._finish_rectangle_drag()
        if auto_confirm_viewed and not getattr(self, "direction_review_mode", False):
            self._auto_confirm_viewed_rectangle()
        index = max(0, min(index, len(self.images) - 1))
        old_image = self._current_image_path()
        if old_image is not None:
            self.selected_rectangle_by_image[old_image] = self.current_rectangle_index
            self.save_current()

        self.current_image_index = index
        image_path = self.images[index]
        try:
            with Image.open(image_path) as opened:
                self.current_pil_image = opened.convert("RGB")
            self.overview_cache_key = None
            self.overview_static_key = None
            self.detail_cache_key = None
        except OSError as error:
            messagebox.showerror("图片读取失败", str(error))
            self.current_pil_image = None

        rectangles = self._current_rectangles()
        preferred_positions = [
            position
            for position, rectangle in enumerate(rectangles)
            if preferred_group_id is not None
            and rectangle.get("group_id") == preferred_group_id
        ]
        if len(preferred_positions) == 1:
            self.current_rectangle_index = preferred_positions[0]
        elif (
            preferred_rectangle_position is not None
            and 0 <= preferred_rectangle_position < len(rectangles)
        ):
            self.current_rectangle_index = preferred_rectangle_position
        else:
            remembered = self.selected_rectangle_by_image.get(image_path)
            if remembered is not None and 0 <= remembered < len(rectangles):
                self.current_rectangle_index = remembered
            else:
                self.current_rectangle_index = self._find_first_missing_rectangle()
        if not rectangles:
            self.current_rectangle_index = -1

        self.frame_completion_state.setdefault(
            image_path,
            self._frame_is_complete(self._get_document(image_path)),
        )
        self.image_name_var.set(image_path.name)
        self._refresh_all()

    def _find_first_missing_rectangle(self) -> int:
        document = self._current_document()
        rectangles = self._current_rectangles()
        if not document or not rectangles:
            return -1
        points_by_rectangle = keypoints_by_rectangle(document, rectangles)
        desired = self.symmetry_source_label.get() or self.active_label.get()
        for position in range(len(rectangles)):
            labels = {
                record["label"] for record in points_by_rectangle.get(position, [])
            }
            if desired not in labels:
                return position
        return 0

    def previous_image(self) -> None:
        if self.track_id_mode:
            self._move_within_current_track(-1)
            return
        if self.current_image_index > 0:
            self._set_image_index(self.current_image_index - 1)
        else:
            self.status_var.set("已经是第一张图片")

    def next_image(self) -> None:
        if self.track_id_mode:
            self._move_within_current_track(1)
            return
        if self.current_image_index + 1 < len(self.images):
            self._set_image_index(self.current_image_index + 1)
        else:
            self.save_current()
            self.status_var.set("已经是最后一张图片")

    def _on_image_combo_selected(self, _event) -> None:
        if self.track_id_mode:
            return
        try:
            index = [path.name for path in self.images].index(self.image_name_var.get())
        except ValueError:
            return
        self._set_image_index(index)

    def save_current(self) -> None:
        image_path = self._current_image_path()
        if image_path is None or image_path not in self.dirty_images:
            return
        try:
            save_document(image_path, self._get_document(image_path))
            self.dirty_images.discard(image_path)
            self.status_var.set(f"已保存：{image_path.name}")
        except OSError as error:
            messagebox.showerror("保存失败", str(error))

    def _schedule_autosave(self) -> None:
        """短延迟合并连续写盘，让密集场景中的标点操作先响应界面。"""
        if self.autosave_job is not None:
            self.root.after_cancel(self.autosave_job)
        self.autosave_job = self.root.after(180, self._run_autosave)

    def _run_autosave(self) -> None:
        self.autosave_job = None
        dirty_count = len(self.dirty_images)
        if dirty_count == 0:
            return
        self.save_all()
        if not self.dirty_images:
            self.status_var.set("自动保存完成")

    def _cancel_autosave(self) -> None:
        if self.autosave_job is not None:
            self.root.after_cancel(self.autosave_job)
            self.autosave_job = None

    def save_all(self) -> None:
        for image_path in list(self.dirty_images):
            try:
                save_document(image_path, self._get_document(image_path))
                self.dirty_images.discard(image_path)
            except OSError as error:
                messagebox.showerror("保存失败", f"{image_path.name}\n{error}")
                break

    def _mark_dirty(self) -> None:
        image_path = self._current_image_path()
        if image_path:
            self.dirty_images.add(image_path)
            if self.current_rectangle_index >= 0:
                dirty_positions = getattr(self, "overview_dirty_positions", None)
                if dirty_positions is None:
                    dirty_positions = {}
                    self.overview_dirty_positions = dirty_positions
                dirty_positions.setdefault(image_path, set()).add(
                    self.current_rectangle_index
                )
            self.status_var.set("有未保存修改")

    def _push_undo(self, image_path: Optional[Path] = None) -> None:
        image_path = image_path or self._current_image_path()
        if image_path is None:
            return
        history = self.undo_stacks.setdefault(image_path, [])
        history.append(copy.deepcopy(self._get_document(image_path).get("shapes", [])))
        rectangle_position = (
            self.current_rectangle_index
            if image_path == self._current_image_path()
            else self.selected_rectangle_by_image.get(image_path, 0)
        )
        self.undo_order.append((image_path, rectangle_position))
        if len(history) > 50:
            del history[0]

    def _discard_last_undo(self, image_path: Optional[Path] = None) -> None:
        image_path = image_path or self._current_image_path()
        if image_path is None:
            return
        history = self.undo_stacks.get(image_path, [])
        if history:
            history.pop()
        for index in range(len(self.undo_order) - 1, -1, -1):
            if self.undo_order[index][0] == image_path:
                del self.undo_order[index]
                break

    def undo(self) -> None:
        while self.undo_order:
            image_path, rectangle_position = self.undo_order.pop()
            history = self.undo_stacks.get(image_path, [])
            if history:
                break
        else:
            self.status_var.set("当前任务没有可撤销操作")
            return
        self._get_document(image_path)["shapes"] = history.pop()
        self.dirty_images.add(image_path)
        if image_path == self._current_image_path():
            self.overview_static_key = None
        self.save_all()
        if image_path in self.images:
            self._set_image_index(
                self.images.index(image_path),
                preferred_rectangle_position=rectangle_position,
                auto_confirm_viewed=False,
            )
        self.status_var.set(f"已撤销：{image_path.name}（已自动保存）")
        self._refresh_all()

    # ----------------------------- 框与点操作 -----------------------------

    def toggle_current_bee_shadow_class(self) -> None:
        document = self._current_document()
        rectangles = self._current_rectangles()
        position = self.current_rectangle_index
        if document is None or not 0 <= position < len(rectangles):
            self.status_var.set("当前没有可切换类别的检测框")
            return

        shape = rectangles[position]["shape"]
        current_label = str(shape.get("label", "")).strip().lower()
        if current_label == "bee":
            target_label = "beeshadow"
        elif current_label == "beeshadow":
            target_label = "bee"
        else:
            self.status_var.set(
                f"当前框类别为 {shape.get('label', '')}，只支持 bee / beeshadow 切换"
            )
            return

        self._push_undo()
        shape["label"] = target_label
        self.overview_static_key = None
        self._mark_dirty()
        self._schedule_autosave()
        self.status_var.set(
            f"当前框类别已从 {current_label} 改为 {target_label}（正在自动保存）"
        )
        self._refresh_all()

    def previous_rectangle(self) -> None:
        if getattr(self, "direction_review_mode", False):
            self.previous_direction_review_item()
            return
        if self.track_id_mode:
            self.previous_track_id()
            return
        rectangles = self._current_rectangles()
        if not rectangles:
            return
        target = (self.current_rectangle_index - 1) % len(rectangles)
        self._select_rectangle_position(target)

    def next_rectangle(self) -> None:
        if getattr(self, "continuous_annotation_mode", False):
            self.copy_continuous_object_to_next()
            return
        if getattr(self, "direction_review_mode", False):
            self.confirm_direction_review_and_next()
            return
        if self.track_id_mode:
            self.confirm_and_advance_track()
            return
        rectangles = self._current_rectangles()
        if not rectangles:
            return
        target = (self.current_rectangle_index + 1) % len(rectangles)
        self._select_rectangle_position(target)

    def _select_rectangle_position(self, position: int) -> None:
        rectangles = self._current_rectangles()
        if not 0 <= position < len(rectangles):
            return
        if self.track_id_mode:
            target_group_id = rectangles[position].get("group_id")
            if target_group_id != self.track_mode_current_id:
                self.status_var.set(
                    f"按 ID 模式已锁定 Track ID {self.track_mode_current_id}，"
                    "请用 Q/E 或 G 切换 ID"
                )
                return
        if position != self.current_rectangle_index:
            if not self.track_id_mode and not getattr(
                self, "direction_review_mode", False
            ):
                self._auto_confirm_viewed_rectangle()
        self.current_rectangle_index = position
        self._remember_current_rectangle()
        self.status_var.set(f"已进入检测框序号 {position + 1}")
        self._refresh_all()

    @staticmethod
    def _required_keypoint_labels() -> Tuple[str, str]:
        return "head", "tail"

    def _keypoint_state(self, records: List[Dict], rectangle: Optional[Dict] = None) -> str:
        if not records:
            if rectangle and rectangle["shape"].get("flags", {}).get(
                REVIEW_REQUIRED_FLAG, False
            ):
                return "pending"
            return "unlabeled"
        if (
            rectangle
            and rectangle["shape"].get("flags", {}).get(
                REVIEW_REQUIRED_FLAG, False
            )
        ) or any(point_is_suggested(record) for record in records):
            return "pending"
        required = self._required_keypoint_labels()
        if all(
            len([record for record in records if record["label"] == label]) == 1
            for label in required
        ):
            return "confirmed"
        return "partial"

    def _rectangle_table_status(
        self, rectangle: Dict, records: List[Dict]
    ) -> Tuple[str, str, bool]:
        counts = {
            label: len([record for record in records if record["label"] == label])
            for label in self._required_keypoint_labels()
        }
        if not records:
            return "未标注", "head、tail", False

        problems = []
        for label, count in counts.items():
            if count == 0:
                problems.append(f"缺 {label}")
            elif count > 1:
                problems.append(f"{label} 重复")
        if problems:
            return "不完整", "；".join(problems), False
        if self._keypoint_state(records, rectangle) == "pending":
            return "待检查", "确认黄色关键点", False
        return "已完成", "—", True

    def _frame_is_complete(self, document: Dict) -> bool:
        rectangles = rectangle_records(document)
        if not rectangles:
            return False
        points_by_rectangle = keypoints_by_rectangle(document, rectangles)
        return all(
            self._rectangle_table_status(
                rectangle,
                points_by_rectangle.get(position, []),
            )[2]
            for position, rectangle in enumerate(rectangles)
        )

    def _update_frame_completion_state(
        self,
        image_path: Optional[Path] = None,
        notify: bool = False,
    ) -> bool:
        image_path = image_path or self._current_image_path()
        if image_path is None:
            return False
        document = self._get_document(image_path)
        complete = self._frame_is_complete(document)
        previous = self.frame_completion_state.get(image_path)
        self.frame_completion_state[image_path] = complete
        if (
            notify
            and not getattr(self, "continuous_annotation_mode", False)
            and previous is False
            and complete
        ):
            total = len(rectangle_records(document))
            image_name = image_path.name
            self.root.after_idle(
                lambda: messagebox.showinfo(
                    "本页标注完成",
                    f"{image_name}\n\n本页所有检测框均已检查并标注完成："
                    f"{total} / {total} 个框。",
                    parent=self.root,
                )
            )
        return complete

    def _auto_confirm_viewed_rectangle(self) -> int:
        document = self._current_document()
        rectangles = self._current_rectangles()
        position = self.current_rectangle_index
        if document is None or not 0 <= position < len(rectangles):
            return 0
        if not rectangle_review_is_pending(document, position):
            return 0
        records = keypoints_for_rectangle(document, position)
        required = self._required_keypoint_labels()
        if not all(
            len([record for record in records if record["label"] == label]) == 1
            for label in required
        ):
            return 0
        self._push_undo()
        changed = confirm_keypoints_for_rectangle(document, position)
        if changed == 0:
            self._discard_last_undo()
            return 0
        image_path = self._current_image_path()
        if image_path is not None:
            self.dirty_images.add(image_path)
            dirty_positions = getattr(self, "overview_dirty_positions", None)
            if dirty_positions is None:
                dirty_positions = {}
                self.overview_dirty_positions = dirty_positions
            dirty_positions.setdefault(image_path, set()).add(position)
            if hasattr(self, "root"):
                self._schedule_autosave()
            else:
                self.save_current()
            if hasattr(self, "frame_completion_state") and hasattr(self, "root"):
                self._update_frame_completion_state(image_path, notify=True)
        return changed

    def _current_group_id(self):
        rectangles = self._current_rectangles()
        if 0 <= self.current_rectangle_index < len(rectangles):
            return rectangles[self.current_rectangle_index].get("group_id")
        return None

    def _track_occurrences(self, group_id) -> List[Tuple[int, Path, int, Dict]]:
        if group_id is None:
            return []
        occurrences = []
        for image_index, image_path in enumerate(self.images):
            document = self._get_document(image_path)
            matches = [
                (position, rectangle)
                for position, rectangle in enumerate(rectangle_records(document))
                if rectangle.get("group_id") == group_id
            ]
            if len(matches) == 1:
                position, rectangle = matches[0]
                occurrences.append((image_index, image_path, position, rectangle))
        return occurrences

    @staticmethod
    def _track_id_sort_key(group_id) -> Tuple[int, object, str]:
        try:
            return 0, int(group_id), str(group_id)
        except (TypeError, ValueError):
            return 1, str(group_id).casefold(), str(group_id)

    def _track_id_rows(self) -> List[Dict]:
        rows: Dict[object, Dict] = {}
        for image_path in self.images:
            document = self._get_document(image_path)
            rectangles = rectangle_records(document)
            points_by_rectangle = keypoints_by_rectangle(document, rectangles)
            for position, rectangle in enumerate(rectangles):
                group_id = rectangle.get("group_id")
                if group_id is None:
                    continue
                row = rows.setdefault(
                    group_id,
                    {
                        "group_id": group_id,
                        "total": 0,
                        "completed": 0,
                        "pending": 0,
                    },
                )
                row["total"] += 1
                status, _detail, complete = self._rectangle_table_status(
                    rectangle,
                    points_by_rectangle.get(position, []),
                )
                if complete:
                    row["completed"] += 1
                elif status == "待检查":
                    row["pending"] += 1
        result = []
        for row in rows.values():
            item = dict(row)
            item["remaining"] = item["total"] - item["completed"]
            result.append(item)
        return sorted(result, key=lambda row: self._track_id_sort_key(row["group_id"]))

    def _track_completion_stats(self, group_id) -> Dict:
        occurrences = self._track_occurrences(group_id)
        completed = 0
        pending = 0
        for _image_index, image_path, position, rectangle in occurrences:
            records = keypoints_for_rectangle(self._get_document(image_path), position)
            status, _detail, complete = self._rectangle_table_status(rectangle, records)
            if complete:
                completed += 1
            elif status == "待检查":
                pending += 1
        return {
            "group_id": group_id,
            "occurrences": occurrences,
            "total": len(occurrences),
            "completed": completed,
            "remaining": len(occurrences) - completed,
            "pending": pending,
        }

    def _current_track_stats_for_display(self) -> Dict:
        cached = getattr(self, "refresh_track_stats", None)
        if cached and cached.get("group_id") == self.track_mode_current_id:
            return cached
        return self._track_completion_stats(self.track_mode_current_id)

    def _current_track_occurrence_position(
        self, occurrences: List[Tuple[int, Path, int, Dict]]
    ) -> int:
        for index, occurrence in enumerate(occurrences):
            if (
                occurrence[0] == self.current_image_index
                and occurrence[2] == self.current_rectangle_index
            ):
                return index
        return -1

    def _go_to_track_id(self, group_id, first_unfinished: bool = False) -> bool:
        occurrences = self._track_occurrences(group_id)
        if not occurrences:
            self.status_var.set(f"Track ID {group_id} 没有可用检测框")
            return False
        target = occurrences[0]
        if first_unfinished:
            for occurrence in occurrences:
                records = keypoints_for_rectangle(
                    self._get_document(occurrence[1]), occurrence[2]
                )
                if not self._rectangle_table_status(
                    occurrence[3], records
                )[2]:
                    target = occurrence
                    break
        self.track_mode_current_id = group_id
        self._set_image_index(
            target[0],
            preferred_group_id=group_id,
            auto_confirm_viewed=False,
        )
        stats = self._track_completion_stats(group_id)
        self.status_var.set(
            f"已进入 Track ID {group_id}：剩余 "
            f"{stats['remaining']} / {stats['total']} 帧"
        )
        return True

    def toggle_track_id_mode(self) -> None:
        if getattr(self, "continuous_annotation_mode", False):
            self.status_var.set("请先退出连续补标模式")
            return
        if self.track_id_mode:
            self._close_track_completion_dialog()
            self.track_id_mode = False
            self.track_mode_current_id = None
            self._refresh_track_mode_ui()
            self.status_var.set("已返回原始逐帧标注模式")
            self._refresh_all()
            return

        rows = self._track_id_rows()
        if not rows:
            self.status_var.set("当前任务没有 Track ID，无法进入按 ID 模式")
            return
        current_group_id = self._current_group_id()
        row_by_id = {row["group_id"]: row for row in rows}
        current_row = row_by_id.get(current_group_id)
        if current_row is not None and current_row["remaining"] > 0:
            target_id = current_group_id
        else:
            target_id = self._next_unfinished_track_id(current_group_id)
            if target_id is None:
                target_id = next(
                    (row["group_id"] for row in rows if row["remaining"] > 0),
                    rows[0]["group_id"],
                )
        self.clear_default_track_id(notify=False, refresh=False)
        self.track_id_mode = True
        self._refresh_track_mode_ui()
        self._go_to_track_id(target_id, first_unfinished=False)

    def previous_track_id(self) -> None:
        rows = self._track_id_rows()
        if not rows:
            return
        track_ids = [row["group_id"] for row in rows]
        current_id = self.track_mode_current_id
        try:
            current_position = track_ids.index(current_id)
        except ValueError:
            current_position = 0
        if current_position <= 0:
            self.status_var.set("已经是第一个 Track ID")
            return
        self._close_track_completion_dialog()
        self._go_to_track_id(track_ids[current_position - 1], first_unfinished=False)

    def next_track_id(self) -> None:
        if getattr(self, "direction_review_mode", False):
            self.next_direction_review_item()
            return
        if not self.track_id_mode:
            self.status_var.set("请先进入按 ID 标注模式")
            return
        rows = self._track_id_rows()
        if not rows:
            return
        track_ids = [row["group_id"] for row in rows]
        try:
            current_position = track_ids.index(self.track_mode_current_id)
        except ValueError:
            current_position = -1
        if current_position + 1 >= len(track_ids):
            self.status_var.set("已经是最后一个 Track ID")
            return
        self._close_track_completion_dialog()
        self._go_to_track_id(
            track_ids[current_position + 1],
            first_unfinished=True,
        )

    def _next_unfinished_track_id(self, current_id):
        rows = self._track_id_rows()
        track_ids = [row["group_id"] for row in rows]
        try:
            start = track_ids.index(current_id) + 1
        except ValueError:
            start = 0
        ordered_rows = rows[start:] + rows[:start]
        return next(
            (
                row["group_id"]
                for row in ordered_rows
                if row["group_id"] != current_id and row["remaining"] > 0
            ),
            None,
        )

    def _confirm_current_track_pair(self) -> bool:
        document = self._current_document()
        position = self.current_rectangle_index
        if document is None or position < 0:
            return False
        records = keypoints_for_rectangle(document, position)
        required = self._required_keypoint_labels()
        if not all(
            len([record for record in records if record["label"] == label]) == 1
            for label in required
        ):
            self.status_var.set("当前框必须各有一个 head 和 tail，E 才能继续")
            return False
        rectangles = self._current_rectangles()
        if not 0 <= position < len(rectangles):
            return False
        if self._rectangle_table_status(rectangles[position], records)[2]:
            return True
        self._push_undo()
        changed = confirm_keypoints_for_rectangle(document, position)
        if changed == 0:
            self._discard_last_undo()
        else:
            self._mark_dirty()
            self.save_current()
        confirmed_records = keypoints_for_rectangle(document, position)
        return self._rectangle_table_status(
            self._current_rectangles()[position], confirmed_records
        )[2]

    def confirm_and_advance_track(self) -> None:
        if not self.track_id_mode:
            return
        group_id = self.track_mode_current_id
        if group_id is None or self._current_group_id() != group_id:
            self.status_var.set("当前检测框与按 ID 模式不一致，请用 G 重新选择 ID")
            return
        if not self._confirm_current_track_pair():
            self._refresh_all()
            return

        occurrences = self._track_occurrences(group_id)
        occurrence_position = self._current_track_occurrence_position(occurrences)
        if occurrence_position < 0:
            self.status_var.set(f"找不到 Track ID {group_id} 的当前帧")
            return
        if occurrence_position + 1 < len(occurrences):
            source = occurrences[occurrence_position]
            target = occurrences[occurrence_position + 1]
            plan = build_trackid_transfer_plan(
                self._get_document(source[1]),
                self._get_document(target[1]),
                group_id,
                overwrite_suggested=True,
            )
            applied = 0
            if plan["actions"]:
                self._push_undo(target[1])
                applied = apply_transfer_plan(
                    self._get_document(target[1]),
                    plan,
                    overwrite_same_label=False,
                    suggested=True,
                )
                if applied:
                    self.dirty_images.add(target[1])
                    self.save_all()
                else:
                    self._discard_last_undo(target[1])
            self._set_image_index(
                target[0],
                preferred_group_id=group_id,
                auto_confirm_viewed=False,
            )
            self.status_var.set(
                f"Track ID {group_id}：已确认第 {occurrence_position + 1} 帧，"
                f"向下一帧复制 {applied} 个黄色待确认点"
            )
            self._refresh_all()
            return

        stats = self._track_completion_stats(group_id)
        if stats["remaining"] > 0:
            self._go_to_track_id(group_id, first_unfinished=True)
            self.status_var.set(
                f"Track ID {group_id} 还有 {stats['remaining']} 帧未完成，"
                "已跳到第一个未完成框"
            )
            return
        self._show_track_completion_dialog(group_id)

    def _track_completion_dialog_exists(self) -> bool:
        dialog = getattr(self, "track_completion_dialog", None)
        if dialog is None:
            return False
        try:
            return bool(dialog.winfo_exists())
        except tk.TclError:
            return False

    def _close_track_completion_dialog(self) -> None:
        dialog = getattr(self, "track_completion_dialog", None)
        self.track_completion_dialog = None
        if dialog is not None:
            try:
                if dialog.winfo_exists():
                    dialog.grab_release()
                    dialog.destroy()
            except tk.TclError:
                pass

    def _show_track_completion_dialog(self, group_id) -> None:
        if self._track_completion_dialog_exists():
            self.track_completion_dialog.lift()
            return
        window = tk.Toplevel(self.root)
        self.track_completion_dialog = window
        window.title("Track ID 标注完成")
        window.geometry("500x240")
        window.resizable(False, False)
        window.transient(self.root)
        window.configure(background=COLORS["background"])
        window.protocol("WM_DELETE_WINDOW", self._close_track_completion_dialog)

        body = ttk.Frame(window, padding=24)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            body,
            text=f"Track ID {group_id} 已全部标注完成",
            style="Title.TLabel",
        ).pack(anchor=tk.W, pady=(0, 12))
        ttk.Label(
            body,
            text="E：确认并进入下一个未完成 ID\nQ：返回上一个 Track ID",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(0, 18))
        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X)
        ttk.Button(
            buttons,
            text="Q  返回上一 ID",
            command=self._return_previous_track_from_completion,
        ).pack(side=tk.LEFT)
        ttk.Button(
            buttons,
            text="E  确认并下一 ID",
            style="Success.TButton",
            command=self._accept_track_completion,
        ).pack(side=tk.RIGHT)
        window.bind("<KeyPress-e>", lambda _event: self._accept_track_completion())
        window.bind("<KeyPress-q>", lambda _event: self._return_previous_track_from_completion())
        window.grab_set()
        window.focus_force()

    def _accept_track_completion(self) -> None:
        current_id = self.track_mode_current_id
        self._close_track_completion_dialog()
        target_id = self._next_unfinished_track_id(current_id)
        if target_id is None:
            self.status_var.set("所有 Track ID 都已标注完成")
            messagebox.showinfo(
                "全部完成",
                "当前任务中的所有 Track ID 都已标注完成。",
                parent=self.root,
            )
            return
        self._go_to_track_id(target_id, first_unfinished=False)

    def _return_previous_track_from_completion(self) -> None:
        self._close_track_completion_dialog()
        self.previous_track_id()

    def _move_within_current_track(self, direction: int) -> None:
        group_id = (
            self.track_mode_current_id
            if self.track_id_mode
            else self._current_group_id()
        )
        if group_id is None:
            self.status_var.set("当前框没有 Track ID，无法沿轨迹切换")
            return
        occurrences = self._track_occurrences(group_id)
        candidates = [
            occurrence
            for occurrence in occurrences
            if (occurrence[0] - self.current_image_index) * direction > 0
        ]
        if not candidates:
            boundary = "之前" if direction < 0 else "之后"
            self.status_var.set(f"Track ID {group_id} 在当前帧{boundary}没有检测框")
            return
        target = min(
            candidates,
            key=lambda occurrence: abs(occurrence[0] - self.current_image_index),
        )
        self._set_image_index(
            target[0],
            preferred_group_id=group_id,
            auto_confirm_viewed=not self.track_id_mode
            and not getattr(self, "direction_review_mode", False),
        )
        self.status_var.set(
            f"Track ID {group_id}：第 {target[0] + 1}/{len(self.images)} 张"
        )

    def previous_track_frame(self) -> None:
        if getattr(self, "continuous_annotation_mode", False):
            self._move_continuous_frame(-1)
            return
        self._move_within_current_track(-1)

    def next_track_frame(self) -> None:
        if getattr(self, "continuous_annotation_mode", False):
            self._move_continuous_frame(1)
            return
        self._move_within_current_track(1)

    def _move_continuous_frame(self, direction: int) -> None:
        target_index = self.current_image_index + direction
        if target_index < 0:
            self.status_var.set("已经是第一帧")
            return
        if target_index >= len(self.images):
            self.status_var.set("已经是最后一帧")
            return
        focus_point = self.continuous_focus_point
        zoom_factor = self.continuous_zoom_factor
        self._set_image_index(target_index, auto_confirm_viewed=False)
        self.continuous_focus_point = focus_point
        self.continuous_zoom_factor = zoom_factor
        active_position = self._continuous_rectangle_position()
        self.current_rectangle_index = active_position
        if active_position >= 0:
            self._remember_current_rectangle()
        self.detail_cache_key = None
        self._refresh_all()
        has_active_box = active_position >= 0
        self.status_var.set(
            f"连续补标 ID {self.continuous_active_track_id}："
            f"第 {target_index + 1}/{len(self.images)} 帧；"
            f"当前帧{'已有该 ID' if has_active_box else '尚未标注该 ID'}"
        )

    def copy_current_frame_to_next(self) -> None:
        if getattr(self, "continuous_annotation_mode", False):
            self.begin_continuous_rectangle_drawing()
            return
        if self.track_id_mode:
            self.status_var.set("按 ID 模式请用 E：只复制当前 ID 并向后移动")
            return
        if not self.images or self.current_image_index >= len(self.images) - 1:
            self.status_var.set("当前已经是最后一张，无法复制到下一张")
            return

        source_index = self.current_image_index
        target_index = source_index + 1
        source_path = self.images[source_index]
        target_path = self.images[target_index]
        preferred_group_id = self._current_group_id()
        plan = build_next_frame_transfer_plan(
            self._get_document(source_path),
            self._get_document(target_path),
            overwrite_suggested=True,
        )

        applied = 0
        if plan["actions"]:
            self._push_undo(target_path)
            applied = apply_transfer_plan(
                self._get_document(target_path),
                plan,
                overwrite_same_label=False,
                suggested=True,
            )
            if applied:
                self.dirty_images.add(target_path)
                self.save_all()

        self._set_image_index(target_index, preferred_group_id=preferred_group_id)
        self.status_var.set(
            f"已从上一张匹配 {plan['matched_tracks']} 个 Track ID，"
            f"复制 {applied} 个黄色待确认点；"
            f"保留 {plan['skipped_existing']} 个已有人工点"
        )
        self._refresh_all()

    def begin_continuous_rectangle_drawing(self) -> None:
        if not getattr(self, "continuous_annotation_mode", False):
            return
        if self.current_pil_image is None or self.detail_transform is None:
            self.status_var.set("请先在右侧整图点击需要放大的区域")
            return
        self.continuous_draw_box_mode = True
        self.rectangle_creation = None
        if self.labels:
            self.active_label.set(self.labels[0])
        self.status_var.set("画框状态：请在左侧放大图中拖动检测框")
        self._refresh_default_track_id_controls()
        self._draw_detail()

    def copy_continuous_object_to_next(self) -> None:
        if not getattr(self, "continuous_annotation_mode", False):
            return
        if not self.images or self.current_image_index >= len(self.images) - 1:
            self.status_var.set("已经是最后一帧，无法继续复制")
            return

        source_path = self._current_image_path()
        source_document = self._current_document()
        source_position = self._continuous_rectangle_position(source_document)
        if source_path is None or source_document is None or source_position < 0:
            self.status_var.set("当前帧还没有该 ID 的检测框，请先按 R 画框")
            return

        target_index = self.current_image_index + 1
        target_path = self.images[target_index]
        target_document = self._get_document(target_path)
        target_position = self._continuous_rectangle_position(target_document)
        copied = target_position < 0
        if copied:
            self._push_undo(target_path)
            target_position = copy_rectangle_with_keypoints(
                source_document,
                source_position,
                target_document,
                suggested=True,
            )
            self.dirty_images.add(target_path)
            self.save_all()

        self.continuous_draw_box_mode = False
        self.rectangle_creation = None
        self._set_image_index(
            target_index,
            preferred_rectangle_position=target_position,
            auto_confirm_viewed=False,
        )
        self._align_continuous_view_to_current_frame()
        track_id = self.continuous_active_track_id
        if copied:
            self.status_var.set(
                f"已将 ID {track_id} 的检测框和关键点复制到第 "
                f"{target_index + 1} 张；复制内容为黄色待确认"
            )
        else:
            self.status_var.set(
                f"第 {target_index + 1} 张已存在 ID {track_id}，"
                "未覆盖人工标注，已直接选中"
            )

    def _records_form_confirmed_pair(self, records: List[Dict]) -> bool:
        required = self._required_keypoint_labels()
        return all(
            len([record for record in records if record["label"] == label]) == 1
            for label in required
        ) and not any(point_is_suggested(record) for record in records)

    def propagate_current_track(self) -> None:
        if self.track_id_mode:
            self.status_var.set("按 ID 模式请用 E 逐帧确认和传播")
            return
        group_id = self._current_group_id()
        if group_id is None:
            self.status_var.set("当前框没有 Track ID，请改用 IoU 传播")
            return
        occurrences = self._track_occurrences(group_id)
        if len(occurrences) < 2:
            self.status_var.set(f"Track ID {group_id} 只出现一次，无需传播")
            return

        anchors = []
        for occurrence in occurrences:
            document = self._get_document(occurrence[1])
            records = keypoints_for_rectangle(document, occurrence[2])
            if self._records_form_confirmed_pair(records):
                anchors.append(occurrence)
        if not anchors:
            messagebox.showinfo(
                "ID 传播",
                "这条轨迹还没有完整且已确认的 head/tail。\n"
                "请先人工标好一个关键帧，再执行传播。",
            )
            return

        planned = []
        action_count = 0
        skipped_existing = 0
        replaced_suggested = 0
        for target in occurrences:
            source = min(anchors, key=lambda item: abs(item[0] - target[0]))
            if source[0] == target[0]:
                continue
            plan = build_trackid_transfer_plan(
                self._get_document(source[1]),
                self._get_document(target[1]),
                group_id,
                overwrite_suggested=True,
            )
            if plan["actions"]:
                planned.append((target, plan))
                action_count += len(plan["actions"])
            skipped_existing += plan["skipped_existing"]
            replaced_suggested += plan["replaced_suggested"]

        if action_count == 0:
            self.status_var.set(
                f"Track ID {group_id} 没有需要新增或更新的待确认关键点"
            )
            return
        if not messagebox.askyesno(
            "确认 Track ID 传播",
            f"Track ID：{group_id}\n"
            f"轨迹帧数：{len(occurrences)}\n"
            f"已确认关键帧：{len(anchors)}\n"
            f"将写入待确认关键点：{action_count}\n"
            f"其中更新旧建议：{replaced_suggested}\n"
            f"保留人工关键点：{skipped_existing}\n\n"
            "是否执行？",
        ):
            return

        changed_images = 0
        applied = 0
        for target, plan in planned:
            self._push_undo(target[1])
            count = apply_transfer_plan(
                self._get_document(target[1]),
                plan,
                overwrite_same_label=False,
                suggested=True,
            )
            if count:
                applied += count
                changed_images += 1
                self.dirty_images.add(target[1])
        self.save_all()
        self.status_var.set(
            f"Track ID {group_id}：已在 {changed_images} 张图写入 "
            f"{applied} 个黄色待确认点"
        )
        self._refresh_all()

    def confirm_current_keypoints(self) -> None:
        document = self._current_document()
        if document is None or self.current_rectangle_index < 0:
            return
        records = keypoints_for_rectangle(document, self.current_rectangle_index)
        required = self._required_keypoint_labels()
        if not all(
            len([record for record in records if record["label"] == label]) == 1
            for label in required
        ):
            self.status_var.set("当前框必须各有一个 head 和 tail 才能确认")
            return
        self._push_undo()
        changed = confirm_keypoints_for_rectangle(
            document, self.current_rectangle_index
        )
        if changed == 0:
            self._discard_last_undo()
            self.status_var.set("当前 head/tail 已经是人工确认状态")
            return
        self._mark_dirty()
        self._schedule_autosave()
        self.status_var.set("已确认当前框，复审进度已更新")
        self._refresh_all()

    def swap_current_head_tail(self) -> None:
        document = self._current_document()
        if document is None or self.current_rectangle_index < 0:
            return
        self._push_undo()
        if not swap_keypoint_labels(document, self.current_rectangle_index):
            self._discard_last_undo()
            self.status_var.set("交换需要当前框各有一个 head 和 tail")
            return
        self._mark_dirty()
        self._schedule_autosave()
        self.status_var.set("已交换 head/tail，并标记为人工确认")
        self._refresh_all()

    def _rectangle_review_reasons(
        self, rectangle: Dict, records: List[Dict]
    ) -> List[str]:
        if not records:
            return []
        reasons = []
        required = self._required_keypoint_labels()
        counts = {
            label: len([record for record in records if record["label"] == label])
            for label in required
        }
        if rectangle["shape"].get("flags", {}).get(
            REVIEW_REQUIRED_FLAG, False
        ) or any(point_is_suggested(record) for record in records):
            reasons.append("自动传播待确认")
        missing = [label for label, count in counts.items() if count == 0]
        if missing:
            reasons.append("缺少 " + "/".join(missing))
        if any(count > 1 for count in counts.values()):
            reasons.append("同名关键点重复")
        if any(not point_in_rect(record["point"], rectangle["rect"]) for record in records):
            reasons.append("关键点超出检测框")
        head = [record for record in records if record["label"] == "head"]
        tail = [record for record in records if record["label"] == "tail"]
        if len(head) == 1 and len(tail) == 1:
            x1, y1, x2, y2 = rectangle["rect"]
            diagonal = math.hypot(x2 - x1, y2 - y1) or 1.0
            distance = math.hypot(
                head[0]["point"][0] - tail[0]["point"][0],
                head[0]["point"][1] - tail[0]["point"][1],
            )
            ratio = distance / diagonal
            if ratio < 0.15 or ratio > 0.85:
                reasons.append(f"头尾距离异常({ratio:.2f})")
        return reasons

    def _collect_review_issues(self) -> List[Dict]:
        issues = []
        last_angle_by_track = {}
        for image_index, image_path in enumerate(self.images):
            document = self._get_document(image_path)
            rectangles = rectangle_records(document)
            points_by_rectangle = keypoints_by_rectangle(document, rectangles)
            for position, rectangle in enumerate(rectangles):
                records = points_by_rectangle.get(position, [])
                reasons = self._rectangle_review_reasons(rectangle, records)
                head = [record for record in records if record["label"] == "head"]
                tail = [record for record in records if record["label"] == "tail"]
                group_id = rectangle.get("group_id")
                if group_id is not None and len(head) == 1 and len(tail) == 1:
                    angle = math.atan2(
                        head[0]["point"][1] - tail[0]["point"][1],
                        head[0]["point"][0] - tail[0]["point"][0],
                    )
                    previous = last_angle_by_track.get(group_id)
                    if previous is not None:
                        delta = abs(
                            (angle - previous + math.pi) % (2 * math.pi) - math.pi
                        )
                        if delta > math.radians(120):
                            reasons.append(f"相邻关键帧方向跳变 {math.degrees(delta):.0f}°")
                    last_angle_by_track[group_id] = angle
                if reasons:
                    issues.append(
                        {
                            "image_index": image_index,
                            "rectangle_position": position,
                            "group_id": group_id,
                            "reasons": list(dict.fromkeys(reasons)),
                        }
                    )
        return issues

    def next_review_issue(self) -> None:
        if self.track_id_mode:
            self.open_track_id_table()
            return
        issues = self._collect_review_issues()
        if not issues:
            self.status_var.set("没有待确认或异常的已标关键点")
            return
        current = (self.current_image_index, self.current_rectangle_index)
        target = next(
            (
                issue
                for issue in issues
                if (issue["image_index"], issue["rectangle_position"]) > current
            ),
            issues[0],
        )
        self._set_image_index(
            target["image_index"],
            preferred_group_id=target["group_id"],
            preferred_rectangle_position=target["rectangle_position"],
        )
        group_text = (
            f"ID {target['group_id']}" if target["group_id"] is not None else "无 ID"
        )
        self.status_var.set(group_text + "：" + "；".join(target["reasons"]))

    def open_frame_table(self) -> None:
        if getattr(self, "direction_review_mode", False):
            self.direction_review_tree.focus_set()
            self.status_var.set("方向复审清单已显示在模型对照图下方")
            return
        if self.track_id_mode:
            self.open_track_id_table()
            return
        document = self._current_document()
        rectangles = self._current_rectangles()
        if document is None or not rectangles:
            self.status_var.set("当前图片没有可列出的检测框")
            return

        points_by_rectangle = keypoints_by_rectangle(document, rectangles)
        rows = []
        for position, rectangle in enumerate(rectangles):
            status, detail, complete = self._rectangle_table_status(
                rectangle,
                points_by_rectangle.get(position, []),
            )
            rows.append(
                {
                    "position": position,
                    "serial": position + 1,
                    "group_id": rectangle.get("group_id"),
                    "status": status,
                    "detail": detail,
                    "complete": complete,
                }
            )

        window = tk.Toplevel(self.root)
        window.title("本帧检测框清单")
        window.geometry("660x520")
        window.minsize(560, 380)
        window.transient(self.root)
        window.configure(background=COLORS["background"])

        header = ttk.Frame(window, padding=(14, 12, 14, 8))
        header.pack(fill=tk.X)
        summary_var = tk.StringVar()
        ttk.Label(
            header,
            text="本帧检测框清单",
            style="Title.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Label(
            header,
            textvariable=summary_var,
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, padx=(12, 0))
        only_unfinished = tk.BooleanVar(value=True)

        table_frame = ttk.Frame(window, padding=(14, 0, 14, 8))
        table_frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(
            table_frame,
            columns=("serial", "track_id", "status", "detail"),
            show="headings",
            selectmode="browse",
        )
        tree.heading("serial", text="序号")
        tree.heading("track_id", text="Track ID")
        tree.heading("status", text="标注状态")
        tree.heading("detail", text="缺少内容 / 操作")
        tree.column("serial", width=70, minwidth=60, anchor=tk.CENTER)
        tree.column("track_id", width=100, minwidth=80, anchor=tk.CENTER)
        tree.column("status", width=100, minwidth=85, anchor=tk.CENTER)
        tree.column("detail", width=280, minwidth=180, anchor=tk.W)
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure("unfinished", foreground="#C94C4C")
        tree.tag_configure("pending", foreground="#B97700")
        tree.tag_configure("complete", foreground="#1F9D75")
        tree.tag_configure("current", background="#FFF3D4")

        def refresh_rows() -> None:
            tree.delete(*tree.get_children())
            unfinished = sum(not row["complete"] for row in rows)
            summary_var.set(f"未完成 {unfinished} / 总数 {len(rows)}")
            for row in rows:
                if only_unfinished.get() and row["complete"]:
                    continue
                tags = []
                if row["position"] == self.current_rectangle_index:
                    tags.append("current")
                if row["complete"]:
                    tags.append("complete")
                elif row["status"] == "待检查":
                    tags.append("pending")
                else:
                    tags.append("unfinished")
                group_text = (
                    str(row["group_id"])
                    if row["group_id"] is not None
                    else "—"
                )
                tree.insert(
                    "",
                    tk.END,
                    iid=str(row["position"]),
                    values=(
                        row["serial"],
                        group_text,
                        row["status"],
                        row["detail"],
                    ),
                    tags=tuple(tags),
                )
            current_id = str(self.current_rectangle_index)
            if tree.exists(current_id):
                tree.selection_set(current_id)
                tree.see(current_id)

        def jump_to(position: int) -> None:
            if not 0 <= position < len(rectangles):
                return
            window.destroy()
            self._select_rectangle_position(position)

        def on_table_click(event) -> None:
            row_id = tree.identify_row(event.y)
            if row_id:
                jump_to(int(row_id))

        def on_table_enter(_event=None) -> None:
            selection = tree.selection()
            if selection:
                jump_to(int(selection[0]))

        options = ttk.Frame(window, padding=(14, 0, 14, 12))
        options.pack(fill=tk.X)
        ttk.Checkbutton(
            options,
            text="仅显示未完成框",
            variable=only_unfinished,
            command=refresh_rows,
        ).pack(side=tk.LEFT)
        ttk.Label(
            options,
            text="单击一行即可进入对应序号",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, padx=14)
        ttk.Button(options, text="关闭", command=window.destroy).pack(side=tk.RIGHT)

        refresh_rows()
        tree.bind("<ButtonRelease-1>", on_table_click)
        tree.bind("<Return>", on_table_enter)
        window.bind("<Escape>", lambda _event: window.destroy())
        tree.focus_set()

    def open_track_id_table(self) -> None:
        rows = self._track_id_rows()
        if not rows:
            self.status_var.set("当前任务没有可列出的 Track ID")
            return

        window = tk.Toplevel(self.root)
        window.title("Track ID 标注清单")
        window.geometry("720x560")
        window.minsize(620, 420)
        window.transient(self.root)
        window.configure(background=COLORS["background"])

        header = ttk.Frame(window, padding=(14, 12, 14, 8))
        header.pack(fill=tk.X)
        summary_var = tk.StringVar()
        ttk.Label(header, text="Track ID 标注清单", style="Title.TLabel").pack(
            side=tk.LEFT
        )
        ttk.Label(header, textvariable=summary_var, style="Muted.TLabel").pack(
            side=tk.LEFT, padx=(12, 0)
        )
        only_unfinished = tk.BooleanVar(value=True)

        table_frame = ttk.Frame(window, padding=(14, 0, 14, 8))
        table_frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(
            table_frame,
            columns=("track_id", "completed", "total", "remaining", "status"),
            show="headings",
            selectmode="browse",
        )
        for column, title, width in (
            ("track_id", "Track ID", 150),
            ("completed", "已完成帧", 110),
            ("total", "总帧数", 100),
            ("remaining", "剩余帧", 100),
            ("status", "状态", 130),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, minwidth=80, anchor=tk.CENTER)
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure("unfinished", foreground="#C94C4C")
        tree.tag_configure("pending", foreground="#B97700")
        tree.tag_configure("complete", foreground="#1F9D75")
        tree.tag_configure("current", background="#FFF3D4")
        row_by_iid = {}

        def refresh_rows() -> None:
            tree.delete(*tree.get_children())
            row_by_iid.clear()
            unfinished_ids = sum(row["remaining"] > 0 for row in rows)
            summary_var.set(f"未完成 ID {unfinished_ids} / 总数 {len(rows)}")
            for row_index, row in enumerate(rows):
                if only_unfinished.get() and row["remaining"] == 0:
                    continue
                iid = str(row_index)
                row_by_iid[iid] = row
                if row["remaining"] == 0:
                    status = "已完成"
                    tags = ["complete"]
                elif row["pending"] > 0:
                    status = f"待确认 {row['pending']} 帧"
                    tags = ["pending"]
                else:
                    status = "未完成"
                    tags = ["unfinished"]
                if row["group_id"] == self.track_mode_current_id:
                    tags.append("current")
                tree.insert(
                    "",
                    tk.END,
                    iid=iid,
                    values=(
                        row["group_id"],
                        row["completed"],
                        row["total"],
                        row["remaining"],
                        status,
                    ),
                    tags=tuple(tags),
                )
                if row["group_id"] == self.track_mode_current_id:
                    tree.selection_set(iid)
                    tree.see(iid)

        def jump_to(iid: str) -> None:
            row = row_by_iid.get(iid)
            if row is None:
                return
            window.destroy()
            self._go_to_track_id(row["group_id"], first_unfinished=True)

        def on_table_click(event) -> None:
            iid = tree.identify_row(event.y)
            if iid:
                jump_to(iid)

        def on_table_enter(_event=None) -> None:
            selection = tree.selection()
            if selection:
                jump_to(selection[0])

        options = ttk.Frame(window, padding=(14, 0, 14, 12))
        options.pack(fill=tk.X)
        ttk.Checkbutton(
            options,
            text="仅显示未完成 ID",
            variable=only_unfinished,
            command=refresh_rows,
        ).pack(side=tk.LEFT)
        ttk.Label(
            options,
            text="单击 ID：跳到该 ID 第一个未完成框",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, padx=14)
        ttk.Button(options, text="关闭", command=window.destroy).pack(side=tk.RIGHT)

        refresh_rows()
        tree.bind("<ButtonRelease-1>", on_table_click)
        tree.bind("<Return>", on_table_enter)
        window.bind("<Escape>", lambda _event: window.destroy())
        tree.focus_set()

    def _remember_current_rectangle(self) -> None:
        image_path = self._current_image_path()
        if image_path:
            self.selected_rectangle_by_image[image_path] = self.current_rectangle_index

    def _on_detail_wheel(self, event) -> str:
        if getattr(self, "continuous_annotation_mode", False):
            return self._on_continuous_zoom_wheel(event)
        if self.track_id_mode:
            self._move_within_current_track(-1 if event.delta > 0 else 1)
            return "break"
        if event.delta > 0:
            self.previous_rectangle()
        else:
            self.next_rectangle()
        return "break"

    @staticmethod
    def _control_pressed(event) -> bool:
        return bool(getattr(event, "state", 0) & 0x0004)

    def _cancel_pointer_press(self) -> None:
        press = getattr(self, "pointer_press", None)
        self.pointer_press = None
        if press is None:
            return
        job = press.get("job")
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass

    def _on_pointer_press(self, event, view: str) -> str:
        if getattr(self, "direction_review_mode", False) and view == "overview":
            return "break"
        if (
            getattr(self, "continuous_annotation_mode", False)
            and getattr(self, "continuous_draw_box_mode", False)
            and view == "detail"
            and self.detail_transform is not None
            and self.current_pil_image is not None
        ):
            self._cancel_pointer_press()
            point = self._clamp_point_to_current_image(
                self._canvas_to_image(
                    float(event.x), float(event.y), self.detail_transform
                )
            )
            self.rectangle_creation = {
                "view": "detail",
                "transform": self.detail_transform,
                "start": point,
                "end": point,
                "start_canvas": (float(event.x), float(event.y)),
            }
            self._draw_detail()
            return "break"
        if self._control_pressed(event):
            return self._begin_rectangle_drag(event, view)
        direct_target, direct_mode = self._rectangle_drag_candidate_at(
            float(event.x), float(event.y), view
        )
        if direct_mode and direct_mode != "move":
            self._cancel_pointer_press()
            self._begin_rectangle_drag_at(
                float(event.x), float(event.y), view, handle_only=False
            )
            return "break"
        if (
            view == "overview"
            and not getattr(self, "continuous_annotation_mode", False)
            and getattr(self, "default_track_id", None) is not None
            and not direct_mode
            and self.overview_transform is not None
            and self.current_pil_image is not None
        ):
            self._cancel_pointer_press()
            point = self._canvas_to_image(
                float(event.x), float(event.y), self.overview_transform
            )
            point = self._clamp_point_to_current_image(point)
            self.rectangle_creation = {
                "view": "overview",
                "transform": self.overview_transform,
                "start": point,
                "end": point,
                "start_canvas": (float(event.x), float(event.y)),
            }
            self._draw_overview()
            return "break"
        self._cancel_pointer_press()
        token = object()
        press = {
            "token": token,
            "view": view,
            "start_x": float(event.x),
            "start_y": float(event.y),
            "latest_x": float(event.x),
            "latest_y": float(event.y),
            "image_path": self._current_image_path(),
            "drag_target": direct_target,
            "direct_move": direct_mode == "move",
            "job": None,
        }
        self.pointer_press = press
        press["job"] = self.root.after(
            self.long_press_delay_ms,
            lambda selected_token=token: self._activate_long_press(selected_token),
        )
        return "break"

    def _activate_long_press(self, token) -> None:
        press = self.pointer_press
        if press is None or press["token"] is not token:
            return
        self.pointer_press = None
        if press["image_path"] != self._current_image_path():
            return
        started = self._begin_rectangle_drag_at(
            press["start_x"],
            press["start_y"],
            press["view"],
            handle_only=True,
        )
        if started:
            self._continue_rectangle_drag_at(
                press["latest_x"],
                press["latest_y"],
                press["view"],
            )

    def _on_pointer_motion(self, event, view: str):
        creation = getattr(self, "rectangle_creation", None)
        if creation is not None and creation.get("view") == view:
            creation["end"] = self._clamp_point_to_current_image(
                self._canvas_to_image(
                    float(event.x), float(event.y), creation["transform"]
                )
            )
            if view == "detail":
                self._draw_detail()
            else:
                self._draw_overview()
            return "break"
        if self.rectangle_drag is not None:
            return self._continue_rectangle_drag(event, view)
        press = self.pointer_press
        if press is None or press["view"] != view:
            return None
        press["latest_x"] = float(event.x)
        press["latest_y"] = float(event.y)
        movement = math.hypot(
            float(event.x) - press["start_x"],
            float(event.y) - press["start_y"],
        )
        if movement > QUICK_CLICK_MAX_MOVEMENT_PX and press.get("direct_move"):
            self._cancel_pointer_press()
            started = self._begin_rectangle_drag_at(
                press["start_x"],
                press["start_y"],
                view,
                handle_only=False,
            )
            if started:
                return self._continue_rectangle_drag_at(
                    float(event.x), float(event.y), view
                )
        return "break"

    def _on_pointer_release(self, event, view: str):
        creation = getattr(self, "rectangle_creation", None)
        if creation is not None and creation.get("view") == view:
            return self._finish_rectangle_creation(event, view)
        if self.rectangle_drag is not None:
            return self._end_rectangle_drag(event, view)
        press = self.pointer_press
        if press is None or press["view"] != view:
            return None
        movement = math.hypot(
            float(event.x) - press["start_x"],
            float(event.y) - press["start_y"],
        )
        self._cancel_pointer_press()
        if movement > QUICK_CLICK_MAX_MOVEMENT_PX:
            self.status_var.set("拖动起点不在检测框内，未执行操作")
            return "break"
        if view == "detail":
            self._on_detail_click(event)
        else:
            self._on_overview_click(event)
        return "break"

    def _clamp_point_to_current_image(
        self, point: Tuple[float, float]
    ) -> Tuple[float, float]:
        image = self.current_pil_image
        if image is None:
            return point
        return (
            min(float(image.width), max(0.0, float(point[0]))),
            min(float(image.height), max(0.0, float(point[1]))),
        )

    def _current_frame_has_track_id(self, track_id: int) -> bool:
        return any(
            self._normalize_track_id(rectangle.get("group_id")) == track_id
            for rectangle in self._current_rectangles()
        )

    def _finish_rectangle_creation(self, event, view: str = "overview") -> str:
        creation = self.rectangle_creation
        self.rectangle_creation = None
        movement = math.hypot(
            float(event.x) - creation["start_canvas"][0],
            float(event.y) - creation["start_canvas"][1],
        )
        if movement <= QUICK_CLICK_MAX_MOVEMENT_PX:
            if getattr(self, "continuous_annotation_mode", False):
                self.status_var.set("请在左侧放大图中拖动，而不是单击")
                self._draw_detail()
            else:
                self._on_overview_click(event)
                self._draw_overview()
            return "break"
        transform = creation.get("transform")
        if transform is None:
            return "break"
        end = self._clamp_point_to_current_image(
            self._canvas_to_image(
                float(event.x), float(event.y), transform
            )
        )
        x1, x2 = sorted((creation["start"][0], end[0]))
        y1, y2 = sorted((creation["start"][1], end[1]))
        if x2 - x1 < 2.0 or y2 - y1 < 2.0:
            self.status_var.set("检测框太小，请重新拖动")
            if view == "detail":
                self._draw_detail()
            else:
                self._draw_overview()
            return "break"
        continuous_mode = bool(
            getattr(self, "continuous_annotation_mode", False)
        )
        track_id = (
            getattr(self, "continuous_active_track_id", None)
            if continuous_mode
            else getattr(self, "default_track_id", None)
        )
        document = self._current_document()
        if track_id is None or document is None:
            self._refresh_all()
            return "break"
        if not continuous_mode and self._current_frame_has_track_id(track_id):
            self.status_var.set(
                f"当前图片已存在 Track ID {track_id}，未新增重复 ID"
            )
            self._draw_overview()
            return "break"
        rectangles = self._current_rectangles()
        label = (
            rectangles[self.current_rectangle_index].get("label")
            if 0 <= self.current_rectangle_index < len(rectangles)
            else "bee"
        )
        self._push_undo()
        position = self._continuous_rectangle_position(document) if continuous_mode else -1
        replacing = position >= 0
        if replacing:
            set_rectangle_bounds(document, position, (x1, y1, x2, y2))
        else:
            position = add_rectangle(
                document,
                (x1, y1, x2, y2),
                track_id,
                label=label or "bee",
            )
        self.current_rectangle_index = position
        self._remember_current_rectangle()
        if continuous_mode:
            self.continuous_draw_box_mode = False
            self.continuous_focus_point = ((x1 + x2) / 2, (y1 + y2) / 2)
            if self.labels:
                self.active_label.set(self.labels[0])
        self.overview_static_key = None
        self.detail_cache_key = None
        self._mark_dirty()
        self._schedule_autosave()
        operation = "更新" if replacing else "新增"
        self.status_var.set(
            f"已{operation} Track ID {track_id} 的检测框；"
            "已自动切回关键点标注"
        )
        self._refresh_all()
        return "break"

    def _rectangle_drag_target(
        self,
        point: Tuple[float, float],
        view: str,
        rectangles: List[Dict],
        tolerance: float,
        handle_only: bool,
    ) -> Tuple[int, str]:
        current = self.current_rectangle_index
        if handle_only:
            if 0 <= current < len(rectangles):
                mode = rectangle_handle_mode(
                    point,
                    rectangles[current]["rect"],
                    tolerance,
                )
                if mode:
                    return current, mode
            handle_candidates = []
            for position, rectangle in enumerate(rectangles):
                if position == current:
                    continue
                candidate_mode = rectangle_handle_mode(
                    point,
                    rectangle["rect"],
                    tolerance,
                )
                if candidate_mode:
                    handle_candidates.append(
                        (rect_area(rectangle["rect"]), position, candidate_mode)
                    )
            if handle_candidates:
                _area, selected, mode = min(handle_candidates)
                return selected, mode
            selected = smallest_containing_rectangle(
                point,
                (
                    (position, rectangle["rect"])
                    for position, rectangle in enumerate(rectangles)
                ),
            )
            return (selected, "move") if selected >= 0 else (-1, "")

        if 0 <= current < len(rectangles):
            current_mode = rectangle_drag_mode(
                point,
                rectangles[current]["rect"],
                tolerance,
            )
            if current_mode:
                return current, current_mode
        candidates = []
        for position, rectangle in enumerate(rectangles):
            if position == current:
                continue
            candidate_mode = rectangle_drag_mode(
                point, rectangle["rect"], tolerance
            )
            if candidate_mode:
                candidates.append(
                    (rect_area(rectangle["rect"]), position, candidate_mode)
                )
        if candidates:
            _area, selected, mode = min(candidates)
            return selected, mode
        return -1, ""

    def _rectangle_drag_candidate_at(
        self,
        canvas_x: float,
        canvas_y: float,
        view: str,
    ) -> Tuple[int, str]:
        """只读判断鼠标是否命中可直接移动或缩放的检测框。"""
        transform = (
            self.detail_transform if view == "detail" else self.overview_transform
        )
        rectangles = self._current_rectangles()
        if transform is None or not rectangles or not self.show_other_boxes.get():
            return -1, ""
        point = self._canvas_to_image(canvas_x, canvas_y, transform)
        tolerance = 9.0 / max(transform[0], 1e-6)
        return self._rectangle_drag_target(
            point,
            view,
            rectangles,
            tolerance,
            handle_only=False,
        )

    def _begin_rectangle_drag(self, event, view: str) -> str:
        if getattr(self, "direction_review_mode", False) and view == "overview":
            return "break"
        self._cancel_pointer_press()
        self._begin_rectangle_drag_at(event.x, event.y, view, handle_only=False)
        return "break"

    def _begin_rectangle_drag_at(
        self,
        canvas_x: float,
        canvas_y: float,
        view: str,
        handle_only: bool,
    ) -> bool:
        transform = (
            self.detail_transform if view == "detail" else self.overview_transform
        )
        document = self._current_document()
        rectangles = self._current_rectangles()
        image_path = self._current_image_path()
        image = self.current_pil_image
        if (
            transform is None
            or document is None
            or image_path is None
            or image is None
            or not rectangles
        ):
            return False
        if not self.show_other_boxes.get():
            self.status_var.set("检测框当前已隐藏；按 H 或勾选“显示全部框”后再调框")
            return False

        point = self._canvas_to_image(canvas_x, canvas_y, transform)
        tolerance = 9.0 / max(transform[0], 1e-6)
        selected, mode = self._rectangle_drag_target(
            point,
            view,
            rectangles,
            tolerance,
            handle_only,
        )

        if selected < 0 or not mode:
            if handle_only:
                self.status_var.set("请在检测框内部或八个白色控制点上长按")
            else:
                self.status_var.set("Ctrl+左键请从检测框内部、边缘或四角开始拖动")
            return False

        if selected != self.current_rectangle_index:
            self._auto_confirm_viewed_rectangle()
        self._push_undo()
        original_rect = tuple(rectangles[selected]["rect"])
        original_keypoints = self._snapshot_drag_keypoints(
            document,
            selected,
            original_rect,
        )
        self.rectangle_drag = {
            "view": view,
            "image_path": image_path,
            "rectangle_position": selected,
            "activate_on_finish": selected != self.current_rectangle_index,
            "original_rect": original_rect,
            "original_keypoints": original_keypoints,
            "start": point,
            "transform": tuple(transform),
            "image_size": (float(image.width), float(image.height)),
            "mode": mode,
            "changed": False,
            "last_redraw": 0.0,
        }
        operation = "移动" if mode == "move" else "缩放"
        self.status_var.set(f"正在{operation}检测框；松开鼠标后自动保存")
        self._refresh_all()
        return True

    @staticmethod
    def _snapshot_drag_keypoints(
        document: Dict,
        rectangle_position: int,
        original_rect: Tuple[float, float, float, float],
    ) -> List[Tuple[int, Tuple[float, float]]]:
        snapshot = []
        for record in keypoints_for_rectangle(document, rectangle_position):
            try:
                relative = relative_position(record["point"], original_rect)
            except ValueError:
                continue
            snapshot.append((record["shape_index"], relative))
        return snapshot

    @staticmethod
    def _apply_drag_keypoints(
        document: Dict,
        snapshot: List[Tuple[int, Tuple[float, float]]],
        bounds: Tuple[float, float, float, float],
    ) -> int:
        changed = 0
        shapes = document.get("shapes", [])
        for shape_index, relative in snapshot:
            if not 0 <= shape_index < len(shapes):
                continue
            shape = shapes[shape_index]
            if shape.get("shape_type") != "point":
                continue
            point = point_from_relative(relative, bounds)
            shape["points"] = [[float(point[0]), float(point[1])]]
            changed += 1
        return changed

    def _continue_rectangle_drag(self, event, view: str):
        return self._continue_rectangle_drag_at(event.x, event.y, view)

    def _continue_rectangle_drag_at(
        self,
        canvas_x: float,
        canvas_y: float,
        view: str,
    ):
        drag = self.rectangle_drag
        if drag is None or drag["view"] != view:
            return None
        if self._current_image_path() != drag["image_path"]:
            return "break"
        point = self._canvas_to_image(
            canvas_x,
            canvas_y,
            drag["transform"],
        )
        bounds = adjust_rectangle_bounds(
            drag["original_rect"],
            drag["start"],
            point,
            drag["mode"],
            drag["image_size"],
            minimum_size=4.0,
        )
        document = self._current_document()
        if document is not None:
            set_rectangle_bounds(
                document,
                drag["rectangle_position"],
                bounds,
            )
            if drag["mode"] == "move":
                self._apply_drag_keypoints(
                    document,
                    drag["original_keypoints"],
                    bounds,
                )
            drag["changed"] = any(
                abs(first - second) > 1e-6
                for first, second in zip(bounds, drag["original_rect"])
            )

        now = time.perf_counter()
        if now - drag["last_redraw"] >= 1.0 / 30.0:
            drag["last_redraw"] = now
            self._refresh_all()
        return "break"

    def _finish_rectangle_drag(self) -> None:
        drag = self.rectangle_drag
        if drag is None:
            return
        self.rectangle_drag = None
        if drag.get("activate_on_finish"):
            self.current_rectangle_index = drag["rectangle_position"]
            self._remember_current_rectangle()
        image_path = drag["image_path"]
        if drag["changed"]:
            self.dirty_images.add(image_path)
            self.overview_dirty_positions.setdefault(image_path, set()).add(
                drag["rectangle_position"]
            )
            self._schedule_autosave()
            operation = "移动" if drag["mode"] == "move" else "缩放"
            keypoint_result = (
                "关键点已同步平移"
                if drag["mode"] == "move"
                else "关键点保持原图位置不变"
            )
            self.status_var.set(
                f"已{operation}检测框，{keypoint_result}（正在自动保存）"
            )
        else:
            self._discard_last_undo(image_path)
            self.status_var.set("检测框没有发生变化")
        self._refresh_all()

    def _end_rectangle_drag(self, event, view: str):
        if self.rectangle_drag is None or self.rectangle_drag["view"] != view:
            return None
        self._continue_rectangle_drag(event, view)
        self._finish_rectangle_drag()
        return "break"

    def _on_detail_click(self, event):
        if self._control_pressed(event):
            return "break"
        if self.detail_transform is None:
            return
        document = self._current_document()
        rectangles = self._current_rectangles()
        if document is None or not rectangles:
            return
        point = self._canvas_to_image(event.x, event.y, self.detail_transform)
        selected = self.current_rectangle_index
        if self.show_other_boxes.get():
            clicked = smallest_containing_rectangle(
                point,
                (
                    (position, rectangle["rect"])
                    for position, rectangle in enumerate(rectangles)
                ),
            )
            if clicked >= 0:
                selected = clicked
        if not 0 <= selected < len(rectangles):
            self.status_var.set("请在检测框内部点击")
            return
        selected_rect = rectangles[selected]["rect"]
        if not point_in_rect(point, selected_rect):
            self.status_var.set("请在黄色检测框内部点击")
            return

        if selected != self.current_rectangle_index:
            self._auto_confirm_viewed_rectangle()
            self.current_rectangle_index = selected
            self._remember_current_rectangle()

        label = self.active_label.get().strip()
        if not label:
            self.status_var.set("请先选择关键点标签")
            return

        self._push_undo()
        add_or_replace_point(
            document, selected, label, point, replace_same_label=True
        )
        message = f"已标注 {label}"
        if (
            self.symmetry_enabled.get()
            and label == self.symmetry_source_label.get()
            and self.symmetry_target_label.get()
        ):
            target_label = self.symmetry_target_label.get()
            ratio = self.symmetry_ratio.get()
            target_point = symmetric_point(
                point,
                selected_rect,
                ratio=ratio,
                clamp_to_rect=True,
            )
            add_or_replace_point(
                document,
                selected,
                target_label,
                target_point,
                replace_same_label=True,
            )
            message += (
                f"，并按 {ratio * 100:.0f}% 比例生成对称点 {target_label}"
            )

        self._mark_dirty()
        self._schedule_autosave()
        self.status_var.set(message + "（正在自动保存）")
        self._refresh_all()

    def _on_detail_middle_press(self, event) -> str:
        if self.detail_transform is None or self.current_pil_image is None:
            return "break"
        image_path = self._current_image_path()
        if image_path is None:
            return "break"
        canvas_width = max(float(self.detail_canvas.winfo_width()), 1.0)
        canvas_height = max(float(self.detail_canvas.winfo_height()), 1.0)
        focus = self._canvas_to_image(
            canvas_width / 2.0,
            canvas_height / 2.0,
            self.detail_transform,
        )
        self.middle_pan = {
            "image_path": image_path,
            "rectangle_position": self.current_rectangle_index,
            "start": (float(event.x), float(event.y)),
            "focus": focus,
            "scale": max(float(self.detail_transform[0]), 1e-6),
            "moved": False,
        }
        self.detail_canvas.configure(cursor="fleur")
        return "break"

    def _on_detail_middle_motion(self, event) -> str:
        pan = getattr(self, "middle_pan", None)
        if pan is None or pan["image_path"] != self._current_image_path():
            return "break"
        dx = float(event.x) - pan["start"][0]
        dy = float(event.y) - pan["start"][1]
        if math.hypot(dx, dy) <= QUICK_CLICK_MAX_MOVEMENT_PX:
            return "break"
        pan["moved"] = True
        focus = self._clamp_point_to_current_image(
            (
                pan["focus"][0] - dx / pan["scale"],
                pan["focus"][1] - dy / pan["scale"],
            )
        )
        if getattr(self, "continuous_annotation_mode", False):
            self.continuous_focus_point = focus
        else:
            key = (pan["image_path"], pan["rectangle_position"])
            self.detail_view_centers[key] = focus
        self.detail_cache_key = None
        self._draw_detail()
        self.status_var.set("正在移动左侧画面")
        return "break"

    def _on_detail_middle_release(self, event) -> str:
        pan = getattr(self, "middle_pan", None)
        if pan is None:
            return "break"
        self._on_detail_middle_motion(event)
        moved = bool(pan.get("moved"))
        self.middle_pan = None
        self.detail_canvas.configure(cursor="crosshair")
        if moved:
            self.status_var.set("左侧画面位置已调整")
        else:
            self._on_detail_middle_click(event)
        return "break"

    def _on_detail_middle_click(self, event) -> None:
        if self.detail_transform is None:
            return
        document = self._current_document()
        if document is None or self.current_rectangle_index < 0:
            return
        point = self._canvas_to_image(event.x, event.y, self.detail_transform)
        scale = self.detail_transform[0]
        maximum_image_distance = 20.0 / max(scale, 1e-6)
        self._push_undo()
        deleted_label = delete_nearest_point(
            document,
            self.current_rectangle_index,
            point,
            max_distance=maximum_image_distance,
        )
        if deleted_label is None:
            self._discard_last_undo()
            self.status_var.set("鼠标附近没有可删除的关键点")
            return
        self._mark_dirty()
        self._schedule_autosave()
        self.status_var.set(f"已删除附近的 {deleted_label} 点（正在自动保存）")
        self._refresh_all()

    def _on_detail_right_click(self, event) -> str:
        if self.detail_transform is None:
            return "break"
        document = self._current_document()
        rectangles = self._current_rectangles()
        if document is None or not rectangles:
            return "break"
        point = self._canvas_to_image(event.x, event.y, self.detail_transform)

        if not self._control_pressed(event):
            selected = self.current_rectangle_index
            if self.show_other_boxes.get():
                selected = smallest_containing_rectangle(
                    point,
                    (
                        (position, rectangle["rect"])
                        for position, rectangle in enumerate(rectangles)
                    ),
                )
            if selected >= 0:
                self._apply_detail_right_click_point_action(point, selected)
            else:
                self.status_var.set("请在检测框内部右键标注 tail")
            return "break"

        if not self.show_other_boxes.get():
            self.status_var.set("检测框当前已隐藏；按 H 显示后再用 Ctrl+右键改 ID")
            return "break"
        selected = -1
        selected = smallest_containing_rectangle(
            point,
            (
                (position, rectangle["rect"])
                for position, rectangle in enumerate(rectangles)
            ),
        )

        if selected < 0:
            self.status_var.set("Ctrl+右键没有点中检测框")
            return "break"

        self._show_detail_rectangle_context_menu(event, point, selected)
        return "break"

    def _show_detail_rectangle_context_menu(
        self,
        event,
        point: Tuple[float, float],
        rectangle_position: int,
    ) -> None:
        if rectangle_position != self.current_rectangle_index:
            self._auto_confirm_viewed_rectangle()
        self.current_rectangle_index = rectangle_position
        self._remember_current_rectangle()
        self._refresh_all()

        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(
            label="修改 Track ID…",
            command=lambda: self._change_detail_rectangle_id(rectangle_position),
        )
        menu.add_separator()
        menu.add_command(
            label="删除该框及关联关键点…",
            command=lambda: self._delete_rectangle_with_keypoints(
                rectangle_position
            ),
        )
        menu.add_command(
            label="删除当前帧及之后所有相同 ID 的框…",
            command=lambda: self._delete_current_and_following_track_boxes(
                rectangle_position
            ),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _delete_rectangle_with_keypoints(self, rectangle_position: int) -> None:
        document = self._current_document()
        rectangles = self._current_rectangles()
        if document is None or not 0 <= rectangle_position < len(rectangles):
            return

        rectangle = rectangles[rectangle_position]
        point_count = len(keypoints_for_rectangle(document, rectangle_position))
        track_id = rectangle.get("group_id")
        track_text = "无 ID" if track_id is None else f"ID {track_id}"
        if not messagebox.askyesno(
            "确认删除检测框",
            f"确定删除该检测框（{track_text}）及其关联的 {point_count} 个关键点吗？\n\n可按 Ctrl+Z 撤销。",
        ):
            return

        self.current_rectangle_index = rectangle_position
        self._remember_current_rectangle()
        self._push_undo()
        deleted_count = delete_rectangle_with_keypoints(
            document, rectangle_position
        )
        if deleted_count == 0:
            self._discard_last_undo()
            self.status_var.set("检测框已不存在")
            return

        remaining = self._current_rectangles()
        self.current_rectangle_index = (
            min(rectangle_position, len(remaining) - 1) if remaining else -1
        )
        self._remember_current_rectangle()
        self.overview_static_key = None
        self._mark_dirty()
        self._schedule_autosave()
        self.status_var.set(
            f"已删除检测框及 {point_count} 个关联关键点（正在自动保存）"
        )
        self._refresh_all()

    def _delete_current_and_following_track_boxes(
        self, rectangle_position: int
    ) -> None:
        document = self._current_document()
        rectangles = self._current_rectangles()
        if document is None or not 0 <= rectangle_position < len(rectangles):
            return
        track_id = self._normalize_track_id(
            rectangles[rectangle_position].get("group_id")
        )
        if track_id is None:
            self.status_var.set("该检测框没有有效 ID，不能批量删除")
            return

        targets = self._track_id_change_targets(
            track_id, rectangle_position, "after"
        )
        if not targets:
            self.status_var.set("没有找到需要删除的同 ID 检测框")
            return
        point_count = sum(
            len(keypoints_for_rectangle(self._get_document(image_path), position))
            for _image_index, image_path, position in targets
        )
        frame_count = len({image_path for _index, image_path, _position in targets})
        if not messagebox.askyesno(
            "确认批量删除检测框",
            f"确定删除当前帧及之后的所有 ID {track_id} 检测框吗？\n\n"
            f"共 {frame_count} 帧、{len(targets)} 个框、{point_count} 个关键点。",
            icon="warning",
            parent=self.root,
        ):
            return

        targets_by_image: Dict[Path, List[int]] = {}
        for _image_index, image_path, position in targets:
            targets_by_image.setdefault(image_path, []).append(position)
        for image_path, positions in targets_by_image.items():
            self._push_undo(image_path)
            target_document = self._get_document(image_path)
            for position in sorted(positions, reverse=True):
                delete_rectangle_with_keypoints(target_document, position)
            self.dirty_images.add(image_path)

        remaining = self._current_rectangles()
        self.current_rectangle_index = (
            min(rectangle_position, len(remaining) - 1) if remaining else -1
        )
        self._remember_current_rectangle()
        self.overview_static_key = None
        self.detail_cache_key = None
        self._schedule_autosave()
        self.status_var.set(
            f"已删除 ID {track_id} 在当前及之后的 {len(targets)} 个框"
            f"和 {point_count} 个关键点（正在自动保存）"
        )
        self._refresh_all()

    def _apply_detail_right_click_point_action(
        self,
        point: Tuple[float, float],
        rectangle_position: int,
    ) -> None:
        document = self._current_document()
        rectangles = self._current_rectangles()
        if document is None or not 0 <= rectangle_position < len(rectangles):
            return
        if rectangle_position != self.current_rectangle_index:
            self._auto_confirm_viewed_rectangle()
        self.current_rectangle_index = rectangle_position
        self._remember_current_rectangle()
        selected_rect = rectangles[rectangle_position]["rect"]
        if not point_in_rect(point, selected_rect):
            self.status_var.set("请在检测框内部右键标注 tail")
            return
        target_label = self.symmetry_target_label.get().strip() or "tail"
        self._push_undo()
        add_or_replace_point(
            document,
            rectangle_position,
            target_label,
            point,
            replace_same_label=True,
        )
        self._mark_dirty()
        self._schedule_autosave()
        self.status_var.set(
            f"已用右键标注 {target_label}（正在自动保存）"
        )
        self._refresh_all()

    def _track_id_change_targets(
        self,
        old_track_id: int,
        rectangle_position: int,
        scope: str,
    ) -> List[Tuple[int, Path, int]]:
        """返回批量改 ID 的（帧序号、图片路径、框序号）列表。"""
        if scope == "current":
            image_path = self._current_image_path()
            return (
                [(self.current_image_index, image_path, rectangle_position)]
                if image_path is not None
                else []
            )

        if scope == "before":
            frame_indices = range(0, self.current_image_index + 1)
        elif scope == "after":
            frame_indices = range(self.current_image_index, len(self.images))
        else:
            raise ValueError(f"未知修改范围：{scope}")

        targets = []
        for image_index in frame_indices:
            image_path = self.images[image_index]
            document = self._get_document(image_path)
            for position, rectangle in enumerate(rectangle_records(document)):
                if self._normalize_track_id(rectangle.get("group_id")) == old_track_id:
                    targets.append((image_index, image_path, position))
        return targets

    def _change_detail_rectangle_id(self, rectangle_position: int) -> None:
        document = self._current_document()
        rectangles = self._current_rectangles()
        if document is None or not 0 <= rectangle_position < len(rectangles):
            return
        selected = rectangles[rectangle_position]
        old_track_id = selected.get("group_id")
        initial_id = self._normalize_track_id(old_track_id)
        if initial_id is None or initial_id <= 0:
            initial_id = self._next_new_track_id(self._task_track_ids())
        dialog = TrackIdChangeDialog(
            "手动修改 Track ID",
            f"当前帧 ID 为 {old_track_id}，请输入修改后的 ID（按 G 确认）：",
            initialvalue=initial_id,
            minvalue=1,
            parent=self.root,
        )
        new_track_id = dialog.result
        if new_track_id is None:
            self.status_var.set("已取消修改 ID")
            return
        scope = getattr(dialog, "selected_scope", "current")
        if self._normalize_track_id(old_track_id) == new_track_id:
            self.status_var.set("输入的 ID 没有变化")
            return

        normalized_old_id = self._normalize_track_id(old_track_id)
        if normalized_old_id is None:
            scope = "current"
        targets = self._track_id_change_targets(
            normalized_old_id,
            rectangle_position,
            scope,
        )
        target_positions = {
            (image_path, position) for _index, image_path, position in targets
        }
        duplicate_frames = set()
        for image_index, image_path, _position in targets:
            target_document = self._get_document(image_path)
            if any(
                (image_path, position) not in target_positions
                and self._normalize_track_id(rectangle.get("group_id")) == new_track_id
                for position, rectangle in enumerate(rectangle_records(target_document))
            ):
                duplicate_frames.add(image_index)
        if duplicate_frames:
            confirmed = messagebox.askyesno(
                "确认同帧重复 ID",
                f"修改范围内有 {len(duplicate_frames)} 帧已经存在 ID {new_track_id}。\n\n"
                f"继续后，这些帧会同时存在多个 ID {new_track_id} 检测框。"
                "是否仍要修改？",
                icon="warning",
                parent=self.root,
            )
            if not confirmed:
                self.status_var.set("已取消修改 ID")
                return

        if not targets:
            self.status_var.set("输入的 ID 没有变化")
            return

        self.current_rectangle_index = rectangle_position
        self._remember_current_rectangle()
        affected_images = []
        shape_changes = 0
        for _image_index, image_path, position in targets:
            if image_path not in affected_images:
                affected_images.append(image_path)
                self._push_undo(image_path)
            target_document = self._get_document(image_path)
            shape_changes += change_rectangle_group_id(
                target_document,
                position,
                int(new_track_id),
            )
            self.dirty_images.add(image_path)
            self.overview_dirty_positions.setdefault(image_path, set()).add(position)

        self._schedule_autosave()
        self._refresh_default_track_id_controls()
        scope_text = {
            "current": "当前帧",
            "before": "当前帧及此前",
            "after": "当前帧及此后",
        }[scope]
        self.status_var.set(
            f"已在{scope_text}将 {len(targets)} 个 ID {old_track_id} 框改为 "
            f"{new_track_id}；同步修改 {max(0, shape_changes - len(targets))} 个 "
            "head/tail（正在自动保存）"
        )
        self._refresh_all()

    def _on_overview_click(self, event):
        if self._control_pressed(event):
            return "break"
        if self.overview_transform is None:
            return
        point = self._clamp_point_to_current_image(
            self._canvas_to_image(event.x, event.y, self.overview_transform)
        )
        rectangles = self._current_rectangles()
        selected = smallest_containing_rectangle(
            point,
            ((position, rectangle["rect"]) for position, rectangle in enumerate(rectangles)),
        )
        if getattr(self, "continuous_annotation_mode", False):
            self.continuous_focus_point = point
            active_position = self._continuous_rectangle_position()
            if selected >= 0 and selected == active_position:
                self.current_rectangle_index = selected
                self._remember_current_rectangle()
            elif active_position >= 0:
                self.current_rectangle_index = active_position
                self._remember_current_rectangle()
            else:
                self.current_rectangle_index = -1
            self.detail_cache_key = None
            self.status_var.set(
                "左侧已放大右侧点击区域；按 R 可画框，滚轮可缩放"
            )
            self._refresh_all()
            return "break"
        if selected >= 0:
            self._select_rectangle_position(selected)
        else:
            self.status_var.set("该位置没有检测框")

    def delete_current_label_point(self) -> None:
        document = self._current_document()
        if document is None or self.current_rectangle_index < 0:
            return
        self._push_undo()
        count = delete_points(
            document, self.current_rectangle_index, self.active_label.get()
        )
        if count == 0:
            self._discard_last_undo()
            self.status_var.set("当前框没有该标签关键点")
            return
        self._mark_dirty()
        self._schedule_autosave()
        self.status_var.set(
            f"已删除 {count} 个 {self.active_label.get()} 点（正在自动保存）"
        )
        self._refresh_all()

    def delete_all_current_points(self) -> None:
        document = self._current_document()
        if document is None or self.current_rectangle_index < 0:
            return
        count = len(keypoints_for_rectangle(document, self.current_rectangle_index))
        if count == 0:
            self.status_var.set("当前框没有关键点")
            return
        if not messagebox.askyesno("确认删除", f"删除当前框的全部 {count} 个关键点？"):
            return
        self._push_undo()
        delete_points(document, self.current_rectangle_index)
        self._mark_dirty()
        self._schedule_autosave()
        self.status_var.set(f"已删除当前框的 {count} 个关键点（正在自动保存）")
        self._refresh_all()

    # ----------------------------- 绘制 -----------------------------

    def _schedule_redraw(self, _event=None) -> None:
        if self.redraw_job is not None:
            self.root.after_cancel(self.redraw_job)
        self.redraw_job = self.root.after(60, self._refresh_all)

    def _refresh_all(self) -> None:
        self.redraw_job = None
        self._refresh_default_track_id_controls()
        self.refresh_track_stats = (
            self._track_completion_stats(self.track_mode_current_id)
            if self.track_id_mode and self.track_mode_current_id is not None
            else None
        )
        self._refresh_direction_review_queue()
        self._draw_detail()
        self._draw_overview()
        self._update_information()
        self._update_frame_completion_state(notify=True)

    def _draw_overview(self) -> None:
        if getattr(self, "direction_review_mode", False):
            self._draw_direction_review_comparison()
            return
        canvas = self.overview_canvas
        image = self.current_pil_image
        if image is None:
            canvas.delete("all")
            self.overview_static_key = None
            width = max(canvas.winfo_width(), 200)
            height = max(canvas.winfo_height(), 200)
            if self.assistant_empty_photo is not None:
                canvas.create_image(
                    width / 2,
                    height / 2 - 35,
                    image=self.assistant_empty_photo,
                    anchor=tk.CENTER,
                )
            canvas.create_text(
                width / 2,
                height / 2 + 85,
                text="打开任务文件夹，开始关键点复审",
                fill="white",
                font=("Microsoft YaHei UI", 13, "bold"),
            )
            canvas.create_text(
                width / 2,
                height / 2 + 112,
                text="支持 X-AnyLabeling / LabelMe JSON",
                fill="#9AA6BB",
                font=("Microsoft YaHei UI", 9),
            )
            self.overview_transform = None
            return

        canvas_width = max(canvas.winfo_width(), 100)
        canvas_height = max(canvas.winfo_height(), 100)
        scale = min(canvas_width / image.width, canvas_height / image.height) * 0.98
        display_width = max(1, int(image.width * scale))
        display_height = max(1, int(image.height * scale))
        offset_x = (canvas_width - display_width) / 2
        offset_y = (canvas_height - display_height) / 2
        cache_key = (id(image), canvas_width, canvas_height, display_width, display_height)
        if cache_key != self.overview_cache_key:
            resized = image.resize(
                (display_width, display_height), Image.Resampling.LANCZOS
            )
            self.overview_photo = ImageTk.PhotoImage(resized)
            self.overview_cache_key = cache_key
        self.overview_transform = (scale, offset_x, offset_y)

        rectangles = self._current_rectangles()
        document = self._current_document()
        points_by_rectangle = (
            keypoints_by_rectangle(document, rectangles) if document else {}
        )
        show_boxes = self.show_other_boxes.get()
        static_key = (
            cache_key,
            len(rectangles),
            show_boxes,
            self.show_label_names.get(),
            self.show_track_ids.get(),
            self.show_box_class_labels.get(),
        )
        image_path = self._current_image_path()
        full_redraw = static_key != self.overview_static_key
        if full_redraw:
            canvas.delete("all")
            canvas.create_image(
                offset_x,
                offset_y,
                image=self.overview_photo,
                anchor=tk.NW,
                tags=("overview_static",),
            )
            for position, rectangle in enumerate(rectangles):
                self._draw_overview_position(
                    position,
                    rectangle,
                    points_by_rectangle.get(position, []),
                    show_box=show_boxes,
                )
            self.overview_static_key = static_key
            if image_path is not None:
                self.overview_dirty_positions.pop(image_path, None)
        elif image_path is not None:
            dirty_positions = self.overview_dirty_positions.pop(image_path, set())
            for position in dirty_positions:
                canvas.delete(f"overview_position_{position}")
                if 0 <= position < len(rectangles):
                    self._draw_overview_position(
                        position,
                        rectangles[position],
                        points_by_rectangle.get(position, []),
                        show_box=show_boxes,
                    )

        canvas.delete("overview_dynamic")
        selected_outline = "#FFD54F"
        if (
            show_boxes
            and rectangles
            and 0 <= self.current_rectangle_index < len(rectangles)
        ):
            selected_rectangle = rectangles[self.current_rectangle_index]
            state = self._keypoint_state(
                points_by_rectangle.get(self.current_rectangle_index, []),
                selected_rectangle,
            )
            selected_outline = {
                "pending": "#FFC107",
                "confirmed": "#69F0AE",
                "partial": "#FF8A65",
            }.get(state, "#FFD54F")
            x1, y1, x2, y2 = selected_rectangle["rect"]
            cx1, cy1 = self._image_to_canvas((x1, y1), self.overview_transform)
            cx2, cy2 = self._image_to_canvas((x2, y2), self.overview_transform)
            canvas.create_rectangle(
                cx1,
                cy1,
                cx2,
                cy2,
                outline=selected_outline,
                width=3,
                tags=("overview_dynamic",),
            )
            self._draw_rectangle_handles(
                canvas,
                selected_rectangle["rect"],
                self.overview_transform,
                selected_outline,
                size=4,
                tags=("overview_dynamic",),
            )

        creation = getattr(self, "rectangle_creation", None)
        if creation is not None and creation.get("view") == "overview":
            cx1, cy1 = self._image_to_canvas(
                creation["start"], self.overview_transform
            )
            cx2, cy2 = self._image_to_canvas(
                creation["end"], self.overview_transform
            )
            canvas.create_rectangle(
                cx1,
                cy1,
                cx2,
                cy2,
                outline="#00E5FF",
                width=3,
                dash=(7, 4),
                tags=("overview_dynamic",),
            )

        if document:
            card_x = max(14.0, offset_x + 14.0)
            card_y = max(12.0, offset_y - 106.0)
            if self.track_id_mode and self.track_mode_current_id is not None:
                stats = self._current_track_stats_for_display()
                occurrence_position = self._current_track_occurrence_position(
                    stats["occurrences"]
                )
                current_complete = False
                if 0 <= self.current_rectangle_index < len(rectangles):
                    current_complete = self._rectangle_table_status(
                        rectangles[self.current_rectangle_index],
                        points_by_rectangle.get(self.current_rectangle_index, []),
                    )[2]
                card_width = 320
                card_outline = "#69F0AE" if current_complete else "#FFC107"
                first_line = f"当前 Track ID：{self.track_mode_current_id}"
                second_line = (
                    f"轨迹进度：第 "
                    f"{occurrence_position + 1 if occurrence_position >= 0 else 0} / "
                    f"{stats['total']} 张"
                )
                third_line = (
                    f"已确认：{stats['completed']} / {stats['total']}"
                    f"｜剩余：{stats['remaining']} 张"
                )
                second_color = "#FFFFFF"
                third_color = "#69F0AE" if stats["remaining"] == 0 else "#FFC107"
            else:
                progress = review_progress(document)
                labeled_boxes = sum(
                    bool(points_by_rectangle.get(position, []))
                    for position in range(len(rectangles))
                )
                card_width = 265
                card_outline = "#FFC107" if progress["pending"] else "#69F0AE"
                first_line = (
                    f"本帧已检查  {progress['reviewed']} / {progress['total']}"
                )
                second_line = f"剩余待检查：{progress['pending']} 个框"
                third_line = (
                    f"本帧检测框：总数 {len(rectangles)}"
                    f"｜已标关键点 {labeled_boxes}"
                )
                second_color = "#FFC107" if progress["pending"] else "#69F0AE"
                third_color = "#8EC5FF"
            canvas.create_rectangle(
                card_x,
                card_y,
                card_x + card_width,
                card_y + 94,
                fill="#101827",
                outline=card_outline,
                width=3,
                tags=("overview_dynamic",),
            )
            canvas.create_text(
                card_x + 14,
                card_y + 11,
                text=first_line,
                fill="#FFFFFF",
                anchor=tk.NW,
                font=("Microsoft YaHei UI", 14, "bold"),
                tags=("overview_dynamic",),
            )
            canvas.create_text(
                card_x + 14,
                card_y + 43,
                text=second_line,
                fill=second_color,
                anchor=tk.NW,
                font=("Microsoft YaHei UI", 10, "bold"),
                tags=("overview_dynamic",),
            )
            canvas.create_text(
                card_x + 14,
                card_y + 67,
                text=third_line,
                fill=third_color,
                anchor=tk.NW,
                font=("Microsoft YaHei UI", 10, "bold"),
                tags=("overview_dynamic",),
            )

    def _draw_direction_review_comparison(self) -> None:
        canvas = self.overview_canvas
        canvas.delete("all")
        image = self.current_pil_image
        document = self._current_document()
        rectangles = self._current_rectangles()
        if (
            image is None
            or document is None
            or not 0 <= self.current_rectangle_index < len(rectangles)
        ):
            canvas.create_text(
                max(canvas.winfo_width(), 200) / 2,
                max(canvas.winfo_height(), 200) / 2,
                text="当前帧没有可用于方向对照的个体",
                fill="white",
                font=("Microsoft YaHei UI", 13, "bold"),
            )
            self.overview_transform = None
            return

        rectangle = rectangles[self.current_rectangle_index]
        metadata = direction_review_metadata(rectangle)
        model_head = metadata.get("model_head")
        model_tail = metadata.get("model_tail")
        records = keypoints_for_rectangle(document, self.current_rectangle_index)
        manual_head = next(
            (record["point"] for record in records if record["label"] == "head"),
            None,
        )
        manual_tail = next(
            (record["point"] for record in records if record["label"] == "tail"),
            None,
        )

        x1, y1, x2, y2 = rectangle["rect"]
        all_points = [
            point
            for point in (model_head, model_tail, manual_head, manual_tail)
            if point is not None
        ]
        if all_points:
            x1 = min(x1, *(point[0] for point in all_points))
            y1 = min(y1, *(point[1] for point in all_points))
            x2 = max(x2, *(point[0] for point in all_points))
            y2 = max(y2, *(point[1] for point in all_points))
        box_width = max(1.0, x2 - x1)
        box_height = max(1.0, y2 - y1)
        padding_x = max(box_width * 1.15, 28.0)
        padding_y = max(box_height * 1.15, 28.0)
        crop_x1 = max(0.0, x1 - padding_x)
        crop_y1 = max(0.0, y1 - padding_y)
        crop_x2 = min(float(image.width), x2 + padding_x)
        crop_y2 = min(float(image.height), y2 + padding_y)

        canvas_width = max(canvas.winfo_width(), 160)
        canvas_height = max(canvas.winfo_height(), 160)
        crop_width = max(1.0, crop_x2 - crop_x1)
        crop_height = max(1.0, crop_y2 - crop_y1)
        scale = min(canvas_width / crop_width, canvas_height / crop_height) * 0.92
        display_width = max(1, int(crop_width * scale))
        display_height = max(1, int(crop_height * scale))
        offset_x = (canvas_width - display_width) / 2
        offset_y = (canvas_height - display_height) / 2
        crop = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))
        resized = crop.resize((display_width, display_height), Image.Resampling.LANCZOS)
        self.direction_review_photo = ImageTk.PhotoImage(resized)
        canvas.create_image(
            offset_x,
            offset_y,
            image=self.direction_review_photo,
            anchor=tk.NW,
        )
        self.overview_transform = (
            scale,
            offset_x - crop_x1 * scale,
            offset_y - crop_y1 * scale,
        )

        rect_x1, rect_y1 = self._image_to_canvas(
            (rectangle["rect"][0], rectangle["rect"][1]), self.overview_transform
        )
        rect_x2, rect_y2 = self._image_to_canvas(
            (rectangle["rect"][2], rectangle["rect"][3]), self.overview_transform
        )
        canvas.create_rectangle(
            rect_x1,
            rect_y1,
            rect_x2,
            rect_y2,
            outline="#D9E3F0",
            width=2,
        )

        def draw_direction(head, tail, line_color, head_color, tail_color, prefix, dash=None):
            if head is None or tail is None:
                return
            hx, hy = self._image_to_canvas(head, self.overview_transform)
            tx, ty = self._image_to_canvas(tail, self.overview_transform)
            canvas.create_line(
                hx,
                hy,
                tx,
                ty,
                fill=line_color,
                width=4,
                arrow=tk.LAST,
                arrowshape=(13, 16, 6),
                dash=dash,
            )
            for x, y, color, label in (
                (hx, hy, head_color, f"{prefix}头"),
                (tx, ty, tail_color, f"{prefix}尾"),
            ):
                canvas.create_oval(
                    x - 7,
                    y - 7,
                    x + 7,
                    y + 7,
                    fill=color,
                    outline="white",
                    width=2,
                )
                canvas.create_text(
                    x + 9,
                    y - 9,
                    text=label,
                    fill=color,
                    anchor=tk.SW,
                    font=("Microsoft YaHei UI", 10, "bold"),
                )

        draw_direction(
            model_head,
            model_tail,
            "#FFD166",
            "#FF66CC",
            "#FFB000",
            "模型",
            dash=(7, 4),
        )
        draw_direction(
            manual_head,
            manual_tail,
            "#69F0AE",
            "#FF4D4D",
            "#00E5FF",
            "标注",
        )

        active = self._active_direction_review_item()
        is_target = bool(
            active
            and active["image_index"] == self.current_image_index
            and active["rectangle_position"] == self.current_rectangle_index
        )
        target_text = "当前复审对象" if is_target else "上下文帧（A/D）"
        score_text = ""
        if model_head is not None and model_tail is not None:
            current_angle = None
            if manual_head is not None and manual_tail is not None:
                current_angle = direction_angle_degrees(
                    manual_head,
                    manual_tail,
                    model_head,
                    model_tail,
                )
            current_angle_text = (
                f"{current_angle:.1f}°" if current_angle is not None else "--"
            )
            score_text = (
                f"模型置信度：头 {float(metadata.get('model_head_score', 0)):.2f}｜"
                f"尾 {float(metadata.get('model_tail_score', 0)):.2f}｜"
                f"原始误差 {float(metadata.get('model_angle_error_deg', 0)):.1f}°｜"
                f"当前误差 {current_angle_text}"
            )
        card_width = min(canvas_width - 24, 650)
        canvas.create_rectangle(
            12,
            12,
            12 + card_width,
            84,
            fill="#101827",
            outline="#FFC107" if is_target else "#8EC5FF",
            width=2,
        )
        canvas.create_text(
            24,
            22,
            text=(
                f"{target_text}｜Track ID {rectangle.get('group_id')}｜"
                f"帧 {metadata.get('frame', '')}"
            ),
            fill="#FFFFFF",
            anchor=tk.NW,
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        canvas.create_text(
            24,
            51,
            text=score_text or "当前帧没有模型预测坐标",
            fill="#FFD166" if score_text else "#FF8A65",
            anchor=tk.NW,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        canvas.create_text(
            canvas_width - 14,
            canvas_height - 12,
            text="绿色实线：当前标注    黄色虚线：原模型预测",
            fill="#FFFFFF",
            anchor=tk.SE,
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    def _draw_overview_position(
        self,
        position: int,
        rectangle: Dict,
        records: List[Dict],
        show_box: bool = True,
    ) -> None:
        if self.overview_transform is None:
            return
        canvas = self.overview_canvas
        tag = f"overview_position_{position}"
        state = self._keypoint_state(records, rectangle)
        outline = {
            "pending": "#FFB300",
            "confirmed": "#69F0AE",
            "partial": "#FF8A65",
        }.get(state, "#55A7FF")
        x1, y1, x2, y2 = rectangle["rect"]
        cx1, cy1 = self._image_to_canvas((x1, y1), self.overview_transform)
        cx2, cy2 = self._image_to_canvas((x2, y2), self.overview_transform)
        if show_box:
            canvas.create_rectangle(
                cx1,
                cy1,
                cx2,
                cy2,
                outline=outline,
                width=2 if state == "pending" else 1,
                tags=("overview_static", tag),
            )
            if self.show_track_ids.get():
                self._draw_track_id_label(
                    canvas,
                    cx1 + 3,
                    cy1 + 3,
                    rectangle.get("group_id"),
                    size=8,
                    tags=("overview_static", tag),
                )
            if self.show_box_class_labels.get():
                self._draw_box_class_label(
                    canvas,
                    cx1 + 3,
                    cy1 + (18 if self.show_track_ids.get() else 3),
                    rectangle.get("label"),
                    size=8,
                    tags=("overview_static", tag),
                )
        scale = self.overview_transform[0]
        for record in records:
            x, y = self._image_to_canvas(record["point"], self.overview_transform)
            color = self._label_color(record["label"])
            suggested = point_is_suggested(record)
            radius = 4 if scale > 0.35 else 3
            canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                fill="#FFD54F" if suggested else color,
                outline=color if suggested else "#111111",
                width=2 if suggested else 1,
                tags=("overview_static", tag),
            )
            if self.show_label_names.get():
                canvas.create_text(
                    x + 5,
                    y - 5,
                    text=record["label"] + ("?" if suggested else ""),
                    fill=color,
                    anchor=tk.SW,
                    font=("Microsoft YaHei UI", 8, "bold"),
                    tags=("overview_static", tag),
                )

    def _draw_continuous_detail(self) -> None:
        canvas = self.detail_canvas
        canvas.delete("all")
        image = self.current_pil_image
        if image is None:
            self.detail_transform = None
            return

        canvas_width = max(canvas.winfo_width(), 100)
        canvas_height = max(canvas.winfo_height(), 100)
        center = self.continuous_focus_point or (
            float(image.width) / 2,
            float(image.height) / 2,
        )
        zoom = min(12.0, max(1.0, float(self.continuous_zoom_factor)))
        crop_width = float(image.width) / zoom
        crop_height = float(image.height) / zoom
        canvas_ratio = canvas_width / canvas_height
        if crop_width / crop_height > canvas_ratio:
            crop_height = min(float(image.height), crop_width / canvas_ratio)
        else:
            crop_width = min(float(image.width), crop_height * canvas_ratio)
        crop_x1 = min(
            max(float(center[0]) - crop_width / 2, 0.0),
            max(0.0, float(image.width) - crop_width),
        )
        crop_y1 = min(
            max(float(center[1]) - crop_height / 2, 0.0),
            max(0.0, float(image.height) - crop_height),
        )
        crop_x2 = crop_x1 + crop_width
        crop_y2 = crop_y1 + crop_height

        scale = min(canvas_width / crop_width, canvas_height / crop_height) * 0.97
        display_width = max(1, int(crop_width * scale))
        display_height = max(1, int(crop_height * scale))
        offset_x = (canvas_width - display_width) / 2
        offset_y = (canvas_height - display_height) / 2
        cache_key = (
            "continuous",
            id(image),
            canvas_width,
            canvas_height,
            round(crop_x1, 3),
            round(crop_y1, 3),
            round(crop_x2, 3),
            round(crop_y2, 3),
            display_width,
            display_height,
        )
        if cache_key != self.detail_cache_key:
            crop = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))
            resized = crop.resize(
                (display_width, display_height), Image.Resampling.LANCZOS
            )
            self.detail_photo = ImageTk.PhotoImage(resized)
            self.detail_cache_key = cache_key
        canvas.create_image(offset_x, offset_y, image=self.detail_photo, anchor=tk.NW)
        self.detail_transform = (
            scale,
            offset_x - crop_x1 * scale,
            offset_y - crop_y1 * scale,
        )

        document = self._current_document()
        rectangles = self._current_rectangles()
        position = self._continuous_rectangle_position(document)
        drag = getattr(self, "rectangle_drag", None)
        highlight_position = (
            drag["rectangle_position"]
            if drag is not None
            and drag.get("view") == "detail"
            and drag.get("image_path") == self._current_image_path()
            else self.current_rectangle_index
        )
        points_by_rectangle = (
            keypoints_by_rectangle(document, rectangles) if document else {}
        )
        show_boxes = self.show_other_boxes.get()
        for other_position, other_rectangle in enumerate(rectangles):
            x1, y1, x2, y2 = other_rectangle["rect"]
            if x2 < crop_x1 or x1 > crop_x2 or y2 < crop_y1 or y1 > crop_y2:
                continue
            self._draw_continuous_detail_position(
                other_rectangle,
                points_by_rectangle.get(other_position, []),
                active=other_position == highlight_position,
                show_box=show_boxes,
            )

        creation = getattr(self, "rectangle_creation", None)
        if creation is not None and creation.get("view") == "detail":
            cx1, cy1 = self._image_to_canvas(
                creation["start"], self.detail_transform
            )
            cx2, cy2 = self._image_to_canvas(
                creation["end"], self.detail_transform
            )
            canvas.create_rectangle(
                cx1,
                cy1,
                cx2,
                cy2,
                outline="#00E5FF",
                width=4,
                dash=(8, 4),
            )

        instruction = (
            "画框中：在左侧拖动完成检测框"
            if self.continuous_draw_box_mode
            else (
                "右侧点击选区｜滚轮缩放｜A/D 前后帧｜R 画框｜"
                "E 复制到下一帧｜V 下一个ID｜"
                + (
                    "H 隐藏全部框"
                    if self.show_other_boxes.get()
                    else "H 显示全部框"
                )
            )
        )
        canvas.create_text(
            16,
            16,
            text=(
                f"连续补标 ID {self.continuous_active_track_id}｜"
                f"{zoom:.1f}×｜{instruction}"
            ),
            fill="#FFFFFF",
            anchor=tk.NW,
            font=("Microsoft YaHei UI", 10, "bold"),
        )

    def _draw_continuous_detail_position(
        self,
        rectangle: Dict,
        records: List[Dict],
        active: bool,
        show_box: bool = True,
    ) -> None:
        if self.detail_transform is None:
            return
        canvas = self.detail_canvas
        state = self._keypoint_state(records, rectangle)
        outline = {
            "pending": "#FFC107",
            "confirmed": "#69F0AE",
            "partial": "#FF8A65",
        }.get(state, "#55A7FF")
        x1, y1, x2, y2 = rectangle["rect"]
        cx1, cy1 = self._image_to_canvas((x1, y1), self.detail_transform)
        cx2, cy2 = self._image_to_canvas((x2, y2), self.detail_transform)
        if show_box:
            canvas.create_rectangle(
                cx1,
                cy1,
                cx2,
                cy2,
                outline=outline,
                width=4 if active else 2,
            )
            if self.show_track_ids.get():
                self._draw_track_id_label(
                    canvas,
                    cx1 + 5,
                    cy1 + 5,
                    rectangle.get("group_id"),
                    size=11 if active else 9,
                )
            if self.show_box_class_labels.get():
                self._draw_box_class_label(
                    canvas,
                    cx1 + 5,
                    cy1 + (24 if self.show_track_ids.get() else 5),
                    rectangle.get("label"),
                    size=11 if active else 9,
                )
        if active and show_box:
            self._draw_rectangle_handles(
                canvas,
                rectangle["rect"],
                self.detail_transform,
                outline,
                size=6,
            )
        radius = 7 if active else 5
        for record in records:
            x, y = self._image_to_canvas(record["point"], self.detail_transform)
            color = self._label_color(record["label"])
            suggested = point_is_suggested(record)
            canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                fill="#FFD54F" if suggested else color,
                outline=color if suggested else "white",
                width=3 if active and suggested else 2,
            )
            if self.show_label_names.get():
                canvas.create_text(
                    x + radius + 3,
                    y - radius - 1,
                    text=record["label"] + ("（待确认）" if suggested else ""),
                    fill=color,
                    anchor=tk.SW,
                    font=("Microsoft YaHei UI", 11 if active else 8, "bold"),
                )

    def _draw_detail(self) -> None:
        if getattr(self, "continuous_annotation_mode", False):
            self._draw_continuous_detail()
            return
        canvas = self.detail_canvas
        canvas.delete("all")
        image = self.current_pil_image
        rectangles = self._current_rectangles()
        if (
            image is None
            or not rectangles
            or not 0 <= self.current_rectangle_index < len(rectangles)
        ):
            canvas.create_text(
                canvas.winfo_width() / 2,
                canvas.winfo_height() / 2,
                text="当前图片没有可用矩形框",
                fill="white",
                font=("Microsoft YaHei UI", 14),
            )
            self.detail_transform = None
            return

        rect = rectangles[self.current_rectangle_index]["rect"]
        x1, y1, x2, y2 = rect
        viewport_rect = rect
        if (
            self.rectangle_drag is not None
            and self.rectangle_drag["view"] == "detail"
            and self.rectangle_drag["image_path"] == self._current_image_path()
            and self.rectangle_drag["rectangle_position"]
            == self.current_rectangle_index
        ):
            viewport_rect = self.rectangle_drag["original_rect"]
        view_x1, view_y1, view_x2, view_y2 = viewport_rect
        box_width = max(1.0, view_x2 - view_x1)
        box_height = max(1.0, view_y2 - view_y1)
        padding_x = max(box_width * 0.65, 18.0)
        padding_y = max(box_height * 0.65, 18.0)
        crop_x1 = max(0.0, view_x1 - padding_x)
        crop_y1 = max(0.0, view_y1 - padding_y)
        crop_x2 = min(float(image.width), view_x2 + padding_x)
        crop_y2 = min(float(image.height), view_y2 + padding_y)

        view_key = (self._current_image_path(), self.current_rectangle_index)
        view_center = getattr(self, "detail_view_centers", {}).get(view_key)
        if view_center is not None:
            crop_width = crop_x2 - crop_x1
            crop_height = crop_y2 - crop_y1
            crop_x1 = min(
                max(float(view_center[0]) - crop_width / 2.0, 0.0),
                max(0.0, float(image.width) - crop_width),
            )
            crop_y1 = min(
                max(float(view_center[1]) - crop_height / 2.0, 0.0),
                max(0.0, float(image.height) - crop_height),
            )
            crop_x2 = crop_x1 + crop_width
            crop_y2 = crop_y1 + crop_height

        canvas_width = max(canvas.winfo_width(), 100)
        canvas_height = max(canvas.winfo_height(), 100)
        crop_width = max(1.0, crop_x2 - crop_x1)
        crop_height = max(1.0, crop_y2 - crop_y1)
        scale = min(canvas_width / crop_width, canvas_height / crop_height) * 0.92
        display_width = max(1, int(crop_width * scale))
        display_height = max(1, int(crop_height * scale))
        offset_x = (canvas_width - display_width) / 2
        offset_y = (canvas_height - display_height) / 2

        cache_key = (
            id(image),
            self.current_rectangle_index,
            canvas_width,
            canvas_height,
            crop_x1,
            crop_y1,
            crop_x2,
            crop_y2,
            display_width,
            display_height,
        )
        if cache_key != self.detail_cache_key:
            crop = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))
            resized = crop.resize(
                (display_width, display_height), Image.Resampling.LANCZOS
            )
            self.detail_photo = ImageTk.PhotoImage(resized)
            self.detail_cache_key = cache_key
        canvas.create_image(offset_x, offset_y, image=self.detail_photo, anchor=tk.NW)
        self.detail_transform = (
            scale,
            offset_x - crop_x1 * scale,
            offset_y - crop_y1 * scale,
        )

        cx1, cy1 = self._image_to_canvas((x1, y1), self.detail_transform)
        cx2, cy2 = self._image_to_canvas((x2, y2), self.detail_transform)
        document = self._current_document()
        points_by_rectangle = (
            keypoints_by_rectangle(document, rectangles) if document else {}
        )
        show_boxes = self.show_other_boxes.get()
        for other_position, other_rectangle in enumerate(rectangles):
            if other_position == self.current_rectangle_index:
                continue
            ox1, oy1, ox2, oy2 = other_rectangle["rect"]
            if ox2 < crop_x1 or ox1 > crop_x2 or oy2 < crop_y1 or oy1 > crop_y2:
                continue
            self._draw_continuous_detail_position(
                other_rectangle,
                points_by_rectangle.get(other_position, []),
                active=(
                    self.rectangle_drag is not None
                    and self.rectangle_drag.get("view") == "detail"
                    and self.rectangle_drag.get("rectangle_position")
                    == other_position
                ),
                show_box=show_boxes,
            )
        current_records = (
            points_by_rectangle.get(self.current_rectangle_index, [])
        )
        state = self._keypoint_state(
            current_records, rectangles[self.current_rectangle_index]
        )
        outline = {
            "pending": "#FFC107",
            "confirmed": "#69F0AE",
            "partial": "#FF8A65",
        }.get(state, "#FFD54F")
        current_is_drag_target = (
            self.rectangle_drag is None
            or self.rectangle_drag.get("view") != "detail"
            or self.rectangle_drag.get("rectangle_position")
            == self.current_rectangle_index
        )
        if show_boxes:
            canvas.create_rectangle(cx1, cy1, cx2, cy2, outline=outline, width=4)
            if self.show_track_ids.get():
                self._draw_track_id_label(
                    canvas,
                    cx1 + 5,
                    cy1 + 5,
                    rectangles[self.current_rectangle_index].get("group_id"),
                    size=11,
                )
            if self.show_box_class_labels.get():
                self._draw_box_class_label(
                    canvas,
                    cx1 + 5,
                    cy1 + (24 if self.show_track_ids.get() else 5),
                    rectangles[self.current_rectangle_index].get("label"),
                    size=11,
                )
            if current_is_drag_target:
                self._draw_rectangle_handles(
                    canvas,
                    rect,
                    self.detail_transform,
                    outline,
                    size=6,
                )

        if document:
            for record in current_records:
                x, y = self._image_to_canvas(record["point"], self.detail_transform)
                color = self._label_color(record["label"])
                suggested = point_is_suggested(record)
                radius = 7
                canvas.create_oval(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    fill="#FFD54F" if suggested else color,
                    outline=color if suggested else "white",
                    width=3 if suggested else 2,
                )
                if self.show_label_names.get():
                    canvas.create_text(
                        x + 10,
                        y - 8,
                        text=record["label"] + ("（待确认）" if suggested else ""),
                        fill=color,
                        anchor=tk.SW,
                        font=("Microsoft YaHei UI", 11, "bold"),
                    )

        canvas.create_text(
            12,
            12,
            text=self._detail_operation_hint(),
            fill="white",
            anchor=tk.NW,
            font=("Microsoft YaHei UI", 10, "bold"),
        )

    def _detail_operation_hint(self) -> str:
        if getattr(self, "direction_review_mode", False):
            return (
                "方向复审：E 确认并进入下一个｜Q/W 上一个/下一个复审对象｜"
                "A/D 查看相同 Track ID 前后帧｜X 交换头尾\n"
                "左侧可直接修正关键点；右侧显示当前标注与原模型预测对照"
            )
        if self.symmetry_enabled.get():
            return (
                f"左键：标注 {self.active_label.get()} 并自动补 "
                f"{self.symmetry_target_label.get()}"
                f"（比例 {self.symmetry_ratio_text.get()}）    "
                f"右键：手动标注 {self.symmetry_target_label.get()}    "
                "Ctrl+右键框：改 ID    "
                "中键：删除附近点    滚轮：切换检测框\n"
                "左键拖动框内：移动｜拖动白点：缩放    "
                "单击：标点/选框    B：框 ID    Y：框类别    H：全部框"
            )
        return (
            f"左键：标注 {self.active_label.get()}    "
            "右键：删除最近点    Ctrl+右键框：改 ID    "
            "中键：删除附近点    滚轮：切换检测框\n"
            "左键拖动框内：移动｜拖动白点：缩放    "
            "单击：标点/选框    B：框 ID    Y：框类别    H：全部框"
        )

    @staticmethod
    def _draw_track_id_label(
        canvas: tk.Canvas,
        x: float,
        y: float,
        group_id,
        size: int,
        tags=(),
    ) -> None:
        if group_id is None:
            return
        text = f"ID {group_id}"
        font = ("Microsoft YaHei UI", size, "bold")
        canvas.create_text(
            x + 1,
            y + 1,
            text=text,
            fill="#111111",
            anchor=tk.NW,
            font=font,
            tags=tags,
        )
        canvas.create_text(
            x,
            y,
            text=text,
            fill="#FFFFFF",
            anchor=tk.NW,
            font=font,
            tags=tags,
        )

    @staticmethod
    def _draw_box_class_label(
        canvas: tk.Canvas,
        x: float,
        y: float,
        label,
        size: int,
        tags=(),
    ) -> None:
        text = str(label or "").strip()
        if not text:
            return
        font = ("Microsoft YaHei UI", size, "bold")
        canvas.create_text(
            x + 1,
            y + 1,
            text=text,
            fill="#111111",
            anchor=tk.NW,
            font=font,
            tags=tags,
        )
        canvas.create_text(
            x,
            y,
            text=text,
            fill="#7FE7FF" if text.lower() == "beeshadow" else "#FFFFFF",
            anchor=tk.NW,
            font=font,
            tags=tags,
        )

    @staticmethod
    def _draw_rectangle_handles(
        canvas: tk.Canvas,
        rect: Tuple[float, float, float, float],
        transform: Tuple[float, float, float],
        color: str,
        size: int,
        tags=(),
    ) -> None:
        x1, y1, x2, y2 = rect
        middle_x = (x1 + x2) / 2.0
        middle_y = (y1 + y2) / 2.0
        handles = [
            (x1, y1),
            (middle_x, y1),
            (x2, y1),
            (x2, middle_y),
            (x2, y2),
            (middle_x, y2),
            (x1, y2),
            (x1, middle_y),
        ]
        for point in handles:
            x, y = BeeKeypointAnnotator._image_to_canvas(point, transform)
            canvas.create_rectangle(
                x - size,
                y - size,
                x + size,
                y + size,
                fill="#FFFFFF",
                outline=color,
                width=2,
                tags=tags,
            )

    @staticmethod
    def _image_to_canvas(
        point: Tuple[float, float], transform: Tuple[float, float, float]
    ) -> Tuple[float, float]:
        scale, offset_x, offset_y = transform
        return point[0] * scale + offset_x, point[1] * scale + offset_y

    @staticmethod
    def _canvas_to_image(
        x: float, y: float, transform: Tuple[float, float, float]
    ) -> Tuple[float, float]:
        scale, offset_x, offset_y = transform
        return (x - offset_x) / scale, (y - offset_y) / scale

    @staticmethod
    def _label_color(label: str) -> str:
        preferred = {
            "head": "#FF4D4D",
            "tail": "#00E5FF",
            "bee": "#55A7FF",
            "bee_shadow": "#FF8A65",
        }
        if label in preferred:
            return preferred[label]
        palette = ["#B388FF", "#69F0AE", "#FFAB40", "#F48FB1", "#FFFF00"]
        checksum = sum((index + 1) * ord(character) for index, character in enumerate(label))
        return palette[checksum % len(palette)]

    def _update_information(self) -> None:
        image_path = self._current_image_path()
        document = self._current_document()
        rectangles = self._current_rectangles()
        if image_path is None or document is None:
            self.progress_var.set("")
            self.points_var.set("")
            self.confirmation_progress.set(0.0)
            return
        current = self.current_rectangle_index + 1 if rectangles else 0
        points_by_rectangle = keypoints_by_rectangle(document, rectangles)
        if getattr(self, "direction_review_mode", False):
            items = self._direction_review_items()
            reviewed = sum(not item["pending"] for item in items)
            pending = len(items) - reviewed
            self.confirmation_progress.set(
                reviewed / len(items) * 100.0 if items else 0.0
            )
            active = self._active_direction_review_item()
            self.progress_var.set(
                f"方向复审  |  当前 {self.direction_review_item_index + 1}/"
                f"{len(items)}  |  已完成 {reviewed}  |  剩余 {pending}"
            )
            if 0 <= self.current_rectangle_index < len(rectangles):
                records = points_by_rectangle.get(self.current_rectangle_index, [])
                group_id = rectangles[self.current_rectangle_index].get("group_id")
                metadata = direction_review_metadata(
                    rectangles[self.current_rectangle_index]
                )
                context = ""
                if active and (
                    active["image_index"] != self.current_image_index
                    or active["rectangle_position"] != self.current_rectangle_index
                ):
                    context = "｜上下文帧"
                self.points_var.set(
                    f"ID {group_id}｜帧 {metadata.get('frame', '')}{context}｜"
                    + "，".join(
                        f"{record['label']}({record['point'][0]:.1f},"
                        f"{record['point'][1]:.1f})"
                        for record in records
                    )
                )
            else:
                self.points_var.set("当前帧没有对应 Track ID")
            return
        if self.track_id_mode and self.track_mode_current_id is not None:
            stats = self._current_track_stats_for_display()
            occurrence_position = self._current_track_occurrence_position(
                stats["occurrences"]
            )
            progress_percent = (
                stats["completed"] / stats["total"] * 100.0
                if stats["total"]
                else 0.0
            )
            self.confirmation_progress.set(progress_percent)
            self.progress_var.set(
                f"按 ID 模式  |  ID {self.track_mode_current_id}  |  "
                f"帧 {occurrence_position + 1 if occurrence_position >= 0 else 0}/"
                f"{stats['total']}  |  已完成 {stats['completed']}  |  "
                f"剩余 {stats['remaining']}"
            )
            if 0 <= self.current_rectangle_index < len(rectangles):
                records = points_by_rectangle.get(self.current_rectangle_index, [])
                state_text = {
                    "unlabeled": "未标注",
                    "pending": "待确认",
                    "confirmed": "已确认",
                    "partial": "不完整",
                }[self._keypoint_state(records, rectangles[self.current_rectangle_index])]
                point_text = "，".join(
                    f"{record['label']}{'?' if point_is_suggested(record) else ''}"
                    f"({record['point'][0]:.1f},{record['point'][1]:.1f})"
                    for record in records
                )
                self.points_var.set(
                    f"ID {self.track_mode_current_id}｜{state_text}｜"
                    + (point_text or "暂无关键点")
                )
            else:
                self.points_var.set("当前 ID 没有可用检测框")
            return
        states = {
            position: self._keypoint_state(
                points_by_rectangle.get(position, []), rectangles[position]
            )
            for position in range(len(rectangles))
        }
        confirmed = sum(state == "confirmed" for state in states.values())
        pending = sum(state == "pending" for state in states.values())
        frame_review = review_progress(document)
        if frame_review["total"]:
            progress_percent = (
                frame_review["reviewed"] / frame_review["total"] * 100.0
            )
        else:
            progress_percent = confirmed / len(rectangles) * 100.0 if rectangles else 0.0
        self.confirmation_progress.set(progress_percent)
        self.progress_var.set(
            f"图片 {self.current_image_index + 1}/{len(self.images)}  |  "
            f"框 {current}/{len(rectangles)}  |  "
            f"已确认 {confirmed}  |  待确认 {pending}"
        )
        if 0 <= self.current_rectangle_index < len(rectangles):
            records = points_by_rectangle.get(self.current_rectangle_index, [])
            group_id = rectangles[self.current_rectangle_index].get("group_id")
            state_text = {
                "unlabeled": "未标注",
                "pending": "待确认",
                "confirmed": "已确认",
                "partial": "不完整",
            }[self._keypoint_state(records, rectangles[self.current_rectangle_index])]
            point_text = "，".join(
                f"{record['label']}{'?' if point_is_suggested(record) else ''}"
                f"({record['point'][0]:.1f},{record['point'][1]:.1f})"
                for record in records
            )
            id_text = f"ID {group_id}" if group_id is not None else "无 Track ID"
            self.points_var.set(
                f"{id_text}｜{state_text}｜" + (point_text or "暂无关键点")
            )
        else:
            self.points_var.set("当前图片无检测框")

    # ----------------------------- 标签管理 -----------------------------

    def _update_label_widgets(self) -> None:
        for combo in (
            self.label_combo,
            self.symmetry_source_combo,
            self.symmetry_target_combo,
        ):
            combo.configure(values=self.labels)
        if self.active_label.get() not in self.labels:
            self.active_label.set(self.labels[0])
        if self.symmetry_source_label.get() not in self.labels:
            self.symmetry_source_label.set(self.labels[0])
        if self.symmetry_target_label.get() not in self.labels:
            self.symmetry_target_label.set(self.labels[min(1, len(self.labels) - 1)])

    def open_label_manager(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("关键点标签管理")
        window.geometry("420x360")
        window.transient(self.root)
        window.grab_set()

        ttk.Label(
            window,
            text="管理关键点标签（不会修改 bee 检测框）",
            style="Title.TLabel",
        ).pack(anchor=tk.W, padx=15, pady=(15, 8))
        listbox = tk.Listbox(window, font=("Microsoft YaHei UI", 11))
        listbox.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        def refresh_list() -> None:
            listbox.delete(0, tk.END)
            for label in self.labels:
                listbox.insert(tk.END, label)

        def add_label() -> None:
            label = simpledialog.askstring("新增标签", "输入新关键点标签：", parent=window)
            if not label:
                return
            label = label.strip()
            if not label or label in self.labels:
                return
            self.labels.append(label)
            self._update_label_widgets()
            refresh_list()
            self._save_settings()

        def rename_label() -> None:
            selection = listbox.curselection()
            if not selection:
                return
            old_label = self.labels[selection[0]]
            new_label = simpledialog.askstring(
                "重命名标签",
                f"将 {old_label} 重命名为：\n"
                "这会同时修改当前文件夹中已有的同名关键点。",
                initialvalue=old_label,
                parent=window,
            )
            if not new_label:
                return
            new_label = new_label.strip()
            if not new_label or new_label == old_label or new_label in self.labels:
                return
            if not messagebox.askyesno(
                "确认重命名",
                f"确认把当前文件夹中的关键点标签 {old_label} 改为 {new_label}？",
                parent=window,
            ):
                return
            changed = 0
            for image_path in self.images:
                count = rename_point_label(
                    self._get_document(image_path), old_label, new_label
                )
                if count:
                    self.dirty_images.add(image_path)
                    changed += count
            self.labels[selection[0]] = new_label
            if self.active_label.get() == old_label:
                self.active_label.set(new_label)
            if self.symmetry_source_label.get() == old_label:
                self.symmetry_source_label.set(new_label)
            if self.symmetry_target_label.get() == old_label:
                self.symmetry_target_label.set(new_label)
            self._update_label_widgets()
            refresh_list()
            self._save_settings()
            self.status_var.set(f"已重命名 {changed} 个关键点")
            self._refresh_all()

        def remove_label() -> None:
            selection = listbox.curselection()
            if not selection or len(self.labels) <= 1:
                return
            label = self.labels[selection[0]]
            if not messagebox.askyesno(
                "移除可选标签",
                f"只从可选列表移除 {label}，已有关键点不会被删除。继续？",
                parent=window,
            ):
                return
            self.labels.remove(label)
            self._update_label_widgets()
            refresh_list()
            self._save_settings()

        buttons = ttk.Frame(window, padding=10)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="新增", command=add_label).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=3
        )
        ttk.Button(buttons, text="重命名", command=rename_label).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=3
        )
        ttk.Button(buttons, text="移除", command=remove_label).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=3
        )
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=3
        )
        refresh_list()

    def open_help(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("蜜蜂关键点标注器 - 使用帮助")
        window.geometry("820x760")
        window.minsize(680, 560)
        window.transient(self.root)

        container = ttk.Frame(window, padding=12)
        container.pack(fill=tk.BOTH, expand=True)
        help_header = ttk.Frame(container)
        help_header.pack(fill=tk.X, pady=(0, 8))
        if self.assistant_help_photo is not None:
            ttk.Label(
                help_header,
                image=self.assistant_help_photo,
            ).pack(side=tk.RIGHT, padx=(12, 4))
        help_titles = ttk.Frame(help_header)
        help_titles.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=(14, 0))
        ttk.Label(
            help_titles,
            text="蜜蜂关键点标注器使用帮助",
            style="Title.TLabel",
        ).pack(anchor=tk.W)
        ttk.Label(
            help_titles,
            text="从人工关键帧到 Track ID 批量复审",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(4, 0))

        help_text = scrolledtext.ScrolledText(
            container,
            wrap=tk.WORD,
            font=("Microsoft YaHei UI", 11),
            padx=14,
            pady=12,
            spacing1=2,
            spacing3=6,
        )
        help_text.pack(fill=tk.BOTH, expand=True)
        help_text.insert(tk.END, self._build_help_content())
        help_text.configure(state=tk.DISABLED)

        ttk.Button(
            container,
            text="关闭",
            command=window.destroy,
        ).pack(anchor=tk.E, pady=(8, 0))

    def _build_help_content(self) -> str:
        shortcut_lines = []
        for action_id, action_label, _defaults in ACTION_DEFINITIONS:
            assigned = [
                shortcut
                for shortcut in self.shortcuts.get(action_id, [])
                if shortcut != "无"
            ]
            shortcut_lines.append(
                f"  {action_label}：{' / '.join(assigned) if assigned else '未设置'}"
            )

        folder_text = str(self.folder) if self.folder else "尚未打开数据文件夹"
        return f"""一、软件用途

本软件用于读取 X-AnyLabeling/LabelMe JSON 中已有的 bee 检测框，并在每个框内标注 head、tail 等关键点。原有检测框不会被删除，新增关键点仍可被 X-AnyLabeling 读取。

当前数据文件夹：
{folder_text}

二、界面说明

1. 左侧“当前检测框放大视图”
   用于观察当前蜜蜂并精确点击关键点。边框颜色会显示当前框的复审状态。

2. 右侧“整图鸟瞰”
   显示整张图片、所有检测框和关键点。左键点击任意检测框即可选中并在左侧放大。
   按 G 可打开本帧框清单，未标注、缺点和待检查框会集中列出；单击序号即可跳转。

3. 顶部工具栏
   可以打开文件夹、切换图片、选择标签、管理标签、开启中心对称、调节对称比例、按 Track ID 传播、打开 IoU 传播、设置快捷键和查看帮助。

三、两种标注模式

1. 原始逐帧模式
   Q/E 切换本页检测框，Z/C 切换图片，G 打开本帧框清单。
   R 可把当前整帧的关键点按 Track ID 复制到下一张。

2. 按 Track ID 标注模式（推荐）
   点击底部“进入按 ID 标注模式”。软件会锁定一个 Track ID，并从它的第一次出现开始检查。
   A/D：只浏览当前 ID 的上一帧/下一帧，不会自动确认黄色点。
   E：明确确认当前图片中该 ID 的 head/tail 正确，清除黄色待确认状态，按框内相对位置复制到下一张，并自动前进。
   Q：返回上一个 Track ID，并从该 ID 的第一帧开始。
   W：进入下一个 Track ID；优先跳到该 ID 的第一处未完成位置。
   G：打开 Track ID 清单，查看每个 ID 的总帧数、已完成帧和剩余帧；单击即可进入。
   一个 ID 的全部帧完成后会弹窗；再按 E 进入下一个未完成 ID，按 Q 返回上一个 ID。

完成标准：该 ID 的每次出现都必须恰好有一个人工确认的 head 和一个 tail，不能残留黄色待确认点。

四、推荐按 ID 标注流程

1. 进入“按 ID 标注模式”，或按 G 从清单选择一个未完成 ID。
2. 当前标签选择 head；可开启中心对称，在左侧放大图标出 head 和 tail。
3. 确保 head/tail 各一个且方向正确，然后按 E；这一步代表你确认当前图片中的关键点正确。
4. 下一帧会出现按相对位置复制的黄色建议点；修正后继续按 E。
5. 按 A/D 可前后对比当前 ID；如果头尾反了，按 X 交换。
6. 当前 ID 全部完成后，在弹窗中按 E 进入下一个 ID。

五、鼠标操作

左键：
  中心对称开启时，添加当前标签，并自动补充对称目标标签。
  中心对称关闭时，只添加当前标签。

右键：
  中心对称开启时，手动标注或修正 tail。
  中心对称关闭时，删除当前框内最近的关键点。

鼠标中键：
  删除鼠标附近的关键点。附近没有点时不会误删远处的点。

鼠标滚轮：
  原始模式切换上一个/下一个检测框；按 ID 模式浏览当前 ID 前后帧。

鼠标侧键：
  默认侧键 1 是上一个检测框，侧键 2 是下一个检测框。
  侧键只在本软件窗口内生效，可在“快捷键”窗口重新分配。

鼠标左键直接拖动：
  在检测框内部直接拖动：移动整个检测框，不需要按 Ctrl。
  在八个白色控制点上直接拖动：按对应方向调整检测框大小。
  单击其他框会先选中它并显示白色控制点；普通短按仍然用于标注关键点。
  原有长按和 Ctrl + 左键拖动仍作为兼容操作保留。
  左侧放大视图和右侧鸟瞰图都可以操作。
  松开鼠标后自动保存；移动框时关键点同步平移，缩放框时关键点保持原图位置不变。
  如果调整错误，按 Ctrl+Z 撤销。

六、中心对称比例

计算方式：
  尾点 = 框中心 + 比例 ×（框中心 - 头点）

0%：尾点位于检测框中心。
100%：严格中心对称。
小于 100%：尾点更靠近中心。
大于 100%：尾点离中心更远。

比例范围为 0%～200%。比例只影响之后自动生成的尾点，不会修改已经标好的点；计算结果超出检测框时会限制在框边界内。

七、自动保存与撤销

新增、修正、删除关键点，以及调整检测框后会在短暂空闲时自动保存，连续点击会合并写盘以保持流畅。
切换图片或关闭软件时会强制保存尚未写入的数据。
第一次修改原 JSON 时，会在同一目录创建 .json.bak 备份。
Ctrl+Z 可以撤销当前图片的上一次点标操作，撤销结果也会立即保存。

八、Track ID 传播与复审

当前检测框有 Track ID 时，优先使用“ID传播”（Ctrl+P）。软件会：
  只匹配完全相同的 Track ID；
  按关键点在源检测框中的相对位置迁移；
  把传播结果标成黄色待确认点；
  保留目标帧已有的人工确认点，只更新旧的黄色建议点。

同一轨迹有多个已确认关键帧时，每个目标帧会采用时间上最近的已确认帧作为来源。
R：以当前图片为源帧，把所有框的关键点按 Track ID 和框内相对位置复制到下一张图片，然后自动切换过去；不覆盖人工确认点。
A/D：同 ID 上一帧/下一帧，并自动选中同一检测框。
Z/C：普通上一张/下一张图片。
空格：确认当前框的 head/tail。
X：交换当前框的 head/tail，并确认。
N：跳到下一处待确认、缺点、重复点、框外点、距离异常或明显方向跳变。
G：打开本帧检测框清单；默认显示未完成框，单击序号直接进入。

黄色框被选中查看后，在切换到其他框或其他图片时，如果它正好有一个 head 和一个 tail，软件会自动确认并把它变成绿色。缺点、重复点等不完整框不会自动确认。
右侧鸟瞰图顶部显示“本帧已检查 / 总待审框数 / 剩余待检查”，进度会保存在 JSON 中；自动确认后可按 Ctrl+Z 撤销，软件会回到对应图片和检测框。
当本页每个框都恰好有一个已确认的 head 和 tail 时，软件只弹出一次“本页标注完成”提示。

九、IoU 关键点传播（备用）

1. 点击“IoU 传播”。
2. 选择已经标好的源图片和需要接收关键点的目标图片。
3. 输入 IoU 阈值。
4. 点击“预览匹配”，检查匹配框和准备写入的关键点数量。
5. 确认后点击“应用到目标图”。

软件会对所有同标签矩形框进行一对一 IoU 匹配，并把关键点按框内相对坐标迁移。默认只补缺失点，不覆盖已经人工标好的同名点。只有旧数据没有 Track ID，或确实需要跨 ID 迁移时才建议使用。

十、标签管理

点击“标签管理”可以新增、重命名和移除可选关键点标签。
重命名会同时修改当前文件夹中已有的同名 point 标注。
移除只会从可选列表移除，不会删除已经存在的关键点。
bee 检测框标签不受关键点标签管理影响。

十一、快捷键自定义

点击“快捷键”或按 Ctrl+K 打开快捷键设置；按 F1 打开本帮助。
每个动作可以同时设置两个快捷键，例如同时保留 Q 和鼠标侧键 1。
单击快捷键框，看到“请按键或侧键…”后，直接按键盘按键、组合键或
鼠标侧键，软件会自动识别并填入；右键单击快捷键框可以清空。
软件会检查冲突；同一个快捷键不能同时分配给两个动作。
设置会保存在 settings.json 中，下次启动继续生效。
“鼠标长按调框”可以调节短按标点与长按调框之间的识别延时。
运行时字母快捷键按物理键识别，不受中文/英文输入法、Caps Lock 或字母大小写影响。
当输入框或设置窗口获得焦点时，主界面快捷键会暂停，避免干扰正常输入。

当前快捷键：
{chr(10).join(shortcut_lines)}

十二、颜色说明

黄色点/橙黄色框：Track ID 自动传播结果，尚未人工确认。
绿色框：head/tail 已人工确认。
蓝色框：尚未标注的 bee 检测框。
橙红色框：关键点不完整。
红色点：head。
青色点：tail。

十三、注意事项

1. 每个框只保留一个同名关键点；重新点击会覆盖该框原来的同名点。
2. 黄色传播点必须逐框复审并按空格确认，尤其是蜜蜂转向、遮挡或检测框变化较大时。
3. 不要删除同目录中的 JPG 图片和原 JSON。
4. 如需恢复最初 JSON，可使用同目录的 .json.bak 备份。
5. 移动检测框时所属关键点同步平移；缩放检测框时关键点保持原图位置不变。
"""

    def open_shortcut_manager(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("快捷键设置")
        window.geometry("740x820")
        window.minsize(650, 680)
        window.transient(self.root)
        window.grab_set()

        outer = ttk.Frame(window, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            outer,
            text="快捷键自定义",
            style="Title.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky=tk.W)
        ttk.Label(
            outer,
            text=(
                "点击快捷键框，然后直接按键盘按键或鼠标侧键；"
                "右键点击快捷键框可清空。"
            ),
        ).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(3, 12))
        ttk.Label(outer, text="动作", style="Title.TLabel").grid(
            row=2, column=0, sticky=tk.W, padx=(0, 12)
        )
        ttk.Label(outer, text="快捷键 1", style="Title.TLabel").grid(
            row=2, column=1, sticky=tk.W
        )
        ttk.Label(outer, text="快捷键 2", style="Title.TLabel").grid(
            row=2, column=2, sticky=tk.W
        )

        shortcut_variables: Dict[str, List[tk.StringVar]] = {}
        shortcut_buttons: Dict[str, List[ttk.Button]] = {}
        configured_long_press = getattr(
            self, "long_press_delay_ms", DEFAULT_LONG_PRESS_DELAY_MS
        )
        long_press_variable = tk.DoubleVar(value=configured_long_press)
        long_press_text = tk.StringVar(value=f"{configured_long_press} ms")
        capture_state = {
            "variable": None,
            "button": None,
            "modifiers": set(),
        }

        def finish_capture(shortcut: str) -> None:
            variable = capture_state["variable"]
            button = capture_state["button"]
            if variable is None or button is None:
                return
            variable.set(shortcut)
            button.configure(text=shortcut)
            capture_state["variable"] = None
            capture_state["button"] = None
            capture_state["modifiers"].clear()

        def start_capture(variable: tk.StringVar, button: ttk.Button) -> None:
            previous_button = capture_state["button"]
            previous_variable = capture_state["variable"]
            if previous_button is not None and previous_variable is not None:
                previous_button.configure(text=previous_variable.get())
            capture_state["variable"] = variable
            capture_state["button"] = button
            capture_state["modifiers"].clear()
            button.configure(text="请按键或侧键…")
            button.focus_set()

        def clear_capture(variable: tk.StringVar, button: ttk.Button):
            if capture_state["button"] is button:
                capture_state["variable"] = None
                capture_state["button"] = None
                capture_state["modifiers"].clear()
            variable.set("无")
            button.configure(text="无")
            return "break"

        def capture_key(event):
            if capture_state["variable"] is None:
                return None
            modifier = self._modifier_name(event.keysym)
            if modifier is not None:
                capture_state["modifiers"].add(modifier)
                return "break"
            shortcut = self._captured_shortcut_with_modifiers(
                event.keysym,
                capture_state["modifiers"],
            )
            if shortcut is not None:
                finish_capture(shortcut)
            return "break"

        def release_key(event):
            modifier = self._modifier_name(event.keysym)
            if modifier is not None:
                capture_state["modifiers"].discard(modifier)
            return "break" if capture_state["variable"] is not None else None

        for row_offset, (action_id, action_label, _defaults) in enumerate(
            ACTION_DEFINITIONS, start=3
        ):
            ttk.Label(outer, text=action_label).grid(
                row=row_offset, column=0, sticky=tk.W, pady=3, padx=(0, 12)
            )
            current = self.shortcuts.get(action_id, ["无", "无"])
            variables = [
                tk.StringVar(value=current[0]),
                tk.StringVar(value=current[1]),
            ]
            shortcut_variables[action_id] = variables
            shortcut_buttons[action_id] = []
            for column, variable in enumerate(variables, start=1):
                button = ttk.Button(
                    outer,
                    text=variable.get(),
                    width=20,
                )
                button.configure(
                    command=lambda selected_variable=variable, selected_button=button: start_capture(
                        selected_variable, selected_button
                    )
                )
                button.bind(
                    "<Button-3>",
                    lambda _event, selected_variable=variable, selected_button=button: clear_capture(
                        selected_variable, selected_button
                    ),
                )
                button.grid(
                    row=row_offset, column=column, sticky=tk.EW, pady=3, padx=3
                )
                shortcut_buttons[action_id].append(button)

        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=0)
        outer.columnconfigure(2, weight=0)
        window.bind("<KeyPress>", capture_key)
        window.bind("<KeyRelease>", release_key)
        window.bind(
            "<Button-4>",
            lambda _event: finish_capture("鼠标侧键1"),
        )
        window.bind(
            "<Button-5>",
            lambda _event: finish_capture("鼠标侧键2"),
        )

        def update_long_press_text(value=None) -> None:
            selected = self._normalize_long_press_delay(
                long_press_variable.get() if value is None else value
            )
            long_press_text.set(f"{selected} ms")

        mouse_settings = ttk.LabelFrame(
            outer,
            text="  鼠标长按调框  ",
            padding=(10, 7),
        )
        mouse_settings.grid(
            row=3 + len(ACTION_DEFINITIONS),
            column=0,
            columnspan=3,
            sticky=tk.EW,
            pady=(10, 0),
        )
        ttk.Label(mouse_settings, text="长按延时").pack(side=tk.LEFT)
        ttk.Scale(
            mouse_settings,
            from_=MIN_LONG_PRESS_DELAY_MS,
            to=MAX_LONG_PRESS_DELAY_MS,
            variable=long_press_variable,
            command=update_long_press_text,
            orient=tk.HORIZONTAL,
            length=300,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        ttk.Label(
            mouse_settings,
            textvariable=long_press_text,
            width=9,
            style="Title.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Label(
            mouse_settings,
            text="100ms 更灵敏｜1000ms 更防误触",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, padx=(8, 0))

        capture_hook = {"hooks": {}, "procedure": None}

        def install_capture_mouse_hook() -> None:
            if win32gui is None or win32con is None:
                return
            try:
                window.update_idletasks()
                window_handle = self._tk_toplevel_window_handle(window)
                if not window_handle:
                    return
                window_handles = [window_handle]
                win32gui.EnumChildWindows(
                    window_handle,
                    lambda child_handle, _extra: window_handles.append(
                        child_handle
                    ),
                    None,
                )

                def capture_window_message(hwnd, message, w_param, l_param):
                    if message in {0x020B, 0x020C}:
                        if message == 0x020B:
                            button_number = (int(w_param) >> 16) & 0xFFFF
                            if button_number not in {1, 2}:
                                old_procedure = capture_hook["hooks"].get(hwnd)
                                if not old_procedure:
                                    return win32gui.DefWindowProc(
                                        hwnd, message, w_param, l_param
                                    )
                                return win32gui.CallWindowProc(
                                    old_procedure,
                                    hwnd,
                                    message,
                                    w_param,
                                    l_param,
                                )
                            shortcut = (
                                "鼠标侧键1"
                                if button_number == 1
                                else "鼠标侧键2"
                            )
                            window.after_idle(
                                lambda selected_shortcut=shortcut: finish_capture(
                                    selected_shortcut
                                )
                            )
                        return 0
                    old_procedure = capture_hook["hooks"].get(hwnd)
                    if not old_procedure:
                        return win32gui.DefWindowProc(
                            hwnd, message, w_param, l_param
                        )
                    return win32gui.CallWindowProc(
                        old_procedure, hwnd, message, w_param, l_param
                    )

                capture_hook["procedure"] = capture_window_message
                for target_handle in window_handles:
                    try:
                        old_procedure = win32gui.SetWindowLong(
                            target_handle,
                            win32con.GWL_WNDPROC,
                            capture_hook["procedure"],
                        )
                        if old_procedure:
                            capture_hook["hooks"][
                                target_handle
                            ] = old_procedure
                    except Exception:
                        continue
            except Exception:
                capture_hook["hooks"].clear()
                capture_hook["procedure"] = None

        def restore_capture_mouse_hook() -> None:
            if win32gui is not None and win32con is not None:
                for window_handle, old_procedure in reversed(
                    list(capture_hook["hooks"].items())
                ):
                    try:
                        if win32gui.IsWindow(window_handle):
                            win32gui.SetWindowLong(
                                window_handle,
                                win32con.GWL_WNDPROC,
                                old_procedure,
                            )
                    except Exception:
                        continue
            capture_hook["hooks"].clear()
            capture_hook["procedure"] = None

        def close_window() -> None:
            restore_capture_mouse_hook()
            window.destroy()

        # 等待 Windows 创建真正的 TkTopLevel 外壳后再安装侧键监听。
        window.after(100, install_capture_mouse_hook)
        window.protocol("WM_DELETE_WINDOW", close_window)

        def restore_defaults() -> None:
            for action_id, variables in shortcut_variables.items():
                defaults = DEFAULT_SHORTCUTS[action_id]
                variables[0].set(defaults[0])
                variables[1].set(defaults[1])
                shortcut_buttons[action_id][0].configure(text=defaults[0])
                shortcut_buttons[action_id][1].configure(text=defaults[1])
            capture_state["variable"] = None
            capture_state["button"] = None
            capture_state["modifiers"].clear()
            long_press_variable.set(DEFAULT_LONG_PRESS_DELAY_MS)
            update_long_press_text()

        def save_shortcuts() -> None:
            proposed = {
                action_id: [variable.get() for variable in variables]
                for action_id, variables in shortcut_variables.items()
            }
            used = {}
            for action_id, shortcuts in proposed.items():
                for shortcut in shortcuts:
                    if shortcut == "无":
                        continue
                    if shortcut in used:
                        other_action = used[shortcut]
                        messagebox.showerror(
                            "快捷键冲突",
                            f"{shortcut} 同时分配给了：\n"
                            f"{ACTION_LABELS[other_action]}\n"
                            f"{ACTION_LABELS[action_id]}\n\n"
                            "请修改其中一个快捷键。",
                            parent=window,
                        )
                        return
                    used[shortcut] = action_id

            self.shortcuts = proposed
            self.long_press_delay_ms = self._normalize_long_press_delay(
                long_press_variable.get()
            )
            self._bind_shortcuts()
            self._refresh_shortcut_button_text()
            self._save_settings()
            self.status_var.set("快捷键设置已保存")
            close_window()

        button_row = ttk.Frame(outer)
        button_row.grid(
            row=4 + len(ACTION_DEFINITIONS),
            column=0,
            columnspan=3,
            sticky=tk.E,
            pady=(15, 0),
        )
        ttk.Button(
            button_row, text="恢复默认", command=restore_defaults
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            button_row,
            text="保存设置",
            style="Accent.TButton",
            command=save_shortcuts,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            button_row, text="取消", command=close_window
        ).pack(side=tk.LEFT, padx=4)

    @staticmethod
    def _modifier_name(keysym: str) -> Optional[str]:
        return {
            "Control_L": "Ctrl",
            "Control_R": "Ctrl",
            "Shift_L": "Shift",
            "Shift_R": "Shift",
            "Alt_L": "Alt",
            "Alt_R": "Alt",
            "Meta_L": "Alt",
            "Meta_R": "Alt",
        }.get(keysym)

    @classmethod
    def _captured_shortcut_with_modifiers(
        cls,
        keysym: str,
        modifiers,
    ) -> Optional[str]:
        key = cls._captured_shortcut_name(keysym, 0)
        if key is None:
            return None
        ordered_modifiers = [
            modifier
            for modifier in ("Ctrl", "Shift", "Alt")
            if modifier in modifiers
        ]
        return "+".join(ordered_modifiers + [key])

    @staticmethod
    def _captured_shortcut_name(keysym: str, state: int) -> Optional[str]:
        modifier_keys = {
            "Control_L",
            "Control_R",
            "Shift_L",
            "Shift_R",
            "Alt_L",
            "Alt_R",
            "Meta_L",
            "Meta_R",
        }
        if keysym in modifier_keys:
            return None

        key_names = {
            "space": "Space",
            "Return": "Enter",
            "BackSpace": "Backspace",
            "Delete": "Delete",
            "Tab": "Tab",
            "Escape": "Escape",
            "Left": "Left",
            "Right": "Right",
            "Up": "Up",
            "Down": "Down",
            "Prior": "PageUp",
            "Next": "PageDown",
            "Home": "Home",
            "End": "End",
            "Insert": "Insert",
        }
        key = key_names.get(keysym)
        if key is None:
            key = keysym.upper() if len(keysym) == 1 else keysym

        modifiers = []
        if state & 0x0004:
            modifiers.append("Ctrl")
        if state & 0x0001:
            modifiers.append("Shift")
        if state & 0x0008:
            modifiers.append("Alt")
        return "+".join(modifiers + [key])

    # ----------------------------- IoU 传播 -----------------------------

    def open_iou_dialog(self) -> None:
        if len(self.images) < 2:
            messagebox.showinfo("IoU 传播", "当前文件夹至少需要两张图片。")
            return

        window = tk.Toplevel(self.root)
        window.title("任意两图 IoU 关键点传播")
        window.geometry("650x470")
        window.transient(self.root)
        window.grab_set()

        names = [path.name for path in self.images]
        source_default = names[max(self.current_image_index, 0)]
        target_default = names[min(self.current_image_index + 1, len(names) - 1)]
        if source_default == target_default:
            source_default = names[max(0, len(names) - 2)]

        source_var = tk.StringVar(value=source_default)
        target_var = tk.StringVar(value=target_default)
        threshold_var = tk.DoubleVar(
            value=float(self.settings.get("iou_threshold", 0.5))
        )
        overwrite_var = tk.BooleanVar(value=False)
        result_var = tk.StringVar(value="先点击“预览匹配”，确认数量后再应用。")
        preview_state = {}

        form = ttk.Frame(window, padding=18)
        form.pack(fill=tk.BOTH, expand=True)
        ttk.Label(form, text="源图片（已有关键点）").grid(
            row=0, column=0, sticky=tk.W, pady=6
        )
        ttk.Combobox(
            form, textvariable=source_var, values=names, state="readonly", width=43
        ).grid(row=0, column=1, sticky=tk.EW, pady=6)
        ttk.Label(form, text="目标图片（接收关键点）").grid(
            row=1, column=0, sticky=tk.W, pady=6
        )
        ttk.Combobox(
            form, textvariable=target_var, values=names, state="readonly", width=43
        ).grid(row=1, column=1, sticky=tk.EW, pady=6)
        ttk.Label(form, text="IoU 阈值").grid(
            row=2, column=0, sticky=tk.W, pady=6
        )
        ttk.Spinbox(
            form,
            textvariable=threshold_var,
            from_=0.0,
            to=1.0,
            increment=0.05,
            width=12,
        ).grid(row=2, column=1, sticky=tk.W, pady=6)
        ttk.Checkbutton(
            form,
            text="覆盖目标框已有的同名关键点（默认不覆盖）",
            variable=overwrite_var,
        ).grid(row=3, column=1, sticky=tk.W, pady=6)

        result = ttk.Label(
            form,
            textvariable=result_var,
            justify=tk.LEFT,
            anchor=tk.NW,
            relief=tk.GROOVE,
            padding=12,
        )
        result.grid(row=4, column=0, columnspan=2, sticky=tk.NSEW, pady=(14, 10))
        form.columnconfigure(1, weight=1)
        form.rowconfigure(4, weight=1)

        def selected_paths():
            if source_var.get() == target_var.get():
                raise ValueError("源图片和目标图片不能相同")
            source_path = self.folder / source_var.get()
            target_path = self.folder / target_var.get()
            threshold = float(threshold_var.get())
            if not 0.0 <= threshold <= 1.0:
                raise ValueError("IoU 阈值必须在 0 到 1 之间")
            return source_path, target_path, threshold

        def preview() -> Optional[Dict]:
            try:
                source_path, target_path, threshold = selected_paths()
                plan = build_transfer_plan(
                    self._get_document(source_path),
                    self._get_document(target_path),
                    threshold,
                    overwrite_same_label=overwrite_var.get(),
                )
            except (ValueError, OSError) as error:
                messagebox.showerror("无法预览", str(error), parent=window)
                return None
            signature = (
                source_path,
                target_path,
                threshold,
                overwrite_var.get(),
            )
            preview_state["signature"] = signature
            preview_state["plan"] = plan
            result_var.set(
                f"源框：{plan['source_rectangle_count']}    "
                f"目标框：{plan['target_rectangle_count']}\n"
                f"达到阈值的一对一匹配框：{len(plan['matches'])}\n"
                f"源图已关联关键点：{plan['source_keypoint_count']}\n"
                f"准备写入目标图的关键点：{len(plan['actions'])}\n"
                f"因目标已有同名点而跳过：{plan['skipped_existing']}\n"
                f"IoU：最小 {plan['minimum_iou']:.3f} / "
                f"平均 {plan['average_iou']:.3f} / "
                f"最大 {plan['maximum_iou']:.3f}"
            )
            return plan

        def apply() -> None:
            try:
                source_path, target_path, threshold = selected_paths()
            except ValueError as error:
                messagebox.showerror("无法应用", str(error), parent=window)
                return
            signature = (
                source_path,
                target_path,
                threshold,
                overwrite_var.get(),
            )
            if preview_state.get("signature") != signature:
                plan = preview()
                if plan is None:
                    return
            else:
                plan = preview_state["plan"]
            if not plan["actions"]:
                messagebox.showinfo(
                    "没有可传播内容",
                    "当前条件下没有需要写入的关键点。",
                    parent=window,
                )
                return
            if not messagebox.askyesno(
                "确认应用",
                f"将 {len(plan['actions'])} 个关键点写入\n{target_path.name}\n继续？",
                parent=window,
            ):
                return

            if target_path == self._current_image_path():
                self._push_undo(target_path)
            target_document = self._get_document(target_path)
            applied = apply_transfer_plan(
                target_document, plan, overwrite_same_label=overwrite_var.get()
            )
            self.dirty_images.add(target_path)
            try:
                save_document(target_path, target_document)
                self.dirty_images.discard(target_path)
            except OSError as error:
                messagebox.showerror("保存失败", str(error), parent=window)
                return

            self.settings["iou_threshold"] = threshold
            self._save_settings()
            result_var.set(
                f"已完成：向 {target_path.name} 写入 {applied} 个关键点。\n"
                "原 JSON 已保存在同目录的 .json.bak 文件中。"
            )
            self.status_var.set(f"IoU 传播完成：{applied} 个关键点")
            if target_path == self._current_image_path():
                self._refresh_all()

        button_row = ttk.Frame(form)
        button_row.grid(row=5, column=0, columnspan=2, sticky=tk.E, pady=4)
        ttk.Button(button_row, text="预览匹配", command=preview).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(
            button_row, text="应用到目标图", style="Accent.TButton", command=apply
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="关闭", command=window.destroy).pack(
            side=tk.LEFT, padx=4
        )

    # ----------------------------- 显示切换与退出 -----------------------------

    def toggle_symmetry(self) -> None:
        self.symmetry_enabled.set(not self.symmetry_enabled.get())
        self._on_symmetry_toggle()

    def _on_symmetry_toggle(self) -> None:
        state = "开启" if self.symmetry_enabled.get() else "关闭"
        self.status_var.set(f"中心对称已{state}")
        self._save_settings()

    def _on_symmetry_ratio_changed(self, value) -> None:
        ratio = min(2.0, max(0.0, float(value)))
        self.symmetry_ratio_text.set(f"{ratio * 100:.0f}%")
        if hasattr(self, "detail_canvas"):
            self._draw_detail()

    def _on_symmetry_ratio_released(self, _event) -> None:
        ratio = min(2.0, max(0.0, self.symmetry_ratio.get()))
        self.symmetry_ratio.set(ratio)
        self.symmetry_ratio_text.set(f"{ratio * 100:.0f}%")
        self._save_settings()
        self.status_var.set(
            f"自动尾点对称比例已设为 {ratio * 100:.0f}%"
        )

    def toggle_label_names(self) -> None:
        self.show_label_names.set(not self.show_label_names.get())
        self._refresh_all()

    def toggle_box_class_labels(self) -> None:
        visible = not self.show_box_class_labels.get()
        self.show_box_class_labels.set(visible)
        self.status_var.set(
            f"检测框类别名称已{'显示' if visible else '隐藏'}"
        )
        self._save_settings()
        self._refresh_all()

    def toggle_track_ids(self) -> None:
        visible = not self.show_track_ids.get()
        self.show_track_ids.set(visible)
        self.status_var.set(f"检测框 ID 已{'显示' if visible else '隐藏'}")
        self._refresh_all()

    def toggle_other_boxes(self) -> None:
        self.show_other_boxes.set(not self.show_other_boxes.get())
        self._refresh_all()

    def toggle_continuous_other_boxes(self) -> None:
        visible = not self.show_other_boxes.get()
        self.show_other_boxes.set(visible)
        self.status_var.set(
            f"全部检测框（含当前框）已{'显示' if visible else '隐藏'}；"
            "关键点保持显示"
        )
        self._refresh_all()

    def _on_close(self) -> None:
        self._cancel_pointer_press()
        self.rectangle_creation = None
        if self.rectangle_drag is not None:
            self._finish_rectangle_drag()
        self._auto_confirm_viewed_rectangle()
        self._cancel_autosave()
        self._stop_keyboard_polling()
        self._stop_windows_keyboard_hook()
        self.save_all()
        self._save_settings()
        self._restore_native_mouse_buttons()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    initial_folder = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    BeeKeypointAnnotator(root, initial_folder=initial_folder)
    root.mainloop()


if __name__ == "__main__":
    main()
