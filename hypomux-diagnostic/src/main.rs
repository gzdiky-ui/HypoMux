//! HypoMux 网卡链路诊断内核 (diagnostic.exe)
//!
//! 【第二阶段 · 任务2】极简、零依赖、高确定性的命令行探测工具。
//!
//! 设计要点：
//! - 免管理员权限：使用 Win32 IPHLPAPI 的 `IcmpSendEcho2Ex`，它在用户态即可
//!   收发 ICMP Echo，无需 raw socket / 管理员令牌。
//! - 源网卡强绑定：`IcmpSendEcho2Ex` 的 `SourceAddress` 参数把探测包死锁在
//!   `--src-ip` 指定的网卡上，确保流量 100% 只从这一张网卡发射（与 HypoMux
//!   主程序 IP_UNICAST_IF 的绑定哲学一致）。源 IP 不属于本机任一网卡时，
//!   API 会失败（典型 1231 ERROR_NETWORK_UNREACHABLE），据此判定「不可用」。
//! - 零第三方 crate：JSON、参数解析、ICMP 全部手写，cargo build 无需联网。
//!
//! 用法：
//!   diagnostic.exe --src-ip 192.168.1.100 [--target-ip 223.5.5.5]
//!
//! 输出（stdout 单行 JSON）：
//!   {"status":"available","loss_rate":0,"avg_latency_ms":12,"jitter_ms":4,...}

use std::os::raw::{c_void};

// ===================== Win32 FFI 声明 =====================

type Handle = *mut c_void;
type IpAddr4 = u32; // IPAddr：4 字节地址，按网络字节序存放在内存中

const INVALID_HANDLE_VALUE: isize = -1;
const IP_SUCCESS: u32 = 0;

/// IP_OPTION_INFORMATION（x64 下含填充，repr(C) 保证布局正确）
#[repr(C)]
struct IpOptionInformation {
    ttl: u8,
    tos: u8,
    flags: u8,
    options_size: u8,
    options_data: *mut u8,
}

/// ICMP_ECHO_REPLY
#[repr(C)]
struct IcmpEchoReply {
    address: IpAddr4,
    status: u32,
    round_trip_time: u32,
    data_size: u16,
    reserved: u16,
    data: *mut c_void,
    options: IpOptionInformation,
}

#[link(name = "Iphlpapi")]
extern "system" {
    fn IcmpCreateFile() -> Handle;
    fn IcmpCloseHandle(handle: Handle) -> i32;

    /// 带源地址版本的 ICMP Echo —— 源地址强绑定的关键。
    fn IcmpSendEcho2Ex(
        icmp_handle: Handle,
        event: Handle,
        apc_routine: *mut c_void,
        apc_context: *mut c_void,
        source_address: IpAddr4,
        destination_address: IpAddr4,
        request_data: *const c_void,
        request_size: u16,
        request_options: *const IpOptionInformation,
        reply_buffer: *mut c_void,
        reply_size: u32,
        timeout: u32,
    ) -> u32;
}

#[link(name = "Kernel32")]
extern "system" {
    fn GetLastError() -> u32;
}

// ===================== 工具函数 =====================

/// 把点分十进制 IPv4 解析为 IPAddr（内存中的网络字节序）。
/// 例如 "1.2.3.4" -> 字节序 [1,2,3,4]，在小端机器上等于 u32 0x04030201。
fn parse_ipv4(s: &str) -> Option<IpAddr4> {
    let parts: Vec<&str> = s.trim().split('.').collect();
    if parts.len() != 4 {
        return None;
    }
    let mut octets = [0u8; 4];
    for (i, p) in parts.iter().enumerate() {
        match p.parse::<u8>() {
            Ok(v) => octets[i] = v,
            Err(_) => return None,
        }
    }
    // 按内存网络字节序拼装：octet[0] 在最低字节
    Some(
        (octets[0] as u32)
            | ((octets[1] as u32) << 8)
            | ((octets[2] as u32) << 16)
            | ((octets[3] as u32) << 24),
    )
}

/// 极简命令行参数取值器：--key value
fn arg_value(args: &[String], key: &str) -> Option<String> {
    let mut it = args.iter();
    while let Some(a) = it.next() {
        if a == key {
            return it.next().cloned();
        }
        // 兼容 --key=value 写法
        if let Some(rest) = a.strip_prefix(&format!("{}=", key)) {
            return Some(rest.to_string());
        }
    }
    None
}

/// 输出 JSON 并退出。loss_rate / 延迟 / jitter 均为整数，便于前端解析。
fn emit_and_exit(
    status: &str,
    loss_rate: u32,
    avg_latency_ms: u32,
    jitter_ms: u32,
    sent: u32,
    received: u32,
    src_ip: &str,
    target_ip: &str,
    note: &str,
) -> ! {
    // 手写 JSON，避免引入 serde 依赖；字符串字段做最小化转义。
    println!(
        "{{\"status\":\"{}\",\"loss_rate\":{},\"avg_latency_ms\":{},\"jitter_ms\":{},\"sent\":{},\"received\":{},\"src_ip\":\"{}\",\"target_ip\":\"{}\",\"note\":\"{}\"}}",
        status,
        loss_rate,
        avg_latency_ms,
        jitter_ms,
        sent,
        received,
        json_escape(src_ip),
        json_escape(target_ip),
        json_escape(note),
    );
    std::process::exit(0);
}

fn json_escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}

// ===================== 主逻辑 =====================

const PROBE_COUNT: u32 = 10; // 连续发射 10 个探测包
const PROBE_TIMEOUT_MS: u32 = 1000; // 单包 1s 超时
const PAYLOAD: &[u8] = b"HypoMux-Diagnostic-Probe"; // 24 字节固定载荷

fn main() {
    let args: Vec<String> = std::env::args().collect();

    // ---- 参数解析 ----
    let src_ip_str = match arg_value(&args, "--src-ip") {
        Some(v) => v,
        None => {
            // 缺少必填参数：直接判定不可用并说明
            emit_and_exit(
                "unavailable", 100, 0, 0, 0, 0, "", "", "missing --src-ip",
            );
        }
    };
    // 目标 IP 可选，默认回退阿里云 DNS 223.5.5.5
    let target_ip_str = arg_value(&args, "--target-ip").unwrap_or_else(|| "223.5.5.5".to_string());

    let src_addr = match parse_ipv4(&src_ip_str) {
        Some(a) => a,
        None => emit_and_exit(
            "unavailable", 100, 0, 0, 0, 0, &src_ip_str, &target_ip_str, "invalid --src-ip",
        ),
    };
    let dst_addr = match parse_ipv4(&target_ip_str) {
        Some(a) => a,
        None => emit_and_exit(
            "unavailable", 100, 0, 0, 0, 0, &src_ip_str, &target_ip_str, "invalid --target-ip",
        ),
    };

    // ---- 创建 ICMP 句柄 ----
    let handle = unsafe { IcmpCreateFile() };
    if handle as isize == INVALID_HANDLE_VALUE || handle.is_null() {
        emit_and_exit(
            "unavailable", 100, 0, 0, 0, 0, &src_ip_str, &target_ip_str, "IcmpCreateFile failed",
        );
    }

    // 回复缓冲区：sizeof(ICMP_ECHO_REPLY) + 载荷 + 8 字节余量（官方推荐）
    let reply_size: u32 =
        (std::mem::size_of::<IcmpEchoReply>() + PAYLOAD.len() + 8) as u32;
    let mut reply_buf: Vec<u8> = vec![0u8; reply_size as usize];

    let mut rtts: Vec<u32> = Vec::with_capacity(PROBE_COUNT as usize);
    let mut bind_error = false;

    for _ in 0..PROBE_COUNT {
        let ret = unsafe {
            IcmpSendEcho2Ex(
                handle,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                src_addr,
                dst_addr,
                PAYLOAD.as_ptr() as *const c_void,
                PAYLOAD.len() as u16,
                std::ptr::null(),
                reply_buf.as_mut_ptr() as *mut c_void,
                reply_size,
                PROBE_TIMEOUT_MS,
            )
        };

        if ret > 0 {
            // 成功收到至少一个回复，解析第一条 reply 的 Status / RTT
            let reply: &IcmpEchoReply =
                unsafe { &*(reply_buf.as_ptr() as *const IcmpEchoReply) };
            if reply.status == IP_SUCCESS {
                rtts.push(reply.round_trip_time);
            }
            // reply.status != IP_SUCCESS 视为该包丢失（不计入 rtts）
        } else {
            // 发送失败：检查是否为源地址不可路由 / 绑定失败
            let err = unsafe { GetLastError() };
            // 1231 = ERROR_NETWORK_UNREACHABLE：源 IP 不属于任何可用网卡 / 链路断开
            // 11003 = IP_BAD_ROUTE，11010 = IP_REQ_TIMED_OUT 属普通丢包
            if err == 1231 || err == 1214 {
                bind_error = true;
            }
        }
    }

    unsafe {
        IcmpCloseHandle(handle);
    }

    // ---- 统计 ----
    let received = rtts.len() as u32;
    let sent = PROBE_COUNT;
    let loss_rate = ((sent - received) * 100) / sent; // 整数百分比

    let (avg_latency_ms, jitter_ms) = if received > 0 {
        let sum: u64 = rtts.iter().map(|&x| x as u64).sum();
        let avg = (sum / received as u64) as u32;
        let max = *rtts.iter().max().unwrap();
        let min = *rtts.iter().min().unwrap();
        (avg, max - min) // jitter = 最大延迟 - 最小延迟
    } else {
        (0, 0)
    };

    // ---- 三级状态判定 ----
    // 不可用：丢包 100% 或源 IP 绑定失败（1231 类错误）
    // 不稳定：5% <= 丢包 < 100%，或抖动 > 100ms
    // 可用  ：丢包 < 5% 且抖动 <= 100ms
    let status = if loss_rate >= 100 || bind_error {
        "unavailable"
    } else if loss_rate >= 5 || jitter_ms > 100 {
        "unstable"
    } else {
        "available"
    };

    let note = if bind_error {
        "source bind failed (WinError 1231)"
    } else {
        ""
    };

    emit_and_exit(
        status,
        loss_rate,
        avg_latency_ms,
        jitter_ms,
        sent,
        received,
        &src_ip_str,
        &target_ip_str,
        note,
    );
}
