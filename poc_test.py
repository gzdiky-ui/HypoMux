import socket
import struct
import ssl
import time
import threading
import psutil

# ==========================================
# 核心常量与网卡配置
# ==========================================
# IPv4 下的 IP_UNICAST_IF (强制指定物理网卡出口)
IP_UNICAST_IF = 31

# 【以太网配置】请确保与你的真实环境一致
NIC_ETH_INDEX = 19
NIC_ETH_NAME = "以太网"
NIC_ETH_IP = "10.20.236.208"  

# 【WLAN配置】请确保与你的真实环境一致
NIC_WLAN_INDEX = 11
NIC_WLAN_NAME = "WLAN"
NIC_WLAN_IP = "192.168.31.80"

# ==========================================
# 核心业务逻辑
# ==========================================
def download_worker(if_index, nic_ip, task_name):
    """
    极客级底层下载器：通过双保险机制实现真正的物理层分流
    """
    try:
        print(f"[TCP] {task_name} 正在拉起 Socket 并钉死在网卡 Index: {if_index}, IP: {nic_ip}...")
        
        # 1. 创建原生 TCP Socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        
        # 2. 【双保险之一：防源地址伪造】
        # 强行绑定本网卡的真实 IP，端口设为 0 (让系统自动分配可用端口)
        # 这一步极其关键！如果不绑定，包的源 IP 可能会与物理出口不匹配，导致网关丢包。
        sock.bind((nic_ip, 0))
        
        # 3. 【双保险之二：绕过路由表限制】
        # 强行指定底层物理网卡出口，不走 Windows 默认的路由判定。
        # 注意：这里必须使用网络字节序（大端序）的 unsigned int (!I)
        sock.setsockopt(socket.IPPROTO_IP, IP_UNICAST_IF, struct.pack("!I", if_index))
        
        # 4. 建立 SSL 上下文并连接清华大学镜像站
        host = "mirrors.tuna.tsinghua.edu.cn"
        port = 443
        context = ssl.create_default_context()
        secure_sock = context.wrap_socket(sock, server_hostname=host)
        
        # 连接服务器
        secure_sock.connect((host, port))
        print(f"[成功] {task_name} 已连通服务器！开始疯狂拉取数据...")
        
        # 5. 手搓 HTTP GET 请求 (下载 Ubuntu 24.04 ISO 镜像)
        request = (f"GET /ubuntu-releases/24.04/ubuntu-24.04-desktop-amd64.iso HTTP/1.1\r\n"
                   f"Host: {host}\r\n"
                   f"Connection: keep-alive\r\n\r\n")
        secure_sock.sendall(request.encode())
        
        # 6. 无限接收数据 (相当于真实下载)，并不写入硬盘以防 IO 瓶颈干扰测试
        while True:
            data = secure_sock.recv(65536)  # 每次拉取 64KB
            if not data:
                print(f"[!] {task_name} 服务器正常断开连接。")
                break
                
    except Exception as e:
        print(f"\n[崩溃] {task_name} 遭遇错误: {e}")

# ==========================================
# 监控与入口
# ==========================================
def get_nic_bytes(io_counters, nic_name):
    """安全获取指定网卡的累计接收字节数"""
    if nic_name in io_counters:
        return io_counters[nic_name].bytes_recv
    return 0

def monitor_os_traffic():
    """
    调用 Windows 底层 API (psutil) 实时读取网卡的真实吞吐量
    """
    print("\n" + "="*70)
    print("HypoMux L5 物理层流量监控器已启动 (数据源: Windows内核)")
    print("="*70)
    print(f"{'时间':<10} | {'以太网 (Index 19) 真实下行':<25} | {'WLAN (Index 11) 真实下行':<25}")
    print("-" * 70)
    
    # 记录初始字节数
    io_initial = psutil.net_io_counters(pernic=True)
    eth_last = get_nic_bytes(io_initial, NIC_ETH_NAME)
    wlan_last = get_nic_bytes(io_initial, NIC_WLAN_NAME)
    
    while True:
        time.sleep(1.0)
        io_now = psutil.net_io_counters(pernic=True)
        
        eth_now = get_nic_bytes(io_now, NIC_ETH_NAME)
        wlan_now = get_nic_bytes(io_now, NIC_WLAN_NAME)
        
        # 计算每秒增量并转为 MB/s
        eth_speed = (eth_now - eth_last) / 1024 / 1024
        wlan_speed = (wlan_now - wlan_last) / 1024 / 1024
        
        print(f"{time.strftime('%H:%M:%S'):<10} | {eth_speed:>14.2f} MB/s            | {wlan_speed:>14.2f} MB/s")
        
        eth_last = eth_now
        wlan_last = wlan_now

if __name__ == "__main__":
    # 在启动前，请确保你两张网卡都是连接状态，且跃点已经恢复为自动(未修改)。
    
    # 1. 启动 Windows 内核数据监控线程
    monitor_thread = threading.Thread(target=monitor_os_traffic, daemon=True)
    monitor_thread.start()
    
    time.sleep(1) # 等待监控器初始化打印
    
    # 2. 启动以太网下载劫持任务 (传入 Index 和 真实 IP)
    eth_thread = threading.Thread(target=download_worker, args=(NIC_ETH_INDEX, NIC_ETH_IP, "任务A(以太网)"), daemon=True)
    eth_thread.start()
    
    # 3. 延迟 2 秒后，启动 WLAN 下载劫持任务，观察是否会抢占
    time.sleep(2)
    wlan_thread = threading.Thread(target=download_worker, args=(NIC_WLAN_INDEX, NIC_WLAN_IP, "任务B(WLAN)"), daemon=True)
    wlan_thread.start()
    
    # 保持主线程存活
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] 测试被用户手动终止。")