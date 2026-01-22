import concurrent.futures
import csv
import platform
import re
import socket
import subprocess
import threading
import time
from time import sleep

from dobot_api import DobotApiFeedBack, DobotApiDashboard

# 全局变量，用于线程间通信
arm_status_dict = {}  # 机械臂状态字典
arm_configs_list = {}  # 存储当前轮次机械臂对应的运行参数 {ip: (script, delay)}
arm_status_lock = threading.Lock()  # 状态字典的线程锁
arm_configs_lock = threading.Lock()  # 配置字典的线程锁


class DobotDemo:
    def __init__(self, ip):
        self.ip = ip
        self.dashboardPort = 29999
        self.feedPortFour = 30004
        self.__globalLockValue = threading.Lock()
        self.feed_thread_stop = threading.Event()

        self.dashboard = DobotApiDashboard(self.ip, self.dashboardPort)
        self.feedFour = DobotApiFeedBack(self.ip, self.feedPortFour)

        class Item:
            def __init__(self):
                self.robotMode = -1
                self.robotCurrentCommandID = 0
                self.MessageSize = -1
                self.DigitalInputs = -1
                self.DigitalOutputs = -1
                self.QActual = [0, 0, 0, 0, 0, 0]

        self.feedData = Item()

    def start(self):
        """启动机器人并使能"""
        try:
            self.dashboard.ClearError()
            self.dashboard.Stop()
            # self.dashboard.DisableRobot()
            # quit()

            if self.parseResultId(self.dashboard.EnableRobot())[0] != 0:
                print(f"机械臂{self.ip}: 使能失败 - 检查29999端口是否被占用")
                return False
            # print(f"机械臂{self.ip}: 使能成功")
            return True
        except Exception as e:
            print(f"机械臂{self.ip}: 使能过程中出现异常: {e}")
            return False

    def GetFeed(self):
        """获取机器人状态反馈"""
        while not self.feed_thread_stop.is_set():
            feedInfo = self.feedFour.feedBackData()
            with self.__globalLockValue:
                if feedInfo is not None:
                    if hex((feedInfo['TestValue'][0])) == '0x123456789abcdef':
                        self.feedData.MessageSize = feedInfo['len'][0]
                        self.feedData.robotMode = feedInfo['RobotMode'][0]
                        self.feedData.DigitalInputs = feedInfo['DigitalInputs'][0]
                        self.feedData.DigitalOutputs = feedInfo['DigitalOutputs'][0]
                        self.feedData.robotCurrentCommandID = feedInfo['CurrentCommandId'][0]
                        self.feedData.QActual = feedInfo['QActual'][0]

    # 在DobotDemo类中添加公共方法
    def get_robot_mode(self):
        with self.__globalLockValue:
            return self.feedData.robotMode

    def get_qactual(self):
        with self.__globalLockValue:
            return self.feedData.QActual

    def stop_feed_thread(self):
        """停止状态反馈线程"""
        self.feed_thread_stop.set()

    def stop_script(self):
        """停止机器脚本"""
        self.dashboard.Stop()

    def parseResultId(self, valueRecv):
        """解析返回值"""
        if "Not Tcp" in valueRecv:
            print("Control Mode Is Not Tcp")
            return [1]
        return [int(num) for num in re.findall(r'-?\d+', valueRecv)] or [2]

    def __del__(self):
        del self.dashboard
        del self.feedFour


class ArmController:
    """
    机械臂控制类 - 负责启动脚本以及TCP通信
    """

    def __init__(self, robot_ip, script_name="part1"):
        self.robot_ip = robot_ip
        self.script_name = script_name
        self.dobotdemo = DobotDemo(self.robot_ip)
        self.tcp_socket = None  # TCP socket对象
        self.port_tcp = 6666  # TCP端口号
        self.connected = False  # 连接状态标志
        self.is_reachable = True  # IP是否可达的标志

    def stop(self):
        """
        停止运行中的脚本
        """
        self.dobotdemo.stop_script()
        return

    def run_script(self):
        """启动机械臂的统一脚本"""
        # 首先检查IP是否可达
        if not self.is_reachable:
            print(f"机械臂{self.robot_ip}: IP不可达，跳过执行")
            with arm_status_lock:
                arm_status_dict[self.robot_ip] = "UNREACHABLE"
            return f"机械臂{self.robot_ip} IP不可达"

        print(f"机械臂{self.robot_ip}: 启动统一脚本 {self.script_name}")

        # 改变机器状态
        with arm_status_lock:
            arm_status_dict[self.robot_ip] = "RUNNING"

        # 启动机械臂使能
        if not self.dobotdemo.start():
            with arm_status_lock:
                arm_status_dict[self.robot_ip] = "ERROR"
            return f"机械臂{self.robot_ip} 使能失败"

        # 启动状态反馈线程
        feed_thread = threading.Thread(target=self.dobotdemo.GetFeed)
        feed_thread.daemon = True
        feed_thread.start()
        sleep(0.5)

        # 运行统一的主脚本
        recvmsg = self.dobotdemo.dashboard.RunScript(self.script_name)
        print(f"机械臂{self.robot_ip}: 脚本启动结果: {recvmsg}")

        # 改变机器状态
        while True:
            current_mode = self.dobotdemo.feedData.robotMode
            if current_mode == 7:
                with arm_status_lock:
                    arm_status_dict[self.robot_ip] = "PAUSE"
                break
            elif current_mode in [9, 11]:
                with arm_status_lock:
                    arm_status_dict[self.robot_ip] = "ERROR"
                    print(f"机械臂{self.robot_ip}出现错误！")
                break
            sleep(0.01)

    def wait_until_ready(self, timeout=30):
        """等待机械臂准备就绪"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            current_mode = self.dobotdemo.get_robot_mode()

            if current_mode == 5:  # 空闲状态
                with arm_status_lock:
                    arm_status_dict[self.robot_ip] = "READY"
                return True
            elif current_mode in [9, 11]:  # 错误状态
                with arm_status_lock:
                    arm_status_dict[self.robot_ip] = "ERROR"
                return False
            sleep(1)
        return False

    def create_tcp_connection(self):
        """创建TCP连接并返回socket对象"""
        if not self.is_reachable:
            print(f"机械臂{self.robot_ip}: IP不可达，跳过TCP连接")
            return None

        try:
            if self.tcp_socket:
                self.tcp_socket.close()

            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(5)  # 设置连接超时
            self.tcp_socket.connect((self.robot_ip, self.port_tcp))
            self.connected = True
            return self.tcp_socket
        except Exception as e:
            print(f"机械臂{self.robot_ip}: TCP连接失败: {e}")
            self.connected = False
            return None

    def wait_for_start_signal(self):
        """等待接收start信号"""
        if not self.is_reachable:
            print(f"机械臂{self.robot_ip}: IP不可达，跳过等待start信号")
            return False

        if not self.connected or not self.tcp_socket:
            print(f"机械臂{self.robot_ip}: 未建立TCP连接")
            return False

        try:
            print(f"机械臂{self.robot_ip}: 等待接收start信号...")
            self.tcp_socket.settimeout(300)  # 设置长超时等待start信号

            data = self.tcp_socket.recv(1024)
            if data:
                message = data.decode('utf-8').strip()
                print(f"机械臂{self.robot_ip}: 收到消息: {message}")

                if message == "start":
                    print(f"机械臂{self.robot_ip}: 收到start信号")
                    # 重置机械臂状态为PAUSE
                    with arm_status_lock:
                        arm_status_dict[self.robot_ip] = "PAUSE"
                    return True
                else:
                    print(f"机械臂{self.robot_ip}: 收到未知信号: {message}")
                    return False

        except socket.timeout:
            print(f"机械臂{self.robot_ip}: 等待start信号超时")
            return False
        except Exception as e:
            print(f"机械臂{self.robot_ip}: 等待start信号异常: {e}")
            self.connected = False
            return False

    def send_script_command(self, script):
        """发送脚本命令到机械臂"""
        if not self.is_reachable:
            print(f"机械臂{self.robot_ip}: IP不可达，跳过发送命令")
            return False

        if not self.connected or not self.tcp_socket:
            print(f"机械臂{self.robot_ip}: 未建立TCP连接")
            return False

        try:
            # 发送脚本编号
            command_data = str(script).encode('utf-8')
            self.tcp_socket.send(command_data)
            print(f"机械臂{self.robot_ip}: 发送脚本代号 {script}")

            # 等待接收"ending"信号
            print(f"机械臂{self.robot_ip}: 等待接收ending信号...")
            while True:
                response = self.tcp_socket.recv(1024)
                response_decoded = response.decode('utf-8').strip()
                print(f"机械臂{self.robot_ip}: 接收响应: {response_decoded}")

                if response_decoded == "ending":
                    print(f"机械臂{self.robot_ip}: 收到ending信号，操作完成")
                    with arm_status_lock:
                        arm_status_dict[self.robot_ip] = "PAUSE"
                    return True

        except socket.timeout:
            print(f"机械臂{self.robot_ip}: 等待ending信号超时")
            return False
        except Exception as e:
            print(f"机械臂{self.robot_ip}: 通信异常: {e}")
            self.connected = False
            return False

    def close_connections(self):
        """关闭所有连接"""
        self.dobotdemo.stop_feed_thread()
        if self.tcp_socket:
            self.tcp_socket.close()
            self.tcp_socket = None
            self.connected = False

    def check_reachability(self):
        """检查IP是否可达"""
        result = ping_ip(self.robot_ip)
        self.is_reachable = "成功" in result
        return self.is_reachable


class TCPCoordinator:
    """
    TCP通信协调器 - 管理所有机械臂的TCP连接和通信
    """

    def __init__(self, controllers):
        self.controllers = controllers
        self.connected_controllers = []

    def prepare_all_connections(self):
        """在第二阶段开始前建立所有TCP连接"""
        print("在第二阶段开始前建立所有TCP连接...")

        for controller in self.controllers:
            if controller.is_reachable:
                if controller.create_tcp_connection():
                    self.connected_controllers.append(controller)
                else:
                    print(f"机械臂{controller.robot_ip}: 连接失败，跳过该机械臂")
                    with arm_status_lock:
                        arm_status_dict[controller.robot_ip] = "ERROR"
            else:
                print(f"机械臂{controller.robot_ip}: IP不可达，跳过连接")

        print(f"成功建立 {len(self.connected_controllers)} 个TCP连接")
        return len(self.connected_controllers) > 0

    def wait_for_all_start_signals(self, timeout=60):
        """等待所有机械臂发送start信号"""
        print("等待所有机械臂发送start信号...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.connected_controllers)) as executor:
            future_to_controller = {
                executor.submit(controller.wait_for_start_signal): controller
                for controller in self.connected_controllers
            }

            completed_count = 0
            total_count = len(self.connected_controllers)

            for future in concurrent.futures.as_completed(future_to_controller):
                controller = future_to_controller[future]
                try:
                    result = future.result()
                    completed_count += 1
                    if not result:
                        print(f"机械臂{controller.robot_ip}: 接收start信号失败")
                        self.connected_controllers.remove(controller)
                except Exception as e:
                    print(f"机械臂{controller.robot_ip}: 等待start信号异常: {e}")
                    self.connected_controllers.remove(controller)

        print(f"成功接收 {len(self.connected_controllers)} 个机械臂的start信号")
        return len(self.connected_controllers) > 0

    def execute_round(self, round_configs, round_index, robot_ips, step_delay):
        """执行一轮操作"""
        print(f"\n开始第{round_index + 1}轮TCP通信")

        # 发送前等待指定延时
        print(f"等待 {step_delay} 秒后发送命令...")
        sleep(step_delay)

        # 处理当前轮次配置到字典
        process_current_round_configs(round_configs, round_index)

        # 使用线程池并行执行所有机械臂的命令
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.connected_controllers)) as executor:
            future_to_controller = {}
            for controller in self.connected_controllers:
                with arm_configs_lock:
                    if controller.robot_ip in arm_configs_list:
                        with arm_status_lock:
                            arm_status_dict[controller.robot_ip] = "RUNNING"
                        script, delay = arm_configs_list[controller.robot_ip]
                        future = executor.submit(controller.send_script_command, script)
                        future_to_controller[future] = (controller, script)
                    else:
                        print(f"机械臂{controller.robot_ip}: 配置中找不到该IP，跳过")
                        continue

            # 等待所有任务完成
            for future in concurrent.futures.as_completed(future_to_controller):
                controller, script = future_to_controller[future]
                try:
                    result = future.result()
                    if result:
                        print(f"机械臂{controller.robot_ip}: 第{round_index + 1}轮操作完成")
                        with arm_status_lock:
                            arm_status_dict[controller.robot_ip] = "PAUSE"
                    else:
                        print(f"机械臂{controller.robot_ip}: 第{round_index + 1}轮操作失败")
                except Exception as e:
                    print(f"机械臂{controller.robot_ip}: 第{round_index + 1}轮操作异常: {e}")

            # 等待所有机械臂完成操作
            print(f"等待第{round_index + 1}轮所有机械臂完成操作...")
            robot_ips = [c.robot_ip for c in self.connected_controllers]
            if not wait_for_all_arms_status("PAUSE", robot_ips, timeout=300):
                print(f"第{round_index + 1}轮操作等待超时")

            print(f"第{round_index + 1}轮完成")

    def close_all_connections(self):
        """关闭所有TCP连接"""
        print("关闭所有TCP连接...")
        for controller in self.connected_controllers:
            controller.close_connections()
        self.connected_controllers = []


class StateMonitor(threading.Thread):
    """
    状态监控线程，检查所有机械臂的状态
    """

    def __init__(self, robot_ips):
        super().__init__()
        self.robot_ips = robot_ips
        self.shutdown_event = threading.Event()
        self.daemon = True

    def run(self):
        """监控所有机械臂的状态"""
        while not self.shutdown_event.is_set():
            try:
                all_ready = True
                with arm_status_lock:
                    for robot_ip in self.robot_ips:
                        status = arm_status_dict.get(robot_ip, "UNKNOWN")
                        if status not in ["ERROR", "FREE", "PAUSE", "UNREACHABLE"]:
                            all_ready = False
                            break

                if all_ready and len(self.robot_ips) > 0:
                    print("监控线程: 所有机械臂均已就绪")
                    break

                sleep(1)
            except Exception as e:
                print(f"监控线程异常: {e}")
                sleep(2)

    def stop(self):
        """停止监控线程"""
        self.shutdown_event.set()


def process_current_round_configs(round_data, round_index):
    """
    处理当前轮次的配置，覆盖字典内容
    """
    global arm_configs_list

    print(f"处理第{round_index + 1}轮数据，包含{len(round_data)}个配置项")

    with arm_configs_lock:
        arm_configs_list.clear()

        for ip, script, delay in round_data:
            arm_configs_list[ip] = (script, delay)

    print("-" * 50)
    print(f"当前轮次字典内容: {arm_configs_list}")
    print("-" * 50)
    return arm_configs_list


def wait_for_all_arms_status(desired_status, robot_ips, timeout=60):
    """
    等待所有机械臂达到指定状态
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        all_desired_status = True
        with arm_status_lock:
            for robot_ip in robot_ips:
                if arm_status_dict.get(robot_ip) != desired_status:
                    all_desired_status = False
                    break

        if all_desired_status and len([ip for ip in robot_ips]) > 0:
            print(f"所有可达机械臂均已达到 {desired_status} 状态")
            return True

        sleep(0.1)

    print(f"等待机械臂达到 {desired_status} 状态超时")
    return False


def ping_ip(ip_address):
    """
    Ping指定的IP地址，返回连通性结果
    """
    operating_system = platform.system().lower()

    if operating_system == "windows":
        command = ['ping', '-n', '1', '-w', '1000', ip_address]
    else:
        command = ['ping', '-c', '1', '-W', '1', ip_address]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3
        )

        if result.returncode == 0 or 'ttl' in result.stdout.lower():
            return f"{ip_address}成功"
        else:
            return f"{ip_address}失败"

    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, Exception):
        return f"{ip_address}失败"


def is_ip_reachable(ip_address):
    """
    检查IP是否可达，返回布尔值
    """
    result = ping_ip(ip_address)
    return "成功" in result


def filter_reachable_ips(ip_list):
    """
    过滤IP列表，只返回可达的IP
    """
    reachable_ips = []
    unreachable_ips = []

    print("开始检测IP连通性...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ip = {executor.submit(ping_ip, ip): ip for ip in ip_list}

        for future in concurrent.futures.as_completed(future_to_ip):
            ip = future_to_ip[future]
            try:
                result = future.result()
                if "成功" in result:
                    reachable_ips.append(ip)
                else:
                    unreachable_ips.append(ip)
                    print(f"✗ {result}")
            except Exception as e:
                unreachable_ips.append(ip)
                print(f"✗ {ip} 检测异常: {e}")

    print(f"\nIP连通性检测完成:")
    print(f"可达IP: {len(reachable_ips)} 个")
    print(f"不可达IP: {len(unreachable_ips)} 个")

    return reachable_ips, unreachable_ips


def filter_configs_by_reachable_ips(configs, reachable_ips):
    """
    根据可达IP列表过滤配置
    """
    filtered_configs = []
    reachable_set = set(reachable_ips)

    for round_configs in configs:
        filtered_round = []
        for ip, script, delay in round_configs:
            if ip in reachable_set:
                filtered_round.append((ip, script, delay))
        if filtered_round:
            filtered_configs.append(filtered_round)

    print(f"配置过滤完成: 原始{len(configs)}轮 -> 过滤后{len(filtered_configs)}轮")
    return filtered_configs


def generate_configs_from_parameters(ip_list, total_steps, step_delay):
    """
    根据IP列表、总步数和步间延时生成配置
    """
    configs = []

    for step in range(1, total_steps + 1):
        round_configs = []
        for ip in ip_list:
            round_configs.append((ip, step, step_delay))
        configs.append(round_configs)

    print(f"生成配置: {len(configs)}轮, 每轮{len(ip_list)}个机械臂, 总步数{total_steps}, 步间延时{step_delay}秒")
    return configs


def main():
    """
    主函数 - 完整的机械臂控制流程
    """
    # ========================= 直接指定参数 =========================
    # 在这里直接指定IP列表、总步数和步间延时
    robot_ips = [
        '192.168.5.1', '192.168.5.2', '192.168.5.3', '192.168.5.4',
        '192.168.5.5', '192.168.5.6', '192.168.5.7', '192.168.5.8',
        '192.168.5.9', '192.168.5.10', '192.168.5.11', '192.168.5.12'
    ]

    total_steps = 17  # 总共发送的步数（从1开始到17）
    step_delay = 0.01  # 每次发送前的延时（秒）
    # ========================= 参数设置结束 =========================

    # 根据参数生成配置
    configs = generate_configs_from_parameters(robot_ips, total_steps, step_delay)

    if not configs:
        print("没有生成有效的机械臂配置，程序退出。")
        return

    print(f"生成 {len(configs)} 轮配置，涉及 {len(robot_ips)} 个机械臂")

    # 检测IP连通性并过滤
    reachable_ips, unreachable_ips = filter_reachable_ips(robot_ips)

    if not reachable_ips:
        print("没有可达的机械臂IP，程序退出。")
        return

    # 根据可达IP过滤配置
    configs = filter_configs_by_reachable_ips(configs, reachable_ips)
    robot_ips = reachable_ips  # 更新为可达的IP列表

    # 创建控制器实例（只创建可达IP的控制器）
    controllers = []
    for ip in robot_ips:
        controller = ArmController(ip)
        controller.is_reachable = True
        controllers.append(controller)

    print("控制器实例创建完成")

    # 初始化状态字典
    with arm_status_lock:
        for ip in robot_ips:
            arm_status_dict[ip] = "INIT"
        for ip in unreachable_ips:
            arm_status_dict[ip] = "UNREACHABLE"

    # 创建状态监控线程并启动
    monitor_thread = StateMonitor(robot_ips)
    monitor_thread.start()
    print("创建状态监控线程并启动")

    # 第一阶段：同时启动所有机械臂的统一脚本
    print("=" * 60)
    print("第一阶段: 启动所有机械臂的统一脚本")
    print("=" * 60)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(controllers)) as executor:
        future_to_controller = {
            executor.submit(controller.run_script): controller
            for controller in controllers
        }
        print("开始同时启动所有机械臂的统一脚本...")

        for future in concurrent.futures.as_completed(future_to_controller):
            controller = future_to_controller[future]
            result = future.result()

    print("所有机械臂脚本启动完成，TCP服务已创建...")

    # 等待所有机械臂进入PAUSE状态（只等待可达的机械臂）
    if not wait_for_all_arms_status("PAUSE", robot_ips):
        print("等待机械臂就绪超时，程序退出")
        return

    # 一轮控制，连接脚本的TCP服务
    start_or_stop = int(input("输入1结束程序，其他则继续运行"))

    if start_or_stop == 1:
        # 第二阶段：按轮次执行TCP通信
        print("\n" + "=" * 60)
        print("第二阶段: 按轮次执行TCP通信")
        print("=" * 60)

        # 在第二阶段开始前建立所有TCP连接
        coordinator = TCPCoordinator(controllers)
        if not coordinator.prepare_all_connections():
            print("TCP连接准备失败，跳过第二阶段")
        else:
            # 等待所有机械臂发送start信号
            if not coordinator.wait_for_all_start_signals():
                print("等待start信号失败，跳过第二阶段")
            else:
                # 二轮控制，是否进行舞蹈动作
                a = int(input("请输入1以开始"))  # 输入1则继续程序，输入0则关闭脚本，关闭程序
                if a == 1:
                    for round_index, round_configs in enumerate(configs):
                        coordinator.execute_round(round_configs, round_index, robot_ips, step_delay)
                        print("-" * 40)

                    # 停止脚本的运行，结束机械臂创建的tcp连接
                    for controller in controllers:
                        controller.stop()

                else:
                    for controller in controllers:
                        controller.stop()
                    exit()

            # 所有轮次完成后关闭连接
            coordinator.close_all_connections()

        print("所有轮次操作完成！")
        print("\n已结束所有脚本！")

        # 清理工作
        print("执行清理工作...")
        monitor_thread.stop()
        monitor_thread.join(timeout=2.0)

        for controller in controllers:
            try:
                controller.close_connections()
            except Exception as e:
                print(f"清理机械臂 {controller.robot_ip} 资源时出错: {e}")
    else:
        for controller in controllers:
            controller.stop()


if __name__ == '__main__':
    main()