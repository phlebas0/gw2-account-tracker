from __future__ import annotations

import csv
import json
import os
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Iterable

import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tracker_core import copper_to_str, make_engine


APP_TITLE = "GW2 Account Tracker"
APP_VERSION = "0.1"
CONFIG_PATH = Path.home() / ".gw2_account_tracker_local.json"
ITEMS_API = "https://api.guildwars2.com/v2/items"
TP_TAX = 0.15

WIN_GREY = "#c0c0c0"
WIN_DARK_GREY = "#808080"
WIN_LIGHT = "#ffffff"
WIN_NAVY = "#000080"
WIN_TEXT = "#000000"
WIN_FIELD = "#ffffff"
WIN_SELECTION = "#000080"
WIN_SELECTION_TEXT = "#ffffff"


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def signed_coin(value: Any) -> str:
    value = safe_int(value)
    if value > 0:
        return "+" + copper_to_str(value)
    return copper_to_str(value)


def display_date(value: Any, include_time: bool = False) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, datetime):
        if include_time:
            return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def parse_ymd(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def utc_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def load_dotenv_database_url() -> str | None:
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"):
        if not candidate.exists():
            continue
        try:
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == "DATABASE_URL":
                    return value.strip().strip('"').strip("'")
        except OSError:
            continue
    return None


def load_saved_database_url() -> str | None:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            value = str(data.get("database_url") or "").strip()
            return value or None
    except (OSError, json.JSONDecodeError):
        return None
    return None


def save_database_url(url: str | None) -> None:
    if not url:
        try:
            CONFIG_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return
    CONFIG_PATH.write_text(json.dumps({"database_url": url}, indent=2), encoding="utf-8")


def initial_database_url() -> str | None:
    return (
        os.getenv("DATABASE_URL", "").strip()
        or load_dotenv_database_url()
        or load_saved_database_url()
        or None
    )


def preset_dates(label: str) -> tuple[date | None, date | None]:
    today = datetime.now(timezone.utc).date()
    days = {
        "7 days": 7,
        "30 days": 30,
        "90 days": 90,
        "365 days": 365,
    }.get(label)
    if days is None:
        return None, None
    return today - timedelta(days=days - 1), today


# -----------------------------------------------------------------------------
# data access
# -----------------------------------------------------------------------------


class DatabaseClient:
    def __init__(self, engine: Engine):
        self.engine = engine

    def test(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1")).scalar_one()

    def rows(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            return [dict(row._mapping) for row in result]

    def row(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.rows(sql, params)
        return rows[0] if rows else None


class ItemNameResolver:
    def __init__(self):
        self.cache: dict[int, str] = {}
        self.lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "gw2-account-tracker-local/0.1"})

    def get_many(self, item_ids: Iterable[int]) -> dict[int, str]:
        ids = list(dict.fromkeys(safe_int(i) for i in item_ids if i is not None))
        with self.lock:
            missing = [i for i in ids if i not in self.cache]

        for start in range(0, len(missing), 200):
            chunk = missing[start : start + 200]
            if not chunk:
                continue
            found: dict[int, str] = {}
            try:
                response = self.session.get(
                    ITEMS_API,
                    params={"ids": ",".join(map(str, chunk))},
                    timeout=15,
                )
                response.raise_for_status()
                for item in response.json():
                    item_id = safe_int(item.get("id"))
                    found[item_id] = str(item.get("name") or f"Item {item_id}")
            except Exception:
                pass

            with self.lock:
                for item_id in chunk:
                    self.cache[item_id] = found.get(item_id, f"Item {item_id}")

        with self.lock:
            return {i: self.cache.get(i, f"Item {i}") for i in ids}

    def clear(self) -> None:
        with self.lock:
            self.cache.clear()


# -----------------------------------------------------------------------------
# classic UI components
# -----------------------------------------------------------------------------


class MetricBox(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str, width: int = 150):
        super().__init__(
            parent,
            bg=WIN_GREY,
            bd=2,
            relief="groove",
            padx=7,
            pady=6,
            width=width,
            height=62,
        )
        self.pack_propagate(False)
        self.title_label = tk.Label(
            self,
            text=title,
            bg=WIN_GREY,
            fg=WIN_TEXT,
            anchor="w",
            font=("MS Sans Serif", 8),
        )
        self.title_label.pack(fill="x")
        self.value_label = tk.Label(
            self,
            text="—",
            bg=WIN_GREY,
            fg=WIN_TEXT,
            anchor="w",
            font=("MS Sans Serif", 11, "bold"),
        )
        self.value_label.pack(fill="x", pady=(2, 0))
        self.delta_label = tk.Label(
            self,
            text="",
            bg=WIN_GREY,
            fg=WIN_TEXT,
            anchor="w",
            font=("MS Sans Serif", 8),
        )
        self.delta_label.pack(fill="x")

    def set(self, value: str, delta: str = "") -> None:
        self.value_label.configure(text=value)
        self.delta_label.configure(text=delta)


class SimpleLineChart(tk.Canvas):
    """Small dependency-free chart for the desktop client."""

    def __init__(self, parent: tk.Misc, **kwargs: Any):
        super().__init__(
            parent,
            bg=WIN_FIELD,
            highlightbackground=WIN_DARK_GREY,
            highlightthickness=1,
            bd=1,
            relief="sunken",
            **kwargs,
        )
        self.values: list[float] = []
        self.labels: list[str] = []
        self.title = ""
        self.unit = ""
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_data(
        self,
        values: Iterable[Any],
        labels: Iterable[str] | None = None,
        title: str = "",
        unit: str = "",
    ) -> None:
        vals: list[float] = []
        labs: list[str] = []
        raw_labels = list(labels or [])
        for idx, value in enumerate(values):
            if value is None:
                continue
            try:
                vals.append(float(value))
                labs.append(raw_labels[idx] if idx < len(raw_labels) else "")
            except (TypeError, ValueError):
                continue
        self.values = vals
        self.labels = labs
        self.title = title
        self.unit = unit
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 100)
        height = max(self.winfo_height(), 80)
        pad_left, pad_right, pad_top, pad_bottom = 58, 14, 24, 28

        self.create_text(
            8,
            6,
            anchor="nw",
            text=self.title,
            fill=WIN_TEXT,
            font=("MS Sans Serif", 8, "bold"),
        )

        if len(self.values) < 2:
            self.create_text(
                width / 2,
                height / 2,
                text="not enough history yet",
                fill=WIN_DARK_GREY,
                font=("MS Sans Serif", 8),
            )
            return

        plot_w = max(width - pad_left - pad_right, 10)
        plot_h = max(height - pad_top - pad_bottom, 10)
        lo = min(self.values)
        hi = max(self.values)
        if hi == lo:
            hi = lo + 1

        self.create_line(pad_left, pad_top, pad_left, pad_top + plot_h, fill="#404040")
        self.create_line(
            pad_left,
            pad_top + plot_h,
            pad_left + plot_w,
            pad_top + plot_h,
            fill="#404040",
        )

        for frac in (0.0, 0.5, 1.0):
            y = pad_top + plot_h * (1 - frac)
            value = lo + (hi - lo) * frac
            self.create_line(pad_left - 3, y, pad_left, y, fill="#404040")
            suffix = f" {self.unit}" if self.unit else ""
            self.create_text(
                pad_left - 6,
                y,
                anchor="e",
                text=f"{value:,.1f}{suffix}",
                fill=WIN_TEXT,
                font=("MS Sans Serif", 7),
            )

        points: list[float] = []
        n = len(self.values)
        for idx, value in enumerate(self.values):
            x = pad_left + (idx / (n - 1)) * plot_w
            y = pad_top + (1 - (value - lo) / (hi - lo)) * plot_h
            points.extend([x, y])
        self.create_line(*points, fill=WIN_NAVY, width=2)

        if self.labels:
            for idx in (0, len(self.labels) - 1):
                x = pad_left if idx == 0 else pad_left + plot_w
                anchor = "sw" if idx == 0 else "se"
                self.create_text(
                    x,
                    height - 4,
                    anchor=anchor,
                    text=self.labels[idx],
                    fill=WIN_TEXT,
                    font=("MS Sans Serif", 7),
                )


def make_tree(
    parent: tk.Misc,
    columns: list[tuple[str, str, int, str]],
    height: int = 12,
) -> tuple[ttk.Frame, ttk.Treeview]:
    frame = ttk.Frame(parent)
    ids = [c[0] for c in columns]
    tree = ttk.Treeview(frame, columns=ids, show="headings", height=height)
    ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)

    for col_id, title, width, anchor in columns:
        tree.heading(col_id, text=title)
        tree.column(col_id, width=width, minwidth=45, anchor=anchor, stretch=True)

    tree.grid(row=0, column=0, sticky="nsew")
    ybar.grid(row=0, column=1, sticky="ns")
    xbar.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return frame, tree


def clear_tree(tree: ttk.Treeview) -> None:
    children = tree.get_children()
    if children:
        tree.delete(*children)


# -----------------------------------------------------------------------------
# connection dialog
# -----------------------------------------------------------------------------


class ConnectionDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, initial: str = ""):
        super().__init__(parent)
        self.title("Database Connection")
        self.configure(bg=WIN_GREY)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: tuple[str, bool] | None = None

        outer = tk.Frame(self, bg=WIN_GREY, bd=2, relief="raised", padx=10, pady=10)
        outer.pack(fill="both", expand=True, padx=4, pady=4)

        tk.Label(
            outer,
            text="Supabase PostgreSQL connection string:",
            bg=WIN_GREY,
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        self.url_var = tk.StringVar(value=initial)
        self.url_entry = tk.Entry(
            outer,
            textvariable=self.url_var,
            width=74,
            relief="sunken",
            bd=2,
            bg=WIN_FIELD,
        )
        self.url_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 8))

        self.remember_var = tk.BooleanVar(value=bool(initial))
        tk.Checkbutton(
            outer,
            text="remember this connection on this computer",
            variable=self.remember_var,
            bg=WIN_GREY,
            activebackground=WIN_GREY,
            anchor="w",
        ).grid(row=2, column=0, columnspan=2, sticky="w")

        warning = (
            "saved connections are stored in your home folder as plain text. "
            "leave this unticked if you do not want the database password stored locally."
        )
        tk.Label(
            outer,
            text=warning,
            wraplength=520,
            justify="left",
            bg=WIN_GREY,
            fg="#404040",
            font=("MS Sans Serif", 8),
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(3, 10))

        buttons = tk.Frame(outer, bg=WIN_GREY)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e")
        tk.Button(buttons, text="OK", width=10, command=self.accept).pack(side="left", padx=3)
        tk.Button(buttons, text="Cancel", width=10, command=self.destroy).pack(side="left", padx=3)

        self.bind("<Return>", lambda _e: self.accept())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.url_entry.focus_set()
        self.update_idletasks()
        self.geometry(f"+{parent.winfo_rootx()+80}+{parent.winfo_rooty()+80}")

    def accept(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Database Connection", "Enter a DATABASE_URL.", parent=self)
            return
        self.result = (url, bool(self.remember_var.get()))
        self.destroy()


# -----------------------------------------------------------------------------
# application
# -----------------------------------------------------------------------------


class GW2LocalApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1220x780")
        self.minsize(980, 640)
        self.configure(bg=WIN_GREY)

        self.db: DatabaseClient | None = None
        self.engine: Engine | None = None
        self.item_names = ItemNameResolver()
        self.latest: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.current_pl_rows: list[dict[str, Any]] = []
        self.current_tx_rows: list[dict[str, Any]] = []
        self.busy_count = 0

        self._build_style()
        self._build_menu()
        self._build_shell()
        self._build_tabs()

        self.after(150, self._startup_connect)

    # ------------------------------------------------------------------ style

    def _build_style(self) -> None:
        self.option_add("*Font", ("MS Sans Serif", 8))
        self.option_add("*Background", WIN_GREY)
        self.option_add("*Foreground", WIN_TEXT)
        self.option_add("*Entry.Background", WIN_FIELD)
        self.option_add("*Text.Background", WIN_FIELD)
        self.option_add("*Listbox.Background", WIN_FIELD)

        style = ttk.Style(self)
        try:
            style.theme_use("classic")
        except tk.TclError:
            pass

        style.configure(".", background=WIN_GREY, foreground=WIN_TEXT, font=("MS Sans Serif", 8))
        style.configure("TFrame", background=WIN_GREY)
        style.configure("TLabel", background=WIN_GREY, foreground=WIN_TEXT)
        style.configure("TLabelframe", background=WIN_GREY, borderwidth=2, relief="groove")
        style.configure("TLabelframe.Label", background=WIN_GREY, foreground=WIN_TEXT)
        style.configure("TNotebook", background=WIN_GREY, borderwidth=2)
        style.configure("TNotebook.Tab", background=WIN_GREY, padding=(8, 3), borderwidth=2)
        style.map("TNotebook.Tab", background=[("selected", WIN_GREY)])
        style.configure(
            "Treeview",
            background=WIN_FIELD,
            fieldbackground=WIN_FIELD,
            foreground=WIN_TEXT,
            rowheight=20,
            borderwidth=1,
            relief="sunken",
        )
        style.map(
            "Treeview",
            background=[("selected", WIN_SELECTION)],
            foreground=[("selected", WIN_SELECTION_TEXT)],
        )
        style.configure("Treeview.Heading", background=WIN_GREY, relief="raised", font=("MS Sans Serif", 8, "bold"))
        style.configure("TCombobox", fieldbackground=WIN_FIELD, background=WIN_GREY)

    # ------------------------------------------------------------------ shell

    def _build_menu(self) -> None:
        menubar = tk.Menu(self, tearoff=False)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Refresh\tF5", command=self.refresh_all)
        file_menu.add_separator()
        file_menu.add_command(label="Export P/L CSV...", command=self.export_pl_csv)
        file_menu.add_command(label="Export Transactions CSV...", command=self.export_tx_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        self.view_menu = view_menu
        menubar.add_cascade(label="View", menu=view_menu)

        tools_menu = tk.Menu(menubar, tearoff=False)
        tools_menu.add_command(label="Database connection...", command=self.change_connection)
        tools_menu.add_command(label="Clear item-name cache", command=self._clear_item_cache)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)
        self.bind("<F5>", lambda _e: self.refresh_all())
        self.bind("<Control-e>", lambda _e: self.export_pl_csv())

    def _build_shell(self) -> None:
        titlebar = tk.Frame(self, bg=WIN_NAVY, bd=1, relief="raised", height=28)
        titlebar.pack(fill="x", padx=3, pady=(3, 0))
        titlebar.pack_propagate(False)
        tk.Label(
            titlebar,
            text=" GW2 Account Tracker",
            bg=WIN_NAVY,
            fg="white",
            font=("MS Sans Serif", 9, "bold"),
            anchor="w",
        ).pack(side="left", fill="both", expand=True)
        self.header_status = tk.Label(
            titlebar,
            text="not connected ",
            bg=WIN_NAVY,
            fg="white",
            anchor="e",
            font=("MS Sans Serif", 8),
        )
        self.header_status.pack(side="right")

        self.main = ttk.Frame(self)
        self.main.pack(fill="both", expand=True, padx=4, pady=4)

        self.status_var = tk.StringVar(value="Ready")
        status = tk.Label(
            self,
            textvariable=self.status_var,
            bg=WIN_GREY,
            fg=WIN_TEXT,
            bd=2,
            relief="sunken",
            anchor="w",
            padx=5,
            height=1,
        )
        status.pack(fill="x", side="bottom", padx=3, pady=(0, 3))

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self.main)
        self.notebook.pack(fill="both", expand=True)

        self.overview_tab = ttk.Frame(self.notebook)
        self.wealth_tab = ttk.Frame(self.notebook)
        self.progression_tab = ttk.Frame(self.notebook)
        self.wallet_tab = ttk.Frame(self.notebook)
        self.characters_tab = ttk.Frame(self.notebook)
        self.trading_tab = ttk.Frame(self.notebook)
        self.health_tab = ttk.Frame(self.notebook)

        tabs = [
            (self.overview_tab, "Overview"),
            (self.wealth_tab, "Wealth"),
            (self.progression_tab, "Progression"),
            (self.wallet_tab, "Wallet"),
            (self.characters_tab, "Characters"),
            (self.trading_tab, "Trading Post"),
            (self.health_tab, "Collector"),
        ]
        for index, (frame, label) in enumerate(tabs):
            self.notebook.add(frame, text=label)
            self.view_menu.add_command(label=label, command=lambda i=index: self.notebook.select(i))

        self._build_overview()
        self._build_wealth()
        self._build_progression()
        self._build_wallet()
        self._build_characters()
        self._build_trading()
        self._build_health()

    # --------------------------------------------------------------- connection

    def _startup_connect(self) -> None:
        url = initial_database_url()
        if url:
            self.connect_database(url, quiet=False)
        else:
            self.change_connection()

    def change_connection(self) -> None:
        existing = initial_database_url() or ""
        dialog = ConnectionDialog(self, existing)
        self.wait_window(dialog)
        if dialog.result:
            url, remember = dialog.result
            if self.connect_database(url, quiet=False):
                if remember:
                    try:
                        save_database_url(url)
                    except OSError as exc:
                        messagebox.showwarning(APP_TITLE, f"Connected, but could not save the connection:\n{exc}")
                else:
                    save_database_url(None)

    def connect_database(self, url: str, quiet: bool = False) -> bool:
        self.set_status("Connecting to Supabase...")
        try:
            engine = make_engine(url)
            db = DatabaseClient(engine)
            db.test()
        except Exception as exc:
            self.header_status.configure(text="not connected ")
            self.set_status("Database connection failed")
            if not quiet:
                messagebox.showerror(APP_TITLE, f"Could not connect to the database.\n\n{exc}")
            return False

        if self.engine is not None:
            try:
                self.engine.dispose()
            except Exception:
                pass
        self.engine = engine
        self.db = db
        self.header_status.configure(text="connected ")
        self.set_status("Connected")
        self.refresh_all()
        return True

    # ------------------------------------------------------------- async helper

    def run_async(
        self,
        status: str,
        work: Callable[[], Any],
        done: Callable[[Any], None],
    ) -> None:
        if self.db is None:
            return
        self.busy_count += 1
        self.set_status(status)

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                self.after(0, lambda: self._async_error(exc))
                return
            self.after(0, lambda: self._async_done(result, done))

        threading.Thread(target=runner, daemon=True).start()

    def _async_done(self, result: Any, done: Callable[[Any], None]) -> None:
        self.busy_count = max(0, self.busy_count - 1)
        done(result)
        if self.busy_count == 0:
            self.set_status("Ready")

    def _async_error(self, exc: Exception) -> None:
        self.busy_count = max(0, self.busy_count - 1)
        self.set_status("Error")
        messagebox.showerror(APP_TITLE, str(exc))

    def set_status(self, text_value: str) -> None:
        self.status_var.set(text_value)
        self.update_idletasks()

    # ---------------------------------------------------------------- overview

    def _build_overview(self) -> None:
        top = ttk.Frame(self.overview_tab)
        top.pack(fill="x", padx=8, pady=(8, 4))
        self.ov_metrics: dict[str, MetricBox] = {}
        for key, title in [
            ("net", "Net worth"),
            ("gold", "Liquid gold"),
            ("ap", "Achievement points"),
            ("play", "Playtime"),
            ("skins", "Skins"),
        ]:
            box = MetricBox(top, title)
            box.pack(side="left", fill="x", expand=True, padx=3)
            self.ov_metrics[key] = box

        range_row = ttk.Frame(self.overview_tab)
        range_row.pack(fill="x", padx=10, pady=(4, 2))
        ttk.Label(range_row, text="History range:").pack(side="left")
        self.overview_range = tk.StringVar(value="90 days")
        combo = ttk.Combobox(
            range_row,
            textvariable=self.overview_range,
            values=["30 days", "90 days", "365 days", "All"],
            state="readonly",
            width=12,
        )
        combo.pack(side="left", padx=(4, 0))
        combo.bind("<<ComboboxSelected>>", lambda _e: self.populate_history_charts())

        self.overview_chart = SimpleLineChart(self.overview_tab, height=230)
        self.overview_chart.pack(fill="both", expand=True, padx=10, pady=5)

        lower = ttk.Frame(self.overview_tab)
        lower.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        lower.columnconfigure(0, weight=1)
        lower.columnconfigure(1, weight=1)
        lower.rowconfigure(0, weight=1)

        prog_box = ttk.LabelFrame(lower, text="Progression snapshot")
        prog_box.grid(row=0, column=0, sticky="nsew", padx=3)
        prog_frame, self.ov_progression_tree = make_tree(
            prog_box,
            [("metric", "Metric", 180, "w"), ("value", "Value", 120, "e")],
            height=8,
        )
        prog_frame.pack(fill="both", expand=True, padx=5, pady=5)

        coll_box = ttk.LabelFrame(lower, text="Collections")
        coll_box.grid(row=0, column=1, sticky="nsew", padx=3)
        coll_frame, self.ov_collections_tree = make_tree(
            coll_box,
            [("name", "Collection", 180, "w"), ("value", "Unlocked", 120, "e")],
            height=8,
        )
        coll_frame.pack(fill="both", expand=True, padx=5, pady=5)

    def _build_wealth(self) -> None:
        top = ttk.Frame(self.wealth_tab)
        top.pack(fill="x", padx=8, pady=8)
        self.wealth_metrics: dict[str, MetricBox] = {}
        for key, title in [
            ("net", "Net worth"),
            ("items", "Tradable items"),
            ("buys", "Buy orders committed"),
            ("sells", "Sell orders net"),
        ]:
            box = MetricBox(top, title, width=180)
            box.pack(side="left", fill="x", expand=True, padx=3)
            self.wealth_metrics[key] = box

        body = ttk.Panedwindow(self.wealth_tab, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        components_box = ttk.LabelFrame(body, text="Current wealth")
        comp_frame, self.wealth_tree = make_tree(
            components_box,
            [("component", "Component", 190, "w"), ("value", "Value", 140, "e")],
            height=14,
        )
        comp_frame.pack(fill="both", expand=True, padx=5, pady=5)
        body.add(components_box, weight=1)

        history_box = ttk.LabelFrame(body, text="History")
        history_top = ttk.Frame(history_box)
        history_top.pack(fill="x", padx=5, pady=(5, 0))
        ttk.Label(history_top, text="Series:").pack(side="left")
        self.wealth_series = tk.StringVar(value="Net worth")
        series_combo = ttk.Combobox(
            history_top,
            textvariable=self.wealth_series,
            values=["Net worth", "Tradable items", "Liquid gold"],
            state="readonly",
            width=18,
        )
        series_combo.pack(side="left", padx=4)
        series_combo.bind("<<ComboboxSelected>>", lambda _e: self.populate_history_charts())
        self.wealth_chart = SimpleLineChart(history_box, height=320)
        self.wealth_chart.pack(fill="both", expand=True, padx=5, pady=5)
        body.add(history_box, weight=2)

    def _build_progression(self) -> None:
        top = ttk.Frame(self.progression_tab)
        top.pack(fill="x", padx=8, pady=8)
        self.prog_metrics: dict[str, MetricBox] = {}
        for key, title in [
            ("ap", "AP"),
            ("mastery", "Mastery earned"),
            ("fractal", "Fractal level"),
            ("wvw", "WvW rank"),
            ("pvp", "PvP rank"),
        ]:
            box = MetricBox(top, title)
            box.pack(side="left", fill="x", expand=True, padx=3)
            self.prog_metrics[key] = box

        chart_box = ttk.LabelFrame(self.progression_tab, text="Progression history")
        chart_box.pack(fill="both", expand=True, padx=8, pady=(0, 5))
        row = ttk.Frame(chart_box)
        row.pack(fill="x", padx=5, pady=(5, 0))
        ttk.Label(row, text="Metric:").pack(side="left")
        self.prog_series = tk.StringVar(value="Achievement points")
        combo = ttk.Combobox(
            row,
            textvariable=self.prog_series,
            values=["Achievement points", "Mastery earned", "Skins", "WvW rank", "PvP rank"],
            state="readonly",
            width=22,
        )
        combo.pack(side="left", padx=4)
        combo.bind("<<ComboboxSelected>>", lambda _e: self.populate_history_charts())
        self.prog_chart = SimpleLineChart(chart_box, height=230)
        self.prog_chart.pack(fill="both", expand=True, padx=5, pady=5)

        lower = ttk.Frame(self.progression_tab)
        lower.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        lower.columnconfigure(0, weight=2)
        lower.columnconfigure(1, weight=1)

        mastery_box = ttk.LabelFrame(lower, text="Mastery points by region")
        mastery_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        mast_frame, self.mastery_tree = make_tree(
            mastery_box,
            [
                ("region", "Region", 180, "w"),
                ("earned", "Earned", 90, "e"),
                ("spent", "Spent", 90, "e"),
                ("unspent", "Unspent", 90, "e"),
            ],
            height=7,
        )
        mast_frame.pack(fill="both", expand=True, padx=5, pady=5)

        pvp_box = ttk.LabelFrame(lower, text="PvP record")
        pvp_box.grid(row=0, column=1, sticky="nsew")
        self.pvp_record_var = tk.StringVar(value="—")
        tk.Label(
            pvp_box,
            textvariable=self.pvp_record_var,
            bg=WIN_GREY,
            justify="left",
            anchor="nw",
            font=("MS Sans Serif", 10),
        ).pack(fill="both", expand=True, padx=12, pady=12)

    def _build_wallet(self) -> None:
        filters = ttk.Frame(self.wallet_tab)
        filters.pack(fill="x", padx=8, pady=8)
        ttk.Label(filters, text="Filter currencies:").pack(side="left")
        self.wallet_filter = tk.StringVar()
        entry = tk.Entry(filters, textvariable=self.wallet_filter, width=36, relief="sunken", bd=2)
        entry.pack(side="left", padx=5)
        self.wallet_filter.trace_add("write", lambda *_args: self.populate_wallet_table())

        frame, self.wallet_tree = make_tree(
            self.wallet_tab,
            [
                ("currency", "Currency", 260, "w"),
                ("balance", "Balance", 150, "e"),
                ("change", "Change", 150, "e"),
            ],
            height=25,
        )
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.wallet_rows: list[dict[str, Any]] = []

    def _build_characters(self) -> None:
        filters = ttk.Frame(self.characters_tab)
        filters.pack(fill="x", padx=8, pady=8)
        ttk.Label(filters, text="Find character:").pack(side="left")
        self.character_filter = tk.StringVar()
        entry = tk.Entry(filters, textvariable=self.character_filter, width=36, relief="sunken", bd=2)
        entry.pack(side="left", padx=5)
        self.character_filter.trace_add("write", lambda *_args: self.populate_character_table())

        frame, self.character_tree = make_tree(
            self.characters_tab,
            [
                ("name", "Character", 180, "w"),
                ("race", "Race", 90, "w"),
                ("profession", "Profession", 110, "w"),
                ("level", "Level", 60, "e"),
                ("playtime", "Playtime", 110, "e"),
                ("deaths", "Deaths", 80, "e"),
                ("created", "Created", 110, "center"),
            ],
            height=25,
        )
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.character_rows: list[dict[str, Any]] = []

    # --------------------------------------------------------------- trading UI

    def _build_trading(self) -> None:
        self.trading_notebook = ttk.Notebook(self.trading_tab)
        self.trading_notebook.pack(fill="both", expand=True, padx=6, pady=6)
        self.pl_tab = ttk.Frame(self.trading_notebook)
        self.tx_tab = ttk.Frame(self.trading_notebook)
        self.by_item_tab = ttk.Frame(self.trading_notebook)
        self.trading_notebook.add(self.pl_tab, text="Profit && Loss")
        self.trading_notebook.add(self.tx_tab, text="Transactions")
        self.trading_notebook.add(self.by_item_tab, text="By item")
        self._build_pl_tab()
        self._build_tx_tab()
        self._build_by_item_tab()

    def _make_date_filter_bar(self, parent: tk.Misc, prefix: str) -> dict[str, Any]:
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(bar, text="Period:").pack(side="left")
        period_var = tk.StringVar(value="30 days")
        period = ttk.Combobox(
            bar,
            textvariable=period_var,
            values=["7 days", "30 days", "90 days", "365 days", "All time", "Custom"],
            state="readonly",
            width=11,
        )
        period.pack(side="left", padx=(3, 8))

        ttk.Label(bar, text="From:").pack(side="left")
        from_var = tk.StringVar()
        from_entry = tk.Entry(bar, textvariable=from_var, width=11, relief="sunken", bd=2)
        from_entry.pack(side="left", padx=(3, 8))

        ttk.Label(bar, text="To:").pack(side="left")
        to_var = tk.StringVar()
        to_entry = tk.Entry(bar, textvariable=to_var, width=11, relief="sunken", bd=2)
        to_entry.pack(side="left", padx=(3, 8))

        ttk.Label(bar, text="Item:").pack(side="left")
        item_var = tk.StringVar()
        item_entry = tk.Entry(bar, textvariable=item_var, width=24, relief="sunken", bd=2)
        item_entry.pack(side="left", padx=(3, 8))

        widgets = {
            "frame": bar,
            "period": period_var,
            "from": from_var,
            "to": to_var,
            "item": item_var,
            "from_entry": from_entry,
            "to_entry": to_entry,
            "period_combo": period,
        }

        def sync_dates(*_args: Any) -> None:
            label = period_var.get()
            if label == "Custom":
                return
            start, end = preset_dates(label)
            from_var.set(start.isoformat() if start else "")
            to_var.set(end.isoformat() if end else "")

        period.bind("<<ComboboxSelected>>", sync_dates)
        sync_dates()
        return widgets

    def _build_pl_tab(self) -> None:
        self.pl_filters = self._make_date_filter_bar(self.pl_tab, "pl")
        bar = self.pl_filters["frame"]

        ttk.Label(bar, text="Result:").pack(side="left")
        self.pl_result = tk.StringVar(value="All")
        combo = ttk.Combobox(
            bar,
            textvariable=self.pl_result,
            values=["All", "Profit only", "Loss only", "Breakeven"],
            state="readonly",
            width=11,
        )
        combo.pack(side="left", padx=(3, 8))

        tk.Button(bar, text="Apply", width=8, command=self.refresh_pl).pack(side="left", padx=2)
        tk.Button(bar, text="Clear", width=8, command=self.clear_pl_filters).pack(side="left", padx=2)
        tk.Button(bar, text="Export...", width=9, command=self.export_pl_csv).pack(side="left", padx=2)

        metrics = ttk.Frame(self.pl_tab)
        metrics.pack(fill="x", padx=8, pady=4)
        self.pl_metrics: dict[str, MetricBox] = {}
        for key, title in [
            ("pnl", "Filtered P/L (FIFO)"),
            ("cost", "Cost basis"),
            ("proceeds", "Net proceeds"),
            ("qty", "Matched quantity"),
            ("rows", "Matched rows"),
        ]:
            box = MetricBox(metrics, title, width=170)
            box.pack(side="left", fill="x", expand=True, padx=3)
            self.pl_metrics[key] = box

        note = tk.Label(
            self.pl_tab,
            text=(
                "P/L is realised FIFO matching from stored TP history. It includes the 15% TP fee/tax assumption. "
                "Unmatched sells are excluded, and GW2 cannot identify whether an identical sold item was bought, farmed or otherwise acquired."
            ),
            bg=WIN_GREY,
            fg="#404040",
            justify="left",
            anchor="w",
            wraplength=1130,
            font=("MS Sans Serif", 8),
        )
        note.pack(fill="x", padx=10, pady=(0, 4))

        frame, self.pl_tree = make_tree(
            self.pl_tab,
            [
                ("sell_date", "Sell date", 110, "center"),
                ("item", "Item", 230, "w"),
                ("qty", "Qty", 65, "e"),
                ("buy", "Buy/unit", 105, "e"),
                ("sell", "Sell/unit", 105, "e"),
                ("cost", "Cost", 120, "e"),
                ("proceeds", "Net proceeds", 125, "e"),
                ("pnl", "P/L", 120, "e"),
                ("roi", "ROI", 70, "e"),
            ],
            height=21,
        )
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.pl_tree.tag_configure("profit", foreground="#006000")
        self.pl_tree.tag_configure("loss", foreground="#800000")

    def _build_tx_tab(self) -> None:
        self.tx_filters = self._make_date_filter_bar(self.tx_tab, "tx")
        bar = self.tx_filters["frame"]

        ttk.Label(bar, text="Side:").pack(side="left")
        self.tx_side = tk.StringVar(value="All")
        ttk.Combobox(
            bar,
            textvariable=self.tx_side,
            values=["All", "buys", "sells"],
            state="readonly",
            width=7,
        ).pack(side="left", padx=(3, 8))

        ttk.Label(bar, text="State:").pack(side="left")
        self.tx_state = tk.StringVar(value="history")
        ttk.Combobox(
            bar,
            textvariable=self.tx_state,
            values=["history", "current", "All"],
            state="readonly",
            width=8,
        ).pack(side="left", padx=(3, 8))

        tk.Button(bar, text="Apply", width=8, command=self.refresh_transactions).pack(side="left", padx=2)
        tk.Button(bar, text="Clear", width=8, command=self.clear_tx_filters).pack(side="left", padx=2)
        tk.Button(bar, text="Export...", width=9, command=self.export_tx_csv).pack(side="left", padx=2)

        metrics = ttk.Frame(self.tx_tab)
        metrics.pack(fill="x", padx=8, pady=4)
        self.tx_metrics: dict[str, MetricBox] = {}
        for key, title in [
            ("rows", "Rows"),
            ("qty", "Quantity"),
            ("buys", "Buy value"),
            ("sells", "Sell value"),
        ]:
            box = MetricBox(metrics, title, width=180)
            box.pack(side="left", fill="x", expand=True, padx=3)
            self.tx_metrics[key] = box

        frame, self.tx_tree = make_tree(
            self.tx_tab,
            [
                ("date", "Date", 120, "center"),
                ("item", "Item", 240, "w"),
                ("side", "Side", 70, "center"),
                ("state", "State", 75, "center"),
                ("qty", "Qty", 70, "e"),
                ("unit", "Unit price", 115, "e"),
                ("total", "Total", 125, "e"),
                ("id", "Transaction ID", 130, "e"),
            ],
            height=23,
        )
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_by_item_tab(self) -> None:
        top = ttk.Frame(self.by_item_tab)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Label(top, text="Sort:").pack(side="left")
        self.by_item_sort = tk.StringVar(value="Highest profit")
        combo = ttk.Combobox(
            top,
            textvariable=self.by_item_sort,
            values=["Highest profit", "Biggest losses", "Most traded quantity", "Most matched rows"],
            state="readonly",
            width=22,
        )
        combo.pack(side="left", padx=4)
        combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_by_item())
        tk.Button(top, text="Refresh", width=9, command=self.refresh_by_item).pack(side="left", padx=3)

        frame, self.by_item_tree = make_tree(
            self.by_item_tab,
            [
                ("item", "Item", 300, "w"),
                ("qty", "Matched quantity", 140, "e"),
                ("pnl", "Realised P/L", 150, "e"),
                ("rows", "Matched rows", 110, "e"),
                ("avg", "P/L per unit", 130, "e"),
            ],
            height=27,
        )
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.by_item_tree.tag_configure("profit", foreground="#006000")
        self.by_item_tree.tag_configure("loss", foreground="#800000")

    # --------------------------------------------------------------- health tab

    def _build_health(self) -> None:
        metrics = ttk.Frame(self.health_tab)
        metrics.pack(fill="x", padx=8, pady=8)
        self.health_metrics: dict[str, MetricBox] = {}
        for key, title in [
            ("status", "Latest status"),
            ("runtime", "Latest runtime"),
            ("ok", "Endpoints OK"),
            ("failed", "Endpoints failed"),
        ]:
            box = MetricBox(metrics, title, width=180)
            box.pack(side="left", fill="x", expand=True, padx=3)
            self.health_metrics[key] = box

        body = ttk.Panedwindow(self.health_tab, orient="vertical")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        jobs_box = ttk.LabelFrame(body, text="Recent jobs")
        jobs_frame, self.jobs_tree = make_tree(
            jobs_box,
            [
                ("started", "Started", 145, "center"),
                ("status", "Status", 80, "center"),
                ("runtime", "Runtime", 90, "e"),
                ("ok", "OK", 60, "e"),
                ("failed", "Failed", 60, "e"),
                ("message", "Message", 450, "w"),
            ],
            height=8,
        )
        jobs_frame.pack(fill="both", expand=True, padx=5, pady=5)
        body.add(jobs_box, weight=1)

        errors_box = ttk.LabelFrame(body, text="Endpoint errors")
        err_frame, self.errors_tree = make_tree(
            errors_box,
            [
                ("time", "Snapshot", 145, "center"),
                ("endpoint", "Endpoint", 260, "w"),
                ("code", "HTTP", 70, "center"),
                ("error", "Error", 520, "w"),
            ],
            height=10,
        )
        err_frame.pack(fill="both", expand=True, padx=5, pady=5)
        body.add(errors_box, weight=2)

    # --------------------------------------------------------------- data refresh

    def refresh_all(self) -> None:
        if self.db is None:
            self.change_connection()
            return

        def work() -> dict[str, Any]:
            assert self.db is not None
            latest = self.db.row("SELECT * FROM account_snapshots ORDER BY snapshot_at DESC LIMIT 1")
            history = self.db.rows("SELECT * FROM account_snapshots ORDER BY snapshot_at")
            wallet = self.db.rows(
                """
                WITH ranked AS (
                    SELECT ws.*, DENSE_RANK() OVER (ORDER BY ws.snapshot_at DESC) AS snapshot_rank
                    FROM wallet_snapshots ws
                )
                SELECT r.snapshot_at, r.snapshot_rank, r.currency_id,
                       COALESCE(cm.name, 'Currency ' || r.currency_id::text) AS currency,
                       r.value, cm.display_order
                FROM ranked r
                LEFT JOIN currency_metadata cm ON cm.currency_id = r.currency_id
                WHERE r.snapshot_rank <= 2
                ORDER BY COALESCE(cm.display_order, 999999), currency
                """
            )
            characters = self.db.rows(
                """
                WITH t AS (SELECT MAX(snapshot_at) AS snapshot_at FROM character_snapshots)
                SELECT character_name, race, profession, level, age_seconds, deaths, created_at, guild_id
                FROM character_snapshots
                WHERE snapshot_at = (SELECT snapshot_at FROM t)
                ORDER BY character_name
                """
            )
            mastery = self.db.rows(
                """
                WITH t AS (SELECT MAX(snapshot_at) AS snapshot_at FROM mastery_snapshots)
                SELECT region, earned, spent, earned - spent AS unspent
                FROM mastery_snapshots
                WHERE snapshot_at = (SELECT snapshot_at FROM t)
                ORDER BY earned DESC, region
                """
            )
            jobs = self.db.rows(
                """
                SELECT id, started_at, finished_at, status, runtime_seconds,
                       endpoints_ok, endpoints_failed, message
                FROM job_runs ORDER BY id DESC LIMIT 30
                """
            )
            errors = self.db.rows(
                """
                SELECT snapshot_at, endpoint, status_code, error
                FROM endpoint_errors ORDER BY id DESC LIMIT 250
                """
            )
            return {
                "latest": latest,
                "history": history,
                "wallet": wallet,
                "characters": characters,
                "mastery": mastery,
                "jobs": jobs,
                "errors": errors,
            }

        self.run_async("Refreshing account data...", work, self._apply_main_data)
        self.refresh_pl()
        self.refresh_transactions()
        self.refresh_by_item()

    def _apply_main_data(self, data: dict[str, Any]) -> None:
        self.latest = data["latest"]
        self.history = data["history"]
        self.wallet_rows = data["wallet"]
        self.character_rows = data["characters"]
        self.mastery_rows = data["mastery"]
        self.jobs_rows = data["jobs"]
        self.error_rows = data["errors"]

        if not self.latest:
            self.header_status.configure(text="connected · no snapshots ")
            return

        snapshot_at = display_date(self.latest.get("snapshot_at"), include_time=True)
        account_name = self.latest.get("account_name") or "GW2 account"
        self.header_status.configure(text=f"{account_name} · {snapshot_at} UTC ")

        self.populate_overview()
        self.populate_wealth()
        self.populate_progression()
        self.populate_wallet_table()
        self.populate_character_table()
        self.populate_health()
        self.populate_history_charts()

    def _history_delta(self, column: str, days: int = 30) -> float | None:
        if not self.history:
            return None
        latest = self.history[-1]
        latest_time = latest.get("snapshot_at")
        if not isinstance(latest_time, datetime):
            return None
        target = latest_time - timedelta(days=days)
        prior = [r for r in self.history if isinstance(r.get("snapshot_at"), datetime) and r["snapshot_at"] <= target]
        if not prior:
            return None
        a = latest.get(column)
        b = prior[-1].get(column)
        if a is None or b is None:
            return None
        return safe_float(a) - safe_float(b)

    def _history_for_range(self, label: str) -> list[dict[str, Any]]:
        if label == "All" or not self.history:
            return self.history
        try:
            days = int(label.split()[0])
        except (ValueError, IndexError):
            return self.history
        latest_time = self.history[-1].get("snapshot_at")
        if not isinstance(latest_time, datetime):
            return self.history
        cutoff = latest_time - timedelta(days=days)
        return [r for r in self.history if isinstance(r.get("snapshot_at"), datetime) and r["snapshot_at"] >= cutoff]

    def populate_overview(self) -> None:
        if not self.latest:
            return
        latest = self.latest
        self.ov_metrics["net"].set(copper_to_str(safe_int(latest.get("liquid_net_worth"))), f"30d {signed_coin(self._history_delta('liquid_net_worth', 30) or 0)}")
        self.ov_metrics["gold"].set(copper_to_str(safe_int(latest.get("liquid_gold"))), f"30d {signed_coin(self._history_delta('liquid_gold', 30) or 0)}")
        ap_delta = self._history_delta("achievement_points_estimate", 30)
        self.ov_metrics["ap"].set(f"{safe_int(latest.get('achievement_points_estimate')):,}", f"30d {safe_int(ap_delta):+d}" if ap_delta is not None else "")
        play_hours = safe_float(latest.get("total_playtime_seconds")) / 3600
        play_delta = self._history_delta("total_playtime_seconds", 30)
        self.ov_metrics["play"].set(f"{play_hours:,.1f} h", f"30d {play_delta / 3600:+.1f} h" if play_delta is not None else "")
        skin_delta = self._history_delta("skins_count", 30)
        self.ov_metrics["skins"].set(f"{safe_int(latest.get('skins_count')):,}", f"30d {safe_int(skin_delta):+d}" if skin_delta is not None else "")

        clear_tree(self.ov_progression_tree)
        progression = [
            ("Mastery points earned", latest.get("mastery_earned")),
            ("Mastery points unspent", latest.get("mastery_unspent")),
            ("Fractal level", latest.get("fractal_level")),
            ("WvW rank", latest.get("wvw_rank")),
            ("PvP rank", latest.get("pvp_rank")),
            ("Legendary items", latest.get("legendary_count")),
            ("Characters", latest.get("character_count")),
            ("Deaths", latest.get("total_deaths")),
        ]
        for metric, value in progression:
            self.ov_progression_tree.insert("", "end", values=(metric, f"{safe_int(value):,}"))

        clear_tree(self.ov_collections_tree)
        collections = [
            ("Skins", latest.get("skins_count")),
            ("Dyes", latest.get("dyes_count")),
            ("Titles", latest.get("titles_count")),
            ("Minis", latest.get("minis_count")),
            ("Outfits", latest.get("outfits_count")),
            ("Gliders", latest.get("gliders_count")),
            ("Mount skins", latest.get("mount_skins_count")),
            ("Recipes", latest.get("recipes_count")),
        ]
        for name, value in collections:
            self.ov_collections_tree.insert("", "end", values=(name, f"{safe_int(value):,}"))

    def populate_wealth(self) -> None:
        if not self.latest:
            return
        latest = self.latest
        self.wealth_metrics["net"].set(copper_to_str(safe_int(latest.get("liquid_net_worth"))))
        self.wealth_metrics["items"].set(copper_to_str(safe_int(latest.get("account_item_market_value"))))
        self.wealth_metrics["buys"].set(copper_to_str(safe_int(latest.get("buy_order_committed"))))
        self.wealth_metrics["sells"].set(copper_to_str(safe_int(latest.get("sell_order_net_value"))))

        components = [
            ("Liquid gold", latest.get("liquid_gold")),
            ("Bank", latest.get("bank_market_value")),
            ("Material storage", latest.get("materials_market_value")),
            ("Shared inventory", latest.get("shared_inventory_market_value")),
            ("Character inventories", latest.get("character_inventory_market_value")),
            ("Delivery box items", latest.get("delivery_market_value")),
            ("Delivery box coins", latest.get("delivery_coins")),
            ("Buy orders committed", latest.get("buy_order_committed")),
            ("Sell orders net", latest.get("sell_order_net_value")),
        ]
        clear_tree(self.wealth_tree)
        for component, value in components:
            self.wealth_tree.insert("", "end", values=(component, copper_to_str(safe_int(value))))

    def populate_progression(self) -> None:
        if not self.latest:
            return
        latest = self.latest
        self.prog_metrics["ap"].set(f"{safe_int(latest.get('achievement_points_estimate')):,}")
        self.prog_metrics["mastery"].set(f"{safe_int(latest.get('mastery_earned')):,}")
        self.prog_metrics["fractal"].set(f"{safe_int(latest.get('fractal_level')):,}")
        self.prog_metrics["wvw"].set(f"{safe_int(latest.get('wvw_rank')):,}")
        self.prog_metrics["pvp"].set(f"{safe_int(latest.get('pvp_rank')):,}")

        clear_tree(self.mastery_tree)
        for row in getattr(self, "mastery_rows", []):
            self.mastery_tree.insert(
                "",
                "end",
                values=(
                    row.get("region"),
                    f"{safe_int(row.get('earned')):,}",
                    f"{safe_int(row.get('spent')):,}",
                    f"{safe_int(row.get('unspent')):,}",
                ),
            )

        wins = safe_int(latest.get("pvp_wins"))
        losses = safe_int(latest.get("pvp_losses"))
        games = wins + losses
        rate = (wins / games * 100) if games else 0.0
        self.pvp_record_var.set(f"Wins: {wins:,}\nLosses: {losses:,}\nGames: {games:,}\nWin rate: {rate:.1f}%")

    def populate_history_charts(self) -> None:
        if not self.history:
            return
        ov_history = self._history_for_range(self.overview_range.get())
        labels = [display_date(r.get("snapshot_at")) for r in ov_history]
        self.overview_chart.set_data(
            [safe_float(r.get("liquid_net_worth")) / 10000 for r in ov_history],
            labels,
            title="Account value",
            unit="g",
        )

        wealth_map = {
            "Net worth": "liquid_net_worth",
            "Tradable items": "account_item_market_value",
            "Liquid gold": "liquid_gold",
        }
        w_col = wealth_map.get(self.wealth_series.get(), "liquid_net_worth")
        self.wealth_chart.set_data(
            [safe_float(r.get(w_col)) / 10000 for r in self.history],
            [display_date(r.get("snapshot_at")) for r in self.history],
            title=self.wealth_series.get(),
            unit="g",
        )

        prog_map = {
            "Achievement points": "achievement_points_estimate",
            "Mastery earned": "mastery_earned",
            "Skins": "skins_count",
            "WvW rank": "wvw_rank",
            "PvP rank": "pvp_rank",
        }
        p_col = prog_map.get(self.prog_series.get(), "achievement_points_estimate")
        self.prog_chart.set_data(
            [safe_float(r.get(p_col)) for r in self.history],
            [display_date(r.get("snapshot_at")) for r in self.history],
            title=self.prog_series.get(),
        )

    def populate_wallet_table(self) -> None:
        rows = getattr(self, "wallet_rows", [])
        latest_rows = {safe_int(r["currency_id"]): r for r in rows if safe_int(r.get("snapshot_rank")) == 1}
        prior_rows = {safe_int(r["currency_id"]): r for r in rows if safe_int(r.get("snapshot_rank")) == 2}
        needle = self.wallet_filter.get().strip().lower()

        clear_tree(self.wallet_tree)
        for currency_id, row in latest_rows.items():
            currency = str(row.get("currency") or f"Currency {currency_id}")
            if needle and needle not in currency.lower():
                continue
            value = safe_int(row.get("value"))
            prior = prior_rows.get(currency_id)
            change = value - safe_int(prior.get("value")) if prior else None
            self.wallet_tree.insert(
                "",
                "end",
                values=(currency, f"{value:,}", f"{change:+,}" if change is not None else "—"),
            )

    def populate_character_table(self) -> None:
        needle = self.character_filter.get().strip().lower()
        clear_tree(self.character_tree)
        for row in getattr(self, "character_rows", []):
            name = str(row.get("character_name") or "")
            if needle and needle not in name.lower():
                continue
            hours = safe_float(row.get("age_seconds")) / 3600
            self.character_tree.insert(
                "",
                "end",
                values=(
                    name,
                    row.get("race") or "",
                    row.get("profession") or "",
                    safe_int(row.get("level")),
                    f"{hours:,.1f} h",
                    f"{safe_int(row.get('deaths')):,}",
                    display_date(row.get("created_at")),
                ),
            )

    def populate_health(self) -> None:
        jobs = getattr(self, "jobs_rows", [])
        latest_job = jobs[0] if jobs else {}
        self.health_metrics["status"].set(str(latest_job.get("status") or "—"))
        self.health_metrics["runtime"].set(f"{safe_float(latest_job.get('runtime_seconds')):.1f}s" if latest_job else "—")
        self.health_metrics["ok"].set(f"{safe_int(latest_job.get('endpoints_ok')):,}" if latest_job else "—")
        self.health_metrics["failed"].set(f"{safe_int(latest_job.get('endpoints_failed')):,}" if latest_job else "—")

        clear_tree(self.jobs_tree)
        for row in jobs:
            self.jobs_tree.insert(
                "",
                "end",
                values=(
                    display_date(row.get("started_at"), include_time=True),
                    row.get("status") or "",
                    f"{safe_float(row.get('runtime_seconds')):.1f}s",
                    safe_int(row.get("endpoints_ok")),
                    safe_int(row.get("endpoints_failed")),
                    row.get("message") or "",
                ),
            )

        clear_tree(self.errors_tree)
        for row in getattr(self, "error_rows", []):
            self.errors_tree.insert(
                "",
                "end",
                values=(
                    display_date(row.get("snapshot_at"), include_time=True),
                    row.get("endpoint") or "",
                    row.get("status_code") if row.get("status_code") is not None else "",
                    row.get("error") or "",
                ),
            )

    # ------------------------------------------------------------ filter helpers

    def _filter_dates(self, filters: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
        label = filters["period"].get()
        if label != "Custom":
            start_d, end_d = preset_dates(label)
        else:
            try:
                start_d = parse_ymd(filters["from"].get())
                end_d = parse_ymd(filters["to"].get())
            except ValueError:
                raise ValueError("Dates must use YYYY-MM-DD.")
        start = utc_start(start_d) if start_d else None
        end = utc_start(end_d + timedelta(days=1)) if end_d else None
        return start, end

    # ------------------------------------------------------------ P/L functions

    def clear_pl_filters(self) -> None:
        self.pl_filters["period"].set("30 days")
        start, end = preset_dates("30 days")
        self.pl_filters["from"].set(start.isoformat() if start else "")
        self.pl_filters["to"].set(end.isoformat() if end else "")
        self.pl_filters["item"].set("")
        self.pl_result.set("All")
        self.refresh_pl()

    def refresh_pl(self) -> None:
        if self.db is None:
            return
        try:
            start, end = self._filter_dates(self.pl_filters)
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        result_filter = self.pl_result.get()
        item_filter = self.pl_filters["item"].get().strip().lower()

        def work() -> list[dict[str, Any]]:
            assert self.db is not None
            where = ["1=1"]
            params: dict[str, Any] = {}
            if start:
                where.append("sell_date >= :start")
                params["start"] = start
            if end:
                where.append("sell_date < :end")
                params["end"] = end
            if result_filter == "Profit only":
                where.append("profit > 0")
            elif result_filter == "Loss only":
                where.append("profit < 0")
            elif result_filter == "Breakeven":
                where.append("profit = 0")

            rows = self.db.rows(
                f"""
                SELECT id, item_id, quantity, buy_price, sell_price, profit, buy_date, sell_date
                FROM matched_flips
                WHERE {' AND '.join(where)}
                ORDER BY sell_date DESC NULLS LAST, id DESC
                """,
                params,
            )
            names = self.item_names.get_many(safe_int(r["item_id"]) for r in rows)
            output: list[dict[str, Any]] = []
            for row in rows:
                item_id = safe_int(row.get("item_id"))
                name = names.get(item_id, f"Item {item_id}")
                if item_filter and item_filter not in name.lower() and item_filter != str(item_id):
                    continue
                qty = safe_int(row.get("quantity"))
                buy = safe_int(row.get("buy_price"))
                sell = safe_int(row.get("sell_price"))
                profit = safe_int(row.get("profit"))
                cost = buy * qty
                proceeds = int(sell * qty * (1 - TP_TAX))
                roi = (profit / cost * 100) if cost else 0.0
                output.append(
                    {
                        **row,
                        "item_name": name,
                        "cost": cost,
                        "proceeds": proceeds,
                        "roi": roi,
                    }
                )
            return output

        self.run_async("Loading Trading Post P/L...", work, self._apply_pl_rows)

    def _apply_pl_rows(self, rows: list[dict[str, Any]]) -> None:
        self.current_pl_rows = rows
        total_pnl = sum(safe_int(r.get("profit")) for r in rows)
        total_cost = sum(safe_int(r.get("cost")) for r in rows)
        total_proceeds = sum(safe_int(r.get("proceeds")) for r in rows)
        total_qty = sum(safe_int(r.get("quantity")) for r in rows)
        self.pl_metrics["pnl"].set(copper_to_str(total_pnl))
        self.pl_metrics["cost"].set(copper_to_str(total_cost))
        self.pl_metrics["proceeds"].set(copper_to_str(total_proceeds))
        self.pl_metrics["qty"].set(f"{total_qty:,}")
        self.pl_metrics["rows"].set(f"{len(rows):,}")

        clear_tree(self.pl_tree)
        for row in rows:
            profit = safe_int(row.get("profit"))
            tag = "profit" if profit > 0 else "loss" if profit < 0 else ""
            self.pl_tree.insert(
                "",
                "end",
                values=(
                    display_date(row.get("sell_date")),
                    row.get("item_name") or "",
                    f"{safe_int(row.get('quantity')):,}",
                    copper_to_str(safe_int(row.get("buy_price"))),
                    copper_to_str(safe_int(row.get("sell_price"))),
                    copper_to_str(safe_int(row.get("cost"))),
                    copper_to_str(safe_int(row.get("proceeds"))),
                    copper_to_str(profit),
                    f"{safe_float(row.get('roi')):+.1f}%",
                ),
                tags=(tag,) if tag else (),
            )

    # -------------------------------------------------------- transaction funcs

    def clear_tx_filters(self) -> None:
        self.tx_filters["period"].set("30 days")
        start, end = preset_dates("30 days")
        self.tx_filters["from"].set(start.isoformat() if start else "")
        self.tx_filters["to"].set(end.isoformat() if end else "")
        self.tx_filters["item"].set("")
        self.tx_side.set("All")
        self.tx_state.set("history")
        self.refresh_transactions()

    def refresh_transactions(self) -> None:
        if self.db is None:
            return
        try:
            start, end = self._filter_dates(self.tx_filters)
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        item_filter = self.tx_filters["item"].get().strip().lower()
        side = self.tx_side.get()
        state = self.tx_state.get()

        def work() -> list[dict[str, Any]]:
            assert self.db is not None
            where = ["1=1"]
            params: dict[str, Any] = {}
            if start:
                where.append("COALESCE(purchased, created) >= :start")
                params["start"] = start
            if end:
                where.append("COALESCE(purchased, created) < :end")
                params["end"] = end
            if side != "All":
                where.append("side = :side")
                params["side"] = side
            if state != "All":
                where.append("transaction_set = :state")
                params["state"] = state

            rows = self.db.rows(
                f"""
                SELECT transaction_id, side, transaction_set, item_id, price, quantity, created, purchased
                FROM tp_transactions
                WHERE {' AND '.join(where)}
                ORDER BY COALESCE(purchased, created) DESC NULLS LAST, transaction_id DESC
                """,
                params,
            )
            names = self.item_names.get_many(safe_int(r["item_id"]) for r in rows)
            output: list[dict[str, Any]] = []
            for row in rows:
                item_id = safe_int(row.get("item_id"))
                name = names.get(item_id, f"Item {item_id}")
                if item_filter and item_filter not in name.lower() and item_filter != str(item_id):
                    continue
                output.append({**row, "item_name": name})
            return output

        self.run_async("Loading Trading Post transactions...", work, self._apply_tx_rows)

    def _apply_tx_rows(self, rows: list[dict[str, Any]]) -> None:
        self.current_tx_rows = rows
        qty = sum(safe_int(r.get("quantity")) for r in rows)
        buy_value = sum(
            safe_int(r.get("price")) * safe_int(r.get("quantity"))
            for r in rows
            if r.get("side") == "buys"
        )
        sell_value = sum(
            safe_int(r.get("price")) * safe_int(r.get("quantity"))
            for r in rows
            if r.get("side") == "sells"
        )
        self.tx_metrics["rows"].set(f"{len(rows):,}")
        self.tx_metrics["qty"].set(f"{qty:,}")
        self.tx_metrics["buys"].set(copper_to_str(buy_value))
        self.tx_metrics["sells"].set(copper_to_str(sell_value))

        clear_tree(self.tx_tree)
        for row in rows:
            quantity = safe_int(row.get("quantity"))
            price = safe_int(row.get("price"))
            self.tx_tree.insert(
                "",
                "end",
                values=(
                    display_date(row.get("purchased") or row.get("created"), include_time=True),
                    row.get("item_name") or "",
                    row.get("side") or "",
                    row.get("transaction_set") or "",
                    f"{quantity:,}",
                    copper_to_str(price),
                    copper_to_str(price * quantity),
                    row.get("transaction_id") or "",
                ),
            )

    def refresh_by_item(self) -> None:
        if self.db is None:
            return
        sort_mode = self.by_item_sort.get()
        order = {
            "Highest profit": "profit DESC",
            "Biggest losses": "profit ASC",
            "Most traded quantity": "quantity DESC",
            "Most matched rows": "matched_rows DESC",
        }.get(sort_mode, "profit DESC")

        def work() -> list[dict[str, Any]]:
            assert self.db is not None
            rows = self.db.rows(
                f"""
                SELECT item_id,
                       SUM(quantity)::bigint AS quantity,
                       SUM(profit)::bigint AS profit,
                       COUNT(*)::bigint AS matched_rows
                FROM matched_flips
                GROUP BY item_id
                ORDER BY {order}
                LIMIT 250
                """
            )
            names = self.item_names.get_many(safe_int(r["item_id"]) for r in rows)
            for row in rows:
                item_id = safe_int(row.get("item_id"))
                row["item_name"] = names.get(item_id, f"Item {item_id}")
            return rows

        self.run_async("Summarising Trading Post items...", work, self._apply_by_item_rows)

    def _apply_by_item_rows(self, rows: list[dict[str, Any]]) -> None:
        clear_tree(self.by_item_tree)
        for row in rows:
            qty = safe_int(row.get("quantity"))
            pnl = safe_int(row.get("profit"))
            avg = pnl / qty if qty else 0
            tag = "profit" if pnl > 0 else "loss" if pnl < 0 else ""
            self.by_item_tree.insert(
                "",
                "end",
                values=(
                    row.get("item_name") or "",
                    f"{qty:,}",
                    copper_to_str(pnl),
                    f"{safe_int(row.get('matched_rows')):,}",
                    copper_to_str(int(avg)),
                ),
                tags=(tag,) if tag else (),
            )

    # ------------------------------------------------------------------ exports

    def export_pl_csv(self) -> None:
        if not self.current_pl_rows:
            messagebox.showinfo(APP_TITLE, "There are no filtered P/L rows to export.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Trading Post P/L",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="gw2_tp_profit_loss.csv",
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "Sell date",
                    "Buy date",
                    "Item ID",
                    "Item",
                    "Quantity",
                    "Buy price (copper/unit)",
                    "Sell price (copper/unit)",
                    "Cost basis (copper)",
                    "Net proceeds (copper)",
                    "Profit/Loss (copper)",
                    "ROI %",
                ]
            )
            for row in self.current_pl_rows:
                writer.writerow(
                    [
                        display_date(row.get("sell_date"), include_time=True),
                        display_date(row.get("buy_date"), include_time=True),
                        row.get("item_id"),
                        row.get("item_name"),
                        row.get("quantity"),
                        row.get("buy_price"),
                        row.get("sell_price"),
                        row.get("cost"),
                        row.get("proceeds"),
                        row.get("profit"),
                        f"{safe_float(row.get('roi')):.4f}",
                    ]
                )
        self.set_status(f"Exported {len(self.current_pl_rows):,} P/L rows")

    def export_tx_csv(self) -> None:
        if not self.current_tx_rows:
            messagebox.showinfo(APP_TITLE, "There are no filtered transaction rows to export.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Trading Post transactions",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="gw2_tp_transactions.csv",
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "Transaction ID",
                    "Side",
                    "State",
                    "Item ID",
                    "Item",
                    "Unit price (copper)",
                    "Quantity",
                    "Created",
                    "Purchased",
                ]
            )
            for row in self.current_tx_rows:
                writer.writerow(
                    [
                        row.get("transaction_id"),
                        row.get("side"),
                        row.get("transaction_set"),
                        row.get("item_id"),
                        row.get("item_name"),
                        row.get("price"),
                        row.get("quantity"),
                        display_date(row.get("created"), include_time=True),
                        display_date(row.get("purchased"), include_time=True),
                    ]
                )
        self.set_status(f"Exported {len(self.current_tx_rows):,} transaction rows")

    # ------------------------------------------------------------------- misc

    def _clear_item_cache(self) -> None:
        self.item_names.clear()
        self.set_status("Item-name cache cleared")

    def show_about(self) -> None:
        messagebox.showinfo(
            "About",
            f"{APP_TITLE} {APP_VERSION}\n\n"
            "local read-only desktop frontend for the same Supabase data used by the Streamlit dashboard.\n\n"
            "Trading Post profit/loss uses the project's FIFO matching model.",
        )


if __name__ == "__main__":
    app = GW2LocalApp()
    app.mainloop()
