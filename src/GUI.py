import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import os
import json
import slicerSoftware
import configFunctions as cF
import threading
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.patches as patches
import slicerSoftware as ss
import re
from pygerber.gerber.api import GerberFile
import os, json
from slicerSoftware import GERBER_EXTENSIONS

GERBfile = ""

def load_config_into_fields(fields):
    """Load existing config.json values into the input fields on launch."""
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path, "r") as f:
            config = json.load(f)

        fields["layerHeight"].delete(0, tk.END)
        fields["layerHeight"].insert(0, config.get("layerHeight", 0.2))

        fields["printSpeed"].delete(0, tk.END)
        fields["printSpeed"].insert(0, config.get("printSpeed", 60))

        bed = config.get("maxBedSize", [220, 220, 250])
        fields["bedX"].delete(0, tk.END)
        fields["bedX"].insert(0, bed[0])
        fields["bedY"].delete(0, tk.END)
        fields["bedY"].insert(0, bed[1])
        fields["bedZ"].delete(0, tk.END)
        fields["bedZ"].insert(0, bed[2])

        fields["layerMode"].set(config.get("layerMode", "single"))

        fields["nozzleSize"].delete(0, tk.END)
        fields["nozzleSize"].insert(0, config.get("nozzleSize", 0.225))

        fields["traceWidth"].delete(0, tk.END)
        fields["traceWidth"].insert(0, config.get("traceWidth", 0.25))

        fields["cureDryTemp"].delete(0, tk.END)
        fields["cureDryTemp"].insert(0, config.get("cure_dry_temp", 90))

        fields["cureDrySeconds"].delete(0, tk.END)
        fields["cureDrySeconds"].insert(0, config.get("cure_dry_seconds", 300))

        fields["cureTemp"].delete(0, tk.END)
        fields["cureTemp"].insert(0, config.get("cure_temp", 170))

        fields["cureSeconds"].delete(0, tk.END)
        fields["cureSeconds"].insert(0, config.get("cure_seconds", 900))

        fields["gerberFile"].delete(0, tk.END)
        fields["gerberFile"].insert(0, config.get("gerberFile", ""))

        fields["gerberJobFile"].delete(0, tk.END)
        fields["gerberJobFile"].insert(0, config.get("gerberJobFile", ""))
        active = config.get("activeHeads", ["conductor3"])
        if active:
            fields["activeHead"].set(active[0])

    except Exception as e:
        print(f"Could not load config: {e}")

def load_heads_into_dropdown(fields):
    """Load head IDs from config into the head selector dropdown."""
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path, "r") as f:
            config = json.load(f)
        heads = config.get("heads", [])
        all_heads = [h["id"] for h in heads]
        if all_heads:
            fields["activeHead"].set(all_heads[0])
            menu = fields["activeHeadMenu"]["menu"]
            menu.delete(0, "end")
            for h in all_heads:
                menu.add_command(label=h, command=lambda v=h: [
                    fields["activeHead"].set(v),
                    on_head_change(fields, v)
                ])
    except Exception as e:
        print(f"Could not load heads: {e}")

def on_head_change(fields, head_id):
    """Update ink settings fields when active head changes."""
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path, "r") as f:
            config = json.load(f)
        heads = config.get("heads", [])
        head = next((h for h in heads if h.get("id") == head_id), None)
        if not head:
            return

        fields["nozzleSize"].delete(0, tk.END)
        fields["nozzleSize"].insert(0, head.get("nozzleSize", 0.225))

        fields["traceWidth"].delete(0, tk.END)
        fields["traceWidth"].insert(0, head.get("traceWidth", fields["traceWidth"].get()))

        fields["cureDryTemp"].delete(0, tk.END)
        fields["cureDryTemp"].insert(0, head.get("cureDryTemp", 90))

        fields["cureDrySeconds"].delete(0, tk.END)
        fields["cureDrySeconds"].insert(0, head.get("cureDrySeconds", 300))

        fields["cureTemp"].delete(0, tk.END)
        fields["cureTemp"].insert(0, head.get("cureTemp", 170))

        fields["cureSeconds"].delete(0, tk.END)
        fields["cureSeconds"].insert(0, head.get("cureSeconds", 900))

    except Exception as e:
        print(f"Could not update head settings: {e}")

def save_settings(fields, output):
    """Save all field values to config.json."""
    if not validate_fields(fields):
        output.insert(tk.END, "Fix invalid fields before saving.\n")
        output.see(tk.END)
        return
    try:
        updates = {
            "layerHeight": float(fields["layerHeight"].get()),
            "printSpeed": float(fields["printSpeed"].get()),
            "maxBedSize": [
                float(fields["bedX"].get()),
                float(fields["bedY"].get()),
                float(fields["bedZ"].get())
            ],
            "layerMode": fields["layerMode"].get(),
            "nozzleSize": float(fields["nozzleSize"].get()),
            "traceWidth": float(fields["traceWidth"].get()),
            "cure_dry_temp": float(fields["cureDryTemp"].get()),
            "cure_dry_seconds": float(fields["cureDrySeconds"].get()),
            "cure_temp": float(fields["cureTemp"].get()),
            "cure_seconds": float(fields["cureSeconds"].get()),
            "gerberFile": fields["gerberFile"].get(),
            "gerberJobFile": fields["gerberJobFile"].get(),
            "activeHeads": [fields["activeHead"].get()]
        }
        cF.updConf(updates)
        output.insert(tk.END, "Settings saved.\n")
        output.see(tk.END)
    except ValueError:
        messagebox.showerror("Error", "Invalid input. Please enter numeric values.")


def browse_file(fields, key="gerberFile"):
    path = filedialog.askopenfilename(
        title="Select a ZIP file",
        filetypes=[("ZIP files", "*.zip")]
    )
    if path:
        fields[key].delete(0, tk.END)
        fields[key].insert(0, path)

def browse_job_file(fields):
    path = filedialog.askopenfilename(
        title="Select a .gbrjob file",
        filetypes=[("Gerber job files", "*.gbrjob")]
    )
    if path:
        fields["gerberJobFile"].delete(0, tk.END)
        fields["gerberJobFile"].insert(0, path)

def previewPCB(fields, root):
    gerber_zip = fields["gerberFile"].get()
    gerber_job_file = fields["gerberJobFile"].get()
    if not gerber_zip:
        messagebox.showerror("Error", "Please set Gerber file first.")
        return

    try:
        import zipfile
        project_root    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        gerber_zip_full = os.path.join(project_root, gerber_zip)
        extract_dir     = os.path.join(project_root, os.path.dirname(gerber_zip))
        gerber_name     = os.path.splitext(os.path.basename(gerber_zip))[0]
        extract_subdir  = os.path.join(extract_dir, gerber_name)
        os.makedirs(extract_subdir, exist_ok=True)

        if gerber_zip.endswith(".zip") and os.path.exists(gerber_zip_full):
            with zipfile.ZipFile(gerber_zip_full, "r") as z:
                z.extractall(extract_subdir)

        # if zip extracted into a single subfolder, use that as the scan dir
        entries = os.listdir(extract_subdir)
        if len(entries) == 1 and os.path.isdir(os.path.join(extract_subdir, entries[0])):
            extract_subdir = os.path.join(extract_subdir, entries[0])

        gerber_job_path = os.path.join(project_root, gerber_job_file)
        gbr_path = None

        # try KiCad job file first
        try:
            from pygerber.gerber.api import GerberJobFile
            gerber_job = GerberJobFile.from_file(gerber_job_path)
            for fa in gerber_job.files_attributes:
                if "copper" in fa.file_function.lower() and "top" in fa.file_function.lower():
                    gbr_path = os.path.join(project_root, os.path.dirname(gerber_zip), fa.path)
                    break
        except Exception:
            pass

        # fallback: scan extracted directory for top copper by filename
        if gbr_path is None:
            print(f"scanning: {extract_subdir}")
            print(f"files: {os.listdir(extract_subdir)}")
            for fname in sorted(os.listdir(extract_subdir)):
                if os.path.splitext(fname.lower())[1] not in GERBER_EXTENSIONS:
                    continue
                layer = ss.get_layer_type_from_filename(fname)
                if layer == "copper_top":
                    gbr_path = os.path.join(extract_subdir, fname)
                    break

        if gbr_path is None:
            messagebox.showerror("Error", "Could not find top copper layer.")
            return

        # detect scale and divisor from the actual gerber file
        gerber_file_raw = GerberFile.from_file(gbr_path)
        source = gerber_file_raw.source_code
        scale = 25.4 if "%MOIN*%" in source else 1.0
        fmt_match = re.search(r'%FSLA[XY](\d)(\d)', source)
        divisor = 10 ** int(fmt_match.group(2)) if fmt_match else 1_000_000

        all_matches  = re.findall(r'X(-?\d+)Y(-?\d+)D0[123]', source)
        all_raw_x    = [int(x) / divisor * scale for x, y in all_matches]
        all_raw_y    = [int(y) / divisor * scale for x, y in all_matches]
        global_min_x = min(all_raw_x)
        global_min_y = min(all_raw_y)

        coords_prev, _, _ = ss.extract_coords(gbr_path, offset_x=global_min_x, offset_y=global_min_y)
        traces_prev, _, _ = ss.extract_traces(gbr_path, offset_x=global_min_x, offset_y=global_min_y)

        show_pcb_preview(root, coords_prev, traces_prev)

    except Exception as e:
        messagebox.showerror("Error", f"Preview failed: {e}")

def show_pcb_preview(root, coords, traces):
    """Show a 2D top-down preview of pads and traces in a popup window."""
    preview = tk.Toplevel(root)
    preview.title("PCB Preview")
    preview.geometry("600x500")

    fig = Figure(figsize=(6, 5), dpi=100)
    ax  = fig.add_subplot(111)
    ax.set_facecolor("#1a1a1a")
    fig.patch.set_facecolor("#1a1a1a")
    ax.set_aspect('equal')
    ax.set_title("PCB Toolpath Preview", color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_edgecolor("white")

    # draw traces
    for (sx, sy), (ex, ey), *_ in traces:
        ax.plot([sx, ex], [sy, ey], color="#ff6600", linewidth=1, alpha=0.8)

    # draw pads
    for x, y, size, shape in coords:
        if shape == 'R':
            rect = patches.Rectangle(
                (x - size/2, y - size/2), size, size,
                linewidth=0, facecolor="#00aaff", alpha=0.9
            )
            ax.add_patch(rect)
        elif shape.startswith('RR:'):
            height = float(shape.split(':')[1])
            rect = patches.Rectangle(
                (x - size/2, y - height/2), size, height,
                linewidth=0, facecolor="#00aaff", alpha=0.9
            )
            ax.add_patch(rect)
        else:
            circle = patches.Circle(
                (x, y), size/2,
                linewidth=0, facecolor="#00aaff", alpha=0.9
            )
            ax.add_patch(circle)

    ax.autoscale_view()
    #ax.invert_yaxis()

    canvas = FigureCanvasTkAgg(fig, master=preview)
    canvas.draw()
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

def generateGcode(fields, output, btn, root):
    if not validate_fields(fields):
        output.insert(tk.END, "Fix invalid fields before generating.\n")
        output.see(tk.END)
        return
    save_settings(fields, output)
    btn.config(state=tk.DISABLED, text="Generating...")
    output.insert(tk.END, "\nGenerating G-code...\n")
    output.see(tk.END)

    def run_slicer():
        try:
            slicerSoftware.run(
                enable_tool_change=fields["toggle_tool_change"].get(),
                enable_heating=fields["toggle_heating"].get(),
                enable_camera_sweep=fields["toggle_camera_sweep"].get(),
                enable_crossover=fields["toggle_crossover"].get(),
                use_arc_moves=fields["toggle_arc_moves"].get(),
            )

            gerber_path = fields["gerberFile"].get()
            gcode_name  = os.path.splitext(os.path.basename(gerber_path))[0] + ".gcode"
            gcode_dir   = os.path.dirname(gerber_path)
            gcode_path  = os.path.join(gcode_dir, gcode_name)

            root.after(0, lambda: output.insert(tk.END, "G-code generated successfully.\n"))
            root.after(0, lambda: output.insert(tk.END, f"Output: {gcode_path}\n"))
            root.after(0, lambda: output.see(tk.END))
            root.after(0, lambda: messagebox.showinfo("Success", "G-code generated successfully."))

        except Exception as e:
            err = str(e)
            root.after(0, lambda: output.insert(tk.END, f"Error: {err}\n"))
            root.after(0, lambda: messagebox.showerror("Error", err))

        root.after(0, lambda: btn.config(state=tk.NORMAL, text="Generate G-code"))

    thread = threading.Thread(target=run_slicer)
    thread.daemon = True
    thread.start()


def make_field_row(parent, row, label_text, field_key, fields, width=8):
    """Helper to create a label + entry row."""
    tk.Label(parent, text=label_text, anchor="w",
             font=("Helvetica", 11)).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))
    entry = tk.Entry(parent, width=width, font=("Helvetica", 11))
    entry.grid(row=row, column=1, sticky="e", pady=3)
    fields[field_key] = entry

def validate_fields(fields):
    """Highlight fields red if invalid, green if valid. Returns True if all valid."""
    valid = True
    numeric_fields = [
        "layerHeight", "printSpeed", "bedX", "bedY", "bedZ",
        "nozzleSize", "traceWidth", "cureDryTemp", "cureDrySeconds",
        "cureTemp", "cureSeconds"
    ]

    for key in numeric_fields:
        entry = fields[key]
        try:
            val = float(entry.get())
            if val <= 0:
                raise ValueError
            entry.config(highlightbackground="green", highlightcolor="green", highlightthickness=1)
        except ValueError:
            entry.config(highlightbackground="red", highlightcolor="red", highlightthickness=2)
            valid = False

    # check file fields not empty
    for key in ["gerberFile"]:
        entry = fields[key]
        if entry.get().strip() == "":
            entry.config(highlightbackground="red", highlightcolor="red", highlightthickness=2)
            valid = False
        else:
            entry.config(highlightbackground="green", highlightcolor="green", highlightthickness=1)

    return valid

def GUI():
    root = tk.Tk()
    root.title("CIP Slicer Software")
    root.resizable(False, False)

    fields = {}

    # ── HEADER ──
    header = tk.Frame(root, bg="#1a1a2e", pady=10)
    header.grid(row=0, column=0, columnspan=2, sticky="ew")
    tk.Label(header, text="CIP Slicer Software", bg="#1a1a2e", fg="white",
             font=("Helvetica", 14, "bold")).pack(side=tk.LEFT, padx=16)
    tk.Label(header, text="v0.1", bg="#1a1a2e", fg="#888",
             font=("Helvetica", 10)).pack(side=tk.RIGHT, padx=16)

    # ── LEFT COLUMN: printer settings ──
    left = tk.LabelFrame(root, text="Printer settings",
                         font=("Helvetica", 11, "bold"), padx=12, pady=8)
    left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=10)

    make_field_row(left, 0, "Layer height (mm)", "layerHeight", fields)
    make_field_row(left, 1, "Print speed (mm/s)", "printSpeed", fields)
    make_field_row(left, 2, "Bed size X (mm)", "bedX", fields)
    make_field_row(left, 3, "Bed size Y (mm)", "bedY", fields)
    make_field_row(left, 4, "Bed size Z (mm)", "bedZ", fields)

    tk.Label(left, text="Layer mode", anchor="w",
             font=("Helvetica", 11)).grid(row=5, column=0, sticky="w", pady=3)
    fields["layerMode"] = tk.StringVar(value="single")
    mode_menu = tk.OptionMenu(left, fields["layerMode"], "single", "multi")
    mode_menu.config(font=("Helvetica", 11), width=6)
    mode_menu.grid(row=5, column=1, sticky="e", pady=3)

    # ── RIGHT COLUMN: ink settings ──
    right = tk.LabelFrame(root, text="Ink settings",
                          font=("Helvetica", 11, "bold"), padx=12, pady=8)
    right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=10)

    make_field_row(right, 0, "Nozzle size (mm)", "nozzleSize", fields)
    make_field_row(right, 1, "Trace width (mm)", "traceWidth", fields)
    make_field_row(right, 2, "Dry temp (°C)", "cureDryTemp", fields)
    make_field_row(right, 3, "Dry time (s)", "cureDrySeconds", fields)
    make_field_row(right, 4, "Cure temp (°C)", "cureTemp", fields)
    make_field_row(right, 5, "Cure time (s)", "cureSeconds", fields)

    # Heads dropdown menu
    tk.Label(right, text="Active head", anchor="w",
             font=("Helvetica", 11)).grid(row=6, column=0, sticky="w", pady=3)
    fields["activeHead"] = tk.StringVar(value="conductor3")
    fields["activeHeadMenu"] = tk.OptionMenu(right, fields["activeHead"], "conductor3")
    fields["activeHeadMenu"].config(font=("Helvetica", 11), width=10)
    fields["activeHeadMenu"].grid(row=6, column=1, sticky="e", pady=3)

    # ── GERBER FILE ROW ──
    file_frame = tk.LabelFrame(root, text="Gerber file",
                               font=("Helvetica", 11, "bold"), padx=12, pady=8)
    file_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))
    file_frame.columnconfigure(0, weight=1)

    fields["gerberFile"] = tk.Entry(file_frame, font=("Helvetica", 11))
    fields["gerberFile"].grid(row=0, column=0, sticky="ew", padx=(0, 8))
    tk.Button(file_frame, text="Browse", font=("Helvetica", 11),
              command=lambda: browse_file(fields, "gerberFile")).grid(row=0, column=1)

    # Job file row
    tk.Label(file_frame, text="Job file (.gbrjob)  —  optional for Altium", font=("Helvetica", 11),
             anchor="w").grid(row=1, column=0, sticky="w", pady=(8, 0))
    fields["gerberJobFile"] = tk.Entry(file_frame, font=("Helvetica", 11))
    fields["gerberJobFile"].grid(row=2, column=0, sticky="ew", padx=(0, 8))
    tk.Button(file_frame, text="Browse", font=("Helvetica", 11),
              command=lambda: browse_job_file(fields)).grid(row=2, column=1)

    # ── DEBUG TOGGLES ──
    toggle_frame = tk.LabelFrame(root, text="Debug / Testing",
                                 font=("Helvetica", 11, "bold"), padx=12, pady=8)
    toggle_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))

    fields["toggle_tool_change"]  = tk.BooleanVar(value=True)
    fields["toggle_heating"]      = tk.BooleanVar(value=True)
    fields["toggle_camera_sweep"] = tk.BooleanVar(value=True)
    fields["toggle_crossover"]    = tk.BooleanVar(value=True)
    fields["toggle_arc_moves"] = tk.BooleanVar(value=False)

    tk.Checkbutton(toggle_frame, text="Use G2 arcs for circles",
                   variable=fields["toggle_arc_moves"],
                   font=("Helvetica", 11)).grid(row=1, column=0, sticky="w", padx=(0, 16))
    tk.Checkbutton(toggle_frame, text="Tool change",
                   variable=fields["toggle_tool_change"],
                   font=("Helvetica", 11)).grid(row=0, column=0, sticky="w", padx=(0, 16))
    tk.Checkbutton(toggle_frame, text="Heating / cure",
                   variable=fields["toggle_heating"],
                   font=("Helvetica", 11)).grid(row=0, column=1, sticky="w", padx=(0, 16))
    tk.Checkbutton(toggle_frame, text="Camera sweep",
                   variable=fields["toggle_camera_sweep"],
                   font=("Helvetica", 11)).grid(row=0, column=2, sticky="w", padx=(0, 16))
    tk.Checkbutton(toggle_frame, text="Crossover bridge",
                   variable=fields["toggle_crossover"],
                   font=("Helvetica", 11)).grid(row=0, column=3, sticky="w")

    # ── BUTTONS ──
    btn_frame = tk.Frame(root)
    btn_frame.grid(row=4, column=0, columnspan=2, sticky="e", padx=12, pady=(0, 6))

    tk.Button(btn_frame, text="Preview PCB", font=("Helvetica", 11),
              command=lambda: previewPCB(fields, root)).pack(side=tk.LEFT, padx=(0, 8))

    tk.Button(btn_frame, text="Save settings", font=("Helvetica", 11),
              command=lambda: save_settings(fields, output)).pack(side=tk.LEFT, padx=(0, 8))

    gen_btn = tk.Button(btn_frame, text="Generate G-code", font=("Helvetica", 11, "bold"))
    gen_btn.pack(side=tk.LEFT)
    gen_btn.config(command=lambda: generateGcode(fields, output, gen_btn, root))

    # ── OUTPUT WINDOW ──
    out_frame = tk.LabelFrame(root, text="Output",
                              font=("Helvetica", 11, "bold"), padx=12, pady=8)
    out_frame.grid(row=5, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))

    output = scrolledtext.ScrolledText(out_frame, height=8, font=("Courier", 10),
                                       state=tk.NORMAL, wrap=tk.WORD)
    output.pack(fill=tk.BOTH, expand=True)


    # ── LOAD CONFIG ON LAUNCH ──
    load_config_into_fields(fields)
    load_heads_into_dropdown(fields)

    root.mainloop()