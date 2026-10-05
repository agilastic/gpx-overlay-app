"""Tkinter GUI: pick a GPX (and optional video), toggle Speed/Elevation/HR/Map overlays with
position, style, and theme, preview a frame, and render the output."""
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from overlay_renderer import POSITIONS, THEMES, DEFAULT_THEME, SPEED_STYLES
from video_builder import (
    render_overlay_video, render_preview_frame, target_dimensions, resolve_fps, RenderCancelled,
    RESOLUTIONS, DEFAULT_RESOLUTION, ASPECT_RATIOS, DEFAULT_ASPECT, FPS_OPTIONS, DEFAULT_FPS,
)


WIDGET_DEFS = [
    ("speed", "Speed", "bottom-left"),
    ("elevation", "Elevation", "bottom-right"),
    ("hr", "Heart Rate", "top-left"),
    ("map", "Map", "top-right"),
]

PREVIEW_W, PREVIEW_H = 480, 270


class WidgetControl(ttk.LabelFrame):
    def __init__(self, master, key, label, default_position, on_change):
        super().__init__(master, text=label, padding=8)
        self.key = key
        self.on_change = on_change

        self.enabled = tk.BooleanVar(value=key in ("speed", "elevation"))
        self.position = tk.StringVar(value=default_position)
        self.scale = tk.DoubleVar(value=2.0)
        self.opacity = tk.DoubleVar(value=1.0)
        self.unit = tk.StringVar(value="kmh" if key == "speed" else ("m" if key == "elevation" else ""))
        self.style = tk.StringVar(value=SPEED_STYLES[0])
        self.max_speed = tk.DoubleVar(value=120)
        self.show_box = tk.BooleanVar(value=False)
        self.text_black = tk.BooleanVar(value=False)

        for var in (self.enabled, self.position, self.scale, self.opacity, self.unit, self.style,
                    self.max_speed, self.show_box, self.text_black):
            var.trace_add("write", lambda *_: self.on_change())

        chk = ttk.Checkbutton(self, text="Enable", variable=self.enabled)
        chk.grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(self, text="Position").grid(row=1, column=0, sticky="w")
        pos_menu = ttk.Combobox(self, textvariable=self.position, values=POSITIONS, state="readonly", width=12)
        pos_menu.grid(row=1, column=1, sticky="w")

        ttk.Label(self, text="Scale").grid(row=2, column=0, sticky="w")
        self._build_slider_with_entry(row=2, variable=self.scale, from_=0.5, to=6.0)

        ttk.Label(self, text="Opacity").grid(row=3, column=0, sticky="w")
        self._build_slider_with_entry(row=3, variable=self.opacity, from_=0.1, to=1.0)

        if key == "speed":
            ttk.Label(self, text="Unit").grid(row=4, column=0, sticky="w")
            unit_menu = ttk.Combobox(self, textvariable=self.unit, values=["kmh", "mph"], state="readonly", width=12)
            unit_menu.grid(row=4, column=1, sticky="w")

            ttk.Label(self, text="Style").grid(row=5, column=0, sticky="w")
            style_menu = ttk.Combobox(self, textvariable=self.style, values=SPEED_STYLES, state="readonly", width=12)
            style_menu.grid(row=5, column=1, sticky="w")

            ttk.Label(self, text="Max speed").grid(row=6, column=0, sticky="w")
            self._build_slider_with_entry(row=6, variable=self.max_speed, from_=10, to=200, increment=5)
        elif key == "elevation":
            ttk.Label(self, text="Unit").grid(row=4, column=0, sticky="w")
            unit_menu = ttk.Combobox(self, textvariable=self.unit, values=["m", "ft"], state="readonly", width=12)
            unit_menu.grid(row=4, column=1, sticky="w")

        toggles_row = 7
        box_chk = ttk.Checkbutton(self, text="Show box", variable=self.show_box)
        box_chk.grid(row=toggles_row, column=0, sticky="w")
        black_chk = ttk.Checkbutton(self, text="Black text", variable=self.text_black)
        black_chk.grid(row=toggles_row, column=1, sticky="w")

        self.columnconfigure(1, weight=1)

    def _build_slider_with_entry(self, row, variable, from_, to, increment=0.05):
        row_frame = ttk.Frame(self)
        row_frame.grid(row=row, column=1, sticky="we")
        row_frame.columnconfigure(0, weight=1)

        slider = ttk.Scale(row_frame, from_=from_, to=to, variable=variable, orient="horizontal")
        slider.grid(row=0, column=0, sticky="we")

        spin = ttk.Spinbox(row_frame, from_=from_, to=to, increment=increment, textvariable=variable, width=6)
        spin.grid(row=0, column=1, padx=(6, 0))
        return row_frame

    def to_config(self):
        cfg = {
            "enabled": self.enabled.get(),
            "position": self.position.get(),
            "scale": round(self.scale.get(), 2),
            "opacity": round(self.opacity.get(), 2),
            "show_box": self.show_box.get(),
            "text_black": self.text_black.get(),
        }
        if self.key in ("speed", "elevation"):
            cfg["unit"] = self.unit.get()
        if self.key == "speed":
            cfg["style"] = self.style.get()
            cfg["max_speed_kmh"] = round(self.max_speed.get(), 1)
        return cfg


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=16)
        self.master = master
        master.title("GPX Video Overlay")
        self.pack(fill="both", expand=True)

        self.video_path = tk.StringVar()
        self.gpx_path = tk.StringVar()
        self.out_path = tk.StringVar()
        self.theme_name = tk.StringVar(value=DEFAULT_THEME)
        self.resolution_name = tk.StringVar(value=DEFAULT_RESOLUTION)
        self.aspect_name = tk.StringVar(value=DEFAULT_ASPECT)
        self.fps_value = tk.StringVar(value=DEFAULT_FPS)
        self.enable_all = tk.BooleanVar(value=False)
        self.preview_time = tk.DoubleVar(value=0.0)
        self._gpx_duration = 0.0
        self._preview_imgtk = None
        self._suppress_enable_all_cb = False

        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="nsew")
        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky="n", padx=(16, 0))
        self.columnconfigure(0, weight=1)

        self._build_file_row(left, "GPX file (required)", self.gpx_path, self._pick_gpx, row=0)
        self._build_file_row(left, "Action-cam video (optional)", self.video_path, self._pick_video, row=1)
        self._build_file_row(left, "Output file", self.out_path, self._pick_output, row=2, save=True)

        theme_row = ttk.Frame(left)
        theme_row.grid(row=3, column=0, columnspan=3, sticky="we", pady=(12, 0))
        ttk.Label(theme_row, text="Theme").pack(side="left")
        theme_menu = ttk.Combobox(theme_row, textvariable=self.theme_name, values=list(THEMES.keys()),
                                   state="readonly", width=20)
        theme_menu.pack(side="left", padx=8)
        theme_menu.bind("<<ComboboxSelected>>", lambda e: self._schedule_preview())

        all_chk = ttk.Checkbutton(theme_row, text="Enable All Overlays", variable=self.enable_all,
                                   command=self._on_enable_all)
        all_chk.pack(side="left", padx=(24, 0))

        output_row = ttk.Frame(left)
        output_row.grid(row=4, column=0, columnspan=3, sticky="we", pady=(8, 0))
        ttk.Label(output_row, text="Resolution").pack(side="left")
        res_menu = ttk.Combobox(output_row, textvariable=self.resolution_name, values=list(RESOLUTIONS.keys()),
                                 state="readonly", width=16)
        res_menu.pack(side="left", padx=8)
        res_menu.bind("<<ComboboxSelected>>", lambda e: self._schedule_preview())

        ttk.Label(output_row, text="Aspect").pack(side="left", padx=(12, 0))
        aspect_menu = ttk.Combobox(output_row, textvariable=self.aspect_name, values=list(ASPECT_RATIOS.keys()),
                                    state="readonly", width=16)
        aspect_menu.pack(side="left", padx=8)
        aspect_menu.bind("<<ComboboxSelected>>", lambda e: self._schedule_preview())

        ttk.Label(output_row, text="FPS").pack(side="left", padx=(12, 0))
        fps_menu = ttk.Combobox(output_row, textvariable=self.fps_value, values=FPS_OPTIONS,
                                 state="readonly", width=10)
        fps_menu.pack(side="left", padx=8, fill=None, expand=False)

        widgets_frame = ttk.Frame(left)
        widgets_frame.grid(row=5, column=0, columnspan=3, pady=(16, 8), sticky="we")
        self.controls = {}
        for i, (key, label, default_pos) in enumerate(WIDGET_DEFS):
            ctrl = WidgetControl(widgets_frame, key, label, default_pos, self._schedule_preview)
            ctrl.grid(row=i // 2, column=i % 2, padx=8, pady=8, sticky="we")
            widgets_frame.columnconfigure(i % 2, weight=1)
            self.controls[key] = ctrl

        slider_row = ttk.Frame(left)
        slider_row.grid(row=6, column=0, columnspan=3, sticky="we", pady=(4, 0))
        ttk.Label(slider_row, text="Preview time").pack(side="left")
        self.time_slider = ttk.Scale(slider_row, from_=0, to=100, variable=self.preview_time,
                                      orient="horizontal", command=lambda v: self._schedule_preview())
        self.time_slider.pack(side="left", fill="x", expand=True, padx=8)

        self.progress = ttk.Progressbar(left, mode="determinate", maximum=100)
        self.progress.grid(row=7, column=0, columnspan=3, sticky="we", pady=(12, 4))

        self.status_label = ttk.Label(left, text="Ready")
        self.status_label.grid(row=8, column=0, columnspan=3, sticky="w")

        render_row = ttk.Frame(left)
        render_row.grid(row=9, column=0, columnspan=3, pady=(12, 0), sticky="we")
        render_row.columnconfigure(0, weight=1)

        self.render_btn = ttk.Button(render_row, text="Render Overlay", command=self._on_render)
        self.render_btn.grid(row=0, column=0, sticky="we")

        self.cancel_btn = ttk.Button(render_row, text="Cancel", command=self._on_cancel, state="disabled")
        self.cancel_btn.grid(row=0, column=1, padx=(8, 0))

        self._cancel_event = None

        left.columnconfigure(1, weight=1)

        ttk.Label(right, text="Preview").pack(anchor="w")
        self.preview_canvas = tk.Canvas(right, width=PREVIEW_W, height=PREVIEW_H, bg="#222222",
                                         highlightthickness=1, highlightbackground="#555555")
        self.preview_canvas.pack()
        self._checker_bg = self._make_checker(PREVIEW_W, PREVIEW_H)
        self._draw_preview_image(self._checker_bg)

        self._preview_job = None

    def _make_checker(self, w, h, cell=12):
        img = Image.new("RGB", (w, h), (60, 60, 60))
        px = img.load()
        for y in range(h):
            for x in range(w):
                if (x // cell + y // cell) % 2 == 0:
                    px[x, y] = (80, 80, 80)
        return img

    def _draw_preview_image(self, pil_image):
        img = pil_image.resize((PREVIEW_W, PREVIEW_H))
        self._preview_imgtk = ImageTk.PhotoImage(img)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(0, 0, anchor="nw", image=self._preview_imgtk)

    def _build_file_row(self, parent, label, var, command, row, save=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=var, width=46)
        entry.grid(row=row, column=1, sticky="we", padx=8)
        ttk.Button(parent, text="Browse…", command=command).grid(row=row, column=2)

    def _pick_video(self):
        path = filedialog.askopenfilename(
            title="Select action-cam video (optional)",
            filetypes=[("Video files", "*.mp4 *.mov *.MP4 *.MOV *.avi"), ("All files", "*.*")],
        )
        if path:
            self.video_path.set(path)
            self._sync_output_default()
            self._schedule_preview()

    def _pick_gpx(self):
        path = filedialog.askopenfilename(
            title="Select GPX file", filetypes=[("GPX files", "*.gpx"), ("All files", "*.*")]
        )
        if path:
            self.gpx_path.set(path)
            self._sync_output_default()
            self._schedule_preview()

    def _sync_output_default(self):
        if self.out_path.get():
            return
        if self.video_path.get():
            base, _ = os.path.splitext(self.video_path.get())
            self.out_path.set(f"{base}_overlay.mp4")
        elif self.gpx_path.get():
            base, _ = os.path.splitext(self.gpx_path.get())
            self.out_path.set(f"{base}_overlay.mov")

    def _pick_output(self):
        path = filedialog.asksaveasfilename(
            title="Save overlay video as", defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4"), ("QuickTime MOV", "*.mov")],
        )
        if path:
            self.out_path.set(path)

    def _on_enable_all(self):
        state = self.enable_all.get()
        for ctrl in self.controls.values():
            ctrl.enabled.set(state)

    def _schedule_preview(self):
        if self._preview_job:
            self.master.after_cancel(self._preview_job)
        self._preview_job = self.master.after(300, self._do_preview)

    def _do_preview(self):
        self._preview_job = None
        gpx_path = self.gpx_path.get().strip()
        if not gpx_path or not os.path.isfile(gpx_path):
            self._draw_preview_image(self._checker_bg)
            return

        widget_config = {key: ctrl.to_config() for key, ctrl in self.controls.items()}
        if not any(c["enabled"] for c in widget_config.values()):
            self._draw_preview_image(self._checker_bg)
            return

        video_path = self.video_path.get().strip() or None
        out_w, out_h = target_dimensions(self.resolution_name.get(), self.aspect_name.get())
        try:
            frame, duration = render_preview_frame(
                gpx_path, widget_config, theme=self.theme_name.get(),
                canvas=(out_w, out_h), elapsed_s=self.preview_time.get(),
                video_path=None,
            )
            self._gpx_duration = duration
            self.time_slider.config(to=max(duration, 0.1))

            if video_path and os.path.isfile(video_path):
                composed = Image.new("RGBA", frame.size, (0, 0, 0, 255))
                composed.alpha_composite(frame)
                self._draw_preview_image(composed.convert("RGB"))
            else:
                bg = self._checker_bg.convert("RGBA")
                bg = bg.resize(frame.size)
                bg.alpha_composite(frame)
                self._draw_preview_image(bg.convert("RGB"))
        except Exception as exc:
            self.status_label.config(text=f"Preview error: {exc}")
            err_img = Image.new("RGB", (PREVIEW_W, PREVIEW_H), (40, 20, 20))
            draw = ImageDraw.Draw(err_img)
            draw.text((10, 10), f"Preview error:\n{exc}", fill=(255, 120, 120))
            self._draw_preview_image(err_img)

    def _on_render(self):
        video_path = self.video_path.get().strip() or None
        gpx_path = self.gpx_path.get().strip()
        out_path = self.out_path.get().strip()

        if not gpx_path or not os.path.isfile(gpx_path):
            messagebox.showerror("Missing GPX", "Please select a valid GPX file.")
            return
        if video_path and not os.path.isfile(video_path):
            messagebox.showerror("Video not found", "The selected action-cam video file does not exist.")
            return
        if not out_path:
            messagebox.showerror("Missing output", "Please choose where to save the output video.")
            return

        widget_config = {key: ctrl.to_config() for key, ctrl in self.controls.items()}
        if not any(c["enabled"] for c in widget_config.values()):
            messagebox.showerror("No overlays selected", "Enable at least one overlay (Speed/Elevation/HR/Map).")
            return

        if not video_path and not out_path.lower().endswith(".mov"):
            messagebox.showinfo(
                "Transparent overlay export",
                "No video selected: output will be a transparent-background .mov overlay "
                "containing only the graphics, with no audio or video of its own.\n"
                "Layer it over your footage in a video editor, where your footage's own audio "
                "is already present.\n"
                "Tip: use a .mov output filename for this mode.",
            )

        fps = self.fps_value.get()
        fps_numeric, _ = resolve_fps(fps)
        duration_s = self._gpx_duration
        est_frames = int(duration_s * fps_numeric)
        if duration_s > 3600:
            hours = duration_s / 3600
            proceed = messagebox.askyesno(
                "Long track — this will take a while",
                f"This GPX track is about {hours:.1f} hours long, which means rendering "
                f"roughly {est_frames:,} frames.\n\n"
                "This can take a long time and produce a very large output file "
                "(tens of GB for a transparent ProRes export).\n\n"
                "Continue?",
            )
            if not proceed:
                return

        self.render_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.progress["value"] = 0
        self.status_label.config(text="Starting render…")

        theme = self.theme_name.get()
        resolution = self.resolution_name.get()
        aspect = self.aspect_name.get()
        self._cancel_event = threading.Event()
        thread = threading.Thread(
            target=self._render_worker,
            args=(gpx_path, video_path, out_path, widget_config, theme, resolution, aspect, fps,
                  self._cancel_event),
            daemon=True,
        )
        thread.start()

    def _on_cancel(self):
        if self._cancel_event is not None:
            self._cancel_event.set()
            self.cancel_btn.config(state="disabled")
            self.status_label.config(text="Cancelling…")

    def _render_worker(self, gpx_path, video_path, out_path, widget_config, theme, resolution, aspect, fps,
                        cancel_event):
        def progress_cb(stage, fraction):
            if stage == "rendering_overlay":
                pct = fraction * 70
                text = f"Rendering overlay frames… {int(fraction * 100)}%"
            else:
                pct = 70 + fraction * 30
                text = f"Encoding output… {int(fraction * 100)}%"
            self.master.after(0, self._update_progress, pct, text)

        try:
            render_overlay_video(
                gpx_path, out_path, widget_config, video_path=video_path,
                theme=theme, resolution=resolution, aspect=aspect, fps=fps, progress_cb=progress_cb,
                cancel_event=cancel_event,
            )
            self.master.after(0, self._render_done, out_path, None)
        except RenderCancelled:
            self.master.after(0, self._render_cancelled, out_path)
        except Exception as exc:
            self.master.after(0, self._render_done, out_path, exc)

    def _render_cancelled(self, out_path):
        self.render_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self._cancel_event = None
        self.progress["value"] = 0
        self.status_label.config(text="Cancelled")
        if out_path and os.path.isfile(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass

    def _update_progress(self, pct, text):
        self.progress["value"] = pct
        self.status_label.config(text=text)

    def _render_done(self, out_path, error):
        self.render_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self._cancel_event = None
        if error:
            self.progress["value"] = 0
            self.status_label.config(text="Failed")
            messagebox.showerror("Render failed", str(error))
        else:
            self.progress["value"] = 100
            self.status_label.config(text=f"Done: {out_path}")
            messagebox.showinfo("Render complete", f"Overlay video saved to:\n{out_path}")


def main():
    root = tk.Tk()
    root.geometry("1120x800")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
