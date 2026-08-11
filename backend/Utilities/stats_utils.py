import glob
import time

import psutil


class StatsService:
    def __init__(self) -> None:
        self.cached = "CPU: N/A\nRAM: N/A\nCPU Temp: N/A"
        # One-time prime so first cpu_percent is meaningful
        try:
            psutil.cpu_percent(interval=0.15)
        except Exception:
            pass

    @staticmethod
    def _cpu_temp_c() -> float:
        try:
            temps = psutil.sensors_temperatures()
            for key in ("cpu-thermal", "cpu_thermal", "coretemp", "k10temp", "soc_thermal"):
                if key in temps and temps[key]:
                    cur = getattr(temps[key][0], "current", None)
                    if cur is not None:
                        return float(cur)
            for arr in temps.values():
                if arr:
                    cur = getattr(arr[0], "current", None)
                    if cur is not None:
                        return float(cur)
        except Exception:
            pass
        # sysfs fallback
        try:
            for ztemp in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
                tname = ""
                try:
                    with open(ztemp.replace("/temp", "/type"), "r") as tf:
                        tname = tf.read().strip().lower()
                except Exception:
                    pass
                if ("cpu" not in tname) and ("soc" not in tname) and ("arm" not in tname):
                    continue
                with open(ztemp, "r") as f:
                    raw = f.read().strip()
                val = float(raw) / (1000.0 if len(raw) > 3 else 1.0)
                if val > 0.0:
                    return val
        except Exception:
            pass
        return 0.0

    def collect_once(self) -> str:
        try:
            cpu_usage = int(round(psutil.cpu_percent(interval=None)))
        except Exception:
            cpu_usage = 0

        try:
            vm = psutil.virtual_memory()
            ram_percent = int(round(vm.percent))
            ram_used = int(vm.used // (1024 * 1024))
            ram_total = int(vm.total // (1024 * 1024))
            ram_str = f"{ram_percent}% ({ram_used}/{ram_total}MB)"
        except Exception:
            ram_str = "N/A"

        t = self._cpu_temp_c()
        temp_str = f"{t:.1f}C" if t > 0.0 else "N/A"

        return f"CPU: {cpu_usage}%\nRAM: {ram_str}\nCPU Temp: {temp_str}"

    def loop_update(self, stop_flag, interval_sec: int = 1) -> None:
        while not stop_flag():
            try:
                self.cached = self.collect_once()
            except Exception:
                pass
            time.sleep(max(0.25, float(interval_sec)))
