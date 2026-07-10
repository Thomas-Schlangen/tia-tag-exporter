"""Tkinter-Oberfläche für den TIA Tag Exporter."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from tia_tag_exporter.config_schema import AppConfig
from tia_tag_exporter.connector import TiaConnectionError

logger = logging.getLogger(__name__)

RunExportFn = Callable[..., None]

_DONE_SENTINEL = "__DONE__"


class TiaTagExporterApp(tk.Tk):
    """Hauptfenster: Versions-/Projekt-/Ordnerauswahl, Optionen, Start-Button, Statuszeile."""

    def __init__(self, config: AppConfig, run_export: RunExportFn) -> None:
        super().__init__()
        self.title("TIA Tag Exporter")
        self.resizable(False, False)

        self._config = config
        self._run_export = run_export
        self._status_queue: queue.Queue[str] = queue.Queue()
        self._export_thread: threading.Thread | None = None

        self._project_path: Path | None = None
        self._version = tk.StringVar(value=config.tia.version)
        self._output_dir = tk.StringVar(value=config.export.output_dir)
        self._include_plc = tk.BooleanVar(value=config.export.include_plc_tags)
        self._include_hmi = tk.BooleanVar(value=config.export.include_hmi_tags)
        self._include_db = tk.BooleanVar(value=config.export.include_db_variables)
        self._status = tk.StringVar(value="Bereit.")

        self._build_widgets()
        self.after(100, self._poll_status_queue)

    def _build_widgets(self) -> None:
        padding = {"padx": 8, "pady": 4}

        version_frame = ttk.Frame(self)
        version_frame.grid(row=0, column=0, columnspan=2, sticky="w", **padding)
        ttk.Label(version_frame, text="TIA-Version:").pack(side="left")
        version_names = list(self._config.tia.versions.keys())
        version_menu = tk.OptionMenu(version_frame, self._version, *version_names)
        version_menu.pack(side="left", padx=(8, 0))
        # OptionMenu setzt beim Erzeugen die Variable auf den ersten Eintrag —
        # die Vorauswahl aus der Config danach explizit wiederherstellen.
        self._version.set(self._config.tia.version)

        ttk.Button(self, text="TIA-Projekt wählen", command=self._choose_project).grid(
            row=1, column=0, sticky="w", **padding
        )
        self._project_label = ttk.Label(self, text="(kein Projekt gewählt)")
        self._project_label.grid(row=1, column=1, sticky="w", **padding)

        ttk.Button(self, text="Ausgabeordner wählen", command=self._choose_output_dir).grid(
            row=2, column=0, sticky="w", **padding
        )
        ttk.Label(self, textvariable=self._output_dir).grid(row=2, column=1, sticky="w", **padding)

        checks_frame = ttk.Frame(self)
        checks_frame.grid(row=3, column=0, columnspan=2, sticky="w", **padding)
        ttk.Checkbutton(checks_frame, text="PLC-Tags", variable=self._include_plc).pack(side="left")
        ttk.Checkbutton(checks_frame, text="HMI-Tags", variable=self._include_hmi).pack(side="left")
        ttk.Checkbutton(checks_frame, text="DB-Variablen", variable=self._include_db).pack(side="left")

        self._start_button = ttk.Button(self, text="Start Export", command=self._start_export)
        self._start_button.grid(row=4, column=0, columnspan=2, pady=(8, 4))

        status_bar = ttk.Label(self, textvariable=self._status, relief="sunken", anchor="w")
        status_bar.grid(row=5, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 8))

    def _choose_project(self) -> None:
        path = filedialog.askopenfilename(
            title="TIA-Projekt wählen",
            filetypes=[("TIA-Portal-Projekt", "*.ap19 *.ap20 *.ap21"), ("Alle Dateien", "*.*")],
        )
        if path:
            self._project_path = Path(path)
            self._project_label.configure(text=str(self._project_path))

    def _choose_output_dir(self) -> None:
        path = filedialog.askdirectory(
            title="Ausgabeordner wählen", initialdir=self._output_dir.get() or "."
        )
        if path:
            self._output_dir.set(path)

    def _start_export(self) -> None:
        if self._export_thread is not None and self._export_thread.is_alive():
            return

        if self._project_path is None:
            messagebox.showerror("TIA Tag Exporter", "Bitte zuerst ein TIA-Projekt auswählen.")
            return

        if not (self._include_plc.get() or self._include_hmi.get() or self._include_db.get()):
            messagebox.showerror(
                "TIA Tag Exporter", "Bitte mindestens eine Kategorie (PLC/HMI/DB) auswählen."
            )
            return

        version_config = self._config.tia.versions.get(self._version.get())
        if version_config is None:
            messagebox.showerror(
                "TIA Tag Exporter", f"Unbekannte TIA-Version: {self._version.get()}"
            )
            return

        output_path = Path(self._output_dir.get()) / f"{self._project_path.stem}_tags.xlsx"

        self._start_button.configure(state="disabled")
        self._status.set("Export läuft ...")

        self._export_thread = threading.Thread(
            target=self._run_export_thread,
            kwargs={
                "dll_path": version_config.dll_path,
                "project_path": self._project_path,
                "output_path": output_path,
                "include_plc": self._include_plc.get(),
                "include_hmi": self._include_hmi.get(),
                "include_db": self._include_db.get(),
            },
            daemon=True,
        )
        self._export_thread.start()

    def _run_export_thread(self, **kwargs: Any) -> None:
        try:
            self._run_export(progress=self._status_queue.put, **kwargs)
        except TiaConnectionError as exc:
            self._status_queue.put(f"FEHLER: Verbindung zu TIA Portal fehlgeschlagen: {exc}")
        except Exception as exc:  # noqa: BLE001 — letzte Instanz gegen rohe Tracebacks in der GUI
            logger.exception("Unerwarteter Fehler beim Export")
            self._status_queue.put(f"FEHLER: {exc}")
        finally:
            self._status_queue.put(_DONE_SENTINEL)

    def _poll_status_queue(self) -> None:
        try:
            while True:
                message = self._status_queue.get_nowait()
                if message == _DONE_SENTINEL:
                    self._start_button.configure(state="normal")
                else:
                    self._status.set(message)
        except queue.Empty:
            pass
        self.after(100, self._poll_status_queue)
