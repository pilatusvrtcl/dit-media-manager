from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import tkinter.messagebox as mbox
from tkinter import ttk

from .models import AppConfig, FileResult, format_size
from .sync_engine import SyncEngine, build_summary_text
from .utils import get_user_config_file, is_host_reachable, is_mount_available, save_source_ip_overrides
from .version import __version__


class RoundedCard(tk.Canvas):
    def __init__(
        self,
        parent: tk.Widget,
        card_color: str,
        radius: int = 18,
        padding: int = 12,
        border_color: str = "#2C2C2C",
    ) -> None:
        super().__init__(parent, highlightthickness=0, bd=0, bg="#111111", height=140)
        self.card_color = card_color
        self.radius = radius
        self.padding = padding
        self.border_color = border_color
        self.inner = tk.Frame(self, bg=card_color)
        self._window_id = self.create_window(
            (padding, padding),
            window=self.inner,
            anchor="nw",
        )
        self.bind("<Configure>", self._on_resize)

    def _draw_rounded_background(self, width: int, height: int) -> None:
        self.delete("card_bg")
        self.delete("card_border")
        radius = max(8, min(self.radius, width // 2, height // 2))
        self.create_arc(0, 0, 2 * radius, 2 * radius, start=90, extent=90, fill=self.card_color, outline=self.card_color, tags="card_bg")
        self.create_arc(width - 2 * radius, 0, width, 2 * radius, start=0, extent=90, fill=self.card_color, outline=self.card_color, tags="card_bg")
        self.create_arc(0, height - 2 * radius, 2 * radius, height, start=180, extent=90, fill=self.card_color, outline=self.card_color, tags="card_bg")
        self.create_arc(width - 2 * radius, height - 2 * radius, width, height, start=270, extent=90, fill=self.card_color, outline=self.card_color, tags="card_bg")
        self.create_rectangle(radius, 0, width - radius, height, fill=self.card_color, outline=self.card_color, tags="card_bg")
        self.create_rectangle(0, radius, width, height - radius, fill=self.card_color, outline=self.card_color, tags="card_bg")

        self.create_arc(0, 0, 2 * radius, 2 * radius, start=90, extent=90, style=tk.ARC, outline=self.border_color, width=1, tags="card_border")
        self.create_arc(width - 2 * radius, 0, width, 2 * radius, start=0, extent=90, style=tk.ARC, outline=self.border_color, width=1, tags="card_border")
        self.create_arc(0, height - 2 * radius, 2 * radius, height, start=180, extent=90, style=tk.ARC, outline=self.border_color, width=1, tags="card_border")
        self.create_arc(width - 2 * radius, height - 2 * radius, width, height, start=270, extent=90, style=tk.ARC, outline=self.border_color, width=1, tags="card_border")
        self.create_line(radius, 0, width - radius, 0, fill=self.border_color, width=1, tags="card_border")
        self.create_line(radius, height - 1, width - radius, height - 1, fill=self.border_color, width=1, tags="card_border")
        self.create_line(0, radius, 0, height - radius, fill=self.border_color, width=1, tags="card_border")
        self.create_line(width - 1, radius, width - 1, height - radius, fill=self.border_color, width=1, tags="card_border")

    def _on_resize(self, event: tk.Event) -> None:
        width = max(event.width, 20)
        height = max(event.height, 20)
        self._draw_rounded_background(width, height)
        inner_width = max(20, width - (2 * self.padding))
        inner_height = max(20, height - (2 * self.padding))
        self.coords(self._window_id, self.padding, self.padding)
        self.itemconfigure(self._window_id, width=inner_width, height=inner_height)


class AppGUI:
    def __init__(self, root: tk.Tk, config: AppConfig) -> None:
        self.root = root
        self.config = config
        self.root.title("Automated DIT Media Manager")
        self.root.geometry("1080x700")
        self.root.minsize(980, 620)
        self.root.configure(bg="#111111")

        self.status_labels: dict[str, tk.StringVar] = {}
        self.last_seen_labels: dict[str, tk.StringVar] = {}
        self.ip_vars: dict[str, tk.StringVar] = {}
        self.rows: list[FileResult] = []
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.last_24h_var = tk.BooleanVar(value=config.sync_options.last_24h_only)
        self.summary_var = tk.StringVar(value="Total Data: 0B | New Files: 0 | Time Elapsed: 0m 00s")
        self._row_insert_count = 0

        self._configure_styles()
        self._build_layout()
        self._refresh_connectivity()
        self._drain_events()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("App.TFrame", background="#111111")
        style.configure("Card.TFrame", background="#1A1A1A")
        style.configure("Title.TLabel", background="#111111", foreground="#FFFFFF", font=("SF Pro", 20, "bold"))
        style.configure("Subtitle.TLabel", background="#111111", foreground="#D8D8D8", font=("SF Pro", 11))
        style.configure("Summary.TLabel", background="#111111", foreground="#FFCC33", font=("SF Pro", 11, "bold"))
        style.configure("Info.TLabel", background="#111111", foreground="#FFFFFF", font=("SF Pro", 10))
        style.configure("DashHeader.TLabel", background="#1A1A1A", foreground="#FFCC33", font=("SF Pro", 10, "bold"))
        style.configure("DashValue.TLabel", background="#1A1A1A", foreground="#FFFFFF", font=("SF Pro", 10))
        style.configure("ConfigHeader.TLabel", background="#111111", foreground="#FFCC33", font=("SF Pro", 12, "bold"))
        style.configure("ConfigHint.TLabel", background="#111111", foreground="#D8D8D8", font=("SF Pro", 10))
        style.configure("Version.TLabel", background="#111111", foreground="#8E8E8E", font=("SF Pro", 9))
        style.configure("TNotebook", background="#111111", borderwidth=0)
        style.configure("TNotebook.Tab", font=("SF Pro", 10, "bold"), padding=(12, 8))
        style.configure("Accent.Horizontal.TProgressbar", troughcolor="#2A2A2A", background="#FFCC33", bordercolor="#2A2A2A", lightcolor="#FFCC33", darkcolor="#FFCC33")
        style.configure("TCheckbutton", background="#111111", foreground="#FFFFFF")
        style.map("TCheckbutton", background=[("active", "#111111")], foreground=[("disabled", "#777777")])
        style.configure(
            "Report.Treeview",
            rowheight=28,
            font=("SF Pro", 11),
            background="#1C1C1C",
            fieldbackground="#1C1C1C",
            foreground="#F5F5F5",
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Report.Treeview.Heading",
            font=("SF Pro", 10, "bold"),
            background="#161616",
            foreground="#FFCC33",
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "Report.Treeview",
            background=[("selected", "#2E2E2E")],
            foreground=[("selected", "#FFCC33")],
        )

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=14, style="App.TFrame")
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(outer, text="DIT Smart Sync", style="Title.TLabel")
        title.pack(anchor=tk.W)
        subtitle = ttk.Label(
            outer,
            text="One-click verified ingest for Camera A, Camera B, and ATEM ISO.",
            style="Subtitle.TLabel",
        )
        subtitle.pack(anchor=tk.W, pady=(2, 10))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True)

        sync_tab = ttk.Frame(notebook, style="App.TFrame")
        config_tab = ttk.Frame(notebook, style="App.TFrame")
        notebook.add(sync_tab, text="Sync")
        notebook.add(config_tab, text="Config")

        self._build_sync_tab(sync_tab)
        self._build_config_tab(config_tab)

        ttk.Label(outer, text=f"v{__version__}", style="Version.TLabel").pack(anchor=tk.E, pady=(8, 0))

    def _build_sync_tab(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent, style="App.TFrame")
        controls.pack(fill=tk.X)

        self.last24_toggle = ttk.Checkbutton(
            controls,
            text="Sync Last 24h",
            variable=self.last_24h_var,
        )
        self.last24_toggle.pack(side=tk.LEFT)

        self.start_btn = tk.Button(
            controls,
            text="Start Smart Sync",
            command=self._start_sync,
            bg="#FFCC33",
            fg="#111111",
            activebackground="#FFD966",
            activeforeground="#111111",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=16,
            pady=7,
            font=("SF Pro", 10, "bold"),
            cursor="hand2",
        )
        self.start_btn.pack(side=tk.LEFT, padx=(14, 0))

        self.progress = ttk.Progressbar(controls, mode="indeterminate", length=200, style="Accent.Horizontal.TProgressbar")
        self.progress_visible = False

        ttk.Label(controls, textvariable=self.summary_var, style="Summary.TLabel").pack(side=tk.RIGHT)

        destination_label = ttk.Label(
            parent,
            text=f"Destination: {self.config.destination_root}",
            style="Info.TLabel",
        )
        destination_label.pack(anchor=tk.W, pady=(8, 10))

        status_card = RoundedCard(parent, card_color="#1A1A1A", radius=24, padding=14)
        status_card.pack(fill=tk.X, pady=(0, 10))
        status_box = status_card.inner

        ttk.Label(status_box, text="Network Status Dashboard", style="DashHeader.TLabel").grid(
            row=0, column=0, columnspan=5, sticky=tk.W, pady=(0, 8)
        )

        ttk.Label(status_box, text="Device", style="DashHeader.TLabel").grid(row=1, column=0, sticky=tk.W, padx=(0, 10))
        ttk.Label(status_box, text="IP", style="DashHeader.TLabel").grid(row=1, column=1, sticky=tk.W, padx=(0, 10))
        ttk.Label(status_box, text="Mount", style="DashHeader.TLabel").grid(row=1, column=2, sticky=tk.W, padx=(0, 10))
        ttk.Label(status_box, text="Status", style="DashHeader.TLabel").grid(row=1, column=3, sticky=tk.W, padx=(0, 10))
        ttk.Label(status_box, text="Last Check", style="DashHeader.TLabel").grid(row=1, column=4, sticky=tk.W)

        for index, source in enumerate(self.config.sources):
            row = index + 2
            status_var = tk.StringVar(value="Checking...")
            seen_var = tk.StringVar(value="-")
            self.status_labels[source.name] = status_var
            self.last_seen_labels[source.name] = seen_var

            ttk.Label(status_box, text=source.name, style="DashValue.TLabel").grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=2)
            ttk.Label(status_box, text=source.ip_address or "-", style="DashValue.TLabel").grid(row=row, column=1, sticky=tk.W, padx=(0, 10), pady=2)
            ttk.Label(status_box, text=str(source.effective_root), style="DashValue.TLabel").grid(row=row, column=2, sticky=tk.W, padx=(0, 10), pady=2)
            ttk.Label(status_box, textvariable=status_var, style="DashValue.TLabel").grid(row=row, column=3, sticky=tk.W, padx=(0, 10), pady=2)
            ttk.Label(status_box, textvariable=seen_var, style="DashValue.TLabel").grid(row=row, column=4, sticky=tk.W, pady=2)

        columns = ("file", "source", "size", "status")
        results_card = RoundedCard(parent, card_color="#1A1A1A", radius=24, padding=14)
        results_card.pack(fill=tk.BOTH, expand=True)
        results_box = results_card.inner

        ttk.Label(results_box, text="Transfer Report", style="DashHeader.TLabel").pack(anchor=tk.W, pady=(0, 8))

        self.table = ttk.Treeview(results_box, columns=columns, show="headings", height=20, style="Report.Treeview")
        self.table.heading("file", text="File Name")
        self.table.heading("source", text="Source")
        self.table.heading("size", text="Size")
        self.table.heading("status", text="Status")

        self.table.column("file", width=520, anchor=tk.W)
        self.table.column("source", width=120, anchor=tk.W)
        self.table.column("size", width=120, anchor=tk.E)
        self.table.column("status", width=180, anchor=tk.W)

        yscroll = ttk.Scrollbar(results_box, orient=tk.VERTICAL, command=self.table.yview)
        self.table.configure(yscrollcommand=yscroll.set)
        self._configure_table_tags(self.table)

        self.table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.root.after(50, lambda: status_card.configure(height=max(170, 72 + (len(self.config.sources) * 28))))

    def _build_config_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent, style="App.TFrame", padding=(0, 4, 0, 0))
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Device IP Configuration", style="ConfigHeader.TLabel").pack(anchor=tk.W, pady=(0, 6))
        ttk.Label(
            frame,
            text="These values are saved in your user profile and persist across app updates.",
            style="ConfigHint.TLabel",
        ).pack(anchor=tk.W, pady=(0, 12))

        card = RoundedCard(frame, card_color="#1A1A1A", radius=24, padding=14)
        card.pack(fill=tk.X)
        body = card.inner

        for index, source in enumerate(self.config.sources):
            ttk.Label(body, text=source.name, style="DashValue.TLabel").grid(row=index, column=0, sticky=tk.W, padx=(0, 12), pady=6)
            ip_var = tk.StringVar(value=source.ip_address)
            self.ip_vars[source.name] = ip_var
            entry = tk.Entry(
                body,
                textvariable=ip_var,
                bg="#111111",
                fg="#FFFFFF",
                insertbackground="#FFCC33",
                highlightthickness=1,
                highlightbackground="#2C2C2C",
                highlightcolor="#FFCC33",
                relief="flat",
                bd=0,
                width=30,
                font=("SF Pro", 11),
            )
            entry.grid(row=index, column=1, sticky=tk.W, pady=6)

        actions = ttk.Frame(frame, style="App.TFrame")
        actions.pack(fill=tk.X, pady=(12, 0))

        tk.Button(
            actions,
            text="Save IPs",
            command=self._save_ip_settings,
            bg="#FFCC33",
            fg="#111111",
            activebackground="#FFD966",
            activeforeground="#111111",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=16,
            pady=7,
            font=("SF Pro", 10, "bold"),
            cursor="hand2",
        ).pack(side=tk.LEFT)

    def _save_ip_settings(self) -> None:
        ip_by_source = {name: var.get().strip() for name, var in self.ip_vars.items()}
        saved_path = save_source_ip_overrides(self.config, ip_by_source)

        for source in self.config.sources:
            source.ip_address = ip_by_source.get(source.name, source.ip_address)

        mbox.showinfo(
            "Config Saved",
            f"IP settings saved to:\n{saved_path}\n\nThey will persist across newer app versions.",
        )

    def _refresh_connectivity(self) -> None:
        now = time.strftime("%H:%M:%S")
        for source in self.config.sources:
            mount_online = is_mount_available(source.effective_root)
            host_online = is_host_reachable(source.ip_address) if source.ip_address else False
            if mount_online:
                state = "Online (Mounted)"
            elif source.ip_address and host_online:
                state = "Reachable (Not Mounted)"
            else:
                state = "Offline"
            self.status_labels[source.name].set(state)
            self.last_seen_labels[source.name].set(now)
        interval_ms = int(self.config.ui_refresh_seconds * 1000)
        self.root.after(interval_ms, self._refresh_connectivity)

    def _start_sync(self) -> None:
        self.start_btn.config(state=tk.DISABLED, bg="#666666", cursor="arrow")
        self.last24_toggle.state(["disabled"])
        if not self.progress_visible:
            self.progress.pack(side=tk.LEFT, padx=(14, 0))
            self.progress_visible = True
        self.progress.configure(mode="indeterminate")
        self.progress.stop()
        self.progress.start(11)
        self.summary_var.set("Sync in progress...")
        self.rows.clear()
        self._row_insert_count = 0
        for item in self.table.get_children():
            self.table.delete(item)

        thread = threading.Thread(target=self._run_sync_job, daemon=True)
        thread.start()

    def _run_sync_job(self) -> None:
        engine = SyncEngine(
            self.config,
            on_source_status=lambda source, status: self.event_queue.put(("source_status", (source, status))),
            on_file_result=lambda row: self.event_queue.put(("file_row", row)),
        )
        report = engine.run(last_24h_only=self.last_24h_var.get())
        self.event_queue.put(("done", report))

    def _drain_events(self) -> None:
        while not self.event_queue.empty():
            event_type, payload = self.event_queue.get_nowait()
            if event_type == "source_status":
                source, status = payload  # type: ignore[misc]
                if source in self.status_labels:
                    self.status_labels[source].set(status)
            elif event_type == "file_row":
                row: FileResult = payload  # type: ignore[assignment]
                self.rows.append(row)
                self._insert_result_row(self.table, row, self._row_insert_count)
                self._row_insert_count += 1
            elif event_type == "done":
                report = payload
                self.summary_var.set(build_summary_text(report))
                self.start_btn.config(state=tk.NORMAL, bg="#FFCC33", cursor="hand2")
                self.last24_toggle.state(["!disabled"])
                self.progress.stop()
                if self.progress_visible:
                    self.progress.pack_forget()
                    self.progress_visible = False
                self._open_final_report(report)

        self.root.after(150, self._drain_events)

    def _open_final_report(self, report) -> None:
        win = tk.Toplevel(self.root)
        win.title("Final Report")
        win.geometry("980x520")
        win.configure(bg="#111111")

        frame = ttk.Frame(win, style="App.TFrame", padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=build_summary_text(report), style="Summary.TLabel").pack(anchor=tk.W, pady=(0, 10))

        cols = ("file", "source", "size", "status")
        report_table = ttk.Treeview(frame, columns=cols, show="headings", height=20, style="Report.Treeview")
        for key, title in zip(cols, ["File Name", "Source", "Size", "Status"]):
            report_table.heading(key, text=title)
        report_table.column("file", width=500)
        report_table.column("source", width=120)
        report_table.column("size", width=120, anchor=tk.E)
        report_table.column("status", width=200)

        yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=report_table.yview)
        report_table.configure(yscrollcommand=yscroll.set)
        self._configure_table_tags(report_table)

        for idx, row in enumerate(report.rows):
            self._insert_result_row(report_table, row, idx)

        report_table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _configure_table_tags(self, table: ttk.Treeview) -> None:
        table.tag_configure("stripe_even", background="#1C1C1C")
        table.tag_configure("stripe_odd", background="#242424")
        table.tag_configure("status_verified", foreground="#FFCC33")
        table.tag_configure("status_skipped", foreground="#E7E7E7")
        table.tag_configure("status_failed", foreground="#FFFFFF")

    def _insert_result_row(self, table: ttk.Treeview, row: FileResult, index: int) -> None:
        status_value = row.status
        status_tag = "status_skipped"
        lowered = row.status.lower()
        if lowered == "verified":
            status_tag = "status_verified"
            status_value = f"● {row.status}"
        elif lowered == "failed":
            status_tag = "status_failed"
            status_value = f"● {row.status}"

        stripe_tag = "stripe_even" if index % 2 == 0 else "stripe_odd"
        table.insert(
            "",
            tk.END,
            values=(row.file_name, row.source, format_size(row.size_bytes), status_value),
            tags=(stripe_tag, status_tag),
        )


def run_app(config: AppConfig) -> None:
    root = tk.Tk()
    AppGUI(root, config)
    root.mainloop()
