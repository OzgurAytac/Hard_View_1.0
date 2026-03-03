import sys
import os
import platform
import traceback
import faulthandler
import atexit
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import psutil
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMainWindow,
    QProgressBar,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# ----------------- Crash Log -----------------
def _log_path():
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(__file__)
    return os.path.join(base, "hardview_crash.log")

LOGFILE = _log_path()

# Faulthandler dosya handle'ını yönet (kapanışta kapat)
_fault_fh = open(LOGFILE, "a", buffering=1, encoding="utf-8", errors="ignore")
faulthandler.enable(_fault_fh)
atexit.register(lambda: _fault_fh.close())

def excepthook(etype, value, tb):
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write("\n\n=== UNHANDLED EXCEPTION ===\n")
        f.write("".join(traceback.format_exception(etype, value, tb)))

sys.excepthook = excepthook


# --- Opsiyonel modüller ---
try:
    import cpuinfo  # py-cpuinfo
except Exception:
    cpuinfo = None

try:
    import wmi  # Windows WMI
except Exception:
    wmi = None

try:
    import pynvml  # nvidia-ml-py
    NVML_AVAILABLE = True
except Exception:
    pynvml = None
    NVML_AVAILABLE = False


# ----------------- Yardımcılar -----------------
def fmt_bytes(n: Optional[int]) -> str:
    if n is None:
        return "N/A"
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(n)
    for u in units:
        if v < step:
            return f"{v:.1f} {u}"
        v /= step
    return f"{v:.1f} PB"

def fmt_ghz_from_mhz(m: Optional[float]) -> str:
    if m is None or m <= 0:
        return "N/A"
    return f"{m / 1000.0:.2f} GHz"

def parse_wmi_date(wmi_date: Optional[str]) -> str:
    if not wmi_date:
        return "N/A"
    try:
        dt = datetime.strptime(wmi_date[:14], "%Y%m%d%H%M%S")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return wmi_date

def entegre_grafik_birimleri(name: str) -> bool:
    s = (name or "").strip().lower()
    intel_markers = ["intel", "uhd", "iris", "hd graphics"]
    amd_igpu_markers = ["radeon graphics", "vega", "apu"]

    if any(k in s for k in intel_markers):
        return True

    if "amd" in s or "radeon" in s:
        if any(k in s for k in amd_igpu_markers) and "rx " not in s and "rtx" not in s and "gtx" not in s:
            return True

    return False

def _clean_str(x: Optional[str]) -> Optional[str]:
    if x is None:
        return None
    if not isinstance(x, str):
        try:
            x = str(x)
        except Exception:
            return None
    x = x.strip()
    return x if x else None

def _is_valid_serial(s: Optional[str]) -> bool:
    s = _clean_str(s)
    if not s:
        return False
    s_norm = s.strip().upper()

    invalid_markers = {
        "TO BE FILLED BY O.E.M.",
        "TO BE FILLED BY OEM",
        "DEFAULT STRING",
        "SYSTEM SERIAL NUMBER",
        "SYSTEMSERIALNUMBER",
        "NONE",
        "N/A",
        "NA",
        "NULL",
        "0",
        "00000000",
        "000000000",
        "0000000000",
        "000000000000",
        "123456789",
        "SERIALNUMBER",
    }
    if s_norm in invalid_markers:
        return False

    stripped = "".join(ch for ch in s_norm if ch.isalnum())
    if not stripped:
        return False
    if set(stripped) <= {"0"}:
        return False
    if set(stripped) <= {"F"}:
        return False

    return True

def _first_valid_serial(candidates: List[Optional[str]]) -> str:
    for c in candidates:
        if _is_valid_serial(c):
            return _clean_str(c) or "N/A"
    for c in candidates:
        c2 = _clean_str(c)
        if c2:
            return c2
    return "N/A"


# ----------------- Veri Modelleri -----------------
@dataclass
class CpuBilgi1:
    name: str
    physical_cores: int
    logical_cores: int

@dataclass
class CpuBilgi2:
    total_usage_percent: float
    per_core_usage: List[float]
    current_freq_mhz: Optional[float]

@dataclass
class AnakartBilgi:
    manufacturer: str
    bios_version: str
    bios_date: str
    serial_number: str  # artık "cihaz seri no" olarak dolduruluyor

@dataclass
class RamModulleri:
    slot: str
    size_bytes: Optional[int]
    speed_mhz: Optional[int]
    manufacturer: Optional[str]
    part_number: Optional[str]
    serial: Optional[str]

@dataclass
class GPUBilgi:
    name: str
    vram_total_bytes: Optional[int]

@dataclass
class OSBilgi:
    product_name: str
    display_version: str
    version: str
    build: str
    architecture: str
    machine_name: str


# ----------------- Provider -----------------
class SystemProvider:
    def __init__(self):
        self.is_windows = platform.system().lower().startswith("win")
        self._wmi = None
        if self.is_windows and wmi is not None:
            try:
                self._wmi = wmi.WMI()
            except Exception:
                self._wmi = None

    def get_cpu_static(self) -> CpuBilgi1:
        name = "N/A"
        if cpuinfo is not None:
            try:
                info = cpuinfo.get_cpu_info()
                name = info.get("brand_raw") or info.get("brand") or "N/A"
            except Exception:
                pass
        if name == "N/A":
            name = platform.processor() or "N/A"

        physical = psutil.cpu_count(logical=False) or 0
        logical = psutil.cpu_count(logical=True) or 0
        return CpuBilgi1(name=name, physical_cores=physical, logical_cores=logical)

    def get_cpu_dynamic(self) -> CpuBilgi2:
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        total = (sum(per_core) / len(per_core)) if per_core else 0.0

        freq = None
        try:
            f = psutil.cpu_freq()
            if f and f.current and f.current > 0:
                freq = float(f.current)
        except Exception:
            pass

        return CpuBilgi2(
            total_usage_percent=float(total),
            per_core_usage=[float(x) for x in per_core],
            current_freq_mhz=freq,
        )

    def get_board_info(self) -> AnakartBilgi:
        """
        Windows'ta "Cihaz Seri Numarası" için daha doğru kaynaklar:
          - Win32_BIOS.SerialNumber (çoğu markada cihaz seri no)
          - Win32_SystemEnclosure.SerialNumber
          - Win32_ComputerSystemProduct.IdentifyingNumber
          - Win32_BaseBoard.SerialNumber (en son çare)
        Ayrıca yaygın geçersiz/dummy değerler filtrelenir.
        """
        if self.is_windows and self._wmi is not None:
            manuf = "N/A"
            baseboard_serial = None

            bios_ver = "N/A"
            bios_date = "N/A"
            bios_serial = None

            enclosure_serial = None
            csproduct_ident = None

            try:
                baseboards = self._wmi.Win32_BaseBoard()
                if baseboards:
                    bb = baseboards[0]
                    manuf = getattr(bb, "Manufacturer", None) or "N/A"
                    baseboard_serial = getattr(bb, "SerialNumber", None)
            except Exception:
                pass

            try:
                bios_list = self._wmi.Win32_BIOS()
                if bios_list:
                    bios = bios_list[0]
                    bios_ver = (
                        getattr(bios, "SMBIOSBIOSVersion", None)
                        or getattr(bios, "Version", None)
                        or "N/A"
                    )
                    rd = getattr(bios, "ReleaseDate", None)
                    bios_date = parse_wmi_date(rd)
                    bios_serial = getattr(bios, "SerialNumber", None)
            except Exception:
                pass

            try:
                encl = self._wmi.Win32_SystemEnclosure()
                if encl:
                    enclosure_serial = getattr(encl[0], "SerialNumber", None)
            except Exception:
                pass

            try:
                csp = self._wmi.Win32_ComputerSystemProduct()
                if csp:
                    csproduct_ident = getattr(csp[0], "IdentifyingNumber", None)
            except Exception:
                pass

            device_serial = _first_valid_serial([bios_serial, enclosure_serial, csproduct_ident, baseboard_serial])

            return AnakartBilgi(
                manufacturer=_clean_str(manuf) or "N/A",
                bios_version=_clean_str(bios_ver) or "N/A",
                bios_date=_clean_str(bios_date) or "N/A",
                serial_number=device_serial,
            )

        return AnakartBilgi("N/A", "N/A", "N/A", "N/A")

    def get_ram_modules(self) -> List[RamModulleri]:
        modules: List[RamModulleri] = []
        if self.is_windows and self._wmi is not None:
            try:
                for m in self._wmi.Win32_PhysicalMemory():
                    slot = (getattr(m, "DeviceLocator", None) or getattr(m, "BankLabel", None) or "Slot")
                    slot = slot.strip() if isinstance(slot, str) else "Slot"

                    cap = getattr(m, "Capacity", None)
                    size_b = int(cap) if cap is not None else None

                    speed = getattr(m, "Speed", None)
                    speed_i = int(speed) if speed is not None else None

                    manuf = getattr(m, "Manufacturer", None)
                    part = getattr(m, "PartNumber", None)
                    serial = getattr(m, "SerialNumber", None)

                    modules.append(
                        RamModulleri(
                            slot=slot,
                            size_bytes=size_b,
                            speed_mhz=speed_i,
                            manufacturer=(manuf.strip() if isinstance(manuf, str) else manuf),
                            part_number=(part.strip() if isinstance(part, str) else part),
                            serial=(serial.strip() if isinstance(serial, str) else serial),
                        )
                    )
            except Exception:
                pass
        return modules

    def get_gpus_static(self) -> List[GPUBilgi]:
        gpus: List[GPUBilgi] = []
        seen = set()
        nvml_added_nvidia = False

        if NVML_AVAILABLE and pynvml is not None:
            nvml_inited = False
            try:
                pynvml.nvmlInit()
                nvml_inited = True
                count = pynvml.nvmlDeviceGetCount()
                for i in range(count):
                    h = pynvml.nvmlDeviceGetHandleByIndex(i)
                    name = pynvml.nvmlDeviceGetName(h)
                    if isinstance(name, bytes):
                        name = name.decode("utf-8", errors="ignore")
                    name = (name or "NVIDIA GPU").strip()

                    mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                    total_vram = int(mem.total) if mem and getattr(mem, "total", None) is not None else None

                    gpus.append(GPUBilgi(name=name, vram_total_bytes=total_vram))
                    seen.add(name.lower())
                    nvml_added_nvidia = True
            except Exception:
                pass
            finally:
                if nvml_inited:
                    try:
                        pynvml.nvmlShutdown()
                    except Exception:
                        pass

        if self.is_windows and self._wmi is not None:
            try:
                for vc in self._wmi.Win32_VideoController():
                    name = getattr(vc, "Name", None) or "GPU"
                    name = name.strip() if isinstance(name, str) else "GPU"
                    key = name.lower()

                    if nvml_added_nvidia and "nvidia" in key:
                        continue
                    if key in seen:
                        continue

                    if entegre_grafik_birimleri(name):
                        gpus.append(GPUBilgi(name=name, vram_total_bytes=None))
                        seen.add(key)
                        continue

                    if "nvidia" in key and not NVML_AVAILABLE:
                        gpus.append(GPUBilgi(name=name, vram_total_bytes=None))
                        seen.add(key)
                        continue

                    ram = getattr(vc, "AdapterRAM", None)
                    vram = int(ram) if ram is not None else None
                    gpus.append(GPUBilgi(name=name, vram_total_bytes=vram))
                    seen.add(key)
            except Exception:
                pass

        return gpus

    def get_os_info(self) -> OSBilgi:
        product_name = platform.platform() or "N/A"
        display_version = "N/A"
        version = platform.version() or "N/A"
        build = "N/A"
        architecture = platform.architecture()[0] if platform.architecture() else "N/A"
        machine_name = platform.node() or "N/A"

        if not self.is_windows:
            return OSBilgi(
                product_name=product_name or "N/A",
                display_version=display_version or "N/A",
                version=version or "N/A",
                build=build or "N/A",
                architecture=architecture or "N/A",
                machine_name=machine_name or "N/A",
            )

        reg_product = None
        reg_display = None
        reg_version = None
        reg_build = None
        reg_ubr = None

        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
            reg_product = _clean_str(winreg.QueryValueEx(key, "ProductName")[0])

            try:
                reg_display = _clean_str(winreg.QueryValueEx(key, "DisplayVersion")[0])
            except Exception:
                try:
                    reg_display = _clean_str(winreg.QueryValueEx(key, "ReleaseId")[0])
                except Exception:
                    reg_display = None

            reg_build = _clean_str(winreg.QueryValueEx(key, "CurrentBuildNumber")[0])

            try:
                reg_ubr = winreg.QueryValueEx(key, "UBR")[0]
            except Exception:
                reg_ubr = None

            try:
                reg_version = _clean_str(winreg.QueryValueEx(key, "CurrentVersion")[0])
            except Exception:
                reg_version = None
        except Exception:
            pass

        wmi_caption = None
        wmi_version = None
        wmi_build = None
        wmi_arch = None
        if self._wmi is not None:
            try:
                os_list = self._wmi.Win32_OperatingSystem()
                if os_list:
                    os0 = os_list[0]
                    wmi_caption = _clean_str(getattr(os0, "Caption", None))
                    wmi_version = _clean_str(getattr(os0, "Version", None))
                    wmi_build = _clean_str(getattr(os0, "BuildNumber", None))
                    wmi_arch = _clean_str(getattr(os0, "OSArchitecture", None))
            except Exception:
                pass

        build_num_for_check = None
        if reg_build and reg_build.isdigit():
            build_num_for_check = int(reg_build)
        elif wmi_build and wmi_build.isdigit():
            build_num_for_check = int(wmi_build)

        if reg_build:
            if reg_ubr is not None:
                try:
                    build = f"{reg_build}.{int(reg_ubr)}"
                except Exception:
                    build = reg_build
            else:
                build = reg_build
        elif wmi_build:
            build = wmi_build

        product_name = wmi_caption or reg_product or product_name

        if build_num_for_check is not None and build_num_for_check >= 22000:
            if isinstance(product_name, str) and "windows 10" in product_name.lower():
                product_name = product_name.replace("Windows 10", "Windows 11").replace("WINDOWS 10", "WINDOWS 11")

        display_version = reg_display or display_version
        version = wmi_version or reg_version or version
        architecture = wmi_arch or architecture

        return OSBilgi(
            product_name=product_name or "N/A",
            display_version=display_version or "N/A",
            version=version or "N/A",
            build=build or "N/A",
            architecture=architecture or "N/A",
            machine_name=machine_name or "N/A",
        )


# ----------------- UI parçaları -----------------
class InfoCard(QGroupBox):
    def __init__(self, title: str):
        super().__init__(title)
        self.setObjectName("InfoCard")
        self.grid = QGridLayout(self)
        self.grid.setColumnStretch(0, 0)
        self.grid.setColumnStretch(1, 1)
        self._row = 0

    def add_row(self, label: str, value_widget: QWidget) -> None:
        lbl = QLabel(label)
        lbl.setObjectName("RowLabel")
        self.grid.addWidget(lbl, self._row, 0, 1, 1, Qt.AlignmentFlag.AlignLeft)
        self.grid.addWidget(value_widget, self._row, 1, 1, 1, Qt.AlignmentFlag.AlignLeft)
        self._row += 1

def make_scroll(content: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setFrameShape(QFrame.Shape.NoFrame)
    s.setWidget(content)
    return s


# ----------------- Ana Uygulama -----------------
class MainWindow(QMainWindow):
    APP_VERSION = "1.0"
    APP_DATE = "02/03/2004"
    APP_DEVELOPER = "Özgür Aytaç"

    def __init__(self):
        super().__init__()

        # Pencere kapanınca objeyi gerçekten yok et (timer vb. her şey temizlenir)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.setWindowTitle(f"HardView ({self.APP_VERSION})")
        self.resize(980, 680)

        self.provider = SystemProvider()

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        header = QLabel("Sistem Bilgisi")
        header.setObjectName("Header")
        header.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        root_layout.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("MainTabs")
        root_layout.addWidget(self.tabs, 1)

        self.cpu_tab = self._build_cpu_tab()
        self.mb_tab = self._build_mb_tab()
        self.ram_tab = self._build_ram_tab()
        self.gpu_tab = self._build_gpu_tab()
        self.about_tab = self._build_about_tab()

        self.tabs.addTab(self.cpu_tab, "İşlemci")
        self.tabs.addTab(self.mb_tab, "Anakart")
        self.tabs.addTab(self.ram_tab, "RAM")
        self.tabs.addTab(self.gpu_tab, "Ekran Kartı")
        self.tabs.addTab(self.about_tab, "Hakkında")

        self.setCentralWidget(root)
        self._apply_theme()

        # psutil warm-up
        try:
            psutil.cpu_percent(interval=None)
            psutil.cpu_percent(interval=None, percpu=True)
        except Exception:
            pass

        # Dinamik update timer (CPU only)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.CoarseTimer)
        self.timer.timeout.connect(self._update_dynamic_cpu)

        # Drag/resize sırasında kısa durdurup tekrar başlat
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_updates)

        QTimer.singleShot(0, self._load_static_all)

    def cleanup(self):
        """Kapanışta CPU tüketen döngüleri durdur, kaynakları bırak."""
        try:
            if hasattr(self, "timer") and self.timer.isActive():
                self.timer.stop()
        except Exception:
            pass

        try:
            if hasattr(self, "_resume_timer") and self._resume_timer.isActive():
                self._resume_timer.stop()
        except Exception:
            pass

        # Provider referansını kes (WMI/COM objeleri bazen kapanışta takılabiliyor)
        try:
            self.provider = None
        except Exception:
            pass

    def closeEvent(self, event):
        # Önce timerları durdur
        self.cleanup()

        # Event loop’un kapanmasını garantiye al
        app = QApplication.instance()
        if app is not None:
            app.quit()

        super().closeEvent(event)

    def _apply_theme(self):
        self.setStyleSheet(
            """
            QWidget { background: #EAF6FF; color: #0A2540; font-family: "Segoe UI"; font-size: 12px; }
            QLabel#Header { color: #0A2540; padding: 6px 4px; }
            QTabWidget::pane { border: 2px solid #B3E5FC; border-radius: 12px; padding: 8px; background: #F6FBFF; }
            QTabBar::tab { background: #B3E5FC; padding: 10px 16px; margin-right: 6px; border-top-left-radius: 10px; border-top-right-radius: 10px; }
            QTabBar::tab:selected { background: #FFD54F; font-weight: 700; }
            QGroupBox#InfoCard { border: 2px solid #B3E5FC; border-radius: 14px; margin-top: 12px; padding: 12px; background: white; }
            QGroupBox#InfoCard::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #0A2540; font-weight: 700; }
            QLabel#RowLabel { color: #335C7A; }
            QProgressBar { border: 1px solid #B3E5FC; border-radius: 8px; text-align: center; height: 18px; background: #F2FAFF; }
            QProgressBar::chunk { background: #FFD54F; border-radius: 8px; }
            QTableWidget { background: white; border: 2px solid #B3E5FC; border-radius: 12px; gridline-color: #D3EEFF; }
            QHeaderView::section { background: #B3E5FC; padding: 6px; border: none; font-weight: 700; }
            """
        )

    # --------- Tabs ---------
    def _build_cpu_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        self.cpu_card = InfoCard("İşlemci Bilgisi")
        self.lbl_cpu_name = QLabel("...")
        self.lbl_cpu_freq = QLabel("...")
        self.lbl_cpu_cores = QLabel("...")
        self.pb_cpu_usage = QProgressBar()
        self.pb_cpu_usage.setRange(0, 100)

        self.cpu_card.add_row("İşlemci İsmi", self.lbl_cpu_name)
        self.cpu_card.add_row("İşlemci GHZ", self.lbl_cpu_freq)
        self.cpu_card.add_row("Çekirdek (Fiziksel / Mantıksal)", self.lbl_cpu_cores)
        self.cpu_card.add_row("İşlemci Kullanımı", self.pb_cpu_usage)

        self.core_table = QTableWidget(0, 2)
        self.core_table.setHorizontalHeaderLabels(["Çekirdek", "Kullanım (%)"])
        self.core_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.core_table.verticalHeader().setVisible(False)
        self.core_table.setSortingEnabled(False)
        self.core_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._core_usage_items: List[QTableWidgetItem] = []

        layout.addWidget(self.cpu_card)
        layout.addWidget(QLabel("Çekirdek Kullanımları:"))
        layout.addWidget(self.core_table, 1)
        return make_scroll(w)

    def _build_mb_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        self.mb_card = InfoCard("Anakart / BIOS Bilgisi")
        self.lbl_mb_manuf = QLabel("...")
        self.lbl_bios_ver = QLabel("...")
        self.lbl_bios_date = QLabel("...")
        self.lbl_serial = QLabel("...")

        self.mb_card.add_row("Anakart Üreticisi", self.lbl_mb_manuf)
        self.mb_card.add_row("BIOS Versiyonu", self.lbl_bios_ver)
        self.mb_card.add_row("BIOS Tarihi", self.lbl_bios_date)
        self.mb_card.add_row("Cihaz Seri Numarası", self.lbl_serial)

        layout.addWidget(self.mb_card)
        layout.addStretch(1)
        return make_scroll(w)

    def _build_ram_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        self.ram_card = InfoCard("RAM Özet")
        self.lbl_ram_total = QLabel("...")
        self.ram_card.add_row("Toplam RAM", self.lbl_ram_total)

        self.ram_table = QTableWidget(0, 5)
        self.ram_table.setHorizontalHeaderLabels(["Slot", "Boyut", "MHz", "Üretici", "Parça No/Seri"])
        self.ram_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ram_table.verticalHeader().setVisible(False)
        self.ram_table.setSortingEnabled(False)
        self.ram_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout.addWidget(self.ram_card)
        layout.addWidget(QLabel("Her slot için RAM modülleri"))
        layout.addWidget(self.ram_table, 1)
        return make_scroll(w)

    def _build_gpu_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        self.gpu_table = QTableWidget(0, 2)
        self.gpu_table.setHorizontalHeaderLabels(["GPU", "VRAM"])
        self.gpu_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.gpu_table.verticalHeader().setVisible(False)
        self.gpu_table.setSortingEnabled(False)
        self.gpu_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        hint = QLabel("Ekran kartı bilgileri bu ekranda gösterilir.")
        hint.setWordWrap(True)

        layout.addWidget(QLabel("Ekran Kartı Bilgileri"))
        layout.addWidget(self.gpu_table, 1)
        layout.addWidget(hint)
        return make_scroll(w)

    def _build_about_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        self.about_app_card = InfoCard("Uygulama")
        self.lbl_app_version = QLabel("...")
        self.lbl_app_date = QLabel("...")
        self.lbl_app_dev = QLabel("...")

        self.about_app_card.add_row("Sürüm:", self.lbl_app_version)
        self.about_app_card.add_row("Tarih:", self.lbl_app_date)
        self.about_app_card.add_row("Geliştirici:", self.lbl_app_dev)

        self.about_os_card = InfoCard("Sistem")

        self.lbl_os_product_name = QLabel("...")
        self.lbl_os_display_version = QLabel("...")
        self.lbl_os_build = QLabel("...")
        self.lbl_os_arch = QLabel("...")
        self.lbl_os_machine = QLabel("...")

        self.about_os_card.add_row("İşletim Sistemi:", self.lbl_os_product_name)
        self.about_os_card.add_row("Güncelleme:", self.lbl_os_display_version)
        self.about_os_card.add_row("Derleme:", self.lbl_os_build)
        self.about_os_card.add_row("Mimari:", self.lbl_os_arch)
        self.about_os_card.add_row("Bilgisayar Adı:", self.lbl_os_machine)

        layout.addWidget(self.about_app_card)
        layout.addWidget(self.about_os_card)
        layout.addStretch(1)
        return make_scroll(w)

    # --------- Static load ---------
    def _init_core_table(self, n: int):
        if n <= 0:
            return
        self.core_table.setRowCount(n)
        self._core_usage_items.clear()

        self.core_table.setUpdatesEnabled(False)
        try:
            for i in range(n):
                item0 = QTableWidgetItem(f"Core {i}")
                item1 = QTableWidgetItem("0")
                self.core_table.setItem(i, 0, item0)
                self.core_table.setItem(i, 1, item1)
                self._core_usage_items.append(item1)
        finally:
            self.core_table.setUpdatesEnabled(True)

    def _load_static_all(self):
        cs = self.provider.get_cpu_static()
        self.lbl_cpu_name.setText(cs.name)
        self.lbl_cpu_cores.setText(f"{cs.physical_cores} / {cs.logical_cores}")
        self._init_core_table(cs.logical_cores)

        bi = self.provider.get_board_info()
        self.lbl_mb_manuf.setText(bi.manufacturer)
        self.lbl_bios_ver.setText(bi.bios_version)
        self.lbl_bios_date.setText(bi.bios_date)
        self.lbl_serial.setText(bi.serial_number)

        total = psutil.virtual_memory().total
        self.lbl_ram_total.setText(fmt_bytes(int(total)))
        self._reload_ram_table_static()

        self._reload_gpu_table_static()

        self._load_about_static()

        self.timer.start(1000)
        self._update_dynamic_cpu()

    def _reload_ram_table_static(self):
        modules = self.provider.get_ram_modules()
        self.ram_table.setRowCount(len(modules))

        self.ram_table.setUpdatesEnabled(False)
        try:
            for r, m in enumerate(modules):
                part_ser = " / ".join([x for x in [(_clean_str(m.part_number) or ""), (_clean_str(m.serial) or "")] if x]) or "N/A"
                self.ram_table.setItem(r, 0, QTableWidgetItem(m.slot))
                self.ram_table.setItem(r, 1, QTableWidgetItem(fmt_bytes(m.size_bytes)))
                self.ram_table.setItem(r, 2, QTableWidgetItem(str(m.speed_mhz) if m.speed_mhz else "N/A"))
                self.ram_table.setItem(r, 3, QTableWidgetItem(m.manufacturer or "N/A"))
                self.ram_table.setItem(r, 4, QTableWidgetItem(part_ser))
        finally:
            self.ram_table.setUpdatesEnabled(True)

    def _reload_gpu_table_static(self):
        gpus = self.provider.get_gpus_static()
        self.gpu_table.setRowCount(len(gpus))

        self.gpu_table.setUpdatesEnabled(False)
        try:
            for r, g in enumerate(gpus):
                self.gpu_table.setItem(r, 0, QTableWidgetItem(g.name))
                self.gpu_table.setItem(r, 1, QTableWidgetItem(fmt_bytes(g.vram_total_bytes)))
        finally:
            self.gpu_table.setUpdatesEnabled(True)

    def _load_about_static(self):
        self.lbl_app_version.setText(self.APP_VERSION)
        self.lbl_app_date.setText(self.APP_DATE)
        self.lbl_app_dev.setText(self.APP_DEVELOPER)

        osb = self.provider.get_os_info()
        self.lbl_os_product_name.setText(osb.product_name)
        self.lbl_os_display_version.setText(osb.display_version)
        self.lbl_os_build.setText(osb.build)
        self.lbl_os_arch.setText(osb.architecture)
        self.lbl_os_machine.setText(osb.machine_name)

    # --------- Dynamic update (CPU only) ---------
    def _update_dynamic_cpu(self):
        cd = self.provider.get_cpu_dynamic()

        self.pb_cpu_usage.setValue(int(cd.total_usage_percent))
        self.pb_cpu_usage.setFormat(f"%{cd.total_usage_percent:.0f}")
        self.lbl_cpu_freq.setText(fmt_ghz_from_mhz(cd.current_freq_mhz))

        if self._core_usage_items and cd.per_core_usage:
            self.core_table.setUpdatesEnabled(False)
            try:
                for i, usage in enumerate(cd.per_core_usage):
                    if i < len(self._core_usage_items):
                        self._core_usage_items[i].setText(f"{usage:.0f}")
            finally:
                self.core_table.setUpdatesEnabled(True)

    # --------- Drag/Resize sırasında durdur ---------
    def _pause_updates(self):
        if self.timer.isActive():
            self.timer.stop()
        self._resume_timer.start(250)

    def _resume_updates(self):
        if not self.timer.isActive():
            self.timer.start(1000)

    def moveEvent(self, e):
        self._pause_updates()
        super().moveEvent(e)

    def resizeEvent(self, e):
        self._pause_updates()
        super().resizeEvent(e)


def main():
    app = QApplication(sys.argv)

    # EXE ortamında kapanışı netleştir
    app.setQuitOnLastWindowClosed(True)

    win = MainWindow()

    # Uygulama kapanırken mutlaka cleanup çalışsın
    app.aboutToQuit.connect(win.cleanup)

    win.show()
    rc = app.exec()

    # Ek güvenlik: event loop bittikten sonra da cleanup
    try:
        win.cleanup()
    except Exception:
        pass

    return rc


if __name__ == "__main__":
    import multiprocessing as mp
    mp.freeze_support()
    raise SystemExit(main())