import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import sys
import time
import concurrent.futures
from main import ArmController, TCPCoordinator, filter_reachable_ips, wait_for_all_arms_status, arm_status_dict, arm_status_lock, StateMonitor

class RedirectText:
    """将 stdout 重定向到 Tkinter Text 组件"""
    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, string):
        self.text_widget.insert(tk.END, string)
        self.text_widget.see(tk.END)

    def flush(self):
        pass

class ArmControlUI:
    def __init__(self, root):
        self.root = root
        self.root.title("机械臂群控系统 V2.0")
        self.root.geometry("900x700")
        
        self.controllers = []
        self.coordinator = None
        self.monitor_thread = None
        self.is_running = False
        self.is_stopping = False

        self.setup_ui()
        
        # 重定向标准输出
        sys.stdout = RedirectText(self.log_area)

    def setup_ui(self):
        # 参数设置
        top_frame = ttk.LabelFrame(self.root, text="参数设置")
        top_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(top_frame, text="机器数量:").grid(row=0, column=0, padx=5, pady=5)
        self.count_var = tk.StringVar(value="1")
        self.count_entry = ttk.Entry(top_frame, textvariable=self.count_var, width=10)
        self.count_entry.grid(row=0, column=1, padx=5, pady=5)
        self.count_entry.bind("<Return>", lambda e: self.update_ip_fields())
        ttk.Button(top_frame, text="生成IP框", command=self.update_ip_fields).grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(top_frame, text="步间延时(s):").grid(row=0, column=3, padx=5, pady=5)
        self.delay_var = tk.StringVar(value="0.01")
        self.delay_entry = ttk.Entry(top_frame, textvariable=self.delay_var, width=10)
        self.delay_entry.grid(row=0, column=4, padx=5, pady=5)

        ttk.Label(top_frame, text="同步步骤数量:").grid(row=0, column=5, padx=5, pady=5)
        self.steps_var = tk.StringVar(value="17")
        self.steps_entry = ttk.Entry(top_frame, textvariable=self.steps_var, width=10)
        self.steps_entry.grid(row=0, column=6, padx=5, pady=5)

        ttk.Label(top_frame, text="脚本名称:").grid(row=0, column=7, padx=5, pady=5)
        self.script_var = tk.StringVar(value="part1")
        self.script_entry = ttk.Entry(top_frame, textvariable=self.script_var, width=10)
        self.script_entry.grid(row=0, column=8, padx=5, pady=5)

        # IP 输入
        self.ip_frame_container = ttk.LabelFrame(self.root, text="机器 IP 地址输入")
        self.ip_frame_container.pack(fill="x", padx=10, pady=5)

        # 创建带滚动条的 IP 输入区域
        self.canvas = tk.Canvas(self.ip_frame_container, height=180)  # 调整高度以完整展示4行
        self.scrollbar = ttk.Scrollbar(self.ip_frame_container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw", tags="inner_frame")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # 确保内部 frame 宽度跟随 canvas 宽度
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig("inner_frame", width=e.width))

        self.ip_entries = []
        self.update_ip_fields()

        # 第三部分：运行结果与控制
        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # 左侧日志区域
        log_frame = ttk.LabelFrame(bottom_frame, text="运行结果")
        log_frame.pack(side="left", fill="both", expand=True)
        self.log_area = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=25)
        self.log_area.pack(fill="both", expand=True)

        # 右侧控制按钮区域
        btn_frame = ttk.LabelFrame(bottom_frame, text="操作控制")
        btn_frame.pack(side="right", fill="y", padx=5)

        self.start_script_btn = ttk.Button(btn_frame, text="开始运行脚本", command=self.start_script_thread)
        self.start_script_btn.pack(fill="x", padx=10, pady=10)

        self.start_sync_btn = ttk.Button(btn_frame, text="开始同步动作", command=self.start_sync_thread)
        self.start_sync_btn.pack(fill="x", padx=10, pady=10)

        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self.stop_all)
        self.stop_btn.pack(fill="x", padx=10, pady=10)

        # 添加急停按钮，使用红色样式（如果支持）
        self.emergency_btn = ttk.Button(btn_frame, text="急停", command=self.emergency_stop)
        self.emergency_btn.pack(fill="x", padx=10, pady=10)

    def update_ip_fields(self):
        """动态生成 IP 输入框"""
        try:
            count = int(self.count_var.get())
        except ValueError:
            messagebox.showerror("错误", "请输入有效的机器数量")
            return

        # 清除现有所有组件（包括容器 Frame）
        for child in self.scrollable_frame.winfo_children():
            child.destroy()
        self.ip_entries = []

        COLS = 4  # 每行显示4个
        # 配置列权重，使列等宽平分
        for c in range(COLS):
            self.scrollable_frame.grid_columnconfigure(c, weight=1)

        for i in range(count):
            row = i // COLS
            col = i % COLS
            
            frame = ttk.Frame(self.scrollable_frame)
            frame.grid(row=row, column=col, padx=10, pady=5, sticky="ew")
            
            ttk.Label(frame, text=f"机械臂{i+1}:").pack(side="left")
            entry = ttk.Entry(frame, width=12)
            # 设置默认值
            entry.insert(0, f"192.168.5.{i+1}")
            entry.pack(side="left", fill="x", expand=True, padx=5)
            self.ip_entries.append(entry)

    def log(self, message):
        self.log_area.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_area.see(tk.END)

    def start_script_thread(self):
        if self.is_running:
            return
        threading.Thread(target=self.run_script_phase, daemon=True).start()

    def run_script_phase(self):
        self.is_running = True
        self.start_script_btn.config(state="disabled")
        try:
            ip_list = [entry.get().strip() for entry in self.ip_entries if entry.get().strip()]
            if not ip_list:
                print("错误：IP 列表为空")
                return

            print("=" * 60)
            print("第一阶段: 启动脚本")
            print("=" * 60)

            script_name = self.script_var.get().strip() or "part1"
            reachable_ips, unreachable_ips = filter_reachable_ips(ip_list)
            if not reachable_ips:
                print("没有可达的机械臂IP")
                return

            self.controllers = []
            for ip in reachable_ips:
                controller = ArmController(ip, script_name)
                controller.is_reachable = True
                self.controllers.append(controller)

            # 初始化状态
            with arm_status_lock:
                for ip in reachable_ips:
                    arm_status_dict[ip] = "INIT"
                for ip in unreachable_ips:
                    arm_status_dict[ip] = "UNREACHABLE"

            # 启动监控
            if self.monitor_thread:
                self.monitor_thread.stop()
            self.monitor_thread = StateMonitor(reachable_ips)
            self.monitor_thread.start()

            # 并发运行脚本
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.controllers)) as executor:
                futures = [executor.submit(c.run_script) for c in self.controllers]
                concurrent.futures.wait(futures)

            print("所有机械臂脚本启动完成，等待就绪...")
            if wait_for_all_arms_status("PAUSE", reachable_ips):
                print("所有机械臂已就绪")
                
                # 第二阶段：建立 TCP 连接 (对应 main.py 600行左右逻辑)
                print("\n" + "=" * 60)
                print("第二阶段: 建立 TCP 连接")
                print("=" * 60)
                
                self.coordinator = TCPCoordinator(self.controllers)
                if not self.coordinator.prepare_all_connections():
                    print("建立 TCP 连接失败")
                    return

                if not self.coordinator.wait_for_all_start_signals():
                    print("等待 start 信号失败")
                    return

                print("\n通讯准备就绪，可以点击'开始同步动作'。")
            else:
                print("等待就绪超时")

        except Exception as e:
            print(f"运行脚本阶段出错: {e}")
        finally:
            self.is_running = False
            self.start_script_btn.config(state="normal")

    def start_sync_thread(self):
        if self.is_running:
            return
        threading.Thread(target=self.run_sync_phase, daemon=True).start()

    def run_sync_phase(self):
        if not self.controllers or not self.coordinator:
            print("错误：通讯未就绪，请先执行'开始运行脚本'并等待就绪")
            return

        self.is_running = True
        self.start_sync_btn.config(state="disabled")
        try:
            steps = int(self.steps_var.get())
            delay = float(self.delay_var.get())

            print("\n" + "=" * 60)
            print("第三阶段: 同步动作")
            print("=" * 60)

            print("开始执行同步动作轮次...")
            reachable_ips = [c.robot_ip for c in self.controllers]
            for i in range(steps):
                if not self.is_running and self.is_stopping: # 如果正在停止则退出
                    print("同步动作已中止")
                    break
                self.coordinator.execute_round(i, reachable_ips, delay)
                print("-" * 40)

            if not self.is_stopping:
                print("所有同步轮次完成")
                # 对应 main.py 618行：停止脚本运行
                for controller in self.controllers:
                    controller.stop()

        except Exception as e:
            print(f"同步动作阶段出错: {e}")
        finally:
            self.is_running = False
            self.start_sync_btn.config(state="normal")

    def stop_all(self):
        self.is_running = False
        self.is_stopping = True
        print("正在停止所有机械臂...")
        
        def do_stop():
            if self.controllers:
                # 使用线程池并发停止所有机械臂
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.controllers)) as executor:
                    futures = [executor.submit(c.stop) for c in self.controllers]
                    concurrent.futures.wait(futures)
            
            if self.coordinator:
                try:
                    self.coordinator.close_all_connections()
                except:
                    pass
            if self.monitor_thread:
                self.monitor_thread.stop()
            print("停止指令已并发发送至所有机械臂")
            self.is_stopping = False

        threading.Thread(target=do_stop, daemon=True).start()

    def emergency_stop(self):
        """急停：立即并发向所有机械臂发送 Stop 信号"""
        self.is_running = False
        self.is_stopping = True
        print("！！！触发急停！！！")
        
        if not self.controllers:
            print("错误：未初始化控制器，无法发送急停信号")
            return

        def do_emergency_stop():
            # 使用线程池确保所有 Stop 信号几乎同时发出
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.controllers)) as executor:
                futures = []
                for c in self.controllers:
                    print(f"向机械臂 {c.robot_ip} 发送急停信号...")
                    futures.append(executor.submit(c.stop))
                concurrent.futures.wait(futures)
            
            print("所有机械臂急停信号已发送完成")
            self.is_stopping = False

        threading.Thread(target=do_emergency_stop, daemon=True).start()

if __name__ == "__main__":
    root = tk.Tk()
    app = ArmControlUI(root)
    root.mainloop()
