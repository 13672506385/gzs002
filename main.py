# -*- coding: utf-8 -*-
import sys
import socket
import struct
import time
import threading
from datetime import datetime
from kivy.app import App
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelHeader
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.clock import Clock
from kivy.core.window import Window

# ================= 设备配置（与原程序完全一致） =================
COMPUTERS = [
    {"name": "中控主机", "mac": "08:BF:B8:17:7E:22", "ip": "192.168.123.10",
     "user": "ADMINISTRATOR", "password": ""},
    {"name": "32寸主机", "mac": "8C:C5:8C:06:D0:9F", "ip": "192.168.123.12",
     "user": "Administrator", "password": ""},
    {"name": "55寸滑轨屏", "mac": "8C:C5:8C:0B:AA:34", "ip": "192.168.123.13",
     "user": "ADMINISTRATOR", "password": ""},
]

POWER_DEVICE = {
    "name": "电源时序器",
    "server_ip": "192.168.123.8",
    "tcp_port": 11600,
    "on_cmd": b'\x01\x16\x00\x00\x00\x01\x11\xAA',
    "off_cmd": b'\x01\x16\x00\x00\x00\x00\x00\xAA',
}

LED_DEVICE = {
    "name": "LED信号处理器",
    "server_ip": "192.168.123.8",
    "udp_port": 11700,
    "commands": [
        b'\x33\x00\x12\x00\x00\x00\x00\x00\xff\x00\x00\x00\x00\x00\x00\x00\x00\x10',
        b'\x33\x00\x12\x00\x00\x00\x00\x00\xff\x00\x00\x00\x00\x00\x00\x00\x00\x11',
        b'\x33\x00\x12\x00\x00\x00\x00\x00\xff\x00\x00\x00\x00\x00\x00\x00\x00\x12',
        b'\x33\x00\x12\x00\x00\x00\x00\x00\xff\x00\x00\x00\x00\x00\x00\x00\x00\x01',
        b'\x33\x00\x12\x00\x00\x00\x00\x00\xff\x00\x00\x00\x00\x00\x00\x00\x00\x02',
        b'\x33\x00\x12\x00\x00\x00\x00\x00\xff\x00\x00\x00\x00\x00\x00\x00\x00\x30',
    ],
}
LED_NAMES = ["信号源1", "信号源2", "信号源3", "信号源4", "信号源5", "信号源6"]

LIGHT_DEVICES = [
    {
        "name": "智能照明模块1",
        "server_ip": "192.168.123.9",
        "tcp_port": 11600,
        "slave_id": 1,
        "channel_count": 12,
        "start_addr": 0,
    },
    {
        "name": "智能照明模块2",
        "server_ip": "192.168.123.9",
        "tcp_port": 11700,
        "slave_id": 2,
        "channel_count": 12,
        "start_addr": 0,
    },
]

# ================= 网络功能函数（与原程序相同） =================
def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc

def build_modbus_rtu_frame(slave_id, func_code, address, value):
    if func_code != 0x05:
        raise ValueError("仅支持功能码 0x05")
    data = struct.pack('>B B H H', slave_id, func_code, address, value)
    crc = crc16(data)
    return data + struct.pack('<H', crc)

def send_modbus_rtu_tcp(ip, port, slave_id, address, on_off, timeout=2.0):
    try:
        value = 0xFF00 if on_off else 0x0000
        frame = build_modbus_rtu_frame(slave_id, 0x05, address, value)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.send(frame)
        response = sock.recv(8)
        sock.close()
        if len(response) >= 8 and response[0] == slave_id and response[1] == 0x05:
            return True, "成功"
        return False, "响应无效"
    except Exception as e:
        return False, str(e)

def send_power_cmd(ip, port, on_off, timeout=5.0):
    try:
        cmd = POWER_DEVICE["on_cmd"] if on_off else POWER_DEVICE["off_cmd"]
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.send(cmd)
        sock.close()
        return True, "成功"
    except socket.timeout:
        return False, f"连接或发送超时（{timeout}秒）"
    except ConnectionRefusedError:
        return False, "连接被拒绝"
    except socket.gaierror:
        return False, "IP地址解析失败"
    except Exception as e:
        return False, f"其他错误: {str(e)}"

def send_led_cmd(ip, port, cmd, timeout=2.0):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(cmd, (ip, port))
        sock.close()
        return True, "成功"
    except Exception as e:
        return False, str(e)

def wake_on_lan(mac_address):
    mac = mac_address.replace(':', '').replace('-', '').replace('.', '')
    if len(mac) != 12:
        raise ValueError("MAC地址格式错误")
    data = b'\xff' * 6 + (bytes.fromhex(mac) * 16)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.sendto(data, ('255.255.255.255', 9))
    sock.close()

# ================= 远程关机（使用 impacket 替代 Windows 命令） =================
def remote_shutdown(ip, username, password):
    """
    通过 SMB 的 WMI 执行远程关机，无需目标电脑额外设置（需开启 Admin$ 共享和 RPC）
    """
    try:
        from impacket.smbconnection import SMBConnection
        from impacket.examples.wmiexec import WMIEXEC
    except ImportError:
        raise Exception("impacket 未安装，请在打包时添加 requirements")

    smb = SMBConnection(remoteName=ip, remoteHost=ip)
    # 尝试登录
    try:
        smb.login(username, password)
    except Exception:
        if password == "":
            smb.login(username, "")
        else:
            raise

    # 执行关机命令（/f 强制关闭，/t 0 立即）
    executer = WMIEXEC(
        username=username,
        password=password,
        domain='',
        hashes=None,
        no_pass=(password == ""),
        aesKey=None,
        smbconnection=smb,
        remote_name=ip,
        share='ADMIN$',
        command='shutdown /s /f /t 0',
        retry_count=1,
        timeout=120,
        codec='utf-8',
        hashes_type=None,
        doKerberos=False,
        kdcHost=None
    )
    executer.run()   # 执行命令，输出会打印但可忽略

# ================= 后台线程（使用 threading，UI 更新通过 Clock.schedule_once） =================
class WakeThread(threading.Thread):
    def __init__(self, mac):
        super().__init__()
        self.mac = mac
    def run(self):
        try:
            wake_on_lan(self.mac)
            msg = "✅ 已发送开机信号"
        except Exception as e:
            msg = f"❌ 开机失败: {e}"
        Clock.schedule_once(lambda dt: App.get_running_app().log(msg))

class ShutdownThread(threading.Thread):
    def __init__(self, ip, user, password):
        super().__init__()
        self.ip = ip
        self.user = user
        self.password = password
    def run(self):
        try:
            remote_shutdown(self.ip, self.user, self.password)
            msg = "✅ 已发送关机指令"
        except Exception as e:
            msg = f"❌ 关机失败: {e}"
        Clock.schedule_once(lambda dt: App.get_running_app().log(msg))

class PowerTotalThread(threading.Thread):
    def __init__(self, on_off):
        super().__init__()
        self.on_off = on_off
    def run(self):
        ip = POWER_DEVICE["server_ip"]
        port = POWER_DEVICE["tcp_port"]
        ok, msg = send_power_cmd(ip, port, self.on_off)
        if ok:
            status = "开启" if self.on_off else "关闭"
            result = f"✅ 电源 {status}成功"
        else:
            result = f"❌ 电源控制失败: {msg}"
        Clock.schedule_once(lambda dt: App.get_running_app().log(result))
        # 同时更新电源状态标签（通过查找标签对象）
        Clock.schedule_once(lambda dt: App.get_running_app().update_power_status(result))

class LEDSelectThread(threading.Thread):
    def __init__(self, channel):
        super().__init__()
        self.channel = channel
    def run(self):
        if self.channel < 0 or self.channel >= len(LED_DEVICE["commands"]):
            msg = "❌ 信号源序号无效"
        else:
            cmd = LED_DEVICE["commands"][self.channel]
            ip = LED_DEVICE["server_ip"]
            port = LED_DEVICE["udp_port"]
            ok, info = send_led_cmd(ip, port, cmd)
            if ok:
                msg = f"✅ {LED_NAMES[self.channel]} 已切换"
            else:
                msg = f"❌ 切换失败: {info}"
        Clock.schedule_once(lambda dt: App.get_running_app().log(msg))
        Clock.schedule_once(lambda dt: App.get_running_app().update_led_status(self.channel, msg))

class LightSingleThread(threading.Thread):
    def __init__(self, dev_idx, channel, on_off):
        super().__init__()
        self.dev_idx = dev_idx
        self.channel = channel
        self.on_off = on_off
    def run(self):
        dev = LIGHT_DEVICES[self.dev_idx]
        ip = dev["server_ip"]
        port = dev["tcp_port"]
        slave = dev["slave_id"]
        addr = dev["start_addr"] + self.channel
        ok, info = send_modbus_rtu_tcp(ip, port, slave, addr, self.on_off)
        if ok:
            status = "开启" if self.on_off else "关闭"
            msg = f"✅ 通道{self.channel+1} {status}成功"
        else:
            msg = f"❌ 通道{self.channel+1} 控制失败: {info}"
        Clock.schedule_once(lambda dt: App.get_running_app().log(msg))
        Clock.schedule_once(lambda dt: App.get_running_app().update_light_status(self.dev_idx, self.channel, msg))

class LightSequenceThread(threading.Thread):
    def __init__(self, dev_idx, on_off, delay_ms=500):
        super().__init__()
        self.dev_idx = dev_idx
        self.on_off = on_off
        self.delay = delay_ms / 1000.0
    def run(self):
        dev = LIGHT_DEVICES[self.dev_idx]
        ip = dev["server_ip"]
        port = dev["tcp_port"]
        slave = dev["slave_id"]
        start = dev["start_addr"]
        count = dev["channel_count"]
        success_all = True

        ch_range = range(count) if self.on_off else range(count-1, -1, -1)
        for ch in ch_range:
            addr = start + ch
            ok, info = send_modbus_rtu_tcp(ip, port, slave, addr, self.on_off)
            if ok:
                msg = f"✅ 通道{ch+1} {'开启' if self.on_off else '关闭'}成功"
            else:
                msg = f"❌ 通道{ch+1} 失败: {info}"
                success_all = False
            Clock.schedule_once(lambda dt, ch=ch, msg=msg: App.get_running_app().log(msg))
            Clock.schedule_once(lambda dt, ch=ch, msg=msg: App.get_running_app().update_light_status(self.dev_idx, ch, msg))
            if ch != ch_range[-1]:
                time.sleep(self.delay)
        final = "✅ 所有通道操作完成" if success_all else "⚠️ 部分通道失败"
        Clock.schedule_once(lambda dt: App.get_running_app().log(final))

class OneKeyThread(threading.Thread):
    def __init__(self, on_off):
        super().__init__()
        self.on_off = on_off
    def run(self):
        def log_msg(msg):
            Clock.schedule_once(lambda dt: App.get_running_app().log(msg))
        try:
            if self.on_off:
                log_msg("🔹 开始一键全开...")
                # 1. 电源时序器开
                log_msg("   ⏳ 开启电源时序器...")
                ok, info = send_power_cmd(POWER_DEVICE["server_ip"], POWER_DEVICE["tcp_port"], True)
                log_msg(f"   {'✅' if ok else '❌'} 电源时序器: {info}")
                # 2. 顺序开照明1
                log_msg("   ⏳ 顺序开启照明模块1...")
                dev1 = LIGHT_DEVICES[0]
                for ch in range(dev1["channel_count"]):
                    addr = dev1["start_addr"] + ch
                    ok, info = send_modbus_rtu_tcp(dev1["server_ip"], dev1["tcp_port"], dev1["slave_id"], addr, True)
                    log_msg(f"      CH{ch+1}: {'✅' if ok else '❌'} {info}")
                    time.sleep(0.5)
                # 3. 顺序开照明2
                log_msg("   ⏳ 顺序开启照明模块2...")
                dev2 = LIGHT_DEVICES[1]
                for ch in range(dev2["channel_count"]):
                    addr = dev2["start_addr"] + ch
                    ok, info = send_modbus_rtu_tcp(dev2["server_ip"], dev2["tcp_port"], dev2["slave_id"], addr, True)
                    log_msg(f"      CH{ch+1}: {'✅' if ok else '❌'} {info}")
                    time.sleep(0.5)
                # 4. WOL唤醒电脑
                log_msg("   ⏳ 发送电脑唤醒信号...")
                for comp in COMPUTERS:
                    try:
                        wake_on_lan(comp["mac"])
                        log_msg(f"      {comp['name']}: ✅ 唤醒包已发送")
                    except Exception as e:
                        log_msg(f"      {comp['name']}: ❌ {e}")
                log_msg("✅ 一键全开执行完毕")
            else:
                log_msg("🔹 开始一键全关...")
                # 1. 关机电脑（并行发送，不等待）
                log_msg("   ⏳ 关闭电脑...")
                for comp in COMPUTERS:
                    try:
                        remote_shutdown(comp["ip"], comp["user"], comp["password"])
                        log_msg(f"      {comp['name']}: ✅ 关机指令已发送")
                    except Exception as e:
                        log_msg(f"      {comp['name']}: ❌ {e}")
                # 2. 倒序关照明2
                log_msg("   ⏳ 关闭照明模块2...")
                dev2 = LIGHT_DEVICES[1]
                for ch in range(dev2["channel_count"]-1, -1, -1):
                    addr = dev2["start_addr"] + ch
                    ok, info = send_modbus_rtu_tcp(dev2["server_ip"], dev2["tcp_port"], dev2["slave_id"], addr, False)
                    log_msg(f"      CH{ch+1}: {'✅' if ok else '❌'} {info}")
                    time.sleep(0.5)
                # 3. 倒序关照明1
                log_msg("   ⏳ 关闭照明模块1...")
                dev1 = LIGHT_DEVICES[0]
                for ch in range(dev1["channel_count"]-1, -1, -1):
                    addr = dev1["start_addr"] + ch
                    ok, info = send_modbus_rtu_tcp(dev1["server_ip"], dev1["tcp_port"], dev1["slave_id"], addr, False)
                    log_msg(f"      CH{ch+1}: {'✅' if ok else '❌'} {info}")
                    time.sleep(0.5)
                # 4. 关电源时序器
                log_msg("   ⏳ 关闭电源时序器...")
                ok, info = send_power_cmd(POWER_DEVICE["server_ip"], POWER_DEVICE["tcp_port"], False)
                log_msg(f"   {'✅' if ok else '❌'} 电源时序器: {info}")
                log_msg("✅ 一键全关执行完毕")
        except Exception as e:
            log_msg(f"❌ 一键操作异常: {e}")
        # 恢复按钮状态（主界面方法）
        Clock.schedule_once(lambda dt: App.get_running_app().enable_onekey_buttons(True))

# ================= Kivy 主界面 =================
class MainApp(App):
    def build(self):
        Window.size = (360, 640)  # 模拟手机尺寸（方便调试）
        self.log_text = ""        # 日志文本累积
        self.root_widget = self.build_ui()
        return self.root_widget

    def build_ui(self):
        # 主 TabbedPanel
        self.tabs = TabbedPanel(do_default_tab=False)
        # ---- 总控标签 ----
        tab_total = TabbedPanelHeader(text='🎛️ 总控')
        tab_total.content = self.build_total_tab()
        self.tabs.add_widget(tab_total)
        # ---- 照明标签 ----
        tab_light = TabbedPanelHeader(text='💡 照明')
        tab_light.content = self.build_light_tab()
        self.tabs.add_widget(tab_light)
        return self.tabs

    def build_total_tab(self):
        # 滚动区域
        scroll = ScrollView()
        main_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=5, padding=10)
        main_box.bind(minimum_height=main_box.setter('height'))

        # 1. 一键全开/全关
        onekey_box = BoxLayout(size_hint_y=None, height=50, spacing=10)
        self.btn_on = Button(text='🚀 一键全开', background_color=(0.2, 0.8, 0.2, 1))
        self.btn_off = Button(text='⛔ 一键全关', background_color=(0.8, 0.2, 0.2, 1))
        self.btn_on.bind(on_press=lambda x: self.onekey_control(True))
        self.btn_off.bind(on_press=lambda x: self.onekey_control(False))
        onekey_box.add_widget(self.btn_on)
        onekey_box.add_widget(self.btn_off)
        main_box.add_widget(onekey_box)

        # 2. 电脑控制
        main_box.add_widget(Label(text='电脑远程开关机', size_hint_y=None, height=30, bold=True))
        self.computer_status = []
        for i, comp in enumerate(COMPUTERS):
            row = BoxLayout(size_hint_y=None, height=40, spacing=5)
            row.add_widget(Label(text=comp['name'], size_hint_x=0.3))
            btn_wake = Button(text='开机', size_hint_x=0.2)
            btn_wake.bind(on_press=lambda x, m=comp['mac']: self.wake_pc(m))
            btn_shut = Button(text='关机', size_hint_x=0.2)
            btn_shut.bind(on_press=lambda x, ip=comp['ip'], u=comp['user'], p=comp['password']: self.shutdown_pc(ip, u, p))
            status_lbl = Label(text='⏳', size_hint_x=0.3)
            row.add_widget(btn_wake)
            row.add_widget(btn_shut)
            row.add_widget(status_lbl)
            main_box.add_widget(row)
            self.computer_status.append(status_lbl)

        # 3. 电源时序器
        main_box.add_widget(Label(text='电源时序器', size_hint_y=None, height=30, bold=True))
        power_row = BoxLayout(size_hint_y=None, height=40, spacing=10)
        btn_power_on = Button(text='全部开启')
        btn_power_off = Button(text='全部关闭')
        btn_power_on.bind(on_press=lambda x: self.control_power_total(True))
        btn_power_off.bind(on_press=lambda x: self.control_power_total(False))
        self.power_status = Label(text='⏳')
        power_row.add_widget(btn_power_on)
        power_row.add_widget(btn_power_off)
        power_row.add_widget(self.power_status)
        main_box.add_widget(power_row)

        # 4. LED 信号源（两行三列）
        main_box.add_widget(Label(text='LED 信号源切换', size_hint_y=None, height=30, bold=True))
        led_grid = GridLayout(cols=3, size_hint_y=None, spacing=5, padding=5)
        led_grid.bind(minimum_height=led_grid.setter('height'))
        self.led_status = []
        for ch, name in enumerate(LED_NAMES):
            cell = BoxLayout(orientation='vertical', size_hint_y=None, height=80)
            btn = Button(text=name, size_hint_y=0.7)
            btn.bind(on_press=lambda x, c=ch: self.select_led_source(c))
            lbl = Label(text='⏳', size_hint_y=0.3)
            cell.add_widget(btn)
            cell.add_widget(lbl)
            led_grid.add_widget(cell)
            self.led_status.append(lbl)
        main_box.add_widget(led_grid)

        # 5. 日志区域
        main_box.add_widget(Label(text='📋 操作日志', size_hint_y=None, height=30, bold=True))
        self.log_label = Label(text='', size_hint_y=None, height=200, halign='left', valign='top')
        self.log_label.bind(texture_size=self.log_label.setter('size'))
        scroll_log = ScrollView(size_hint_y=None, height=200)
        scroll_log.add_widget(self.log_label)
        main_box.add_widget(scroll_log)

        scroll.add_widget(main_box)
        return scroll

    def build_light_tab(self):
        scroll = ScrollView()
        main_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=10, padding=10)
        main_box.bind(minimum_height=main_box.setter('height'))
        self.light_status_groups = []   # 每个模块一个列表

        for dev_idx, dev in enumerate(LIGHT_DEVICES):
            group = BoxLayout(orientation='vertical', size_hint_y=None, spacing=5)
            group.add_widget(Label(text=f"{dev['name']} (12通道)", size_hint_y=None, height=30, bold=True))
            grid = GridLayout(cols=4, size_hint_y=None, spacing=2, padding=2)
            grid.bind(minimum_height=grid.setter('height'))
            # 表头
            grid.add_widget(Label(text='通道', size_hint_x=0.25))
            grid.add_widget(Label(text='开', size_hint_x=0.25))
            grid.add_widget(Label(text='关', size_hint_x=0.25))
            grid.add_widget(Label(text='状态', size_hint_x=0.25))
            status_labels = []
            for ch in range(dev["channel_count"]):
                grid.add_widget(Label(text=f'CH{ch+1}', size_hint_x=0.25))
                btn_on = Button(text='开', size_hint_x=0.25)
                btn_on.bind(on_press=lambda x, idx=dev_idx, c=ch: self.control_light_single(idx, c, True))
                btn_off = Button(text='关', size_hint_x=0.25)
                btn_off.bind(on_press=lambda x, idx=dev_idx, c=ch: self.control_light_single(idx, c, False))
                lbl = Label(text='⏳', size_hint_x=0.25)
                grid.add_widget(btn_on)
                grid.add_widget(btn_off)
                grid.add_widget(lbl)
                status_labels.append(lbl)
            group.add_widget(grid)

            # 顺序按钮
            seq_box = BoxLayout(size_hint_y=None, height=40, spacing=10)
            btn_seq_on = Button(text='顺序开启 (500ms)')
            btn_seq_off = Button(text='顺序关闭 (500ms)')
            btn_seq_on.bind(on_press=lambda x, idx=dev_idx: self.control_light_sequence(idx, True))
            btn_seq_off.bind(on_press=lambda x, idx=dev_idx: self.control_light_sequence(idx, False))
            seq_box.add_widget(btn_seq_on)
            seq_box.add_widget(btn_seq_off)
            group.add_widget(seq_box)

            main_box.add_widget(group)
            self.light_status_groups.append(status_labels)

        scroll.add_widget(main_box)
        return scroll

    # ========== 控制方法（启动线程） ==========
    def wake_pc(self, mac):
        self.log(f"正在唤醒 {mac}...")
        t = WakeThread(mac)
        t.daemon = True
        t.start()

    def shutdown_pc(self, ip, user, password):
        self.log(f"正在关闭 {ip}...")
        t = ShutdownThread(ip, user, password)
        t.daemon = True
        t.start()

    def control_power_total(self, on_off):
        self.log(f"电源时序器 总{'开启' if on_off else '关闭'}...")
        t = PowerTotalThread(on_off)
        t.daemon = True
        t.start()

    def select_led_source(self, channel):
        self.log(f"切换 {LED_NAMES[channel]}...")
        t = LEDSelectThread(channel)
        t.daemon = True
        t.start()

    def control_light_single(self, dev_idx, channel, on_off):
        dev = LIGHT_DEVICES[dev_idx]
        self.log(f"{dev['name']} 通道{channel+1} {'开启' if on_off else '关闭'}...")
        t = LightSingleThread(dev_idx, channel, on_off)
        t.daemon = True
        t.start()

    def control_light_sequence(self, dev_idx, on_off):
        dev = LIGHT_DEVICES[dev_idx]
        self.log(f"{dev['name']} 顺序{'开启' if on_off else '关闭'}...")
        t = LightSequenceThread(dev_idx, on_off)
        t.daemon = True
        t.start()

    def onekey_control(self, on_off):
        self.btn_on.disabled = True
        self.btn_off.disabled = True
        self.log(f"启动一键{'全开' if on_off else '全关'}...")
        t = OneKeyThread(on_off)
        t.daemon = True
        t.start()

    def enable_onekey_buttons(self, enabled):
        self.btn_on.disabled = not enabled
        self.btn_off.disabled = not enabled

    # ========== UI 更新方法（由 Clock.schedule_once 调用） ==========
    def log(self, msg):
        # 追加日志到 label，保留最近20行
        lines = self.log_label.text.split('\n')
        lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        if len(lines) > 30:
            lines = lines[-30:]
        self.log_label.text = '\n'.join(lines)

    def update_power_status(self, msg):
        if "✅" in msg:
            self.power_status.text = "✅ 成功"
        elif "❌" in msg:
            self.power_status.text = "❌ 失败"
        else:
            self.power_status.text = "⏳ 完成"

    def update_led_status(self, channel, msg):
        if "✅" in msg:
            self.led_status[channel].text = "✅"
        elif "❌" in msg:
            self.led_status[channel].text = "❌"
        else:
            self.led_status[channel].text = "⏳"

    def update_light_status(self, dev_idx, channel, msg):
        lbl = self.light_status_groups[dev_idx][channel]
        if "✅" in msg:
            lbl.text = "✅"
        elif "❌" in msg:
            lbl.text = "❌"
        else:
            lbl.text = "⏳"

if __name__ == '__main__':
    MainApp().run()