#!/usr/bin/env python3
"""
A股监控系统 - 主控程序
一键启动所有功能：股票监控 + 跨市场行情 + Telegram Bot

用法:
    python main.py              # 启动所有服务
    python main.py --monitor    # 仅启动监控
    python main.py --markets    # 仅启动跨市场行情采集
    python main.py --bot        # 仅启动 Bot
    python main.py --test       # 测试模式
"""
import argparse
import sys
import subprocess
import time
import signal
import os
import threading

# 全局进程列表
processes = []
log_handles = []
shutdown_in_progress = False


def signal_handler(signum, frame):
    """处理退出信号"""
    global shutdown_in_progress
    if shutdown_in_progress:
        print("\n强制退出...")
        os._exit(1)
    
    shutdown_in_progress = True
    print("\n\n⏹  收到退出信号，正在停止所有服务...")
    stop_all_services()
    print("✅ 所有服务已停止")
    sys.exit(0)


def stop_all_services():
    """停止所有服务"""
    for name, proc in processes:
        try:
            # 发送 SIGTERM
            if proc.poll() is None:  # 进程还在运行
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                    print(f"   ✓ {name} 已停止")
                except subprocess.TimeoutExpired:
                    # 超时强制 SIGKILL
                    proc.kill()
                    proc.wait()
                    print(f"   ✗ {name} 强制停止")
            else:
                print(f"   • {name} 已退出")
        except Exception as e:
            print(f"   ! {name} 停止失败: {e}")
    for handle in log_handles:
        try:
            handle.close()
        except Exception:
            pass
    log_handles.clear()


def start_service(name, script, args=None, log_file=None):
    """启动一个服务"""
    cmd = [sys.executable, script]
    if args:
        cmd.extend(args)
    
    try:
        if log_file:
            # 输出重定向到日志文件（避免管道缓冲区写满导致进程阻塞）
            fh = open(log_file, "a", encoding="utf-8")
            log_handles.append(fh)
            proc = subprocess.Popen(
                cmd,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
        else:
            # 管道模式（调用方需要读取 proc.stdout）
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True
            )
        processes.append((name, proc))
        log_info = f" → {log_file}" if log_file else ""
        print(f"   ✓ {name} 已启动 (PID: {proc.pid}){log_info}")
        return proc
    except Exception as e:
        print(f"   ✗ {name} 启动失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="A股与跨市场监控系统主控程序")
    parser.add_argument("--monitor", action="store_true", help="仅启动监控")
    parser.add_argument("--markets", action="store_true", help="仅启动跨市场行情采集")
    parser.add_argument("--bot", action="store_true", help="仅启动 Bot")
    parser.add_argument("--test", action="store_true", help="测试模式")
    args = parser.parse_args()

    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print()
    print("╔══════════════════════════════════════╗")
    print("║      📊 A股监控系统 V2 启动          ║")
    print("╚══════════════════════════════════════╝")
    print()

    # 根据参数启动服务
    run_monitor = args.monitor or args.test or (not args.bot and not args.markets)
    run_markets = args.markets or args.monitor or args.test or (
        not args.bot and not args.markets
    )

    if args.bot or (not args.monitor and not args.test and not args.markets):
        print("🤖 启动 Telegram Bot...")
        bot_proc = start_service("Telegram Bot", "bot.py", log_file="bot.log")
        if bot_proc:
            time.sleep(2)  # 等待 Bot 初始化

    if run_monitor:
        print("📊 启动股票监控...")
        monitor_args = ["--test"] if args.test else []
        monitor_proc = start_service("股票监控", "monitor.py", monitor_args)
    else:
        monitor_proc = None

    if run_markets:
        print("🌏 启动跨市场行情采集...")
        market_args = ["--test"] if args.test else []
        market_proc = start_service("全球市场监控", "market_monitor.py", market_args)
    else:
        market_proc = None

    if not processes:
        print("\n❌ 没有服务启动成功")
        return

    print()
    print("✅ 系统运行中")
    print("-" * 40)
    for name, proc in processes:
        print(f"   • {name}: PID {proc.pid}")
    print("-" * 40)
    print("\n💡 提示:")
    print("   - 在 Telegram 发送 /start 给 Bot 查看帮助")
    print("   - 按 Ctrl+C 停止所有服务")
    print("   - 日志保存在 monitor.log 和 bot.log")
    print()

    # 在后台转发监控输出，主线程统一监督所有子进程。
    def forward_output(proc):
        if proc and proc.stdout:
            for line in proc.stdout:
                print(line, end='')
                sys.stdout.flush()

    for proc in (monitor_proc, market_proc):
        if proc:
            threading.Thread(target=forward_output, args=(proc,), daemon=True).start()

    try:
        while True:
            time.sleep(1)
            exited = [(name, proc) for name, proc in processes if proc.poll() is not None]
            if not exited:
                continue
            name, proc = exited[0]
            if args.test:
                test_processes = [
                    child for child_name, child in processes
                    if child_name in {"股票监控", "全球市场监控"}
                ]
                if test_processes and all(child.poll() is not None for child in test_processes):
                    if all(child.returncode == 0 for child in test_processes):
                        print("\n✅ 测试运行完成")
                        break
            print(f"\n⚠️  {name} 意外退出 (退出码: {proc.returncode})")
            stop_all_services()
            sys.exit(proc.returncode or 1)
    except KeyboardInterrupt:
        print("\n")

    # 确保清理
    if not shutdown_in_progress:
        stop_all_services()


if __name__ == "__main__":
    main()
