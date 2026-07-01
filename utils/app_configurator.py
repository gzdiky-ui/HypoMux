"""
HypoMux 应用快捷配置模块
自动为常见应用配置 SOCKS5 代理，实现"一键开启加速"

支持的应用：
- Steam
- IDM (Internet Download Manager)
- qBittorrent
- 系统全局代理
"""

import os
import winreg
import shutil
import re
from pathlib import Path
from typing import Tuple, Optional


class AppConfigurator:
    """应用快捷配置器"""

    def __init__(self, proxy_host: str = "127.0.0.1", proxy_port: int = 1080):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.proxy_url = f"socks5://{proxy_host}:{proxy_port}"

    # ========== Steam 配置 ==========

    def configure_steam(self) -> Tuple[bool, str]:
        """
        自动配置 Steam 使用 SOCKS5 代理

        方法 1：修改 Steam 注册表配置（优先）
        方法 2：创建带启动参数的桌面快捷方式（备选）

        Returns:
            (成功, 消息)
        """
        messages = []

        # 方法 1：尝试修改 Steam 注册表配置
        registry_success = self._configure_steam_registry()
        if registry_success:
            messages.append("OK 已配置 Steam 注册表代理设置")
        else:
            messages.append(" 未找到 Steam 注册表配置（可能未安装 Steam）")

        # 方法 2：创建桌面快捷方式（作为备选方案）
        try:
            steam_path = self._find_steam_path()
            if not steam_path:
                if not registry_success:
                    return False, "未找到 Steam 安装路径，请确保已安装 Steam"
                else:
                    return True, messages[0]

            # 创建桌面快捷方式
            desktop = Path.home() / "Desktop"
            shortcut_path = desktop / "Steam (NetBooster 加速).lnk"

            # 使用 PowerShell 创建快捷方式
            ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
$Shortcut.TargetPath = "{steam_path}"
$Shortcut.Arguments = "-tcp -noreactlogin -proxy={self.proxy_host}:{self.proxy_port}"
$Shortcut.WorkingDirectory = "{Path(steam_path).parent}"
$Shortcut.Description = "Steam with HypoMux Multi-NIC Acceleration"
$Shortcut.Save()
"""

            import subprocess
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                messages.append("OK 已在桌面创建快捷方式: Steam (HypoMux 加速).lnk")
            else:
                messages.append(f" 创建快捷方式失败: {result.stderr}")

        except Exception as e:
            messages.append(f" 创建快捷方式失败: {str(e)}")

        # 组合返回消息
        success_msg = "\n".join(messages)
        success_msg += f"\n\n 重要提示：\n"
        success_msg += f"1. 完全关闭 Steam（右键托盘图标 → 退出）\n"
        success_msg += f"2. 确保 HypoMux 已启动加速（端口 {self.proxy_port}）\n"

        if registry_success:
            success_msg += f"3. 正常启动 Steam 即可（无需使用快捷方式）\n"
            success_msg += f"4. 在 Steam 设置 → 下载 中选择合适的下载服务器\n"
        else:
            success_msg += f"3. 使用桌面的「Steam (HypoMux 加速)」快捷方式启动\n"

        return True, success_msg

    def _configure_steam_registry(self) -> bool:
        """
        通过注册表配置 Steam 代理（推荐方法）
        Steam 会读取 HKCU\\Software\\Valve\\Steam\\HTTP_PROXY 和 SOCKS_PROXY
        """
        try:
            # 打开或创建 Steam 注册表项
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Valve\Steam",
                0,
                winreg.KEY_WRITE
            ) as key:
                # 设置 SOCKS5 代理
                # 格式：socks5://ip:port 或 ip:port
                proxy_value = f"{self.proxy_host}:{self.proxy_port}"
                winreg.SetValueEx(key, "Socks5Proxy", 0, winreg.REG_SZ, proxy_value)
                winreg.SetValueEx(key, "Socks5ProxyPort", 0, winreg.REG_DWORD, self.proxy_port)

                return True
        except FileNotFoundError:
            # Steam 注册表项不存在
            return False
        except Exception as e:
            print(f"配置 Steam 注册表失败: {e}")
            return False

    def _find_steam_path(self) -> Optional[str]:
        """查找 Steam 安装路径"""
        # 常见安装路径
        common_paths = [
            r"C:\Program Files (x86)\Steam\steam.exe",
            r"C:\Program Files\Steam\steam.exe",
            r"D:\Steam\steam.exe",
            r"E:\Steam\steam.exe",
        ]

        for path in common_paths:
            if os.path.exists(path):
                return path

        # 从注册表读取
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                steam_path = winreg.QueryValueEx(key, "SteamExe")[0]
                if os.path.exists(steam_path):
                    return steam_path
        except:
            pass

        return None

    def restore_steam(self) -> Tuple[bool, str]:
        """恢复 Steam 默认设置（删除代理配置）"""
        messages = []

        # 恢复注册表配置
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Valve\Steam",
                0,
                winreg.KEY_WRITE
            ) as key:
                try:
                    winreg.DeleteValue(key, "Socks5Proxy")
                    messages.append("OK 已删除 Steam 注册表代理配置")
                except FileNotFoundError:
                    pass

                try:
                    winreg.DeleteValue(key, "Socks5ProxyPort")
                except FileNotFoundError:
                    pass

        except Exception as e:
            messages.append(f" 清理注册表失败: {str(e)}")

        # 删除快捷方式
        try:
            desktop = Path.home() / "Desktop"
            shortcut_path = desktop / "Steam (NetBooster 加速).lnk"

            if shortcut_path.exists():
                shortcut_path.unlink()
                messages.append("OK 已删除 Steam 加速快捷方式")
        except Exception as e:
            messages.append(f" 删除快捷方式失败: {str(e)}")

        if not messages:
            return True, "Steam 代理配置不存在，无需恢复"

        return True, "\n".join(messages) + "\n\n重启 Steam 后生效。"

    # ========== IDM 配置 ==========

    def configure_idm(self) -> Tuple[bool, str]:
        """
        自动配置 IDM (Internet Download Manager) 使用 SOCKS5 代理

        方法：修改注册表
        """
        try:
            # IDM 配置存储在注册表中
            key_path = r"Software\DownloadManager"

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                # 启用代理
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                # 设置代理类型 (5 = SOCKS5)
                winreg.SetValueEx(key, "ProxyType", 0, winreg.REG_DWORD, 5)
                # 设置代理地址
                winreg.SetValueEx(key, "ProxyHost", 0, winreg.REG_SZ, self.proxy_host)
                # 设置代理端口
                winreg.SetValueEx(key, "ProxyPort", 0, winreg.REG_DWORD, self.proxy_port)

            return True, f"IDM 已配置为使用 SOCKS5 代理 {self.proxy_host}:{self.proxy_port}\n\n重启 IDM 后生效。"

        except FileNotFoundError:
            return False, "未找到 IDM 注册表配置，请确保已安装 IDM"
        except PermissionError:
            return False, "权限不足，请以管理员身份运行 NetBooster"
        except Exception as e:
            return False, f"配置失败: {str(e)}"

    def restore_idm(self) -> Tuple[bool, str]:
        """恢复 IDM 默认设置（禁用代理）"""
        try:
            key_path = r"Software\DownloadManager"

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)

            return True, "已禁用 IDM 代理设置"

        except FileNotFoundError:
            return False, "未找到 IDM 注册表配置"
        except Exception as e:
            return False, f"恢复失败: {str(e)}"

    # ========== qBittorrent 配置 ==========

    def configure_qbittorrent(self) -> Tuple[bool, str]:
        """
        自动配置 qBittorrent 使用 SOCKS5 代理

        方法：修改配置文件 qBittorrent.ini
        """
        try:
            # qBittorrent 配置文件路径
            config_path = Path.home() / "AppData/Roaming/qBittorrent/qBittorrent.ini"

            if not config_path.exists():
                return False, "未找到 qBittorrent 配置文件，请确保已安装并运行过 qBittorrent"

            # 备份原配置
            backup_path = config_path.with_suffix(".ini.netbooster_backup")
            if not backup_path.exists():
                shutil.copy(config_path, backup_path)

            # 读取配置
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 修改代理设置
            # 启用代理
            content = self._update_ini_value(content, "Preferences", "Connection\\ProxyType", "2")  # 2 = SOCKS5
            content = self._update_ini_value(content, "Preferences", "Connection\\Proxy\\IP", self.proxy_host)
            content = self._update_ini_value(content, "Preferences", "Connection\\Proxy\\Port", str(self.proxy_port))
            content = self._update_ini_value(content, "Preferences", "Connection\\Proxy\\OnlyForTorrents", "false")

            # 写入配置
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(content)

            return True, f"qBittorrent 已配置为使用 SOCKS5 代理 {self.proxy_host}:{self.proxy_port}\n\n重启 qBittorrent 后生效。\n\n备份已保存至:\n{backup_path}"

        except Exception as e:
            return False, f"配置失败: {str(e)}"

    def restore_qbittorrent(self) -> Tuple[bool, str]:
        """恢复 qBittorrent 默认设置"""
        try:
            config_path = Path.home() / "AppData/Roaming/qBittorrent/qBittorrent.ini"
            backup_path = config_path.with_suffix(".ini.netbooster_backup")

            if backup_path.exists():
                shutil.copy(backup_path, config_path)
                return True, f"已从备份恢复 qBittorrent 配置"
            else:
                # 手动禁用代理
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                content = self._update_ini_value(content, "Preferences", "Connection\\ProxyType", "0")  # 0 = 禁用

                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(content)

                return True, "已禁用 qBittorrent 代理设置"

        except FileNotFoundError:
            return False, "未找到 qBittorrent 配置文件"
        except Exception as e:
            return False, f"恢复失败: {str(e)}"

    def _update_ini_value(self, content: str, section: str, key: str, value: str) -> str:
        """更新 INI 格式配置文件中的值"""
        # 查找 [Section] 下的 Key=Value
        section_pattern = rf"\[{re.escape(section)}\]"
        key_pattern = rf"^{re.escape(key)}=.*$"

        lines = content.split('\n')
        in_section = False
        key_found = False

        for i, line in enumerate(lines):
            # 检测是否进入目标 section
            if re.match(section_pattern, line.strip()):
                in_section = True
                continue

            # 检测是否离开当前 section
            if in_section and line.strip().startswith('['):
                in_section = False
                # 如果 key 未找到，在上一个 section 末尾插入
                if not key_found:
                    lines.insert(i, f"{key}={value}")
                    key_found = True
                break

            # 在目标 section 内查找 key
            if in_section and re.match(key_pattern, line.strip()):
                lines[i] = f"{key}={value}"
                key_found = True
                break

        # 如果 section 或 key 不存在，追加到末尾
        if not key_found:
            if not any(re.match(section_pattern, line.strip()) for line in lines):
                lines.append(f"\n[{section}]")
            lines.append(f"{key}={value}")

        return '\n'.join(lines)

    # ========== Windows 系统代理配置 ==========

    def configure_system_proxy(self, auto_mode: bool = True) -> Tuple[bool, str]:
        """
        自动配置 Windows 系统代理（支持 SOCKS5）

        Args:
            auto_mode: True=自动静默配置（用于一键加速），False=手动配置（显示详细提示）

        新版 Windows 10/11 支持 socks= 格式的系统代理，兼容：
        - Chrome/Edge/Firefox（现代浏览器）
        - Steam（从 IE 获取代理）
        - IDM（默认开启"从 IE 获取代理"）
        - 迅雷、百度网盘等下载工具
        """
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                # 启用代理
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)

                # Windows 10/11 支持 socks= 格式
                # 格式：socks=127.0.0.1:1080
                proxy_value = f"socks={self.proxy_host}:{self.proxy_port}"
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_value)

            # 通知系统代理设置已更改（触发应用刷新）
            import ctypes
            internet_set_option = ctypes.windll.Wininet.InternetSetOptionW
            internet_set_option(0, 39, 0, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
            internet_set_option(0, 37, 0, 0)  # INTERNET_OPTION_REFRESH

            if auto_mode:
                return True, f"系统代理已配置: {proxy_value}"
            else:
                return True, f" Windows 系统代理已设置为 SOCKS5 模式\n\n代理地址: {proxy_value}\n\n支持的应用：\n• Chrome/Edge/Firefox 浏览器\n• Steam 游戏平台\n• IDM 下载器\n• 迅雷、百度网盘\n\n️ 部分老旧应用可能不支持，请使用专用配置功能。"

        except Exception as e:
            return False, f"配置失败: {str(e)}"

    def restore_system_proxy(self, auto_mode: bool = True) -> Tuple[bool, str]:
        """
        禁用 Windows 系统代理

        Args:
            auto_mode: True=自动静默恢复（用于停止加速），False=手动恢复（显示详细提示）
        """
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)

            # 通知系统代理设置已更改
            import ctypes
            internet_set_option = ctypes.windll.Wininet.InternetSetOptionW
            internet_set_option(0, 39, 0, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
            internet_set_option(0, 37, 0, 0)  # INTERNET_OPTION_REFRESH

            if auto_mode:
                return True, "系统代理已禁用"
            else:
                return True, "已禁用 Windows 系统代理"

        except Exception as e:
            return False, f"恢复失败: {str(e)}"

    def get_current_system_proxy(self) -> Tuple[bool, str]:
        """
        获取当前系统代理状态

        Returns:
            (是否启用, 代理地址)
        """
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                try:
                    enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
                    proxy_server = winreg.QueryValueEx(key, "ProxyServer")[0]

                    if enabled == 1:
                        return True, proxy_server
                    else:
                        return False, ""
                except FileNotFoundError:
                    return False, ""

        except Exception:
            return False, ""
