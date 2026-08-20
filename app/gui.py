#!/usr/bin/env python3
"""
Argo SBD Decoder - GUI (tkinter)

Cross-platform graphical interface.
Developed by SOCIB and IEO-CSIC.
Based on the Coriolis Argo data processing chain (DOI: 10.17882/45589).
Funded by the Euro-Argo ONE project (Grant Agreement No. 101188133).
"""

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# ─── Paths ───
# Support both normal Python execution and PyInstaller frozen mode
if getattr(sys, 'frozen', False):
    _BASE = Path(sys._MEIPASS)
    APP_DIR = _BASE
    ROOT_DIR = _BASE
    SCRIPTS_DIR = ROOT_DIR / "scripts"
    CONFIG_DIR = ROOT_DIR / "config"
    ASSETS_DIR = ROOT_DIR / "assets"
    DOCS_DIR = ROOT_DIR / "docs"
    LOGS_DIR = Path.home() / ".argo_sbd_decoder" / "logs"
    CONFIG_PATH = Path.home() / ".argo_sbd_decoder" / "settings.json"
    NKE_MAP_PATH = CONFIG_DIR / "nke_to_decoder_id.json"
else:
    APP_DIR = Path(__file__).resolve().parent
    ROOT_DIR = APP_DIR.parent
    SCRIPTS_DIR = ROOT_DIR / "scripts"
    CONFIG_DIR = ROOT_DIR / "config"
    ASSETS_DIR = ROOT_DIR / "assets"
    DOCS_DIR = ROOT_DIR / "docs"
    LOGS_DIR = ROOT_DIR / "logs"
    CONFIG_PATH = CONFIG_DIR / "settings.json"
    NKE_MAP_PATH = CONFIG_DIR / "nke_to_decoder_id.json"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


# ─── Settings ───
DEFAULT_SETTINGS = {
    "decoder_id": 212,
    "nke_decoding_type": "",
    "wmo": "",
    "email": "",
    "imap_server": "imap.gmail.com",
    "imap_port": 993,
    "imap_sender": "sbdservice@sbd.iridium.com",
    "imap_label": "INBOX",
    "float_root": "",
    "bathymetry_file": "",
    "last_imei": "",
    "last_since": "",
    "last_before": "",
}


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    if CONFIG_PATH.exists():
        try:
            s.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return s


def save_settings(app) -> None:
    s = {
        "decoder_id": app.decoder_id,
        "nke_decoding_type": app.var_float_type.get(),
        "wmo": app.var_wmo.get(),
        "email": app.var_email.get(),
        "email_provider": app.var_provider.get(),
        "imap_server": app.var_imap_server.get(),
        "imap_port": int(app.var_imap_port.get() or 993),
        "imap_sender": app.var_sender.get(),
        "imap_label": app.var_label.get(),
        "float_root": app.var_root.get(),
        "bathymetry_file": app.var_bathy.get(),
        "last_imei": app.var_imei.get(),
        "last_since": app.var_since.get(),
        "last_before": app.var_before.get(),
    }
    CONFIG_PATH.write_text(json.dumps(s, indent=2), encoding="utf-8")


def load_float_types() -> dict:
    """Load NKE float type → decoder_id mapping."""
    if NKE_MAP_PATH.exists():
        try:
            data = json.loads(NKE_MAP_PATH.read_text(encoding="utf-8"))
            return {k: v["decoder_id"] for k, v in data.items()}
        except Exception:
            pass
    return {"ARVOR_PROVOR_5900A02_to_5900A04": 212}


def resolve_root(root: str, imei: str) -> str:
    """Resolve the float root folder."""
    root = root.strip()
    if not root:
        return ""
    p = Path(root)
    if p.name == imei and imei:
        return root
    if (p / "sbd_raw").exists():
        return root
    if imei:
        candidate = p / imei
        if candidate.exists():
            return str(candidate)
        if not (p / "decoded").exists():
            return str(candidate)
    return root


# ─── Main Application ───
class ArgoDecoderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Argo SBD Decoder")
        self.root.geometry("1350x850")
        self.root.minsize(1000, 600)
        self.root.configure(bg="#f6f8fa")

        self.settings = load_settings()
        self.float_types = load_float_types()
        self.decoder_id = self.settings.get("decoder_id", 212)
        self.python_exe = sys.executable

        self._build_ui()
        self._load_plots()
        self.log("Argo SBD Decoder ready.")
        self.log(f"Python: {self.python_exe}")

    def _build_ui(self):
        # ─── Style configuration ───
        style = ttk.Style()
        style.configure("TEntry", fieldbackground="#f0f4f8", borderwidth=2, relief="solid")
        style.configure("TCombobox", fieldbackground="#f0f4f8")
        style.configure("Browse.TButton", padding=(8, 4))

        # ─── Header ───
        hdr = tk.Frame(self.root, bg="#003366", height=60)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        # Logo
        logo_path = ASSETS_DIR / "euro_argo_logo.png"
        if logo_path.exists():
            try:
                self._logo_img = tk.PhotoImage(file=str(logo_path))
                # Scale down if too big
                w, h = self._logo_img.width(), self._logo_img.height()
                if h > 50:
                    factor = max(1, h // 50)
                    self._logo_img = self._logo_img.subsample(factor, factor)
                tk.Label(hdr, image=self._logo_img, bg="#003366").pack(side="left", padx=(12, 8), pady=4)
            except Exception:
                pass

        tk.Label(hdr, text="Argo SBD Decoder", font=("Segoe UI", 18, "bold"),
                 fg="white", bg="#003366").pack(side="left", pady=(8, 0))
        tk.Label(hdr, text="Developed by SOCIB and IEO-CSIC",
                 font=("Segoe UI", 8), fg="#a0c3e1", bg="#003366").pack(side="left", padx=(20, 0), pady=(18, 0))

        # Help button (right side of header)
        tk.Button(hdr, text="? Help", command=self._show_help, bg="#004080", fg="white",
                  font=("Segoe UI", 9, "bold"), relief="flat", padx=12, pady=4,
                  activebackground="#005599", activeforeground="white"
                  ).pack(side="right", padx=12, pady=14)

        # ─── Footer ───
        ftr = tk.Frame(self.root, bg="#ebedf0", height=22)
        ftr.pack(fill="x", side="bottom")
        ftr.pack_propagate(False)
        tk.Label(ftr, text="Developed by SOCIB and IEO-CSIC | Based on the Coriolis Argo decoder (DOI: 10.17882/45589) | Euro-Argo ONE project (Grant Agreement No. 101188133)",
                 font=("Segoe UI", 7), fg="#6e7680", bg="#ebedf0").pack(side="left", padx=10)

        # ─── Main PanedWindow (left | right) ───
        pw_main = tk.PanedWindow(self.root, orient="horizontal", sashwidth=5,
                                  bg="#dce0e4", borderwidth=0)
        pw_main.pack(fill="both", expand=True)

        # LEFT: controls + log (vertical paned)
        pw_left = tk.PanedWindow(pw_main, orient="vertical", sashwidth=4,
                                  bg="#dce0e4", borderwidth=0)
        pw_main.add(pw_left, minsize=450, stretch="always")

        # RIGHT: preview
        right_frame = tk.Frame(pw_main, bg="white")
        pw_main.add(right_frame, minsize=300, stretch="always")

        # ─── Controls panel (top-left) with vertical scroll ───
        ctrl_container = tk.Frame(pw_left, bg="white")
        pw_left.add(ctrl_container, minsize=250, stretch="always")

        ctrl_canvas = tk.Canvas(ctrl_container, bg="white", highlightthickness=0)
        ctrl_scrollbar = tk.Scrollbar(ctrl_container, orient="vertical", command=ctrl_canvas.yview)
        ctrl = tk.Frame(ctrl_canvas, bg="white", padx=16, pady=12)

        ctrl.bind("<Configure>", lambda e: ctrl_canvas.configure(scrollregion=ctrl_canvas.bbox("all")))
        self._ctrl_canvas_window = ctrl_canvas.create_window((0, 0), window=ctrl, anchor="nw")
        ctrl_canvas.configure(yscrollcommand=ctrl_scrollbar.set)

        def _on_canvas_configure(event):
            ctrl_canvas.itemconfig(self._ctrl_canvas_window, width=event.width)
        ctrl_canvas.bind("<Configure>", _on_canvas_configure)

        ctrl_canvas.pack(side="left", fill="both", expand=True)
        ctrl_scrollbar.pack(side="right", fill="y")

        # Mousewheel scrolling — only when cursor is over the controls panel
        def _on_mousewheel(event):
            ctrl_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        def _bind_mousewheel(event):
            ctrl_canvas.bind_all("<MouseWheel>", _on_mousewheel)
        def _unbind_mousewheel(event):
            ctrl_canvas.unbind_all("<MouseWheel>")
        ctrl_container.bind("<Enter>", _bind_mousewheel)
        ctrl_container.bind("<Leave>", _unbind_mousewheel)

        self._build_controls(ctrl)

        # ─── Log panel (bottom-left) ───
        log_frame = tk.Frame(pw_left, bg="white")
        pw_left.add(log_frame, minsize=100, stretch="always")
        tk.Label(log_frame, text="LOG", font=("Segoe UI", 9, "bold"),
                 fg="#003366", bg="white", anchor="w").pack(fill="x", padx=10, pady=(6, 0))
        self.txt_log = tk.Text(log_frame, height=8, font=("Consolas", 9),
                               bg="#fafbfc", fg="#222222", relief="flat", wrap="word")
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(2, 8))
        self.txt_log.tag_configure("recovery", background="#d4edda", foreground="#155724",
                                   font=("Consolas", 10, "bold"))

        # ─── Preview panel (right) ───
        self._build_preview(right_frame)

        # Set initial sash positions
        self.root.after(100, lambda: pw_main.sash_place(0, 650, 0))
        self.root.after(100, lambda: pw_left.sash_place(0, 0, 420))


    def _build_controls(self, parent):
        """Build the controls section with proper grid layout."""
        s = self.settings
        row = 0

        # ── Section 1: Configuration ──
        tk.Label(parent, text="1. FLOAT CONFIGURATION", font=("Segoe UI", 10, "bold"),
                 fg="#003366", bg="white").grid(row=row, column=0, columnspan=4, sticky="w", pady=(0, 6))
        row += 1

        tk.Label(parent, text="Float type", bg="white", fg="#222222").grid(row=row, column=0, sticky="w")
        self.var_float_type = tk.StringVar(value=s.get("nke_decoding_type", ""))
        cmb = ttk.Combobox(parent, textvariable=self.var_float_type, values=list(self.float_types.keys()),
                           state="readonly", width=50)
        cmb.grid(row=row, column=1, columnspan=3, sticky="ew", padx=(4, 0))
        if self.var_float_type.get() not in self.float_types:
            cmb.current(0) if cmb["values"] else None
        cmb.bind("<<ComboboxSelected>>", lambda e: self._on_float_change())
        row += 1

        tk.Label(parent, text="IMEI", bg="white", fg="#222222").grid(row=row, column=0, sticky="w", pady=4)
        self.var_imei = tk.StringVar(value=s.get("last_imei", ""))
        tk.Entry(parent, textvariable=self.var_imei, width=22, bg="#f0f4f8", relief="solid", bd=1).grid(row=row, column=1, sticky="w", padx=(4, 8))
        tk.Label(parent, text="WMO (optional)", bg="white", fg="#555555").grid(row=row, column=2, sticky="w")
        self.var_wmo = tk.StringVar(value=s.get("wmo", ""))
        tk.Entry(parent, textvariable=self.var_wmo, width=12, bg="#f0f4f8", relief="solid", bd=1).grid(row=row, column=3, sticky="w")
        row += 1

        tk.Label(parent, text="Working folder", bg="white", fg="#222222").grid(row=row, column=0, sticky="w", pady=4)
        self.var_root = tk.StringVar(value=s.get("float_root", ""))
        tk.Entry(parent, textvariable=self.var_root, width=50, bg="#f0f4f8", relief="solid", bd=1).grid(row=row, column=1, columnspan=2, sticky="ew", padx=(4, 4))
        tk.Button(parent, text="Browse", command=self._browse_root, width=7, bg="#e2e6ea", relief="raised", bd=1).grid(row=row, column=3, sticky="w")
        row += 1

        tk.Label(parent, text="GEBCO bathy", bg="white", fg="#222222").grid(row=row, column=0, sticky="w", pady=4)
        self.var_bathy = tk.StringVar(value=s.get("bathymetry_file", ""))
        tk.Entry(parent, textvariable=self.var_bathy, width=50, bg="#f0f4f8", relief="solid", bd=1).grid(row=row, column=1, columnspan=2, sticky="ew", padx=(4, 4))
        tk.Button(parent, text="Browse", command=self._browse_bathy, width=7, bg="#e2e6ea", relief="raised", bd=1).grid(row=row, column=3, sticky="w")
        row += 1

        # ── Section 2: Download ──
        ttk.Separator(parent, orient="horizontal").grid(row=row, column=0, columnspan=4, sticky="ew", pady=8)
        row += 1
        tk.Label(parent, text="2. DOWNLOAD SBDs", font=("Segoe UI", 10, "bold"),
                 fg="#003366", bg="white").grid(row=row, column=0, columnspan=4, sticky="w", pady=(0, 6))
        row += 1

        tk.Label(parent, text="Email provider", bg="white", fg="#222222").grid(row=row, column=0, sticky="w")
        self._email_providers = {
            "Gmail": {"server": "imap.gmail.com", "port": 993, "folder": "[Gmail]/All Mail"},
            "Outlook / Hotmail": {"server": "outlook.office365.com", "port": 993, "folder": "INBOX"},
            "Yahoo": {"server": "imap.mail.yahoo.com", "port": 993, "folder": "INBOX"},
            "iCloud": {"server": "imap.mail.me.com", "port": 993, "folder": "INBOX"},
            "Other": {"server": "", "port": 993, "folder": "INBOX"},
        }
        self.var_provider = tk.StringVar(value=self._detect_provider(s))
        cmb_provider = ttk.Combobox(parent, textvariable=self.var_provider,
                                     values=list(self._email_providers.keys()),
                                     state="readonly", width=20)
        cmb_provider.grid(row=row, column=1, sticky="w", padx=(4, 8))
        cmb_provider.bind("<<ComboboxSelected>>", lambda e: self._on_provider_change())

        # Hidden vars for server/port (auto-filled by provider selection)
        self.var_imap_server = tk.StringVar(value=s.get("imap_server", "imap.gmail.com"))
        self.var_imap_port = tk.StringVar(value=str(s.get("imap_port", 993)))
        row += 1

        # Server/port row (shown only when "Other" is selected)
        self._imap_detail_row = row
        self._imap_detail_parent = parent
        self.lbl_server = tk.Label(parent, text="IMAP server", bg="white", fg="#222222")
        self.ent_server = tk.Entry(parent, textvariable=self.var_imap_server, width=25, bg="#f0f4f8", relief="solid", bd=1)
        self.lbl_port = tk.Label(parent, text="Port", bg="white", fg="#222222")
        self.ent_port = tk.Entry(parent, textvariable=self.var_imap_port, width=6, bg="#f0f4f8", relief="solid", bd=1)
        row += 1

        # Show/hide server details based on provider
        self._toggle_imap_details()

        tk.Label(parent, text="Email address", bg="white", fg="#222222").grid(row=row, column=0, sticky="w")
        self.var_email = tk.StringVar(value=s.get("email", s.get("gmail", "")))
        tk.Entry(parent, textvariable=self.var_email, width=25, bg="#f0f4f8", relief="solid", bd=1).grid(row=row, column=1, sticky="w", padx=(4, 8))
        tk.Label(parent, text="App password", bg="white", fg="#222222").grid(row=row, column=2, sticky="w")
        pwd_frame = tk.Frame(parent, bg="white")
        pwd_frame.grid(row=row, column=3, sticky="w")
        self.var_pwd = tk.StringVar()
        tk.Entry(pwd_frame, textvariable=self.var_pwd, show="*", width=15, bg="#f0f4f8", relief="solid", bd=1).pack(side="left")
        tk.Button(pwd_frame, text="?", command=self._show_app_password_help, bg="#e9ecef",
                  font=("Segoe UI", 8, "bold"), width=2, relief="raised", bd=1,
                  cursor="hand2").pack(side="left", padx=(4, 0))
        row += 1

        tk.Label(parent, text="From (YYYYMMDD)", bg="white", fg="#222222").grid(row=row, column=0, sticky="w", pady=4)
        date_frame_from = tk.Frame(parent, bg="white")
        date_frame_from.grid(row=row, column=1, sticky="w", padx=(4, 8))
        self.var_since = tk.StringVar(value=s.get("last_since", ""))
        ent_since = tk.Entry(date_frame_from, textvariable=self.var_since, width=10, bg="#f0f4f8", relief="solid", bd=1)
        ent_since.pack(side="left")
        tk.Button(date_frame_from, text="📅", command=lambda: self._pick_date(self.var_since),
                  relief="raised", bd=1, font=("Segoe UI", 9), cursor="hand2").pack(side="left", padx=2)

        tk.Label(parent, text="Until (YYYYMMDD)", bg="white", fg="#222222").grid(row=row, column=2, sticky="w")
        date_frame_until = tk.Frame(parent, bg="white")
        date_frame_until.grid(row=row, column=3, sticky="w")
        self.var_before = tk.StringVar(value=s.get("last_before", ""))
        ent_before = tk.Entry(date_frame_until, textvariable=self.var_before, width=10, bg="#f0f4f8", relief="solid", bd=1)
        ent_before.pack(side="left")
        tk.Button(date_frame_until, text="📅", command=lambda: self._pick_date(self.var_before),
                  relief="raised", bd=1, font=("Segoe UI", 9), cursor="hand2").pack(side="left", padx=2)
        row += 1

        tk.Label(parent, text="Sender address", bg="white", fg="#222222").grid(row=row, column=0, sticky="w", pady=4)
        self.var_sender = tk.StringVar(value=s.get("imap_sender", "sbdservice@sbd.iridium.com"))
        tk.Entry(parent, textvariable=self.var_sender, width=35, bg="#f0f4f8", relief="solid", bd=1).grid(row=row, column=1, columnspan=2, sticky="w", padx=(4, 8))
        row += 1

        tk.Label(parent, text="IMAP folder", bg="white", fg="#222222").grid(row=row, column=0, sticky="w", pady=4)
        self.var_label = tk.StringVar(value=s.get("imap_label", "INBOX"))
        self.ent_label = tk.Entry(parent, textvariable=self.var_label, width=25, bg="#f0f4f8", relief="solid", bd=1)
        self.ent_label.grid(row=row, column=1, sticky="w", padx=(4, 8))
        row += 1

        btn_dl = tk.Button(parent, text="Download SBDs", command=self._download,
                           bg="#0077B6", fg="#ffffff", font=("Segoe UI", 10, "bold"),
                           relief="raised", bd=2, padx=14, pady=4, cursor="hand2",
                           highlightbackground="#0077B6")
        btn_dl.grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        row += 1

        # ── Section 3: Processing ──
        ttk.Separator(parent, orient="horizontal").grid(row=row, column=0, columnspan=4, sticky="ew", pady=8)
        row += 1
        tk.Label(parent, text="3. DECODE & PRODUCTS", font=("Segoe UI", 10, "bold"),
                 fg="#003366", bg="white").grid(row=row, column=0, columnspan=4, sticky="w", pady=(0, 6))
        row += 1

        btn_frame_top = tk.Frame(parent, bg="white")
        btn_frame_top.grid(row=row, column=0, columnspan=4, sticky="ew")
        self._make_flow_btn(btn_frame_top, "Install libraries", self._install_deps)
        btn_frame_top.bind("<Configure>", lambda e: self._reflow_buttons(btn_frame_top))
        row += 1

        btn_frame = tk.Frame(parent, bg="white")
        btn_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self._make_flow_btn(btn_frame, "Decode SBDs", self._decode, "#2E8B57", "white")
        self._make_flow_btn(btn_frame, "TS + Sections", self._quicklook)
        self._make_flow_btn(btn_frame, "Map", self._map)
        self._make_flow_btn(btn_frame, "Forecast Position", self._nav)
        self._make_flow_btn(btn_frame, "All products", self._all, "#0077B6", "white")
        btn_frame.bind("<Configure>", lambda e: self._reflow_buttons(btn_frame))
        row += 1

        btn_frame2 = tk.Frame(parent, bg="white")
        btn_frame2.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self._make_flow_btn(btn_frame2, "Open output folder", self._open_folder, "#E6A01E", "#222")
        btn_frame2.bind("<Configure>", lambda e: self._reflow_buttons(btn_frame2))
        row += 1

        # Configure grid weights for resizing
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=0)


    def _make_flow_btn(self, parent, text, cmd, bg="#e9ecef", fg="#222222"):
        """Create a button that participates in flow layout."""
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                      font=("Segoe UI", 9, "bold"), relief="raised", bd=1,
                      padx=12, pady=5, cursor="hand2",
                      highlightbackground=bg)
        # Store button in parent's flow list
        if not hasattr(parent, '_flow_buttons'):
            parent._flow_buttons = []
        parent._flow_buttons.append(b)

    def _reflow_buttons(self, container):
        """Reposition buttons in a flow/wrap layout based on container width."""
        if not hasattr(container, '_flow_buttons'):
            return
        buttons = container._flow_buttons
        if not buttons:
            return

        container_width = container.winfo_width()
        if container_width <= 1:
            # Widget not yet visible, place them linearly
            for btn in buttons:
                btn.place_forget()
            x, y = 0, 4
            for btn in buttons:
                btn.update_idletasks()
                btn.place(x=x, y=y)
                x += btn.winfo_reqwidth() + 8
            container.configure(height=max(40, y + buttons[0].winfo_reqheight() + 8))
            return

        x, y = 0, 4
        row_height = 0
        for btn in buttons:
            btn.update_idletasks()
            bw = btn.winfo_reqwidth()
            bh = btn.winfo_reqheight()
            if x + bw > container_width and x > 0:
                # Wrap to next row
                x = 0
                y += row_height + 4
                row_height = 0
            btn.place(x=x, y=y)
            x += bw + 8
            row_height = max(row_height, bh)

        total_height = y + row_height + 8
        container.configure(height=total_height)

    def _detect_provider(self, settings: dict) -> str:
        """Detect email provider from saved IMAP server setting."""
        server = settings.get("imap_server", "imap.gmail.com").lower()
        for name, info in {
            "Gmail": "imap.gmail.com",
            "Outlook / Hotmail": "outlook.office365.com",
            "Yahoo": "imap.mail.yahoo.com",
            "iCloud": "imap.mail.me.com",
        }.items():
            if info in server:
                return name
        if server:
            return "Other"
        return "Gmail"

    def _on_provider_change(self):
        """Update IMAP server, port and folder when provider changes."""
        provider = self.var_provider.get()
        info = self._email_providers.get(provider, {})
        if provider != "Other":
            self.var_imap_server.set(info.get("server", ""))
            self.var_imap_port.set(str(info.get("port", 993)))
            self.var_label.set(info.get("folder", "INBOX"))
        self._toggle_imap_details()

    def _toggle_imap_details(self):
        """Show/hide IMAP server and port fields based on provider."""
        row = self._imap_detail_row
        if self.var_provider.get() == "Other":
            self.lbl_server.grid(row=row, column=0, sticky="w", pady=4)
            self.ent_server.grid(row=row, column=1, sticky="w", padx=(4, 8))
            self.lbl_port.grid(row=row, column=2, sticky="w")
            self.ent_port.grid(row=row, column=3, sticky="w")
        else:
            self.lbl_server.grid_remove()
            self.ent_server.grid_remove()
            self.lbl_port.grid_remove()
            self.ent_port.grid_remove()

    def _build_preview(self, parent):
        """Build the plot preview panel."""
        tk.Label(parent, text="PREVIEW", font=("Segoe UI", 10, "bold"),
                 fg="#003366", bg="white", anchor="w").pack(fill="x", padx=10, pady=(8, 2))

        # Image display
        self.preview_label = tk.Label(parent, bg="#fcfdfe", relief="flat")
        self.preview_label.pack(fill="both", expand=True, padx=8, pady=4)
        self._preview_img = None

        # Plot selector
        self.var_plot = tk.StringVar()
        self.cmb_plot = ttk.Combobox(parent, textvariable=self.var_plot, state="readonly")
        self.cmb_plot.pack(fill="x", padx=8, pady=(0, 8))
        self.cmb_plot.bind("<<ComboboxSelected>>", lambda e: self._show_plot())

    def _load_plots(self):
        """Scan products folder for PNG files."""
        root = resolve_root(self.var_root.get(), self.var_imei.get())
        prod = Path(root) / "products" if root else Path(".")
        plots = []
        if prod.exists():
            plots = sorted(prod.rglob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        display_names = [str(p.relative_to(prod)) if prod.exists() else p.name for p in plots]
        self._plot_paths = plots
        self.cmb_plot["values"] = display_names
        if display_names:
            self.cmb_plot.current(0)
            self._show_plot()

    def _show_plot(self):
        """Display selected plot in the preview panel."""
        idx = self.cmb_plot.current()
        if idx < 0 or idx >= len(self._plot_paths):
            return
        path = self._plot_paths[idx]
        try:
            img = tk.PhotoImage(file=str(path))
            # Scale to fit the preview area
            pw = self.preview_label.winfo_width() or 600
            ph = self.preview_label.winfo_height() or 500
            iw, ih = img.width(), img.height()
            if iw > 0 and ih > 0:
                scale = max(1, iw // pw + 1, ih // ph + 1)
                img = img.subsample(scale, scale)
            self._preview_img = img
            self.preview_label.configure(image=img)
        except Exception as e:
            self.log(f"Cannot display {path.name}: {e}")

    # ─── Actions ───
    def log(self, msg: str):
        tag = None
        # Highlight the entire recovery forecast block
        if any(kw in msg for kw in ["RECOVERY FORECAST", "GO TO", ">>>",
                                     "Drift speed", "Heading:", "Last known fix",
                                     "Pred. lat", "Pred. lon"]):
            tag = "recovery"

        if tag:
            self.txt_log.insert("end", f"[{self._time()}] {msg}\n", tag)
        else:
            self.txt_log.insert("end", f"[{self._time()}] {msg}\n")
        self.txt_log.see("end")

    def _time(self):
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def _on_float_change(self):
        ft = self.var_float_type.get()
        self.decoder_id = self.float_types.get(ft, 212)
        save_settings(self)

    def _browse_root(self):
        d = filedialog.askdirectory(initialdir=self.var_root.get() or str(Path.home()))
        if d:
            self.var_root.set(d)
            save_settings(self)

    def _pick_date(self, var: tk.StringVar):
        """Open a simple date picker popup."""
        popup = tk.Toplevel(self.root)
        popup.title("Select date")
        popup.geometry("220x220")
        popup.resizable(False, False)
        popup.grab_set()

        # Year/Month selectors
        frm = tk.Frame(popup)
        frm.pack(pady=8)

        import calendar
        from datetime import datetime

        # Parse current value or use today
        try:
            current = datetime.strptime(var.get(), "%Y%m%d")
        except (ValueError, TypeError):
            current = datetime.today()

        var_year = tk.IntVar(value=current.year)
        var_month = tk.IntVar(value=current.month)

        tk.Label(frm, text="Year:").pack(side="left", padx=4)
        sp_year = tk.Spinbox(frm, from_=2020, to=2030, width=6, textvariable=var_year)
        sp_year.pack(side="left", padx=2)
        tk.Label(frm, text="Month:").pack(side="left", padx=4)
        sp_month = tk.Spinbox(frm, from_=1, to=12, width=4, textvariable=var_month)
        sp_month.pack(side="left", padx=2)

        # Day selector
        frm2 = tk.Frame(popup)
        frm2.pack(pady=4)
        tk.Label(frm2, text="Day:").pack(side="left", padx=4)
        var_day = tk.IntVar(value=current.day)
        sp_day = tk.Spinbox(frm2, from_=1, to=31, width=4, textvariable=var_day)
        sp_day.pack(side="left", padx=2)

        # Preview
        lbl_preview = tk.Label(popup, text="", font=("Segoe UI", 11, "bold"), fg="#003366")
        lbl_preview.pack(pady=8)

        def update_preview(*args):
            try:
                y, m, d = var_year.get(), var_month.get(), var_day.get()
                lbl_preview.config(text=f"{y:04d}-{m:02d}-{d:02d}")
            except Exception:
                pass

        var_year.trace_add("write", update_preview)
        var_month.trace_add("write", update_preview)
        var_day.trace_add("write", update_preview)
        update_preview()

        def confirm():
            try:
                y, m, d = var_year.get(), var_month.get(), var_day.get()
                var.set(f"{y:04d}{m:02d}{d:02d}")
            except Exception:
                pass
            popup.destroy()

        tk.Button(popup, text="OK", command=confirm, bg="#0077B6", fg="white",
                  font=("Segoe UI", 9, "bold"), width=10, relief="flat").pack(pady=6)

        popup.wait_window()

    def _browse_bathy(self):
        f = filedialog.askopenfilename(filetypes=[("NetCDF", "*.nc"), ("All", "*.*")])
        if f:
            self.var_bathy.set(f)
            save_settings(self)

    def _get_root(self) -> str:
        r = resolve_root(self.var_root.get(), self.var_imei.get())
        if r:
            self.var_root.set(r)
        return r

    def _run_script(self, script_name: str, args: list):
        """Run a Python script in background thread."""
        script = SCRIPTS_DIR / script_name
        if not script.exists():
            self.log(f"ERROR: {script_name} not found")
            return
        cmd = [self.python_exe, str(script)] + args
        self.log(f">> {' '.join(args[:6])}")

        def _worker():
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, cwd=str(SCRIPTS_DIR))
                for line in proc.stdout:
                    self.root.after(0, self.log, line.rstrip())
                proc.wait()
                self.root.after(0, self.log, f"Exit code: {proc.returncode}")
                self.root.after(200, self._load_plots)
            except Exception as e:
                self.root.after(0, self.log, f"ERROR: {e}")

        threading.Thread(target=_worker, daemon=True).start()


    def _open_folder(self):
        r = self._get_root()
        prod = Path(r) / "products" if r else None
        target = prod if prod and prod.exists() else (Path(r) if r else None)
        if target and target.exists():
            if sys.platform == "win32":
                os.startfile(str(target))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        else:
            self.log("Folder not found.")

    def _show_app_password_help(self):
        """Show how to create an App Password for each email provider."""
        win = tk.Toplevel(self.root)
        win.title("How to create an App Password")
        win.geometry("520x380")
        win.resizable(False, False)

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        text = tk.Text(frame, wrap="word", font=("Consolas", 9), padx=10, pady=10)
        text.pack(fill="both", expand=True)

        help_text = """An App Password is NOT your regular email password.
It is a special password generated by your email provider
that allows third-party apps to access your account securely.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GMAIL:
  1. Go to https://myaccount.google.com/apppasswords
  2. You may need to enable 2-Step Verification first
  3. Select "Mail" and your device, then click "Generate"
  4. Copy the 16-character password (no spaces)

OUTLOOK / HOTMAIL:
  1. Go to https://account.microsoft.com/security
  2. Select "Advanced security options"
  3. Under "App passwords", click "Create a new app password"
  4. Copy the generated password

YAHOO:
  1. Go to https://login.yahoo.com/account/security
  2. Click "Generate app password"
  3. Select "Other App", give it a name
  4. Copy the generated password

iCLOUD:
  1. Go to https://appleid.apple.com
  2. Sign in, go to "App-Specific Passwords"
  3. Click "+" to generate a new password
  4. Copy the generated password

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NOTE: Your regular email password will NOT work.
You must generate an App Password as described above.
"""
        text.insert("1.0", help_text)
        text.config(state="disabled")

        tk.Button(win, text="Close", command=win.destroy, bg="#0077B6", fg="#ffffff",
                  font=("Segoe UI", 9, "bold"), relief="flat", padx=12).pack(pady=(0, 8))

    def _show_help(self):
        """Open the user manual in a scrollable window."""
        manual_path = DOCS_DIR / "USER_MANUAL.md"
        if not manual_path.exists():
            # Try alternative locations (standalone exe may have different structure)
            alt_paths = [
                Path(sys.executable).parent / "docs" / "USER_MANUAL.md",
                Path(sys.executable).parent / "USER_MANUAL.md",
            ]
            for alt in alt_paths:
                if alt.exists():
                    manual_path = alt
                    break
            else:
                messagebox.showinfo("Help",
                    "User manual not found.\n\n"
                    "Expected at: docs/USER_MANUAL.md\n"
                    "Visit: https://www.socib.es")
                return

        try:
            content = manual_path.read_text(encoding="utf-8")
        except Exception:
            content = manual_path.read_text(encoding="latin-1")

        help_win = tk.Toplevel(self.root)
        help_win.title("Argo SBD Decoder - User Manual")
        help_win.geometry("800x600")
        help_win.minsize(600, 400)

        # Scrollable text widget
        frame = tk.Frame(help_win)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        text = tk.Text(frame, wrap="word", font=("Consolas", 10),
                       yscrollcommand=scrollbar.set, padx=12, pady=12)
        text.pack(fill="both", expand=True)
        scrollbar.config(command=text.yview)

        text.insert("1.0", content)
        text.config(state="disabled")  # read-only

    def _install_deps(self):
        req = ROOT_DIR / "requirements.txt"
        if not req.exists():
            self.log("ERROR: requirements.txt not found")
            return
        self.log("Installing dependencies...")

        def _worker():
            try:
                # First ensure pip is available
                check = subprocess.run(
                    [self.python_exe, "-m", "pip", "--version"],
                    capture_output=True, text=True
                )
                if check.returncode != 0:
                    self.root.after(0, self.log, "pip not found, running ensurepip...")
                    ensurepip = subprocess.run(
                        [self.python_exe, "-m", "ensurepip", "--upgrade"],
                        capture_output=True, text=True
                    )
                    if ensurepip.returncode != 0:
                        self.root.after(0, self.log, "ERROR: Could not install pip. Install it manually:")
                        self.root.after(0, self.log, "  python -m ensurepip --upgrade")
                        self.root.after(0, self.log, "  Or reinstall Python with pip enabled.")
                        return
                    self.root.after(0, self.log, "pip installed successfully.")

                # Now install requirements
                cmd = [self.python_exe, "-m", "pip", "install", "-r", str(req)]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in proc.stdout:
                    self.root.after(0, self.log, line.rstrip())
                proc.wait()
                self.root.after(0, self.log, "Done." if proc.returncode == 0 else f"pip exit code: {proc.returncode}")
            except Exception as e:
                self.root.after(0, self.log, f"ERROR: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _download(self):
        save_settings(self)
        em, pw, im = self.var_email.get(), self.var_pwd.get(), self.var_imei.get()
        si, bf = self.var_since.get(), self.var_before.get()
        r = self._get_root()
        if not all([em, pw, im, si, bf, r]):
            self.log("ERROR: fill all download fields (Email, password, IMEI, dates, folder)")
            return
        Path(r).mkdir(parents=True, exist_ok=True)
        os.environ["IMAP_APP_PASSWORD"] = pw
        self.log(f"Downloading SBD files from {self.var_imap_server.get()}...")
        self._run_script("download_sbd_imap.py", [
            "--email", em, "--imei", im, "--since", si, "--before", bf,
            "--outdir", r, "--fromaddr", self.var_sender.get(), "--label", self.var_label.get(),
            "--imap_server", self.var_imap_server.get(), "--imap_port", self.var_imap_port.get()
        ])

    def _decode(self):
        save_settings(self)
        r = self._get_root()
        if not r or not Path(r).exists():
            self.log("ERROR: working folder not found")
            return
        self.log(f"Decoding (decoder_id={self.decoder_id})...")
        self._run_script("decode_sbd_batch.py", [
            "--root", r, "--decoder_id", str(self.decoder_id), "--wmo", self.var_wmo.get()
        ])

    def _quicklook(self):
        save_settings(self)
        r = self._get_root()
        o = str(Path(r) / "products")
        self.log("Generating TS + sections...")
        self._run_script("generate_quicklook_products.py", [
            "--root", r, "--outdir", o, "--imei", self.var_imei.get(),
            "--technical_csv", "Technical Message.csv"
        ])

    def _map(self):
        save_settings(self)
        r = self._get_root()
        o = str(Path(r) / "products")
        self.log("Generating map...")
        self._run_script("generate_quicklook_products.py", [
            "--root", r, "--outdir", o, "--imei", self.var_imei.get(),
            "--technical_csv", "Technical Message.csv", "--skip_profiles"
        ])

    def _nav(self):
        save_settings(self)
        r = self._get_root()
        o = str(Path(r) / "products")
        self.log("Generating forecast position...")
        self._run_script("generate_navigation_products.py", [
            "--root", r, "--outdir", o, "--imei", self.var_imei.get(),
            "--technical_csv", "Technical Message.csv"
        ])

    def _all(self):
        save_settings(self)
        r = self._get_root()
        if not r or not Path(r).exists():
            self.log("ERROR: working folder not found")
            return
        o = str(Path(r) / "products")
        im = self.var_imei.get()
        did = str(self.decoder_id)
        wmo = self.var_wmo.get()
        self.log("=== Full pipeline ===")

        def _pipeline():
            scripts = [
                ("decode_sbd_batch.py", ["--root", r, "--decoder_id", did, "--wmo", wmo]),
                ("generate_quicklook_products.py", ["--root", r, "--outdir", o, "--imei", im, "--technical_csv", "Technical Message.csv"]),
                ("generate_navigation_products.py", ["--root", r, "--outdir", o, "--imei", im, "--technical_csv", "Technical Message.csv"]),
            ]
            for i, (name, args) in enumerate(scripts, 1):
                self.root.after(0, self.log, f"Step {i}/{len(scripts)}: {name}...")
                script = SCRIPTS_DIR / name
                cmd = [self.python_exe, str(script)] + args
                try:
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, cwd=str(SCRIPTS_DIR))
                    for line in proc.stdout:
                        self.root.after(0, self.log, line.rstrip())
                    proc.wait()
                except Exception as e:
                    self.root.after(0, self.log, f"ERROR: {e}")
                    return
            self.root.after(0, self.log, "=== Pipeline complete ===")
            self.root.after(200, self._load_plots)

        threading.Thread(target=_pipeline, daemon=True).start()


# ─── Entry point ───
if __name__ == "__main__":
    root = tk.Tk()
    app = ArgoDecoderApp(root)
    root.mainloop()
