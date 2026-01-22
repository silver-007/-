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

"""
1.优化筛选逻辑，剔除不能到达的ip
2.一轮判断，是否运行舞蹈动作

"""

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

    def __init__(self, robot_ip, script_name="part1"):      # part1、part2、part3、part4
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

        # 读取及其各关节角的位置
        # sleep(5)
        # print(f"QActual:{self.dobotdemo.get_qactual()}")
        # print(f"robotmode:{self.dobotdemo.get_robot_mode()}")
        
        # 运行统一的主脚本
        # print("**************")
        recvmsg = self.dobotdemo.dashboard.RunScript(self.script_name)
        print(f"机械臂{self.robot_ip}: 脚本启动结果: {recvmsg}")

        # 改变机器状态
        while True:
            current_mode = self.dobotdemo.feedData.robotMode
            # print(f"*********{current_mode}***********")
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

        # return f"机械臂{self.robot_ip} 脚本启动成功"

    def wait_until_ready(self, timeout=30):
        """等待机械臂准备就绪"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            # with self.dobotdemo.__globalLockValue:
            #     current_mode = self.dobotdemo.feedData.robotMode
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
        # 如果IP不可达，直接返回None
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
            # print(f"机械臂{self.robot_ip}: TCP连接成功")
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
            # 只对可达的IP建立连接
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

        # 使用线程池并行等待所有机械臂的start信号
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.connected_controllers)) as executor:
            future_to_controller = {
                executor.submit(controller.wait_for_start_signal): controller
                for controller in self.connected_controllers
            }

            # 等待所有start信号
            # start_time = time.time()
            completed_count = 0
            total_count = len(self.connected_controllers)

            for future in concurrent.futures.as_completed(future_to_controller):
                controller = future_to_controller[future]
                try:
                    result = future.result()
                    completed_count += 1
                    if result:
                        a =1
                        # print(f"机械臂{controller.robot_ip}: 成功接收start信号 ({completed_count}/{total_count})")
                    else:
                        print(f"机械臂{controller.robot_ip}: 接收start信号失败")
                        # 从连接列表中移除失败的控制器
                        self.connected_controllers.remove(controller)
                except Exception as e:
                    print(f"机械臂{controller.robot_ip}: 等待start信号异常: {e}")
                    self.connected_controllers.remove(controller)

                # 检查是否超时
                # if time.time() - start_time > timeout:
                #     print("等待start信号超时")
                #     break

        print(f"成功接收 {len(self.connected_controllers)} 个机械臂的start信号")
        return len(self.connected_controllers) > 0

    def execute_round(self, round_configs, round_index, robot_ips):
        """执行一轮操作"""
        print(f"\n开始第{round_index + 1}轮TCP通信")
        while True:
            # 只等待可达的机械臂
            # reachable_ips = [ip for ip in robot_ips]
            # if wait_for_all_arms_status("PAUSE", reachable_ips):

            # 处理当前轮次配置到字典
            process_current_round_configs(round_configs, round_index)

            # 使用线程池并行执行所有机械臂的命令
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.connected_controllers)) as executor:
                # 为每个连接的控制器提交任务
                future_to_controller = {}
                for controller in self.connected_controllers:
                    # 获取当前机械臂的脚本编号
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
            return

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
                # 检查所有机械臂的状态
                all_ready = True
                with arm_status_lock:
                    for robot_ip in self.robot_ips:
                        status = arm_status_dict.get(robot_ip, "UNKNOWN")
                        if status not in ["ERROR", "FREE", "PAUSE", "UNREACHABLE"]:
                            all_ready = False
                            break

                # 如果全部就绪，则打印信息
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


def save_configs_to_csv(configs, filename='robot_ips.csv'):
    """
    将三维嵌套配置列表保存为CSV文件，保持原始结构
    """
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['outer_index', 'inner_index', 'ip', 'script', 'delay'])

            for outer_idx, inner_list in enumerate(configs):
                for inner_idx, tuple_data in enumerate(inner_list):
                    ip, script, delay = tuple_data
                    writer.writerow([outer_idx, inner_idx, ip, script, delay])

        print(f"成功将 {len(configs)} 个外层列表的配置保存到 {filename}")
        return True
    except Exception as e:
        print(f"保存文件时出错: {e}")
        return False


def read_configs_from_csv(filename):
    """
    从CSV文件读取配置，恢复原始嵌套列表结构
    """
    from collections import defaultdict
    configs = []
    ip_set = set()

    try:
        grouped_data = defaultdict(list)

        with open(filename, 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            header = next(reader)  # 跳过表头

            for row_num, row in enumerate(reader, start=2):
                if len(row) < 5:
                    print(f"警告: 第{row_num}行数据不完整，已跳过")
                    continue

                try:
                    outer_idx = int(row[0])
                    inner_idx = int(row[1])
                    ip = row[2]
                    script = int(row[3])
                    delay = float(row[4])

                    grouped_data[outer_idx].append((inner_idx, (ip, script, delay)))
                    ip_set.add(ip)

                except (ValueError, IndexError) as e:
                    print(f"解析第{row_num}行数据时出错: {e}，已跳过该行")
                    continue

        # 重建嵌套列表结构
        max_outer_idx = max(grouped_data.keys()) if grouped_data else -1

        for outer_idx in range(max_outer_idx + 1):
            if outer_idx in grouped_data:
                inner_items = grouped_data[outer_idx]
                inner_items.sort(key=lambda x: x[0])
                inner_list = [item[1] for item in inner_items]
                configs.append(inner_list)
            else:
                configs.append([])

        ip_list = sorted(list(ip_set))
        print(f"成功读取配置: {len(configs)}个外层列表, {len(ip_list)}个唯一IP地址")
        return configs, ip_list

    except FileNotFoundError:
        print(f"错误: 文件 {filename} 不存在")
        return [], []
    except Exception as e:
        print(f"读取文件时出错: {e}")
        return [], []


def process_current_round_configs(round_data, round_index):
    """
    处理当前轮次的配置，覆盖字典内容
    """
    global arm_configs_list

    print(f"处理第{round_index + 1}轮数据，包含{len(round_data)}个配置项")

    # 清空字典内容（覆盖之前轮次的数据）
    with arm_configs_lock:
        arm_configs_list.clear()

        # 更新为当前轮配置，只包含可达的IP
        for ip, script, delay in round_data:
            # if test(ip):  # 只添加可达的IP
            arm_configs_list[ip] = (script, delay)
            #     print(f"  IP地址: {ip} -> 脚本编号: {script}, 延迟时间: {delay}")
            # else:
            #     print(f"  IP地址: {ip} 不可达，已从本轮配置中移除")

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
                # 跳过不可达的IP
                # if not is_ip_reachable(robot_ip):
                #     continue
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
    # 根据操作系统选择合适的ping参数[2,4,7](@ref)
    operating_system = platform.system().lower()

    if operating_system == "windows":
        command = ['ping', '-n', '1', '-w', '1000', ip_address]  # Windows系统参数[4](@ref)
    else:
        command = ['ping', '-c', '1', '-W', '1', ip_address]  # Linux/macOS系统参数[2](@ref)

    try:
        # 执行ping命令[1,4](@ref)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3  # 整体超时3秒
        )

        # 检查ping结果[2,7](@ref)
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

    # 使用多线程并行检测IP连通性
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ip = {executor.submit(ping_ip, ip): ip for ip in ip_list}

        for future in concurrent.futures.as_completed(future_to_ip):
            ip = future_to_ip[future]
            try:
                result = future.result()
                if "成功" in result:
                    reachable_ips.append(ip)
                    # print(f"✓ {result}")
                else:
                    unreachable_ips.append(ip)
                    print(f"✗ {result}")
            except Exception as e:
                unreachable_ips.append(ip)
                print(f"✗ {ip} 检测异常: {e}")

    print(f"\nIP连通性检测完成:")
    print(f"可达IP: {len(reachable_ips)} 个")
    print(f"不可达IP: {len(unreachable_ips)} 个")

    # if unreachable_ips:
    #     print("不可达IP列表:", unreachable_ips)

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
        if filtered_round:  # 只添加非空的轮次
            filtered_configs.append(filtered_round)

    print(f"配置过滤完成: 原始{len(configs)}轮 -> 过滤后{len(filtered_configs)}轮")
    return filtered_configs


def main():
    """
    主函数 - 完整的机械臂控制流程
    """
    # 读取所有机械臂配置
    configs, robot_ips = read_configs_from_csv("test_12_12.csv")       # part4,part1

    if not configs:
        print("没有读取到有效的机械臂配置，程序退出。")
        return

    print(f"读取到 {len(configs)} 轮配置，涉及 {len(robot_ips)} 个机械臂")

    # 检测IP连通性并过滤
    reachable_ips, unreachable_ips = filter_reachable_ips(robot_ips)

    if not reachable_ips:
        print("没有可达的机械臂I32 P，程序退出。")
        return

    # 根据可达IP过滤配置
    configs = filter_configs_by_reachable_ips(configs, reachable_ips)
    robot_ips = reachable_ips  # 更新为可达的IP列表

    # 创建控制器实例（只创建可达IP的控制器）
    controllers = []
    for ip in robot_ips:
        controller = ArmController(ip)
        # 预先设置可达性状态
        controller.is_reachable = True
        controllers.append(controller)

    print("控制器实例创建完成")

    # 初始化状态字典
    with arm_status_lock:
        for ip in robot_ips:
            arm_status_dict[ip] = "INIT"
        # 为不可达的IP设置状态
        for ip in unreachable_ips:
            arm_status_dict[ip] = "UNREACHABLE"

    # 创建状态监控线程并启动
    monitor_thread = StateMonitor(robot_ips)
    monitor_thread.start()
    print("创建状态监控线程并启动")
    # quit()

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
            # print(f"{result}")

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
                # 执行所有轮次的操作

                # 二轮控制，是否进行舞蹈动作123
                a = int(input("请输入1以开始"))  # 输入1则继续程序，输入0则关闭脚本，关闭程序
                if 1 == a:
                    for round_index, round_configs in enumerate(configs):
                        coordinator.execute_round(round_configs, round_index, robot_ips)
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
