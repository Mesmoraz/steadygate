"""The windowed setup wizard. Tk widgets only: the logic lives in state.py and install.py."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tkinter as tk
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from installer.install import InstallError, WinRegistry, default_install_dir, run_install
from installer.state import Page, WizardState
from steadygate.scaffold import FORMATS, write_files

SITE_URL = "https://mesmoraz.github.io/steadygate/"
NO_DISPLAY = "steadygate-setup needs a desktop session. In a terminal, run: steadygate init"
_RED = "#b00020"
_AMBER = "#9a6700"
_AUDIENCES = (
    "For AI and LLM app teams running evals",
    "For QA and release owners signing off releases",
    "For teams with noisy, flaky tests",
)
_FIELDS = (
    ("results_path", "Results file"),
    ("metric", "Metric"),
    ("tolerance_text", "Tolerance"),
    ("eval_command", "Eval command"),
)


def os_name_for(platform: str) -> str:
    if platform == "win32":
        return "windows"
    return "macos" if platform == "darwin" else "linux"


def _cli_name() -> str:
    return "steadygate.exe" if sys.platform == "win32" else "steadygate"


def embedded_cli() -> Path:
    """The CLI bundled next to the wizard (``$STEADYGATE_CLI`` when not frozen)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(meipass) / "bin" / _cli_name()
    return Path(os.environ.get("STEADYGATE_CLI", _cli_name()))


def _attach_console() -> None:
    """A ``--windowed`` build has no stdout on Windows; borrow the parent's console."""
    if sys.platform != "win32" or sys.stdout is not None:
        return
    import ctypes

    if ctypes.windll.kernel32.AttachConsole(-1):
        sys.stdout = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115


def self_check() -> int:
    _attach_console()
    cli = embedded_cli()
    if not cli.is_file():
        print(f"steadygate-setup: cli={cli} missing", file=sys.stdout or sys.stderr)
        return 1
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [str(cli), "--version"], capture_output=True, text=True, creationflags=flags
        )
    except OSError as exc:
        print(f"steadygate-setup: cli={cli} failed: {exc}", file=sys.stdout or sys.stderr)
        return 1
    if proc.returncode != 0:
        print(f"steadygate-setup: cli={cli} exited {proc.returncode}", file=sys.stdout)
        return 1
    print(f"steadygate-setup: cli={cli} version={proc.stdout.strip()}", file=sys.stdout)
    if sys.stdout is not None:
        sys.stdout.flush()
    return 0


def _setter(state: WizardState, key: str) -> Callable[[str], None]:
    return lambda value: setattr(state, key, value)


class _App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.os_name = os_name_for(sys.platform)
        home = Path.home()
        self.state = WizardState(install_dir=default_install_dir(self.os_name, os.environ, home))
        base = tkfont.nametofont("TkDefaultFont")
        self.heading = base.copy()
        self.heading.configure(size=base.cget("size") + 5, weight="bold")
        # Pack the bar first so a tall page can never push the buttons out of the window.
        bar = ttk.Frame(root, padding=(16, 8))
        bar.pack(fill="x", side="bottom")
        self.body = ttk.Frame(root, padding=16)
        self.body.pack(fill="both", expand=True)
        ttk.Button(bar, text="Cancel", command=root.destroy).pack(side="right")
        self.next_btn = ttk.Button(bar, text="Next", command=self.on_next)
        self.next_btn.pack(side="right", padx=8)
        self.back_btn = ttk.Button(bar, text="Back", command=self.on_back)
        self.back_btn.pack(side="right")
        self.refresh: Callable[[], None] = lambda: None
        self.render()

    # navigation
    def render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.refresh = lambda: None
        builders = {
            Page.WELCOME: self.page_welcome,
            Page.LOCATION: self.page_location,
            Page.PROJECT: self.page_project,
            Page.DONE: self.page_done,
        }
        builders[self.state.page]()
        self.update_buttons()

    def update_buttons(self) -> None:
        page = self.state.page
        self.back_btn.state(["disabled"] if page is Page.WELCOME else ["!disabled"])
        self.next_btn.configure(text="Finish" if page is Page.DONE else "Next")
        ok = page is Page.DONE or self.state.can_go_next()
        self.next_btn.state(["!disabled"] if ok else ["disabled"])

    def on_next(self) -> None:
        s = self.state
        if s.page is Page.DONE:
            self.root.destroy()
            return
        if s.page is Page.PROJECT and s.setup_project and not s.errors():
            try:
                s.written = write_files(s.planned(), s.overwrite)
            except OSError as exc:
                messagebox.showerror("Steadygate setup", str(exc), parent=self.root)
                return
        elif s.page is Page.PROJECT:
            s.written = []
        s.next()
        self.render()

    def on_back(self) -> None:
        self.state.back()
        self.render()

    # helpers
    def title(self, text: str) -> None:
        ttk.Label(self.body, text=text, font=self.heading).pack(anchor="w", pady=(0, 12))

    def text_var(self, value: str, on_change: Callable[[str], None]) -> tk.StringVar:
        var = tk.StringVar(master=self.root, value=value)

        def changed(*_: object) -> None:
            on_change(var.get())
            self.refresh()
            self.update_buttons()

        var.trace_add("write", changed)
        return var

    # pages
    def page_welcome(self) -> None:
        self.title("Set up Steadygate")
        ttk.Label(
            self.body,
            text=(
                "Steadygate tells you whether a new version of your AI feature or flaky test "
                "suite is safe to ship, compared with your own history."
            ),
            wraplength=510,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))
        for line in _AUDIENCES:
            ttk.Label(self.body, text=f"•  {line}").pack(anchor="w", pady=2)

    def page_location(self) -> None:
        s = self.state
        self.title("Install location")
        row = ttk.Frame(self.body)
        row.pack(fill="x")
        folder = self.text_var(str(s.install_dir), s.set_install_dir)
        ttk.Entry(row, textvariable=folder).pack(side="left", fill="x", expand=True)
        folder_error = ttk.Label(self.body, text="", foreground=_RED)

        def change() -> None:
            chosen = filedialog.askdirectory(initialdir=str(s.install_dir), parent=self.root)
            if chosen:
                folder.set(str(Path(chosen)))

        ttk.Button(row, text="Change…", command=change).pack(side="left", padx=(8, 0))
        folder_error.pack(anchor="w")
        on_path = tk.BooleanVar(master=self.root, value=s.add_to_path)
        ttk.Checkbutton(
            self.body,
            text="Add steadygate to my PATH",
            variable=on_path,
            command=lambda: setattr(s, "add_to_path", on_path.get()),
        ).pack(anchor="w", pady=12)
        result = ttk.Label(self.body, text="", wraplength=510, justify="left")
        button = ttk.Button(self.body, text="Install")

        def install() -> None:
            if s.errors():
                return
            registry = WinRegistry() if self.os_name == "windows" else None
            try:
                done = run_install(
                    embedded_cli(),
                    s.install_dir,
                    s.add_to_path,
                    os_name=self.os_name,
                    env=os.environ,
                    home=Path.home(),
                    registry=registry,
                )
            except (InstallError, OSError) as exc:
                result.configure(text=str(exc), foreground=_RED)
                button.configure(text="Retry")
                return
            s.installed = done
            note = "PATH updated." if done.path_changed else "PATH not changed."
            result.configure(text=f"Installed {done.binary}\n{note}", foreground="")
            button.configure(text="Install")
            self.update_buttons()

        button.configure(command=install)
        button.pack(anchor="w")

        def refresh() -> None:
            message = s.errors().get("install_dir", "")
            folder_error.configure(text=message)
            button.state(["disabled"] if message else ["!disabled"])

        self.refresh = refresh
        refresh()
        result.pack(anchor="w", pady=12)
        if s.installed is not None:
            result.configure(text=f"Installed {s.installed.binary}")

    def page_project(self) -> None:
        s = self.state
        self.title("Set up a repository")
        form = ttk.Frame(self.body)
        on = tk.BooleanVar(master=self.root, value=s.setup_project)

        def toggle() -> None:
            s.setup_project = on.get()
            self.refresh()
            self.update_buttons()

        ttk.Checkbutton(
            self.body, text="Set up a repository now", variable=on, command=toggle
        ).pack(anchor="w")
        form.pack(fill="both", expand=True, pady=(8, 0))
        form.columnconfigure(1, weight=1)
        errors: dict[str, ttk.Label] = {}

        def field_row(row: int, key: str, label: str) -> ttk.Frame:
            ttk.Label(form, text=label).grid(row=row * 2, column=0, sticky="w", padx=(0, 8))
            cell = ttk.Frame(form)
            cell.grid(row=row * 2, column=1, sticky="ew")
            errors[key] = ttk.Label(form, text="", foreground=_RED)
            errors[key].grid(row=row * 2 + 1, column=1, sticky="w")
            return cell

        repo_cell = field_row(0, "repo", "Folder")
        repo = self.text_var(
            str(s.repo or ""), lambda v: setattr(s, "repo", Path(v) if v else None)
        )
        ttk.Entry(repo_cell, textvariable=repo).pack(side="left", fill="x", expand=True)

        def pick() -> None:
            chosen = filedialog.askdirectory(parent=self.root)
            if chosen:
                repo.set(str(Path(chosen)))

        ttk.Button(repo_cell, text="Choose…", command=pick).pack(side="left", padx=(8, 0))
        fmt_cell = field_row(1, "format", "Format")
        fmt = ttk.Combobox(fmt_cell, values=list(FORMATS), state="readonly", width=14)
        fmt.set(s.format)
        fmt.pack(side="left")
        field_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate(_FIELDS, start=2):
            cell = field_row(i, key, label)
            field_vars[key] = self.text_var(str(getattr(s, key)), _setter(s, key))
            ttk.Entry(cell, textvariable=field_vars[key]).pack(fill="x")

        def format_chosen(_: object) -> None:
            s.set_format(next(f for f in FORMATS if f == fmt.get()))
            for key, var in field_vars.items():
                var.set(str(getattr(s, key)))

        fmt.bind("<<ComboboxSelected>>", format_chosen)
        warn = ttk.Label(form, text="", foreground=_AMBER, wraplength=510, justify="left")
        warn.grid(row=12, column=0, columnspan=2, sticky="w")
        files = ttk.Frame(form)
        files.grid(row=13, column=0, columnspan=2, sticky="w", pady=(4, 0))
        shown: list[list[tuple[Path, bool]] | None] = [None]

        def refresh() -> None:
            found = s.errors()
            for key, lab in errors.items():
                lab.configure(text=found.get(key, ""))
                (lab.grid if key in found else lab.grid_remove)()
            warn.configure(text="\n".join(s.warnings()))
            planned = s.planned() if s.setup_project and not found else []
            listing = [(p.path, p.exists) for p in planned]
            if listing == shown[0]:
                return
            shown[0] = listing
            for child in files.winfo_children():
                child.destroy()
            for item in planned:
                if not item.exists:
                    ttk.Label(files, text=f"Will write {item.path}").pack(anchor="w")
                    continue
                var = tk.BooleanVar(master=self.root, value=item.path in s.overwrite)

                def flip(p: Path = item.path, v: tk.BooleanVar = var) -> None:
                    (s.overwrite.add if v.get() else s.overwrite.discard)(p)

                ttk.Checkbutton(
                    files, text=f"Overwrite {item.path}", variable=var, command=flip
                ).pack(anchor="w")

        self.refresh = refresh
        refresh()

    def page_done(self) -> None:
        s = self.state
        self.title("All set")
        done: list[str] = []
        if s.installed is not None:
            done.append(f"Installed {s.installed.binary}")
        done += [f"Wrote {p}" for p in s.written]
        for line in done:
            ttk.Label(self.body, text=line, wraplength=510).pack(anchor="w")
        cli = "steadygate"
        if s.installed is not None and not s.add_to_path:
            cli = f'"{s.installed.binary}"'
        steps = [f"In a new terminal, run:  {cli} --version"]
        if s.written and s.repo is not None:
            root = s.repo.resolve()
            rel = " ".join(p.relative_to(root).as_posix() for p in s.written)
            steps += [f"In {s.repo}, run:  git add {rel}", "Then commit and push to main."]
        else:
            steps.append("To set up a repository later, run:  steadygate init")
        text = tk.Text(self.body, height=5, wrap="word", relief="flat")
        text.insert("1.0", "\n".join(steps))
        text.configure(state="disabled")
        text.pack(fill="x", pady=12)
        ttk.Button(
            self.body, text="Open the Steadygate site", command=lambda: webbrowser.open(SITE_URL)
        ).pack(anchor="w")


def _run_gui() -> None:
    root = tk.Tk()
    root.title("Steadygate setup")
    root.geometry("560x420")
    root.resizable(False, False)
    _App(root)
    root.mainloop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="steadygate-setup")
    parser.add_argument("--self-check", action="store_true", help="check the bundled CLI")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    try:
        _run_gui()
    except tk.TclError:
        print(NO_DISPLAY, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
