#!/usr/bin/env python3
"""
A股监控系统 - 主控程序
一键启动所有功能：股票监控 + Telegram Bot

用法:
    python main.py              # 启动所有服务
    python main.py --monitor    # 仅启动监控
    python main.py --bot        # 仅启动 Bot
    python main.py --test       # 测试模式
"""
import argparse
import sys
import subprocess
import time
import signal
import os

# 全局进程列表
processes = []
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


def start_service(name, script, args=None):
    """启动一个服务"""
    cmd = [sys.executable, script]
    if args:
        cmd.extend(args)
    
    try:
        # 简化版本：直接启动子进程
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True
        )
        processes.append((name, proc))
        print(f"   ✓ {name} 已启动 (PID: {proc.pid})")
        return proc
    except Exception as e:
        print(f"   ✗ {name} 启动失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="A股监控系统主控程序")
    parser.add_argument("--monitor", action="store_true", help="仅启动监控")
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
    if args.bot or (not args.monitor and not args.test):
        print("🤖 启动 Telegram Bot...")
        bot_proc = start_service("Telegram Bot", "bot.py")
        if bot_proc:
            time.sleep(2)  # 等待 Bot 初始化

    if args.monitor or args.test or (not args.bot):
        print("📊 启动股票监控...")
        monitor_args = ["--test"] if args.test else []
        monitor_proc = start_service("股票监控", "monitor.py", monitor_args)
    else:
        monitor_proc = None

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

    # 实时输出监控日志（如果有）
    if monitor_proc:
        try:
            for line in monitor_proc.stdout:
                print(line, end='')
                sys.stdout.flush()
        except KeyboardInterrupt:
            print("\n")  # 换行
        except Exception as e:
            print(f"\n读取日志出错: {e}")

    # 保持运行（如果只启动了 Bot）
    elif processes:
        try:
            print("按 Ctrl+C 停止服务...\n")
            while True:
                time.sleep(1)
                # 检查进程是否还在运行
                all_running = True
                for name, proc in processes:
                    if proc.poll() is not None:
                        print(f"\n⚠️  {name} 意外退出 (退出码: {proc.returncode})")
                        all_running = False
                        break
                
                if not all_running:
                    stop_all_services()
                    sys.exit(1)
                    
        except KeyboardInterrupt:
            print("\n")  # 换行

    # 确保清理
    if not shutdown_in_progress:
        stop_all_services()


if __name__ == "__main__":
    main()
