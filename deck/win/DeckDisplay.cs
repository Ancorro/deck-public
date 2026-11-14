// The phone screen: the Virtual Display Driver monitor, turned on (extend) / off like Settings → Display, sized to fit
// the phone. Used by phonescreen.ps1. No admin needed.
using System; using System.Linq; using System.Runtime.InteropServices;
public static class DeckDisplay {
  [StructLayout(LayoutKind.Sequential)] public struct LUID { public uint Low; public int High; }
  [StructLayout(LayoutKind.Sequential)] public struct SOURCE { public LUID adapterId; public uint id, modeInfoIdx, statusFlags; }
  [StructLayout(LayoutKind.Sequential)] public struct TARGET { public LUID adapterId; public uint id, modeInfoIdx; public int outputTechnology, rotation, scaling;
    public uint refreshNum, refreshDen; public int scanLineOrdering, targetAvailable; public uint statusFlags; }
  [StructLayout(LayoutKind.Sequential)] public struct PATH { public SOURCE sourceInfo; public TARGET targetInfo; public uint flags; }
  [StructLayout(LayoutKind.Sequential, Size = 64)] public struct MODE { public int infoType; public uint id; public LUID adapterId; }
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] public struct TARGET_NAME {
    public int type, size; public LUID adapterId; public uint id, flags; public int outputTechnology; public ushort edidManufactureId, edidProductCodeId;
    public uint connectorInstance; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string monitorFriendlyDeviceName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string monitorDevicePath; }
  [DllImport("user32.dll")] static extern int GetDisplayConfigBufferSizes(uint flags, out uint np, out uint nm);
  [DllImport("user32.dll")] static extern int QueryDisplayConfig(uint flags, ref uint np, [Out] PATH[] p, ref uint nm, [Out] MODE[] m, IntPtr topo);
  [DllImport("user32.dll")] static extern int SetDisplayConfig(uint np, PATH[] p, uint nm, MODE[] m, uint flags);
  [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref TARGET_NAME r);
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] public struct DISPLAY_DEVICE {
    public int cb; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DeviceName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceString; public int StateFlags;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceID;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceKey; }
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] public struct DEVMODE {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
    public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra; public int dmFields;
    public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;
    public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
    public short dmLogPixels; public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency,
    dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight; }
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern bool EnumDisplayDevices(string dev, int i, ref DISPLAY_DEVICE dd, int flags);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern bool EnumDisplaySettings(string dev, int mode, ref DEVMODE dm);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int ChangeDisplaySettingsEx(string dev, ref DEVMODE dm, IntPtr hwnd, int flags, IntPtr p);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
  [DllImport("user32.dll")] static extern int GetSystemMetrics(int i);
  static DEVMODE Mode() { var d = new DEVMODE(); d.dmSize = (short)Marshal.SizeOf(typeof(DEVMODE)); return d; }
  static System.Collections.Generic.IEnumerable<DISPLAY_DEVICE> Devices() {
    for (int i = 0; ; i++) { var dd = new DISPLAY_DEVICE(); dd.cb = Marshal.SizeOf(dd); if (!EnumDisplayDevices(null, i, ref dd, 0)) yield break; yield return dd; } }
  static string VddDevice() { return Devices().First(d => d.DeviceString.Contains("Virtual Display")).DeviceName; }

  // Size the (attached) virtual monitor and put it just right of the others, top-aligned. Physical pixels.
  public static int Size(int w, int h) {
    string dev = VddDevice(); int right = 0;
    foreach (var d in Devices()) {
      if (d.DeviceName == dev || (d.StateFlags & 1) == 0) continue;
      var c = Mode(); if (EnumDisplaySettings(d.DeviceName, -1, ref c)) right = Math.Max(right, c.dmPositionX + c.dmPelsWidth); }
    var m = Mode(); bool found = false;
    for (int i = 0; EnumDisplaySettings(dev, i, ref m); i++, m = Mode()) if (m.dmPelsWidth == w && m.dmPelsHeight == h) { found = true; break; }
    if (!found) throw new Exception("no " + w + "x" + h + " mode");
    m.dmFields |= 0x20; m.dmPositionX = right; m.dmPositionY = 0;  // DM_POSITION
    return ChangeDisplaySettingsEx(dev, ref m, IntPtr.Zero, 1, IntPtr.Zero);  // CDS_UPDATEREGISTRY
  }
  public static string Status() {
    string dev = VddDevice(); var c = Mode();
    bool on = (Devices().First(d => d.DeviceName == dev).StateFlags & 1) != 0 && EnumDisplaySettings(dev, -1, ref c);
    var modes = new System.Collections.Generic.List<string>(); var m = Mode();
    for (int i = 0; EnumDisplaySettings(dev, i, ref m); i++, m = Mode()) { var s = m.dmPelsWidth + "x" + m.dmPelsHeight; if (!modes.Contains(s)) modes.Add(s); }
    // ox/oy: the desktop's top-left corner, which is (0, 0) of the VNC framebuffer.
    return "{\"on\":" + (on ? "true" : "false") + ",\"ox\":" + GetSystemMetrics(76) + ",\"oy\":" + GetSystemMetrics(77) + ",\"x\":" + c.dmPositionX + ",\"y\":" + c.dmPositionY + ",\"w\":" + c.dmPelsWidth
      + ",\"h\":" + c.dmPelsHeight + ",\"modes\":[" + string.Join(",", modes.Select(x => "\"" + x + "\"")) + "]}";
  }
  const uint ALL_PATHS = 1, ACTIVE = 1, INVALID = 0xffffffff;
  const uint APPLY = 0x80, SUPPLIED = 0x20, ALLOW_CHANGES = 0x400, SAVE = 0x200;

  static void Query(out PATH[] p, out MODE[] m) {
    uint np, nm; GetDisplayConfigBufferSizes(ALL_PATHS, out np, out nm);
    p = new PATH[np]; m = new MODE[nm];
    int rc = QueryDisplayConfig(ALL_PATHS, ref np, p, ref nm, m, IntPtr.Zero);
    if (rc != 0) throw new Exception("QueryDisplayConfig " + rc);
    Array.Resize(ref p, (int)np); Array.Resize(ref m, (int)nm);
  }
  public static string Name(TARGET t) {
    var r = new TARGET_NAME(); r.type = 2; r.size = Marshal.SizeOf(r); r.adapterId = t.adapterId; r.id = t.id;
    return DisplayConfigGetDeviceInfo(ref r) == 0 ? r.monitorFriendlyDeviceName + " | " + r.monitorDevicePath : "?";
  }
  public static string List() {
    PATH[] p; MODE[] m; Query(out p, out m);
    return string.Join("\n", p.Where(x => x.targetInfo.targetAvailable != 0)
      .Select(x => ((x.flags & ACTIVE) != 0 ? "ACTIVE " : "       ") + "src " + x.sourceInfo.id + " tgt " + x.targetInfo.id + " " + Name(x.targetInfo)).Distinct());
  }
  static bool IsVdd(TARGET t) { var n = Name(t); return n.IndexOf("MTT", StringComparison.OrdinalIgnoreCase) >= 0 || n.IndexOf("VDD", StringComparison.OrdinalIgnoreCase) >= 0 || n.IndexOf("Virtual", StringComparison.OrdinalIgnoreCase) >= 0; }
  // on = keep every active path and add one path to the virtual monitor; off = keep every active path but its.
  public static int Set(bool on) {
    PATH[] p; MODE[] m; Query(out p, out m);
    var keep = p.Where(x => (x.flags & ACTIVE) != 0 && !IsVdd(x.targetInfo)).ToList();
    if (on) {
      var used = keep.Select(x => x.sourceInfo.adapterId.Low + ":" + x.sourceInfo.id).ToHashSet();
      var v = p.First(x => x.targetInfo.targetAvailable != 0 && IsVdd(x.targetInfo) && !used.Contains(x.sourceInfo.adapterId.Low + ":" + x.sourceInfo.id));
      v.flags |= ACTIVE; v.sourceInfo.modeInfoIdx = INVALID; v.targetInfo.modeInfoIdx = INVALID;
      keep.Add(v);
    }
    return SetDisplayConfig((uint)keep.Count, keep.ToArray(), (uint)m.Length, m, APPLY | SUPPLIED | ALLOW_CHANGES | SAVE);
  }

  // ---- scaling: Windows' per-monitor "Scale" (undocumented DisplayConfig types -3 get / -4 set, as Settings uses) ----
  [StructLayout(LayoutKind.Sequential)] public struct DPI_GET { public int type, size; public LUID adapterId; public uint id; public int min, cur, max; }
  [StructLayout(LayoutKind.Sequential)] public struct DPI_SET { public int type, size; public LUID adapterId; public uint id; public int rel; }
  [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref DPI_GET r);
  [DllImport("user32.dll")] static extern int DisplayConfigSetDeviceInfo(ref DPI_SET r);
  static readonly int[] SCALES = { 100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500 };
  // Set the attached virtual monitor's scale to the nearest step at or below pct (Windows caps it per resolution).
  public static int Scale(int pct) {
    PATH[] p; MODE[] m; Query(out p, out m);
    var v = p.First(x => (x.flags & ACTIVE) != 0 && IsVdd(x.targetInfo));
    var g = new DPI_GET(); g.type = -3; g.size = Marshal.SizeOf(g); g.adapterId = v.sourceInfo.adapterId; g.id = v.sourceInfo.id;
    int rc = DisplayConfigGetDeviceInfo(ref g); if (rc != 0) return rc;
    int rec = Math.Abs(g.min), want = Array.FindLastIndex(SCALES, x => x <= pct);
    var s = new DPI_SET(); s.type = -4; s.size = Marshal.SizeOf(s); s.adapterId = g.adapterId; s.id = g.id;
    s.rel = Math.Min(want - rec, g.max);
    return DisplayConfigSetDeviceInfo(ref s);
  }

  // ---- windows: list the desktop's app windows and move one onto the phone monitor (maximized) or back ----
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int l, t, r, b; }
  [StructLayout(LayoutKind.Sequential)] public struct MONITORINFO { public int size; public RECT mon, work; public uint flags; }
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] static extern bool IsZoomed(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr h, int i);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int w, int hh, uint flags);
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, IntPtr extra);
  [DllImport("user32.dll")] static extern IntPtr MonitorFromPoint(POINT pt, uint flags);
  [DllImport("user32.dll")] static extern bool GetMonitorInfo(IntPtr mon, ref MONITORINFO mi);
  [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr h, int attr, out int v, int size);
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int x, y; }

  static RECT Work(int x, int y) {
    var pt = new POINT(); pt.x = x; pt.y = y;
    var mi = new MONITORINFO(); mi.size = Marshal.SizeOf(mi); GetMonitorInfo(MonitorFromPoint(pt, 2), ref mi); return mi.work; }
  static RECT PhoneRect() { var c = Mode(); EnumDisplaySettings(VddDevice(), -1, ref c);
    var r = new RECT(); r.l = c.dmPositionX; r.t = c.dmPositionY; r.r = c.dmPositionX + c.dmPelsWidth; r.b = c.dmPositionY + c.dmPelsHeight; return r; }
  static string Esc(string s) { return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", " ").Replace("\n", " ").Replace("\t", " "); }

  // Alt-tab-able windows, front to back: [{hwnd, title, app, phone}].
  public static string Windows() {
    var ph = PhoneRect(); var rows = new System.Collections.Generic.List<string>();
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h) || GetWindow(h, 4) != IntPtr.Zero || (GetWindowLong(h, -20) & 0x80) != 0) return true;  // owned / tool windows
      int cloaked; if (DwmGetWindowAttribute(h, 14, out cloaked, 4) == 0 && cloaked != 0) return true;  // other virtual desktops, UWP ghosts
      var t = new System.Text.StringBuilder(256); GetWindowText(h, t, 256); if (t.Length == 0) return true;
      uint pid; GetWindowThreadProcessId(h, out pid); string app = "";
      try { app = System.Diagnostics.Process.GetProcessById((int)pid).ProcessName; } catch { }
      if (app == "TextInputHost" || app == "ShellExperienceHost") return true;
      RECT r; GetWindowRect(h, out r); int cx = (r.l + r.r) / 2, cy = (r.t + r.b) / 2;
      bool on = !IsIconic(h) && cx >= ph.l && cx < ph.r && cy >= ph.t && cy < ph.b;
      rows.Add("{\"hwnd\":" + h.ToInt64() + ",\"title\":\"" + Esc(t.ToString()) + "\",\"app\":\"" + Esc(app) + "\",\"phone\":" + (on ? "true" : "false") + "}");
      return true; }, IntPtr.Zero);
    return "[" + string.Join(",", rows) + "]";
  }
  // Move a window onto the phone monitor (maximized) or back to the primary one, and bring it to the front.
  public static void Move(long hwnd, bool toPhone) {
    var h = new IntPtr(hwnd);
    RECT w; if (toPhone) { var ph = PhoneRect(); w = Work(ph.l + 1, ph.t + 1); } else w = Work(1, 1);
    ShowWindow(h, 9);  // SW_RESTORE: a maximized window won't move
    SetWindowPos(h, IntPtr.Zero, w.l, w.t, (w.r - w.l) * 9 / 10, (w.b - w.t) * 9 / 10, 0x4 | 0x10);  // NOZORDER | NOACTIVATE
    if (toPhone) ShowWindow(h, 3);  // SW_MAXIMIZE
    keybd_event(0x12, 0, 0, IntPtr.Zero); keybd_event(0x12, 0, 2, IntPtr.Zero);  // a tap of Alt lets a background process take focus
    SetForegroundWindow(h);
  }
}
