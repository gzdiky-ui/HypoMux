"""
HypoMux 进程级强制代理模块
类似 Proxifier 的功能，强制指定进程的网络流量通过 SOCKS5 代理

实现方案：
1. 使用 Windows LSP (Layered Service Provider) 或 WFP (Windows Filtering Platform)
2. 通过 Windows API Hook 劫持 Winsock API 调用
3. 使用 SocksCap/ProxyCap 的开源替代方案

注意：完整实现需要底层 Windows 驱动开发，这里提供轻量级实现方案
"""

import os
import sys
import subprocess
import winreg
from typing import List, Dict, Tuple, Optional
from pathlib import Path
import psutil


class ProcessProxyManager:
    """进程级代理管理器"""

    def __init__(self, proxy_host: str = "127.0.0.1", proxy_port: int = 1080):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.proxified_processes: Dict[int, str] = {}  # pid -> 进程名

    # ========== 方案 1：环境变量注入（轻量级，适用于部分应用） ==========

    def launch_with_proxy_env(self, exe_path: str, args: List[str] = None) -> Tuple[bool, str, Optional[int]]:
        """
        使用代理环境变量启动进程（适用于遵守标准代理环境变量的应用）

        支持的环境变量：
        - HTTP_PROXY / HTTPS_PROXY
        - ALL_PROXY (SOCKS5)
        - NO_PROXY

        Args:
            exe_path: 可执行文件路径
            args: 命令行参数列表

        Returns:
            (成功, 消息, 进程PID)
        """
        try:
            if not os.path.exists(exe_path):
                return False, f"可执行文件不存在: {exe_path}", None

            # 构建代理环境变量
            proxy_env = os.environ.copy()
            socks5_proxy = f"socks5://{self.proxy_host}:{self.proxy_port}"
            http_proxy = f"http://{self.proxy_host}:{self.proxy_port}"

            proxy_env["ALL_PROXY"] = socks5_proxy
            proxy_env["HTTP_PROXY"] = http_proxy
            proxy_env["HTTPS_PROXY"] = http_proxy
            proxy_env["SOCKS_PROXY"] = socks5_proxy
            proxy_env["SOCKS5_PROXY"] = socks5_proxy

            # 启动进程
            cmd = [exe_path] + (args or [])
            process = subprocess.Popen(
                cmd,
                env=proxy_env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )

            self.proxified_processes[process.pid] = Path(exe_path).name

            return True, f"已启动进程（PID: {process.pid}）\n环境变量代理已注入", process.pid

        except Exception as e:
            return False, f"启动失败: {str(e)}", None

    # ========== 方案 2：使用 Proxifier 命令行（如果用户已安装） ==========

    def launch_with_proxifier(self, exe_path: str, args: List[str] = None) -> Tuple[bool, str, Optional[int]]:
        """
        使用 Proxifier 命令行启动进程（需要用户已安装 Proxifier）

        Proxifier 命令行格式：
        ProxifierPE.exe <executable> [arguments]
        """
        try:
            # 查找 Proxifier 安装路径
            proxifier_paths = [
                r"C:\Program Files\Proxifier\Proxifier.exe",
                r"C:\Program Files (x86)\Proxifier\Proxifier.exe",
            ]

            proxifier_exe = None
            for path in proxifier_paths:
                if os.path.exists(path):
                    proxifier_exe = path
                    break

            if not proxifier_exe:
                return False, "未找到 Proxifier 安装，请先安装 Proxifier 或使用其他方案", None

            # 构建命令
            cmd = [proxifier_exe, exe_path] + (args or [])
            process = subprocess.Popen(cmd)

            return True, f"已通过 Proxifier 启动进程（PID: {process.pid}）", process.pid

        except Exception as e:
            return False, f"启动失败: {str(e)}", None

    # ========== 方案 3：使用 Proxychains-Windows（开源替代） ==========

    def launch_with_proxychains(self, exe_path: str, args: List[str] = None) -> Tuple[bool, str, Optional[int]]:
        """
        使用 Proxychains-Windows 启动进程（需要预先安装）

        GitHub: https://github.com/shunf4/proxychains-windows

        使用方法：
        proxychains.exe <program> [args]
        """
        try:
            # 查找 proxychains 可执行文件
            proxychains_exe = self._find_proxychains()

            if not proxychains_exe:
                return False, "未找到 Proxychains-Windows，请从 GitHub 下载: https://github.com/shunf4/proxychains-windows", None

            # 生成配置文件
            config_path = self._generate_proxychains_config()

            # 构建命令
            env = os.environ.copy()
            env["PROXYCHAINS_CONF_FILE"] = str(config_path)

            cmd = [proxychains_exe, exe_path] + (args or [])
            process = subprocess.Popen(cmd, env=env)

            self.proxified_processes[process.pid] = Path(exe_path).name

            return True, f"已通过 Proxychains 启动进程（PID: {process.pid}）", process.pid

        except Exception as e:
            return False, f"启动失败: {str(e)}", None

    def _find_proxychains(self) -> Optional[str]:
        """查找 proxychains 可执行文件"""
        # 常见安装路径
        common_paths = [
            r"C:\Program Files\proxychains\proxychains.exe",
            r"C:\Program Files (x86)\proxychains\proxychains.exe",
            Path.home() / "AppData/Local/proxychains/proxychains.exe",
        ]

        for path in common_paths:
            if os.path.exists(path):
                return str(path)

        # 检查 PATH 环境变量
        import shutil
        proxychains = shutil.which("proxychains")
        if proxychains:
            return proxychains

        return None

    def _generate_proxychains_config(self) -> Path:
        """生成 Proxychains 配置文件"""
        config_dir = Path.home() / ".netbooster"
        config_dir.mkdir(exist_ok=True)

        config_path = config_dir / "proxychains.conf"

        config_content = f"""# NetBooster Auto-Generated Proxychains Config
strict_chain
proxy_dns
remote_dns_subnet 224
tcp_read_time_out 15000
tcp_connect_time_out 8000

[ProxyList]
socks5 {self.proxy_host} {self.proxy_port}
"""

        config_path.write_text(config_content, encoding='utf-8')
        return config_path

    # ========== 方案 4：创建启动器脚本（推荐，最稳定） ==========

    def create_launcher_script(self, app_name: str, exe_path: str, args: List[str] = None) -> Tuple[bool, str, Optional[Path]]:
        """
        创建带代理配置的启动器脚本（推荐方案）

        为指定应用创建桌面快捷方式和启动脚本，注入环境变量或启动参数

        Args:
            app_name: 应用名称（如 "Steam"）
            exe_path: 可执行文件路径
            args: 额外的启动参数

        Returns:
            (成功, 消息, 脚本路径)
        """
        try:
            if not os.path.exists(exe_path):
                return False, f"可执行文件不存在: {exe_path}", None

            # 创建启动器目录
            launcher_dir = Path.home() / ".netbooster" / "launchers"
            launcher_dir.mkdir(parents=True, exist_ok=True)

            # 生成 PowerShell 启动脚本
            script_name = f"launch_{app_name.lower().replace(' ', '_')}.ps1"
            script_path = launcher_dir / script_name

            args_str = " ".join(args) if args else ""

            script_content = f"""# NetBooster 代理启动器 - {app_name}
# 自动生成于 NetBooster

$env:ALL_PROXY = "socks5://{self.proxy_host}:{self.proxy_port}"
$env:HTTP_PROXY = "http://{self.proxy_host}:{self.proxy_port}"
$env:HTTPS_PROXY = "http://{self.proxy_host}:{self.proxy_port}"
$env:SOCKS_PROXY = "socks5://{self.proxy_host}:{self.proxy_port}"

Write-Host "NetBooster: 启动 {app_name} (代理: {self.proxy_host}:{self.proxy_port})" -ForegroundColor Green

Start-Process -FilePath "{exe_path}" -ArgumentList "{args_str}"
"""

            script_path.write_text(script_content, encoding='utf-8')

            # 创建桌面快捷方式
            desktop = Path.home() / "Desktop"
            shortcut_name = f"{app_name} (NetBooster 加速).lnk"
            shortcut_path = desktop / shortcut_name

            # 使用 PowerShell 创建快捷方式
            ps_create_shortcut = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"{script_path}`""
$Shortcut.WorkingDirectory = "{Path(exe_path).parent}"
$Shortcut.Description = "{app_name} with NetBooster Multi-NIC Acceleration"
$Shortcut.Save()
"""

            result = subprocess.run(
                ["powershell", "-Command", ps_create_shortcut],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                return True, f"已创建 {app_name} 加速启动器\n\n脚本位置: {script_path}\n桌面快捷方式: {shortcut_name}\n\n使用桌面快捷方式启动 {app_name} 即可享受多网卡加速！", script_path
            else:
                return False, f"创建快捷方式失败: {result.stderr}", script_path

        except Exception as e:
            return False, f"创建启动器失败: {str(e)}", None

    # ========== 预设应用快捷启动器 ==========

    def create_steam_launcher(self) -> Tuple[bool, str]:
        """创建 Steam 加速启动器"""
        steam_path = self._find_steam_path()
        if not steam_path:
            return False, "未找到 Steam 安装路径"

        # Steam 支持命令行代理参数
        proxy_arg = f"-proxy=socks5://{self.proxy_host}:{self.proxy_port}"
        return self.create_launcher_script("Steam", steam_path, [proxy_arg])[:2]

    def create_epic_launcher(self) -> Tuple[bool, str]:
        """创建 Epic Games 加速启动器"""
        epic_path = self._find_epic_path()
        if not epic_path:
            return False, "未找到 Epic Games 安装路径"

        return self.create_launcher_script("Epic Games", epic_path)[:2]

    def create_ubisoft_launcher(self) -> Tuple[bool, str]:
        """创建 Ubisoft Connect 加速启动器"""
        ubisoft_path = self._find_ubisoft_path()
        if not ubisoft_path:
            return False, "未找到 Ubisoft Connect 安装路径"

        return self.create_launcher_script("Ubisoft Connect", ubisoft_path)[:2]

    # ========== 应用路径查找 ==========

    def _find_steam_path(self) -> Optional[str]:
        """查找 Steam 安装路径"""
        common_paths = [
            r"C:\Program Files (x86)\Steam\steam.exe",
            r"C:\Program Files\Steam\steam.exe",
            r"D:\Steam\steam.exe",
            r"E:\Steam\steam.exe",
        ]

        for path in common_paths:
            if os.path.exists(path):
                return path

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                steam_path = winreg.QueryValueEx(key, "SteamExe")[0]
                if os.path.exists(steam_path):
                    return steam_path
        except:
            pass

        return None

    def _find_epic_path(self) -> Optional[str]:
        """查找 Epic Games 启动器路径"""
        common_paths = [
            r"C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\Win32\EpicGamesLauncher.exe",
            r"C:\Program Files\Epic Games\Launcher\Portal\Binaries\Win32\EpicGamesLauncher.exe",
        ]

        for path in common_paths:
            if os.path.exists(path):
                return path

        return None

    def _find_ubisoft_path(self) -> Optional[str]:
        """查找 Ubisoft Connect 路径"""
        common_paths = [
            r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\UbisoftConnect.exe",
            r"C:\Program Files\Ubisoft\Ubisoft Game Launcher\UbisoftConnect.exe",
        ]

        for path in common_paths:
            if os.path.exists(path):
                return path

        return None

    # ========== 进程监控 ==========

    def get_running_proxified_processes(self) -> List[Dict]:
        """获取当前运行中的被代理进程列表"""
        running = []
        for pid, name in list(self.proxified_processes.items()):
            if psutil.pid_exists(pid):
                try:
                    proc = psutil.Process(pid)
                    running.append({
                        "pid": pid,
                        "name": name,
                        "status": proc.status(),
                        "memory_mb": proc.memory_info().rss / 1024 / 1024
                    })
                except:
                    pass
            else:
                # 进程已退出，从列表移除
                del self.proxified_processes[pid]

        return running

    def cleanup(self):
        """清理资源"""
        self.proxified_processes.clear()
