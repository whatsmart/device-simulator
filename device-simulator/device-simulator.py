#!/usr/bin/env python3

import gi
import os.path
import queue
import threading
import selectors
import socket
gi.require_version('Gtk', '3.0')
gi.require_version("GLib", "2.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gtk, Gio, GLib, Gdk
from . import hipc, jsonrpc


class Simulator(object):
    """智能硬件设备模拟器
    """

    rpcid = 0

    def __init__(self):
        self.path = os.path.dirname(__file__)
        self.parser = hipc.Parser()
        self.devices = []
        self.stop = False
        self.selector = selectors.DefaultSelector()
        self.in_queue = queue.Queue()
        self.out_queue = queue.Queue()
        self.parser.done_callback = self.in_queue.put
        self.callbacks = {}

        self.builder = Gtk.Builder()
        self.builder.add_from_file(self.path + "/main.ui")
        self.style_context = Gtk.StyleContext.new()
        self.style_provider = Gtk.CssProvider.new()
        try:
            self.style_provider.load_from_path(os.path.join(self.path, "style.css"))
            self.style_context.add_provider_for_screen(Gdk.Screen.get_default(), self.style_provider,
                                                       Gtk.STYLE_PROVIDER_PRIORITY_USER)
        except GLib.Error as e:
            print(e)

        # 主窗口
        self.window = self.builder.get_object("main_window")
        self.window.set_title("设备模拟器")
        self.window.set_default_size(600, 400)
        self.window.connect("destroy", self.quit)

        # 网关地址和连接
        self.builder.get_object("sock_type").connect("changed", self.combobox_socktype_changed)
        # self.builder.get_object("sock_addr").set_placeholder_text("网络地址或路径")
        # self.builder.get_object("sock_port").set_placeholder_text("端口")
        self.builder.get_object("sock_addr").set_text("121.42.156.167")
        self.builder.get_object("sock_port").set_text("8080")
        self.builder.get_object("sock_type").set_active(1)
        self.builder.get_object("connect").connect("clicked", self.connect_to_gateway)

        # 设置菜单项目点击事件
        self.builder.get_object("add_lighting").connect("activate", self.add_lighting_activate)
        self.builder.get_object("add_heater").connect("activate", self.add_heater_activate)

        # device_list是Gtk.ListBox类型
        self.device_list = self.builder.get_object("device_list")

    def combobox_socktype_changed(self, combobox):
        active = self.builder.get_object("sock_type").get_active()
        if active == 0:
            self.builder.get_object("port_box").hide()
        elif active == 1:
            self.builder.get_object("port_box").show()

    def disconnect_from_gateway(self, button):
        self.stop = True

        self.builder.get_object("sock_addr").set_sensitive(True)
        self.builder.get_object("sock_port").set_sensitive(True)
        self.builder.get_object("connect").set_label("连接")
        self.builder.get_object("connect").disconnect_by_func(self.disconnect_from_gateway)
        self.builder.get_object("connect").connect("clicked", self.connect_to_gateway)

    def connect_to_gateway(self, button):
        sock = None
        sock_type = self.builder.get_object("sock_type")
        addr = self.builder.get_object("sock_addr").get_text()
        port = self.builder.get_object("sock_port").get_text()

        try:
            if sock_type.get_active() == 0:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, 0)
                sock.connect(addr)

            elif sock_type.get_active() == 1:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
                sock.settimeout(10)
                sock.connect((addr, int(port)))
        except (socket.error, socket.herror, socket.gaierror, socket.timeout) as e:
            print(e.strerror)
        else:
            sock.setblocking(False)
            self.selector.register(sock, selectors.EVENT_READ | selectors.EVENT_WRITE)

            self.builder.get_object("sock_addr").set_sensitive(False)
            self.builder.get_object("sock_port").set_sensitive(False)
            self.builder.get_object("connect").set_label("断开")
            self.builder.get_object("connect").disconnect_by_func(self.connect_to_gateway)
            self.builder.get_object("connect").connect("clicked", self.disconnect_from_gateway)

            self.stop = False
            thread = threading.Thread(target=self.networking_thread)
            thread.start()

            dev = {
                   "vender": "asdfsdaf",
                   "uniqid": "er-fd-ef-gf-cv-df",
                   "hwversion": "asdfasdf",
                   "swversion": "ffffff",
                   "type": "lighting",
                   "operations": []
                   }

            rpc_request = jsonrpc.Request(jsonrpc="2.0", method="add_device", params=dev, id=1).dumps()
            hipc_request = hipc.Request(resource="device", headers={}, body=rpc_request.encode("utf-8")).bytes()
            self.out_queue.put(hipc_request)

    def alert_connection_down(self):
        self.builder.get_object("sock_addr").set_sensitive(True)
        self.builder.get_object("sock_port").set_sensitive(True)
        self.builder.get_object("connect").set_label("连接")
        self.builder.get_object("connect").disconnect_by_func(self.disconnect_from_gateway)
        self.builder.get_object("connect").connect("clicked", self.connect_to_gateway)

        dialog = Gtk.MessageDialog(self.window, Gtk.DialogFlags.MODAL, Gtk.MessageType.WARNING,
                                   Gtk.ButtonsType.OK, "网络连接已断开")
        dialog.run()
        dialog.destroy()

    @classmethod
    def next_rpcid(cls):
        cls.rpcid += 1
        return cls.rpcid

    def add_callback(self, id, callback):
        self.callbacks[id] = callback

    def invoke_callback(self, id):
        self.callbacks[id]()

    def networking_thread(self):
        while not self.stop:
            events = self.selector.select(-1)
            for key, mask in events:
                if mask & selectors.EVENT_READ:
                    data = key.fileobj.recv(8192)
                    if len(data) > 0:
                        self.parser.parse(data)
                    else:
                        GLib.idle_add(self.alert_connection_down)

                        self.selector.unregister(key.fileobj)
                        key.fileobj.close()
                        if len(self.selector.get_map()) == 0:
                            return

                if mask & selectors.EVENT_WRITE:
                    try:
                        message = self.out_queue.get(block=False)
                        print(message)
                        key.fileobj.send(message)
                    except queue.Empty:
                        pass

        map = self.selector.get_map()
        fileobjs = []
        for key in map.keys():
            fileobjs.append(map[key].fileobj)

        for fileobj in fileobjs:
            self.selector.unregister(fileobj)
            fileobj.close()

    def handle_incoming(self):
        try:
            message = self.in_queue.get(block=False)
            if isinstance(message, hipc.Request):
                if message.resource == "control":
                    pass
            elif isinstance(message, hipc.Response):
                print("Response:\n", message, sep="")
        except queue.Empty:
            pass

        return True

    def add_lighting_activate(self, menuitem):
        print("add lighting menuitem clicked")

    def add_heater_activate(self, menuitem):
        builder = Gtk.Builder()
        builder.add_from_file(self.path + "/add_general_device.ui")
        dialog = builder.get_object("add_device_dialog")
        dialog.set_title("添加智能热水器")
        dialog.set_transient_for(self.window)
        lb_type = builder.get_object("type")
        lb_type.set_text("heater")
        bt_ok = builder.get_object("ok")
        bt_ok.connect("clicked", self.add_heater_done, builder)
        dialog.show_all()

    def add_heater_done(self, button, builder):
        tv_uniqid = builder.get_object("uniqid")
        tv_vender = builder.get_object("vender")
        tv_hwversion = builder.get_object("hwversion")
        tv_swversion = builder.get_object("swversion")
        device = dict()
        device["uniqid"] = tv_uniqid.get_text()
        device["vender"] = tv_vender.get_text()
        device["hwversion"] = tv_hwversion.get_text()
        device["swversion"] = tv_swversion.get_text()
        device["type"] = "heater"
        device["operations"]= ["power_on", "power_off"]
        device["state"] = {}
        device["state"]["water_temperature "]= 30
        device["state"]["max_temperature"] = 75
        device["state"]["power"] = "on"
        device["state"]["max_power"] = 1000

        box = Gtk.Box(Gtk.Orientation.HORIZONTAL)
        icon = Gtk.Image.new_from_file(os.path.join(self.path, "images/lighting.png"))
        icon.set_size_request(20, 20)
        label = Gtk.Label.new("智能热水器")
        box.add(icon)
        box.add(label)
        self.device_list.add(box)
        self.device_list.show_all()

        dialog = builder.get_object("add_device_dialog")
        dialog.destroy()

    def start(self):
        self.window.show_all()
        GLib.idle_add(self.handle_incoming)
        Gtk.main()

    def quit(self, window):
        self.stop = True
        Gtk.main_quit()

if __name__ == "__main__":
    simulator = Simulator()
    simulator.start()
