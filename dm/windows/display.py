"""Transport guest snapshots to BMP and a real Tk software-framebuffer window."""
import struct


def write_bmp(frame, path):
    row_size = (frame.width * 3 + 3) & ~3
    payload = bytearray()
    for y in reversed(range(frame.height)):
        row = frame.rgba[y*frame.width*4:(y+1)*frame.width*4]
        for x in range(0, len(row), 4):
            payload.extend((row[x+2], row[x+1], row[x]))
        payload.extend(b"\0" * (row_size - frame.width * 3))
    header = struct.pack("<2sIHHI", b"BM", 54 + len(payload), 0, 0, 54)
    header += struct.pack("<IiiHHIIiiII", 40, frame.width, frame.height, 1,
                          24, 0, len(payload), 2835, 2835, 0, 0)
    path.write_bytes(header + payload)


def show_frame(frame, report, *, auto_close=0.0, on_display=None):
    import tkinter as tk

    root = tk.Tk()
    try:
        root.title("nxnative Windows | Synthetic ARM64 first frame")
        root.resizable(False, False)
        title = tk.Label(root, text="ARM64 code -> guest pixels -> Windows",
                         font=("Segoe UI", 15, "bold"), padx=20, pady=12)
        title.pack()
        ppm = f"P6\n{frame.width} {frame.height}\n255\n".encode("ascii") + frame.rgb
        image = tk.PhotoImage(data=ppm, format="PPM").zoom(3, 3)
        label = tk.Label(root, image=image, borderwidth=0)
        label.pack(padx=20)
        status = tk.Label(root, text="CPU verified | synthetic SVC | software framebuffer",
                          font=("Segoe UI", 10), padx=20, pady=12)
        status.pack()
        root.update_idletasks()
        root.update()
        # Verify mapped widgets and actual Tk photo pixels before claiming display.
        if not root.winfo_viewable() or not label.winfo_ismapped():
            raise RuntimeError("Tk window did not become viewable")
        for x, y in ((0, 0), (frame.width//2, frame.height//2),
                     (frame.width-1, frame.height-1)):
            offset = (y * frame.width + x) * 4
            if tuple(image.get(x*3, y*3)) != tuple(frame.rgba[offset:offset+3]):
                raise RuntimeError("Tk photo pixels differ from guest snapshot")
        report["display_success"] = True
        report["display_status"] = "tk_window_mapped_and_photo_verified"
        report["display_backend"] = f"Tk {root.tk.call('info', 'patchlevel')} software framebuffer"
        print("DISPLAY SUCCESS: Windows Tk window mapped; guest pixels verified.", flush=True)
        if on_display:
            on_display()
        if auto_close > 0:
            root.after(max(1, int(auto_close * 1000)), root.destroy)
        root.mainloop()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
