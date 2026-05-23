import os
import sys
import shutil
import threading
import time
import ctypes
import platform
import subprocess
from tkinter import *
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

# --- 全局配置 ---
VERSION = "1.0"
AUTHOR = "一键清理工具"

# 需要管理员权限
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

# --- 扫描类别定义 ---
SCAN_CATEGORIES = [
    {
        "name": "Windows 临时文件",
        "key": "temp",
        "paths": [os.environ.get("TEMP", ""), os.environ.get("TMP", "")],
        "extensions": [],
        "enabled": True
    },
    {
        "name": "回收站",
        "key": "recycle",
        "paths": [],
        "extensions": [],
        "enabled": False
    },
    {
        "name": "浏览器缓存 (Edge/Chrome)",
        "key": "browser_cache",
        "paths": [],
        "extensions": [],
        "enabled": True
    },
    {
        "name": "Windows 更新缓存",
        "key": "windows_update",
        "paths": [r"C:\Windows\SoftwareDistribution\Download"],
        "extensions": [],
        "enabled": False
    },
    {
        "name": "日志文件 (.log)",
        "key": "log_files",
        "paths": [],
        "extensions": [".log"],
        "enabled": True
    },
    {
        "name": "预读文件 (Prefetch)",
        "key": "prefetch",
        "paths": [r"C:\Windows\Prefetch"],
        "extensions": [],
        "enabled": False
    },
    {
        "name": "缩略图缓存",
        "key": "thumbcache",
        "paths": [],
        "extensions": [],
        "enabled": True
    },
    {
        "name": "Recent 文档历史",
        "key": "recent",
        "paths": [],
        "extensions": [],
        "enabled": True
    },
]

# 系统文件夹列表（默认排除）
SYSTEM_FOLDERS = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    r"C:\System Volume Information",
    r"C:\$Recycle.Bin",
    r"C:\Boot",
    r"C:\Recovery",
]


# 获取浏览器缓存路径
def get_browser_cache_paths():
    paths = []
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        for browser in ["Google\\Chrome\\User Data\\Default\\Cache",
                         "Google\\Chrome\\User Data\\Default\\Code Cache",
                         "Microsoft\\Edge\\User Data\\Default\\Cache",
                         "Microsoft\\Edge\\User Data\\Default\\Code Cache"]:
            p = os.path.join(local_appdata, browser)
            if os.path.exists(p):
                paths.append(p)
    return paths

# 获取缩略图缓存路径
def get_thumbcache_paths():
    paths = []
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        p = os.path.join(local_appdata, "Microsoft\\Windows\\Explorer")
        if os.path.exists(p):
            paths.append(p)
    return paths

# 获取 Recent 路径
def get_recent_paths():
    paths = []
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        p = os.path.join(appdata, "Microsoft\\Windows\\Recent")
        if os.path.exists(p):
            paths.append(p)
    return paths

# 初始化路径
for cat in SCAN_CATEGORIES:
    if cat["key"] == "browser_cache":
        cat["paths"] = get_browser_cache_paths()
    elif cat["key"] == "thumbcache":
        cat["paths"] = get_thumbcache_paths()
    elif cat["key"] == "recent":
        cat["paths"] = get_recent_paths()


class DiskCleaner:
    """核心清理引擎"""

    def __init__(self):
        self.total_files = 0
        self.total_size = 0
        self.scanned_files = []
        self.stop_flag = False
        self.cancel_scan = False

    def get_drives(self):
        """获取所有可用磁盘"""
        drives = []
        if platform.system() == "Windows":
            import string
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    drives.append(drive)
        else:
            drives = ["/"]
        return drives

    def get_drive_info(self, drive_path):
        """获取磁盘信息"""
        try:
            free_bytes = ctypes.c_ulonglong(0)
            total_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(drive_path),
                None,
                ctypes.pointer(total_bytes),
                ctypes.pointer(free_bytes)
            )
            total = total_bytes.value
            free = free_bytes.value
            used = total - free
            percent_used = (used / total) * 100 if total > 0 else 0
            return {
                "total": total,
                "free": free,
                "used": used,
                "percent_used": percent_used
            }
        except:
            return None

    def format_size(self, size_bytes):
        """格式化文件大小显示"""
        if size_bytes == 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        idx = 0
        size = float(size_bytes)
        while size >= 1024 and idx < len(units) - 1:
            size /= 1024
            idx += 1
        return f"{size:.2f} {units[idx]}"

    def parse_size_threshold(self, text):
        """解析大小阈值，如 '1GB', '500MB'"""
        text = text.strip().upper()
        if text.endswith("TB"):
            return float(text[:-2]) * 1024**4
        elif text.endswith("GB"):
            return float(text[:-2]) * 1024**3
        elif text.endswith("MB"):
            return float(text[:-2]) * 1024**2
        elif text.endswith("KB"):
            return float(text[:-2]) * 1024
        elif text.endswith("B"):
            return float(text[:-1])
        else:
            try:
                return float(text) * 1024**3  # 默认 GB
            except:
                return 1 * 1024**3

    def is_system_folder(self, path):
        """检查路径是否在系统文件夹列表中"""
        path_lower = path.lower()
        for sf in SYSTEM_FOLDERS:
            if path_lower.startswith(sf.lower()):
                return True
        return False

    def scan_directory(self, directory, extensions=None, callback=None):
        """扫描目录中的文件"""
        if not os.path.exists(directory):
            return []

        results = []
        try:
            for root, dirs, files in os.walk(directory):
                if self.cancel_scan:
                    return results

                for f in files:
                    if self.cancel_scan:
                        return results

                    file_path = os.path.join(root, f)

                    if extensions:
                        ext = os.path.splitext(f)[1].lower()
                        if ext not in extensions:
                            continue

                    try:
                        size = os.path.getsize(file_path)
                        if size > 0:
                            results.append({
                                "path": file_path,
                                "size": size,
                                "name": f
                            })
                    except:
                        pass

                    if callback:
                        callback(f, size, len(results))
        except:
            pass

        return results

    def scan_large_files(self, drive, threshold_bytes, exclude_system=True,
                         progress_callback=None, file_found_callback=None):
        """扫描大文件"""
        self.cancel_scan = False
        large_files = []
        total_scanned = 0
        dirs_scanned = 0

        if not os.path.exists(drive):
            return large_files, total_scanned

        try:
            for root, dirs, files in os.walk(drive):
                if self.cancel_scan:
                    break

                # 跳过系统文件夹
                if exclude_system and self.is_system_folder(root):
                    dirs.clear()  # 不进入子目录
                    continue

                # 跳过隐藏目录（以$开头）
                if os.path.basename(root).startswith("$"):
                    dirs.clear()
                    continue

                dirs_scanned += 1

                for f in files:
                    if self.cancel_scan:
                        break

                    file_path = os.path.join(root, f)

                    try:
                        # 快速检查文件大小（使用 stat）
                        stat_info = os.stat(file_path)
                        size = stat_info.st_size

                        if size >= threshold_bytes:
                            mtime = stat_info.st_mtime
                            modified = datetime.fromtimestamp(mtime)

                            item = {
                                "path": file_path,
                                "size": size,
                                "name": f,
                                "modified": modified.strftime("%Y-%m-%d %H:%M:%S")
                            }
                            large_files.append(item)
                            total_scanned += 1

                            if file_found_callback:
                                file_found_callback(item)

                    except (OSError, PermissionError):
                        pass

                if progress_callback and dirs_scanned % 50 == 0:
                    progress_callback(dirs_scanned, len(large_files))

        except Exception as e:
            pass

        # 按大小降序排列
        large_files.sort(key=lambda x: x["size"], reverse=True)
        return large_files, total_scanned

    def scan_junk(self, drive, categories, progress_callback=None):
        """扫描指定磁盘的垃圾文件"""
        self.cancel_scan = False
        all_junk = {cat["key"]: [] for cat in categories}
        total_size = 0
        total_files = 0

        for cat_idx, cat in enumerate(categories):
            if self.cancel_scan:
                break
            if not cat["enabled"]:
                continue

            cat_files = []
            category_size = 0

            if cat["key"] == "recycle":
                recycle_path = f"{drive[:3]}$Recycle.Bin"
                if os.path.exists(recycle_path):
                    try:
                        for root, dirs, files in os.walk(recycle_path):
                            if self.cancel_scan:
                                break
                            for f in files:
                                if self.cancel_scan:
                                    break
                                fp = os.path.join(root, f)
                                try:
                                    s = os.path.getsize(fp)
                                    if s > 0:
                                        cat_files.append({"path": fp, "size": s, "name": f})
                                        category_size += s
                                except:
                                    pass
                    except:
                        pass
            else:
                for base_path in cat["paths"]:
                    if self.cancel_scan:
                        break
                    if not base_path:
                        continue

                    if drive and not base_path.lower().startswith(drive[:2].lower()):
                        if os.path.exists(drive) and not base_path.lower().startswith(drive[:2].lower()):
                            if not base_path.lower().startswith("c:"):
                                continue

                    results = self.scan_directory(
                        base_path,
                        cat["extensions"] if cat["extensions"] else None,
                    )
                    cat_files.extend(results)
                    for r in results:
                        category_size += r["size"]

            all_junk[cat["key"]] = cat_files
            total_size += category_size
            total_files += len(cat_files)

            if progress_callback:
                progress_callback(cat_idx + 1, len(categories), cat["name"],
                                  len(cat_files), category_size, all_junk)

        return all_junk, total_size, total_files

    def clean_junk(self, all_junk, progress_callback=None):
        """清理垃圾文件"""
        total_files = sum(len(files) for files in all_junk.values())
        cleaned = 0
        total_freed = 0
        failed = 0

        for cat_key, files in all_junk.items():
            for f in files:
                if self.stop_flag:
                    return cleaned, total_freed, failed

                try:
                    if os.path.exists(f["path"]):
                        os.remove(f["path"])
                        cleaned += 1
                        total_freed += f["size"]
                except:
                    failed += 1

                if progress_callback:
                    progress_callback(cleaned, total_files, total_freed, failed)

        return cleaned, total_freed, failed


class LargeFileTab:
    """大文件查找标签页"""

    def __init__(self, parent, engine, main_app):
        self.parent = parent
        self.engine = engine
        self.main = main_app
        self.large_files = []
        self.is_scanning = False
        self.selected_count = 0

        self.build_ui()

    def build_ui(self):
        # ---- 顶部控制栏 ----
        control_frame = Frame(self.parent, bg="#ffffff")
        control_frame.pack(fill=X, padx=10, pady=10)

        # 磁盘选择
        Label(control_frame, text="磁盘:", font=("微软雅黑", 10),
              bg="#ffffff").pack(side=LEFT, padx=(0, 5))
        self.drive_combo = ttk.Combobox(control_frame, width=10,
                                        font=("微软雅黑", 10), state="readonly")
        self.drive_combo.pack(side=LEFT, padx=(0, 15))
        drives = self.engine.get_drives()
        self.drive_combo["values"] = drives
        if drives:
            self.drive_combo.set(drives[0])

        # 大小阈值
        Label(control_frame, text="最小大小:", font=("微软雅黑", 10),
              bg="#ffffff").pack(side=LEFT, padx=(0, 5))
        self.threshold_var = StringVar(value="1GB")
        self.threshold_combo = ttk.Combobox(control_frame, textvariable=self.threshold_var,
                                             width=8, font=("微软雅黑", 10), state="editable")
        self.threshold_combo["values"] = ["100MB", "500MB", "1GB", "2GB", "5GB", "10GB"]
        self.threshold_combo.pack(side=LEFT, padx=(0, 15))

        # 排除系统文件夹
        self.exclude_system_var = BooleanVar(value=True)
        ttk.Checkbutton(control_frame, text="排除系统文件夹",
                        variable=self.exclude_system_var).pack(side=LEFT, padx=(0, 15))

        # 按钮
        self.scan_btn = ttk.Button(control_frame, text="🔍 开始查找",
                                    command=self.start_scan, width=12)
        self.scan_btn.pack(side=LEFT, padx=2)

        self.cancel_btn = ttk.Button(control_frame, text="⏹ 停止",
                                      command=self.cancel_scan, width=8, state=DISABLED)
        self.cancel_btn.pack(side=LEFT, padx=2)

        # ---- 结果表格 ----
        result_frame = Frame(self.parent)
        result_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 5))

        # Treeview
        columns = ("name", "size", "modified", "path")
        self.tree = ttk.Treeview(result_frame, columns=columns, show="headings",
                                  selectmode="extended")

        self.tree.heading("name", text="文件名")
        self.tree.heading("size", text="大小")
        self.tree.heading("modified", text="修改时间")
        self.tree.heading("path", text="完整路径")

        self.tree.column("name", width=200, minwidth=150)
        self.tree.column("size", width=120, minwidth=100, anchor=E)
        self.tree.column("modified", width=160, minwidth=120)
        self.tree.column("path", width=400, minwidth=200)

        # 滚动条
        v_scroll = ttk.Scrollbar(result_frame, orient=VERTICAL, command=self.tree.yview)
        h_scroll = ttk.Scrollbar(result_frame, orient=HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")

        result_frame.grid_rowconfigure(0, weight=1)
        result_frame.grid_columnconfigure(0, weight=1)

        # 绑定双击打开位置
        self.tree.bind("<Double-1>", self.on_item_double_click)

        # ---- 底部操作栏 ----
        action_frame = Frame(self.parent, bg="#f0f0f0")
        action_frame.pack(fill=X, padx=10, pady=8)

        self.info_label = Label(action_frame, text="共 0 个大文件",
                                font=("微软雅黑", 9), bg="#f0f0f0", fg="#7f8c8d")
        self.info_label.pack(side=LEFT)

        self.size_label = Label(action_frame, text="",
                                font=("微软雅黑", 10, "bold"),
                                bg="#f0f0f0", fg="#e74c3c")
        self.size_label.pack(side=LEFT, padx=(15, 0))

        ttk.Button(action_frame, text="📂 打开位置",
                    command=self.open_selected_location).pack(side=RIGHT, padx=2)
        ttk.Button(action_frame, text="🗑 删除选中",
                    command=self.delete_selected).pack(side=RIGHT, padx=2)
        ttk.Button(action_frame, text="📋 复制路径",
                    command=self.copy_selected_path).pack(side=RIGHT, padx=2)

        # ---- 进度条 ----
        progress_frame = Frame(self.parent, bg="#f0f0f0")
        progress_frame.pack(fill=X, padx=10, pady=(0, 8))

        self.progress_bar = ttk.Progressbar(progress_frame, length=100, mode="determinate")
        self.progress_bar.pack(fill=X)

        self.progress_label = Label(progress_frame, text="", font=("微软雅黑", 9),
                                    bg="#f0f0f0", fg="#7f8c8d")
        self.progress_label.pack(anchor=W)

    def update_drive_list(self):
        drives = self.engine.get_drives()
        self.drive_combo["values"] = drives
        if drives and not self.drive_combo.get():
            self.drive_combo.set(drives[0])

    def start_scan(self):
        """开始扫描大文件"""
        drive = self.drive_combo.get()
        if not drive:
            messagebox.showwarning("提示", "请先选择一个磁盘！")
            return

        threshold_text = self.threshold_var.get()
        try:
            threshold_bytes = self.engine.parse_size_threshold(threshold_text)
        except:
            messagebox.showwarning("提示", "请输入有效的大小阈值（如 1GB、500MB）")
            return

        exclude_system = self.exclude_system_var.get()

        # 清除旧数据
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.large_files = []
        self.info_label.config(text="正在扫描...")
        self.size_label.config(text="")
        self.progress_bar["value"] = 0

        self.is_scanning = True
        self.scan_btn.config(state=DISABLED)
        self.cancel_btn.config(state=NORMAL)

        self.progress_label.config(text="正在扫描磁盘，这可能需要一些时间...")

        def scan_thread():
            def progress_callback(dirs_scanned, files_found):
                self.progress_bar["value"] = min(dirs_scanned % 100, 100)
                self.progress_label.config(
                    text=f"已扫描 {dirs_scanned} 个目录，发现 {files_found} 个大文件..."
                )
                self.info_label.config(text=f"扫描中... 已发现 {files_found} 个大文件")

            def file_found_callback(item):
                # 实时插入到表格
                self.tree.insert("", "end", values=(
                    item["name"],
                    self.engine.format_size(item["size"]),
                    item["modified"],
                    item["path"]
                ))

            try:
                files, total = self.engine.scan_large_files(
                    drive, threshold_bytes, exclude_system,
                    progress_callback, file_found_callback
                )

                self.main.root.after(0, lambda: self.on_scan_complete(files))
            except Exception as e:
                self.main.root.after(0, lambda: self.on_scan_error(str(e)))

        t = threading.Thread(target=scan_thread, daemon=True)
        t.start()

    def on_scan_complete(self, files):
        """扫描完成"""
        self.large_files = files
        self.is_scanning = False
        self.scan_btn.config(state=NORMAL)
        self.cancel_btn.config(state=DISABLED)
        self.progress_bar["value"] = 100

        if not files:
            self.info_label.config(text="未找到大文件")
            self.size_label.config(text="")
            self.progress_label.config(text="扫描完成，未找到大文件")
        else:
            total_size = sum(f["size"] for f in files)
            self.info_label.config(text=f"共找到 {len(files)} 个大文件")
            self.size_label.config(text=f"总计: {self.engine.format_size(total_size)}")
            self.progress_label.config(
                text=f"扫描完成！找到 {len(files)} 个大文件，总计 {self.engine.format_size(total_size)}"
            )

    def on_scan_error(self, error):
        """扫描出错"""
        self.is_scanning = False
        self.scan_btn.config(state=NORMAL)
        self.cancel_btn.config(state=DISABLED)
        self.info_label.config(text="扫描出错")
        self.progress_label.config(text=f"错误: {error}")

    def cancel_scan(self):
        """取消扫描"""
        self.engine.cancel_scan = True
        self.engine.stop_flag = True
        self.progress_label.config(text="正在取消扫描...")
        self.cancel_btn.config(state=DISABLED)

    def open_selected_location(self):
        """在资源管理器中打开选中文件的位置"""
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择文件")
            return

        paths = set()
        for item_id in selected:
            values = self.tree.item(item_id, "values")
            if len(values) >= 4:
                paths.add(values[3])

        for path in paths:
            try:
                subprocess.Popen(f'explorer /select,"{path}"')
            except Exception as e:
                messagebox.showerror("错误", f"无法打开位置: {e}")

    def delete_selected(self):
        """删除选中的文件"""
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择要删除的文件")
            return

        items_info = []
        total_size = 0
        for item_id in selected:
            values = self.tree.item(item_id, "values")
            if len(values) >= 4:
                items_info.append((item_id, values[3], values[1]))
                size = self.engine.parse_size_threshold(values[1].replace(" ", ""))
                total_size += size

        result = messagebox.askyesno(
            "确认删除",
            f"确定要删除选中的 {len(items_info)} 个文件吗？\n"
            f"共 {self.engine.format_size(total_size)}\n\n"
            f"⚠️ 文件将被永久删除，无法恢复！",
            icon="warning"
        )

        if not result:
            return

        deleted_count = 0
        failed_count = 0
        freed_size = 0

        for item_id, path, size_str in items_info:
            try:
                if os.path.exists(path):
                    file_size = os.path.getsize(path)
                    os.remove(path)
                    self.tree.delete(item_id)
                    deleted_count += 1
                    freed_size += file_size
                    # 从 large_files 中移除
                    self.large_files = [f for f in self.large_files if f["path"] != path]
            except Exception as e:
                failed_count += 1

        # 更新统计
        remaining = len(self.tree.get_children())
        remaining_size = sum(f["size"] for f in self.large_files)
        self.info_label.config(text=f"共 {remaining} 个大文件")
        self.size_label.config(text=f"总计: {self.engine.format_size(remaining_size)}")

        msg = f"✅ 成功删除 {deleted_count} 个文件，释放 {self.engine.format_size(freed_size)}"
        if failed_count > 0:
            msg += f"\n⚠️ {failed_count} 个文件删除失败（可能权限不足）"
        messagebox.showinfo("删除结果", msg)

    def copy_selected_path(self):
        """复制选中文件的路径"""
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择文件")
            return

        paths = []
        for item_id in selected:
            values = self.tree.item(item_id, "values")
            if len(values) >= 4:
                paths.append(values[3])

        self.main.root.clipboard_clear()
        self.main.root.clipboard_append("\n".join(paths))
        messagebox.showinfo("提示", f"已复制 {len(paths)} 个路径到剪贴板")

    def on_item_double_click(self, event):
        """双击打开位置"""
        self.open_selected_location()


class Application:
    """主程序 GUI"""

    def __init__(self, root):
        self.root = root
        self.root.title("一键磁盘清理工具")
        self.root.geometry("900x680")
        self.root.minsize(800, 600)

        self.setup_styles()

        # 核心引擎
        self.engine = DiskCleaner()
        self.scan_results = None
        self.scan_total_size = 0
        self.selected_drive = StringVar()
        self.is_scanning = False
        self.is_cleaning = False

        # 类别勾选状态
        self.category_vars = {}
        for cat in SCAN_CATEGORIES:
            self.category_vars[cat["key"]] = BooleanVar(value=cat["enabled"])

        self.build_ui()
        self.refresh_drives()

    def setup_styles(self):
        """设置界面样式"""
        self.root.configure(bg="#f0f0f0")

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except:
            pass

        style.configure("Title.TLabel", font=("微软雅黑", 16, "bold"))
        style.configure("Header.TLabel", font=("微软雅黑", 11))
        style.configure("Info.TLabel", font=("微软雅黑", 10))
        style.configure("Size.TLabel", font=("微软雅黑", 12, "bold"), foreground="#e74c3c")
        style.configure("DriveInfo.TLabel", font=("微软雅黑", 9))
        style.configure("Clean.TButton", font=("微软雅黑", 11))

    def center_window(self):
        """居中显示窗口"""
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def build_ui(self):
        """构建用户界面"""
        # ===== 顶部标题栏 =====
        header_frame = Frame(self.root, bg="#2c3e50", height=60)
        header_frame.pack(fill=X)
        header_frame.pack_propagate(False)

        title_label = Label(header_frame, text="🧹 一键磁盘清理工具",
                           font=("微软雅黑", 16, "bold"),
                           bg="#2c3e50", fg="white")
        title_label.pack(side=LEFT, padx=20, pady=10)

        version_label = Label(header_frame, text=f"v{VERSION}",
                             font=("微软雅黑", 9),
                             bg="#2c3e50", fg="#95a5a6")
        version_label.pack(side=RIGHT, padx=20, pady=10)

        # ===== 主内容（使用 Notebook 标签页） =====
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=BOTH, expand=True, padx=10, pady=10)

        # ---- Tab 1: 垃圾清理 ----
        self.clean_tab = Frame(self.notebook, bg="#f0f0f0")
        self.notebook.add(self.clean_tab, text="  垃圾清理  ")
        self.build_clean_tab()

        # ---- Tab 2: 大文件查找 ----
        self.largefile_tab_frame = Frame(self.notebook, bg="#f0f0f0")
        self.notebook.add(self.largefile_tab_frame, text="  大文件查找  ")
        self.largefile_tab = LargeFileTab(self.largefile_tab_frame, self.engine, self)

    def build_clean_tab(self):
        """构建垃圾清理标签页"""
        parent = self.clean_tab

        # ---- 磁盘选择区域 ----
        drive_frame = LabelFrame(parent, text=" 磁盘选择 ",
                                 font=("微软雅黑", 10, "bold"),
                                 bg="#ffffff", fg="#2c3e50",
                                 padx=10, pady=10, relief="groove")
        drive_frame.pack(fill=X, padx=10, pady=(10, 5))

        drive_row = Frame(drive_frame, bg="#ffffff")
        drive_row.pack(fill=X)

        Label(drive_row, text="选择磁盘:", font=("微软雅黑", 10),
              bg="#ffffff").pack(side=LEFT, padx=(0, 10))

        self.drive_combo = ttk.Combobox(drive_row, textvariable=self.selected_drive,
                                        font=("微软雅黑", 10), width=15, state="readonly")
        self.drive_combo.pack(side=LEFT, padx=(0, 10))
        self.drive_combo.bind("<<ComboboxSelected>>", self.on_drive_selected)

        self.refresh_btn = ttk.Button(drive_row, text="🔄 刷新", command=self.refresh_drives)
        self.refresh_btn.pack(side=LEFT, padx=5)

        # 磁盘信息标签
        self.drive_info_label = Label(drive_frame, text="请选择磁盘查看信息",
                                     font=("微软雅黑", 9), bg="#ffffff", fg="#7f8c8d")
        self.drive_info_label.pack(anchor=W, pady=(5, 0))

        # 磁盘使用率进度条
        self.drive_usage_bar = ttk.Progressbar(drive_frame, length=400, mode="determinate")
        self.drive_usage_bar.pack(fill=X, pady=(5, 0))

        # ---- 扫描类别选择 ----
        category_frame = LabelFrame(parent, text=" 清理项目 ",
                                    font=("微软雅黑", 10, "bold"),
                                    bg="#ffffff", fg="#2c3e50",
                                    padx=10, pady=10, relief="groove")
        category_frame.pack(fill=X, padx=10, pady=5)

        select_frame = Frame(category_frame, bg="#ffffff")
        select_frame.pack(fill=X, pady=(0, 5))

        ttk.Button(select_frame, text="全选", command=self.select_all).pack(side=LEFT, padx=2)
        ttk.Button(select_frame, text="取消全选", command=self.deselect_all).pack(side=LEFT, padx=2)

        self.category_frame_inner = Frame(category_frame, bg="#ffffff")
        self.category_frame_inner.pack(fill=BOTH, expand=True)

        row, col = 0, 0
        for cat in SCAN_CATEGORIES:
            cb = ttk.Checkbutton(
                self.category_frame_inner,
                text=cat["name"],
                variable=self.category_vars[cat["key"]],
                command=self.update_scan_estimate
            )
            cb.grid(row=row, column=col, sticky=W, padx=10, pady=3)
            col += 1
            if col >= 2:
                col = 0
                row += 1

        # ---- 操作按钮 ----
        btn_frame = Frame(parent, bg="#f0f0f0")
        btn_frame.pack(fill=X, padx=10, pady=5)

        self.scan_btn = ttk.Button(btn_frame, text="🔍 开始扫描", command=self.start_scan,
                                   width=15)
        self.scan_btn.pack(side=LEFT, padx=2)

        self.clean_btn = ttk.Button(btn_frame, text="🧹 开始清理", command=self.start_clean,
                                    width=15, state=DISABLED)
        self.clean_btn.pack(side=LEFT, padx=2)

        self.cancel_btn = ttk.Button(btn_frame, text="⏹ 取消", command=self.cancel_operation,
                                     width=10, state=DISABLED)
        self.cancel_btn.pack(side=LEFT, padx=2)

        # ---- 扫描结果区域 ----
        result_frame = LabelFrame(parent, text=" 扫描结果 ",
                                  font=("微软雅黑", 10, "bold"),
                                  bg="#ffffff", fg="#2c3e50",
                                  padx=10, pady=10, relief="groove")
        result_frame.pack(fill=BOTH, expand=True, padx=10, pady=(5, 10))

        result_inner = Frame(result_frame, bg="#ffffff")
        result_inner.pack(fill=BOTH, expand=True)

        text_frame = Frame(result_inner, bg="#ffffff")
        text_frame.pack(fill=BOTH, expand=True)

        self.result_text = Text(text_frame, font=("Consolas", 9),
                               bg="#fafafa", fg="#2c3e50",
                               wrap=WORD, relief="solid", borderwidth=1,
                               height=8)
        self.result_text.pack(side=LEFT, fill=BOTH, expand=True)

        scrollbar = ttk.Scrollbar(text_frame, orient=VERTICAL, command=self.result_text.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.result_text.config(yscrollcommand=scrollbar.set)

        # 扫描统计
        self.stats_frame = Frame(result_inner, bg="#ffffff", pady=5)
        self.stats_frame.pack(fill=X)

        self.status_label = Label(self.stats_frame, text="就绪 - 请选择磁盘并点击扫描",
                                  font=("微软雅黑", 9), bg="#ffffff", fg="#7f8c8d")
        self.status_label.pack(side=LEFT)

        self.size_label = Label(self.stats_frame, text="",
                                font=("微软雅黑", 10, "bold"),
                                bg="#ffffff", fg="#e74c3c")
        self.size_label.pack(side=RIGHT)

        # ---- 进度条 ----
        progress_frame = Frame(parent, bg="#f0f0f0")
        progress_frame.pack(fill=X, padx=10, pady=(0, 10))

        self.progress_bar = ttk.Progressbar(progress_frame, length=100, mode="determinate")
        self.progress_bar.pack(fill=X)

        self.progress_label = Label(progress_frame, text="", font=("微软雅黑", 9),
                                   bg="#f0f0f0", fg="#7f8c8d")
        self.progress_label.pack(anchor=W)

    def refresh_drives(self):
        """刷新磁盘列表"""
        drives = self.engine.get_drives()
        self.drive_combo["values"] = drives
        if drives:
            self.selected_drive.set(drives[0])
        self.on_drive_selected()
        # 同步更新大文件标签页的磁盘列表
        if hasattr(self, 'largefile_tab'):
            self.largefile_tab.drive_combo["values"] = drives

    def on_drive_selected(self, event=None):
        """磁盘选择事件"""
        drive = self.selected_drive.get()
        if not drive:
            return

        info = self.engine.get_drive_info(drive)
        if info:
            total_str = self.engine.format_size(info["total"])
            used_str = self.engine.format_size(info["used"])
            free_str = self.engine.format_size(info["free"])

            self.drive_info_label.config(
                text=f"总容量: {total_str}  |  已用: {used_str}  |  可用: {free_str}  ({info['percent_used']:.1f}%)"
            )

            self.drive_usage_bar["value"] = info["percent_used"]

    def select_all(self):
        """全选"""
        for var in self.category_vars.values():
            var.set(True)

    def deselect_all(self):
        """取消全选"""
        for var in self.category_vars.values():
            var.set(False)

    def update_scan_estimate(self):
        pass

    def get_selected_categories(self):
        """获取选中的清理类别"""
        selected = []
        for cat in SCAN_CATEGORIES:
            if self.category_vars[cat["key"]].get():
                cat_copy = cat.copy()
                selected.append(cat_copy)
        return selected

    def log(self, message):
        """输出日志到文本框"""
        self.result_text.insert(END, message + "\n")
        self.result_text.see(END)
        self.root.update_idletasks()

    def set_buttons_state(self, scanning=False, cleaning=False):
        """设置按钮状态"""
        self.is_scanning = scanning
        self.is_cleaning = cleaning
        self.scan_btn.config(state=DISABLED if (scanning or cleaning) else NORMAL)
        self.clean_btn.config(state=DISABLED if (scanning or cleaning or not self.scan_results) else NORMAL)
        self.cancel_btn.config(state=NORMAL if (scanning or cleaning) else DISABLED)
        self.drive_combo.config(state=DISABLED if (scanning or cleaning) else "readonly")
        self.refresh_btn.config(state=DISABLED if (scanning or cleaning) else NORMAL)

    def cancel_operation(self):
        """取消当前操作"""
        if self.is_scanning:
            self.engine.cancel_scan = True
            self.engine.stop_flag = True
            self.log("⏹ 正在取消扫描...")
        if self.is_cleaning:
            self.engine.stop_flag = True
            self.log("⏹ 正在取消清理...")

    def start_scan(self):
        """开始扫描（后台线程）"""
        drive = self.selected_drive.get()
        if not drive:
            messagebox.showwarning("提示", "请先选择一个磁盘！")
            return

        selected = self.get_selected_categories()
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个清理项目！")
            return

        self.set_buttons_state(scanning=True)
        self.result_text.delete(1.0, END)
        self.size_label.config(text="")
        self.status_label.config(text="正在扫描中...", fg="#e67e22")
        self.progress_bar["value"] = 0
        self.progress_label.config(text="准备扫描...")
        self.scan_results = None

        self.log(f"{'='*50}")
        self.log(f"📂 扫描磁盘: {drive}")
        self.log(f"🕐 开始时间: {datetime.now().strftime('%H:%M:%S')}")
        self.log(f"{'='*50}\n")

        def scan_thread():
            def progress_callback(current, total, cat_name, files_count, size, all_junk):
                pct = int((current / total) * 100)
                self.progress_bar["value"] = pct
                self.progress_label.config(
                    text=f"[{current}/{total}] 正在扫描 {cat_name}... 发现 {files_count} 个文件 ({self.engine.format_size(size)})"
                )
                self.root.update_idletasks()

            try:
                results, total_size, total_files = self.engine.scan_junk(
                    drive, selected, progress_callback
                )

                if self.engine.cancel_scan:
                    self.root.after(0, lambda: self.log("\n⏹ 扫描已取消"))
                    self.root.after(0, self.on_scan_cancelled)
                    return

                self.scan_results = results
                self.scan_total_size = total_size

                self.root.after(0, lambda: self.on_scan_complete(results, total_size, total_files, drive))
            except Exception as e:
                self.root.after(0, lambda: self.log(f"\n❌ 扫描出错: {str(e)}"))
                self.root.after(0, lambda: self.set_buttons_state(scanning=False))

        t = threading.Thread(target=scan_thread, daemon=True)
        t.start()

    def on_scan_complete(self, results, total_size, total_files, drive):
        """扫描完成 UI 更新"""
        self.progress_bar["value"] = 100

        if total_files == 0:
            self.log(f"\n✅ 扫描完成，未发现垃圾文件！")
            self.status_label.config(text="扫描完成 - 未发现垃圾文件", fg="#27ae60")
            self.size_label.config(text="0 B")
        else:
            size_str = self.engine.format_size(total_size)
            self.log(f"\n{'='*50}")
            self.log(f"✅ 扫描完成！")
            self.log(f"📊 共发现 {total_files} 个垃圾文件，可释放 {size_str}")
            self.log(f"{'='*50}\n")

            for cat_key, files in results.items():
                if files:
                    cat_size = sum(f["size"] for f in files)
                    cat_name = ""
                    for c in SCAN_CATEGORIES:
                        if c["key"] == cat_key:
                            cat_name = c["name"]
                            break
                    self.log(f"  📁 {cat_name}: {len(files)} 个文件 ({self.engine.format_size(cat_size)})")

            self.log(f"\n💡 共可释放: {size_str}")

            self.status_label.config(text=f"扫描完成 - 发现可清理空间", fg="#27ae60")
            self.size_label.config(text=f"可释放: {size_str}")

        self.set_buttons_state(scanning=False)
        self.clean_btn.config(state=NORMAL if total_files > 0 else DISABLED)

    def on_scan_cancelled(self):
        """扫描取消 UI 更新"""
        self.status_label.config(text="扫描已取消", fg="#e67e22")
        self.size_label.config(text="")
        self.progress_label.config(text="")
        self.set_buttons_state(scanning=False)

    def start_clean(self):
        """开始清理（后台线程）"""
        if not self.scan_results:
            messagebox.showwarning("提示", "请先扫描垃圾文件！")
            return

        total_files = sum(len(files) for files in self.scan_results.values())
        size_str = self.engine.format_size(self.scan_total_size)

        result = messagebox.askyesno(
            "确认清理",
            f"确定要清理以下垃圾文件吗？\n\n"
            f"📊 共 {total_files} 个文件\n"
            f"💾 可释放空间: {size_str}\n\n"
            f"⚠️ 注意: 清理后文件将被永久删除！",
            icon="warning"
        )
        if not result:
            return

        self.set_buttons_state(cleaning=True)
        self.log(f"\n{'='*50}")
        self.log(f"🧹 开始清理...")
        self.log(f"{'='*50}")

        self.status_label.config(text="正在清理中...", fg="#e74c3c")
        self.progress_bar["value"] = 0
        self.progress_label.config(text="准备清理...")
        total_files = sum(len(files) for files in self.scan_results.values())

        def clean_thread():
            def progress_callback(cleaned, total, freed, failed):
                pct = int((cleaned / total) * 100) if total > 0 else 0
                self.progress_bar["value"] = pct
                freed_str = self.engine.format_size(freed)
                self.progress_label.config(
                    text=f"清理进度: {cleaned}/{total}  已释放: {freed_str}  失败: {failed}"
                )
                self.root.update_idletasks()

            try:
                cleaned, freed, failed = self.engine.clean_junk(
                    self.scan_results, progress_callback
                )

                self.root.after(0, lambda: self.on_clean_complete(cleaned, freed, failed))
            except Exception as e:
                self.root.after(0, lambda: self.log(f"\n❌ 清理出错: {str(e)}"))
                self.root.after(0, lambda: self.set_buttons_state(cleaning=False))

        t = threading.Thread(target=clean_thread, daemon=True)
        t.start()

    def on_clean_complete(self, cleaned, freed, failed):
        """清理完成 UI 更新"""
        self.progress_bar["value"] = 100
        freed_str = self.engine.format_size(freed)

        self.log(f"\n{'='*50}")
        self.log(f"✅ 清理完成！")
        self.log(f"  ✔ 成功清理: {cleaned} 个文件")
        self.log(f"  💾 释放空间: {freed_str}")
        if failed > 0:
            self.log(f"  ⚠ 清理失败: {failed} 个文件")
        self.log(f"{'='*50}")

        self.status_label.config(text=f"清理完成 - 释放 {freed_str}", fg="#27ae60")
        self.size_label.config(text=f"已释放: {freed_str}")
        self.progress_label.config(text=f"清理完成！共释放 {freed_str}")

        self.scan_results = None
        self.set_buttons_state(cleaning=False)


def main():
    root = Tk()
    # 先创建,用 alpha 隐藏避免弹窗时产生第二个窗口
    root.attributes("-alpha", 0.0)

    if platform.system() == "Windows" and not is_admin():
        result = messagebox.askyesno(
            "权限提示",
            "⚠️ 建议以管理员身份运行以获得最佳清理效果。\n\n"
            "是否继续以当前权限运行？（部分系统文件可能无法清理）"
        )
        if not result:
            root.destroy()
            sys.exit(0)

    try:
        app = Application(root)
        app.center_window()
        root.attributes("-alpha", 1.0)  # UI 构建完毕再显示
    except Exception as e:
        root.attributes("-alpha", 1.0)
        messagebox.showerror("启动错误", f"应用程序启动失败:\n{str(e)}")
        root.destroy()
        sys.exit(1)

    def on_closing():
        if app.is_scanning or app.is_cleaning:
            result = messagebox.askyesno("确认退出", "当前有操作正在进行，确定要退出吗？")
            if result:
                app.engine.stop_flag = True
                app.engine.cancel_scan = True
                root.destroy()
        elif hasattr(app, 'largefile_tab') and app.largefile_tab.is_scanning:
            result = messagebox.askyesno("确认退出", "大文件扫描正在进行，确定要退出吗？")
            if result:
                app.engine.stop_flag = True
                app.engine.cancel_scan = True
                root.destroy()
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
