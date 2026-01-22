import sys
import csv
import os
import subprocess
import threading
import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QLineEdit, QLabel,
                             QPushButton, QGroupBox, QMessageBox, QFileDialog,
                             QTextEdit, QSplitter)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QTextCursor


class ScriptThread(QThread):
    """脚本执行线程"""
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str)

    def __init__(self, main_script="main.py"):
        super().__init__()
        self.main_script = main_script
        self.process = None

    def run(self):
        try:
            # 检查主脚本文件是否存在
            if not os.path.exists(self.main_script):
                self.finished_signal.emit(f"错误: 主脚本文件 {self.main_script} 不存在")
                return

            # 使用subprocess运行脚本
            cmd = [sys.executable, self.main_script]
            self.output_signal.emit(f"执行命令: {' '.join(cmd)}")

            # 设置环境变量，确保使用UTF-8编码
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                encoding='utf-8',
                env=env
            )

            # 实时读取输出
            while True:
                output = self.process.stdout.readline()
                if output == '' and self.process.poll() is not None:
                    break
                if output:
                    self.output_signal.emit(output.strip())

            # 检查退出代码
            return_code = self.process.poll()
            if return_code == 0:
                self.finished_signal.emit("脚本执行完成")
            else:
                self.finished_signal.emit(f"脚本执行出错，退出码: {return_code}")

        except Exception as e:
            self.finished_signal.emit(f"执行脚本时发生错误: {str(e)}")


class CSVEditor(QMainWindow):
    def __init__(self,csv_name):
        super().__init__()
        self.current_file = csv_name
        self.outer_index = 0
        self.script_thread = None
        self.is_running = False
        self.init_ui()
        self.load_file_data()

    def init_ui(self):
        self.setWindowTitle("群控机械臂测试版V1.0")
        self.setGeometry(100, 100, 1600, 900)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 文件操作区域
        file_group = QGroupBox("文件操作")
        file_layout = QHBoxLayout(file_group)

        self.file_label = QLabel(f"当前文件: {self.current_file}")
        self.browse_btn = QPushButton("选择文件")
        self.clear_btn = QPushButton("清空文件")
        self.save_btn = QPushButton("写入CSV文件")

        self.browse_btn.clicked.connect(self.browse_file)
        self.clear_btn.clicked.connect(self.clear_file)
        self.save_btn.clicked.connect(self.save_to_csv)

        file_layout.addWidget(self.file_label)
        file_layout.addWidget(self.browse_btn)
        file_layout.addWidget(self.clear_btn)
        file_layout.addStretch()
        file_layout.addWidget(self.save_btn)

        # 机器输入区域 - 修改为33个IP地址
        machine_group = QGroupBox("IP地址配置 (192.168.5.1-192.168.5.33)")
        machine_layout = QGridLayout(machine_group)

        # 只需要脚本和延时输入框，不再需要IP输入框
        self.script_inputs = {}
        self.delay_inputs = {}
        self.group_boxes = {}  # 存储组框的引用

        # 创建33个输入位置（3行11列）
        position = 0
        for row in range(3):
            for col in range(11):
                if position >= 33:  # 创建33个输入位置
                    break

                # 计算机器对应的IP地址
                ip_num = position + 1
                ip_address = f"192.168.5.{ip_num}"

                # 机器组框 - 标题直接显示IP地址
                machine_box = QGroupBox(f"IP: {ip_address}")
                self.group_boxes[position] = machine_box  # 保存引用
                machine_box_layout = QVBoxLayout(machine_box)

                # 脚本输入（IP地址已由标题确定，无需再输入）
                script_layout = QHBoxLayout()
                script_layout.addWidget(QLabel("脚本:"))
                script_input = QLineEdit()
                script_input.setPlaceholderText("函数序号/名称 (输入0跳过)")
                self.script_inputs[position] = script_input
                script_layout.addWidget(script_input)
                machine_box_layout.addLayout(script_layout)

                # 延时输入 - 设置默认值为0.01
                delay_layout = QHBoxLayout()
                delay_layout.addWidget(QLabel("延时:"))
                delay_input = QLineEdit()
                delay_input.setText("0.01")  # 设置默认值为0.01
                delay_input.setPlaceholderText("例如: 0.05")
                self.delay_inputs[position] = delay_input
                delay_layout.addWidget(delay_input)
                machine_box_layout.addLayout(delay_layout)

                machine_layout.addWidget(machine_box, row, col)
                position += 1

        # 创建分割器，使配置区域和结果区域可以调整大小
        splitter = QSplitter(Qt.Vertical)

        # 上半部分：配置区域
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.addWidget(file_group)
        config_layout.addWidget(machine_group)

        # 状态信息
        self.status_label = QLabel("准备就绪")
        config_layout.addWidget(self.status_label)

        # 在右下角添加运行脚本按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)  # 添加弹性空间，将按钮推到右侧

        # 创建运行脚本按钮
        self.run_script_btn = QPushButton("运行脚本")
        self.run_script_btn.clicked.connect(self.run_script)
        button_layout.addWidget(self.run_script_btn)

        # 停止脚本按钮
        self.stop_script_btn = QPushButton("停止脚本")
        self.stop_script_btn.clicked.connect(self.stop_script)
        self.stop_script_btn.setEnabled(False)
        button_layout.addWidget(self.stop_script_btn)

        # 清空日志按钮
        self.clear_log_btn = QPushButton("清空日志")
        self.clear_log_btn.clicked.connect(self.clear_log)
        button_layout.addWidget(self.clear_log_btn)

        config_layout.addLayout(button_layout)

        # 下半部分：结果区域
        result_group = QGroupBox("运行结果")
        result_layout = QVBoxLayout(result_group)

        # 创建运行结果文本框
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setFont(QFont("Consolas", 9))  # 使用等宽字体，便于查看输出
        result_layout.addWidget(self.result_text)

        # 添加到分割器
        splitter.addWidget(config_widget)
        splitter.addWidget(result_group)
        splitter.setStretchFactor(0, 3)  # 配置区域占3份
        splitter.setStretchFactor(1, 2)  # 结果区域占2份

        # 将分割器添加到主布局
        main_layout.addWidget(splitter)

    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择CSV文件", "", "CSV文件 (*.csv)")
        if file_path:
            self.current_file = file_path
            self.file_label.setText(f"当前文件: {self.current_file}")
            self.load_file_data()

    def load_file_data(self):
        """加载现有文件数据，计算当前outer_index"""
        if not os.path.exists(self.current_file):
            self.outer_index = 0
            self.status_label.setText(f"文件不存在，将创建新文件。outer_index: {self.outer_index}")
            return

        try:
            max_outer_index = -1
            # 使用UTF-8编码读取文件，避免编码错误
            with open(self.current_file, 'r', newline='', encoding='utf-8') as file:
                reader = csv.reader(file)
                for row in reader:
                    if len(row) >= 1 and row[0].isdigit():
                        max_outer_index = max(max_outer_index, int(row[0]))

            self.outer_index = max_outer_index + 1 if max_outer_index != -1 else 0
            self.status_label.setText(f"文件加载成功。outer_index: {self.outer_index}")

        except Exception as e:
            QMessageBox.warning(self, "错误", f"读取文件时出错: {str(e)}")
            self.outer_index = 0

    def clear_file(self):
        """清空文件内容"""
        reply = QMessageBox.question(self, "确认清空",
                                     "确定要清空文件内容吗？此操作不可撤销！",
                                     QMessageBox.Yes | QMessageBox.No)

        if reply == QMessageBox.Yes:
            try:
                with open(self.current_file, 'w', newline='', encoding='utf-8') as file:
                    file.write("")
                self.outer_index = 0
                self.status_label.setText(f"文件已清空。outer_index: {self.outer_index}")
                QMessageBox.information(self, "成功", "文件已清空")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"清空文件时出错: {str(e)}")

    def save_to_csv(self):
        """将当前输入的数据保存到CSV文件"""
        valid_records = 0

        # 验证输入并收集有效数据
        records = []
        for i in range(33):  # 33个IP地址
            # IP地址直接从组框标题中提取（格式为"IP: 192.168.5.X"）
            ip = self.group_boxes[i].title().split(": ")[1]  # 提取IP地址部分
            script = self.script_inputs[i].text().strip()
            delay = self.delay_inputs[i].text().strip()

            # 如果脚本为空或为0，跳过该记录
            if not script or script == "0":
                continue

            # 验证必填字段
            if not script:
                QMessageBox.warning(self, "输入错误", f"IP地址 {ip} 的脚本不能为空")
                return

            if not delay:
                QMessageBox.warning(self, "输入错误", f"IP地址 {ip} 的延时不能为空")
                return

            # 验证延时是否为数字
            try:
                float(delay)
            except ValueError:
                QMessageBox.warning(self, "输入错误", f"IP地址 {ip} 的延时必须是数字")
                return

            # 记录有效数据
            records.append({
                'inner_index': i,
                'ip': ip,
                'script': script,
                'delay': delay
            })
            valid_records += 1

        # 如果没有有效记录，提示用户
        if valid_records == 0:
            QMessageBox.information(self, "提示", "没有有效记录可保存（所有脚本均为空或0）")
            return

        try:
            # 检查是否需要添加表头（文件不存在或为空）
            need_header = not os.path.exists(self.current_file) or os.path.getsize(self.current_file) == 0

            # 写入文件，使用UTF-8编码
            with open(self.current_file, 'a', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)

                # 如果需要添加表头，先写入表头
                if need_header:
                    writer.writerow(['outer_index', 'inner_index', 'ip', 'script', 'delay'])

                for record in records:
                    writer.writerow([
                        self.outer_index,  # outer_index
                        record['inner_index'],  # inner_index
                        record['ip'],  # ip
                        record['script'],  # script
                        record['delay']  # delay
                    ])

            # 清空输入框（只清空脚本输入框，延时输入框重置为默认值0.01）
            for i in range(33):
                self.script_inputs[i].clear()
                self.delay_inputs[i].setText("0.01")  # 重置为默认值

            # 更新状态
            self.status_label.setText(f"数据已成功写入！outer_index: {self.outer_index}, 有效记录: {valid_records}条")
            self.outer_index += 1

            QMessageBox.information(self, "成功", f"数据已成功写入CSV文件，共写入{valid_records}条记录")

        except Exception as e:
            QMessageBox.warning(self, "错误", f"写入文件时出错: {str(e)}")

    def run_script(self):
        """运行脚本功能"""
        if self.is_running:
            QMessageBox.information(self, "提示", "脚本正在运行中，请等待完成")
            return

        # 清空结果文本框
        self.result_text.clear()

        # 更新按钮状态
        self.run_script_btn.setEnabled(False)
        self.stop_script_btn.setEnabled(True)
        self.is_running = True
        self.status_label.setText("正在运行脚本...")

        # 创建并启动脚本线程
        self.script_thread = ScriptThread()
        self.script_thread.output_signal.connect(self.append_output)
        self.script_thread.finished_signal.connect(self.script_finished)
        self.script_thread.start()

    def stop_script(self):
        """停止脚本执行"""
        if self.script_thread and self.script_thread.isRunning():
            if self.script_thread.process:
                self.script_thread.process.terminate()
            self.script_thread.terminate()
            self.append_output("脚本已被用户停止")
            self.script_finished("脚本已停止")

    def append_output(self, text):
        """向结果文本框追加文本"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")

        # 添加时间戳和文本
        self.result_text.append(f"[{timestamp}] {text}")

        # 自动滚动到底部
        cursor = self.result_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.result_text.setTextCursor(cursor)

    def script_finished(self, message):
        """脚本执行完成"""
        self.run_script_btn.setEnabled(True)
        self.stop_script_btn.setEnabled(False)
        self.is_running = False
        self.status_label.setText(message)
        self.append_output(f"=== {message} ===")

        # 显示完成对话框
        if "错误" in message or "出错" in message:
            QMessageBox.warning(self, "脚本执行结果", message)
        else:
            QMessageBox.information(self, "脚本执行结果", message)

    def clear_log(self):
        """清空运行日志"""
        self.result_text.clear()
        self.append_output("日志已清空")


def main(csv_name):
    app = QApplication(sys.argv)

    # 设置字体
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    window = CSVEditor(csv_name)
    window.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main("test_12.csv")