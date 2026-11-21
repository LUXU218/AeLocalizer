"""在运行中的 AE 里执行一小段 ExtendScript。

为什么需要它: AE 的  AfterFX.exe -r 脚本.jsx  在已有实例时是"转交执行",
命令本身会一直挂到那个 AfterFX 退出, 所以这里 Popen 不等待, 只轮询结果文件。

★ 路径坑: -r 对含空格的路径会静默不执行, 所以临时脚本写到无空格的 %TEMP%。
★ 编码坑: ExtendScript 的 File.writeln 按系统编码(GBK)写, 读回来要按 gbk 解。
"""
import io
import os
import subprocess
import tempfile
import time

__all__ = ["run_script", "project_path", "project_names", "title_has_unsaved", "exe_of_pid"]


def title_has_unsaved(title):
    """AE 窗口标题里带 * 表示工程有未保存改动。"""
    return "*" in str(title or "")


def exe_of_pid(pid):
    """取进程映像全路径; 失败返回空串。"""
    try:
        import ctypes
        from ctypes import wintypes as wt

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if not k.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(size)):
                return ""
            return buf.value
        finally:
            k.CloseHandle(ctypes.c_void_p(h))
    except Exception:
        return ""


def run_script(exe, body, timeout=25.0, out_dir=""):
    """把 body 作为 ExtendScript 送进已运行的 AE 执行, 返回 (成功, 文本或错误)。

    body 里可以用占位符 __OUT__ 指向结果文件路径(脚本自己负责写它)。
    """
    if not exe or not os.path.exists(exe):
        return False, "找不到 AfterFX.exe"
    tmp = out_dir or os.environ.get("TEMP") or tempfile.gettempdir()
    try:
        os.makedirs(tmp, exist_ok=True)
    except Exception:
        tmp = tempfile.gettempdir()
    stamp = "%d_%d" % (int(time.time() * 1000), os.getpid())
    out_file = os.path.join(tmp, "aeloc_jsx_out_%s.txt" % stamp)
    jsx_path = os.path.join(tmp, "aeloc_jsx_%s.jsx" % stamp)
    try:
        with io.open(jsx_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(body.replace("__OUT__", out_file.replace("\\", "/")))
    except Exception as exc:
        return False, "写脚本失败: %s" % exc
    try:
        subprocess.Popen([exe, "-r", jsx_path], cwd=os.path.dirname(exe) or None)
    except Exception as exc:
        return False, "转交失败: %s" % exc
    end = time.time() + max(5.0, timeout)
    while time.time() < end:
        if os.path.exists(out_file):
            try:
                with io.open(out_file, "r", encoding="gbk", errors="replace") as f:
                    return True, f.read()
            except Exception:
                return True, ""
        time.sleep(0.25)
    return False, "脚本超时（AE 可能弹了模态框）"


_PROJ_JSX = """var OUT = "__OUT__";
var f = new File(OUT); f.open("w");
var p = "";
try { if (app.project && app.project.file) p = app.project.file.fsName; } catch (e) {}
f.write(p);
f.close();
"""


_NAMES_JSX = """var OUT = "__OUT__";
var f = new File(OUT); f.open("w");
var seen = {};
function add(tag, s) { s = String(s); var k = tag + s; if (s && !seen[k]) { seen[k] = 1; f.writeln(tag + s); } }
try {
  var n = app.project.numItems;
  for (var i = 1; i <= n; i++) {
    var it = app.project.item(i);
    if (!(it instanceof CompItem)) continue;
    var L = it.numLayers;
    for (var k = 1; k <= L; k++) {
      var lay = it.layer(k);
      var par = null;
      try { par = lay.property("ADBE Effect Parade"); } catch (e) { par = null; }
      if (!par) continue;
      var m = par.numProperties;
      for (var j = 1; j <= m; j++) {
        var fx = par.property(j);
        if (!fx) continue;
        try { add("E\t", fx.name); } catch (e2) {}
        var pc = 0;
        try { pc = fx.numProperties; } catch (e3) { pc = 0; }
        for (var q = 1; q <= pc; q++) {
          var p2 = null;
          try { p2 = fx.property(q); } catch (e4) { p2 = null; }
          if (!p2) continue;
          try { add("P\t", p2.name); } catch (e5) {}
        }
      }
    }
  }
} catch (e) { f.writeln("ERR " + e.toString()); }
f.close();
"""


def project_names(exe, timeout=25.0):
    """列出当前工程里每个效果的显示名与参数名(用于实时换名)。失败返回 []。"""
    ok, txt = run_script(exe, _NAMES_JSX, timeout=timeout)
    if not ok:
        return []
    # 脚本输出形如 "E\t效果名" / "P\t参数名"（tag 与名字之间是真正的制表符），
    # 这里必须把前缀去掉再返回，否则调用方拿 "P\tBall Size" 去查词典永远查不到。
    names = []
    seen = set()
    for line in str(txt or "").splitlines():
        line = line.rstrip("\r")
        if not line.strip() or line.startswith("ERR "):
            continue
        name = line.split("\t", 1)[1] if "\t" in line else line
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def project_path(exe, timeout=15.0):
    """问 AE: 当前打开的工程文件在哪(没有就返回空串)。"""
    ok, txt = run_script(exe, _PROJ_JSX, timeout=timeout)
    if not ok:
        return ""
    return str(txt or "").strip()
