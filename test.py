import sys
import os
import json
import tempfile
import subprocess
import io
from PIL import Image
import time
import pygame
from mutagen import File
import shutil

from srgb2lin import convert
import mesh_processing

from PySide6.QtWidgets import (QApplication, QMainWindow, QSplitter, QTreeWidget, QTreeWidgetItem,
                              QFrame, QLabel, QLineEdit, QPushButton, QCheckBox, QMenuBar, QMenu,
                              QTextEdit, QFileDialog, QMessageBox, QVBoxLayout, QHBoxLayout,
                              QSlider, QWidget, QScrollBar, QStyledItemDelegate)
from PySide6.QtCore import Qt, QTimer, QSize, QThread, Signal, QPoint
from PySide6.QtGui import QPixmap, QIcon, QAction

import pyvista as pv
from pyvistaqt import QtInteractor
from pathlib import Path

if not sys.platform.startswith('win32'):
    app = QApplication(sys.argv)
    QMessageBox.critical(None, "OS Error", "This application only supports Windows operating systems.")
    sys.exit(1)

class ByteReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
    
    def read_byte(self) -> int:
        byte = self.data[self.pos]
        self.pos += 1
        return byte
    
    def skip(self, amt: int) -> None:
        self.pos += amt
    
    def read_bytes(self, amt: int) -> bytes:
        bytes_data = self.data[self.pos:self.pos + amt]
        self.pos += amt
        return bytes_data
    
    def read_uint32(self) -> int:
        sum = 0
        for i in range(4):
            sum += self.read_byte() << (8 * i)
        return sum
    
    def read_string(self, len_: int) -> str:
        if len_ != -1:
            return self.read_bytes(len_).decode('utf-8')
        else:
            bytes_list = []
            while True:
                byte = self.read_byte()
                if byte == 0x00:
                    break
                bytes_list.append(byte)
            return bytes(bytes_list).decode('utf-8') if bytes_list else ""

class NonEditableDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        tree_widget = parent
        while tree_widget and not isinstance(tree_widget, QTreeWidget):
            tree_widget = tree_widget.parent()
        if not tree_widget:
            return super().createEditor(parent, option, index)
        
        item = tree_widget.itemFromIndex(index)
        if not item:
            return super().createEditor(parent, option, index)
        
        if index.column() == 0:
            key_text = item.text(0)
            if key_text.isdigit():
                return None
        elif index.column() == 1:
            value = item.text(1)
            if value in ("<dict>", "<list>"):
                return None
        return super().createEditor(parent, option, index)

class CacheTreeItem(QTreeWidgetItem):
    def __init__(self, strings):
        super().__init__(strings)
        self.size_bytes = self._parse_size(strings[2])

    def _parse_size(self, size_str):
        try:
            size, unit = size_str.split()
            size = float(size)
            units = {'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}
            return size * units.get(unit, 1)
        except (ValueError, IndexError):
            return 0

    def __lt__(self, other):
        col = self.treeWidget().sortColumn()
        if col == 2:
            return self.size_bytes < other.size_bytes
        return self.text(col) < other.text(col)

class CacheLoader(QThread):
    update_tree = Signal(list)

    def __init__(self, cache_dir):
        super().__init__()
        self.cache_dir = cache_dir

    def run(self):
        if not os.path.exists(self.cache_dir):
            return
        files = os.listdir(self.cache_dir)
        cache_files = []
        for file in files:
            filepath = os.path.join(self.cache_dir, file)
            try:
                size = os.path.getsize(filepath)
                formatted_size = self.format_size(size)
                date = time.ctime(os.path.getmtime(filepath))
                ftype = self.get_file_type(filepath)
                file_data = (file, ftype, formatted_size, date)
                cache_files.append(file_data)
            except FileNotFoundError:
                continue
            except Exception:
                continue
        self.update_tree.emit(cache_files)

    def format_size(self, size_in_bytes):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_in_bytes < 1024.0:
                return f"{size_in_bytes:.2f} {unit}"
            size_in_bytes /= 1024.0
        return f"{size_in_bytes:.2f} TB"

    def get_file_type(self, filepath):
        try:
            with open(filepath, 'rb') as f:
                data = f.read()
            
            reader = ByteReader(data)
            ident = reader.read_string(4)
            if ident != "RBXH":
                return "Unknown"
            
            reader.skip(4)
            link_len = reader.read_uint32()
            reader.read_string(link_len)  # Skip link
            reader.skip(1)
            req_status_code = reader.read_uint32()
            
            # Add redirect and error status code checking
            if req_status_code in {301, 302, 303, 307, 308}:
                return f"Redirect ({req_status_code})"
            elif req_status_code != 200:
                return f"Error ({req_status_code})"
            
            header_data_len = reader.read_uint32()
            reader.skip(4)
            file_size = reader.read_uint32()
            reader.skip(8 + header_data_len)
            cont = reader.read_bytes(file_size)
            begin = cont[:min(48, len(cont))].decode('utf-8', errors='ignore')
            
            if "<roblox!" in begin:
                return "RBXM Animation"
            elif "<roblox xml" in begin:
                return "XML"
            elif '"version' not in begin and "version" in begin:
                mesh_version = cont[:12].decode('utf-8')
                num_only_ver = mesh_version[8:]
                return f"Mesh (v{num_only_ver})"
            elif '{"locale":"' in begin:
                return "Translation (JSON)"
            elif "PNG\r\n" in begin:
                return "PNG"
            elif begin.startswith("GIF8"):
                return "GIF"
            elif "JFIF" in begin:
                return "JFIF"
            elif "OggS" in begin:
                return "OGG"
            elif any(x in begin for x in ["TSSE", "Lavf", "matroska"]):
                return "MP3"
            elif "KTX " in begin:
                return "KTX"
            elif begin.startswith("#EXTM3U"):
                return "EXTM3U (VideoFrame)"
            elif '"name": "' in begin:
                return "TTF (JSON)"
            elif '{"applicationSettings' in begin or '{"version' in begin or "webmB" in begin:
                return "JSON/VideoFrame"
            else:
                return "Unknown"
        except Exception as e:
            print(f"Error identifying file type for {filepath}: {e}")
            return "Unknown"

class AudioPlayer:
    def __init__(self, parent, filepath, preview_frame):
        self.parent = parent
        self.filepath = filepath
        self.preview_frame = preview_frame
        self.is_playing = False
        self.position = 0
        self.duration = 0
        self.active = True
        self.start_time = 0
        
        try:
            audio = File(filepath)
            self.duration = audio.info.length if audio else 0
            self.parent.log_info(f"Audio loaded: {self.filepath}, Duration: {self.parent.format_time(self.duration)}")
        except Exception as e:
            self.parent.log_error(f"Failed to load audio duration {self.filepath}: {e}")
            self.duration = 0
        
        pygame.mixer.init()
        self.setup_ui()
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_progress)
        self.timer.start(100)

    def setup_ui(self):
        layout = self.preview_frame.layout()
        if not layout:
            self.parent.log_error("No layout found in preview_frame for AudioPlayer")
            return

        file_name = os.path.basename(self.filepath)
        size = os.path.getsize(self.filepath)
        layout.addWidget(QLabel(f"File: {file_name}"))
        ftype = "MP3" if self.filepath.endswith('.mp3') else "OGG"
        layout.addWidget(QLabel(f"Type: {ftype}, Size: {self.parent.format_size(size)}, Duration: {self.parent.format_time(self.duration)}"))

        controls_frame = QFrame()
        controls_layout = QHBoxLayout(controls_frame)
        
        self.play_pause_button = QPushButton("Play" if not self.is_playing else "Pause")
        self.play_pause_button.clicked.connect(self.toggle_play_pause)
        controls_layout.addWidget(self.play_pause_button)
        
        controls_layout.addWidget(QLabel("Volume:"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self.parent.persistent_volume * 100))
        self.volume_slider.valueChanged.connect(self.set_volume)
        self.volume_slider.sliderReleased.connect(self.log_volume)
        controls_layout.addWidget(self.volume_slider)
        
        layout.addWidget(controls_frame)

        self.progress_slider = QSlider(Qt.Horizontal)
        self.progress_slider.setRange(0, int(self.duration * 1000))
        self.progress_slider.sliderPressed.connect(self.start_scrub)
        self.progress_slider.sliderReleased.connect(self.seek_audio)
        layout.addWidget(self.progress_slider)

        self.time_label = QLabel(f"00:00 / {self.parent.format_time(self.duration)}")
        layout.addWidget(self.time_label)

        button_frame = QFrame()
        button_layout = QHBoxLayout(button_frame)
        button_layout.addWidget(QPushButton("Close Preview", clicked=self.parent.get_close_command(self.preview_frame)))
        button_layout.addWidget(QPushButton("Open Externally", clicked=lambda: self.parent.open_externally(self.filepath)))
        layout.addWidget(button_frame)

        self.preview_frame.show()
        self.preview_frame.update()
        self.parent.log_info(f"Audio player UI set up for {self.filepath}")

    def toggle_play_pause(self):
        if not self.active:
            return
        if not self.is_playing:
            if self.position >= self.duration:
                self.position = 0
                self.progress_slider.setValue(0)
            pygame.mixer.music.load(self.filepath)
            pygame.mixer.music.play(loops=0)
            pygame.mixer.music.set_pos(self.position)
            pygame.mixer.music.set_volume(self.parent.persistent_volume)
            self.start_time = time.time() - self.position
            self.is_playing = True
            self.play_pause_button.setText("Pause")
            self.parent.log_info(f"Playing audio: {self.filepath} from {self.parent.format_time(self.position)}")
        else:
            self.position = self.get_current_position()
            pygame.mixer.music.stop()
            self.is_playing = False
            self.play_pause_button.setText("Play")
            self.parent.log_info(f"Paused audio: {self.filepath} at {self.parent.format_time(self.position)}")

    def set_volume(self, value):
        if not self.active:
            return
        volume = value / 100
        pygame.mixer.music.set_volume(volume)
        self.parent.persistent_volume = volume

    def log_volume(self):
        volume = self.volume_slider.value() / 100
        self.parent.log_info(f"Set volume to {volume:.2f} for {self.filepath}")

    def start_scrub(self):
        if not self.active or not self.is_playing:
            return
        self.position = self.get_current_position()
        pygame.mixer.music.stop()
        self.is_playing = False
        self.play_pause_button.setText("Play")

    def seek_audio(self):
        if not self.active:
            return
        self.position = self.progress_slider.value() / 1000
        self.position = max(0, min(self.position, self.duration))
        self.time_label.setText(f"{self.parent.format_time(self.position)} / {self.parent.format_time(self.duration)}")
        self.parent.log_info(f"Seeked audio: {self.filepath} to {self.parent.format_time(self.position)}")
        if self.is_playing:
            pygame.mixer.music.load(self.filepath)
            pygame.mixer.music.play(loops=0)
            pygame.mixer.music.set_pos(self.position)
            self.start_time = time.time() - self.position

    def get_current_position(self):
        if self.is_playing:
            return time.time() - self.start_time
        return self.position

    def update_progress(self):
        if not self.active or not self.preview_frame.isVisible():
            return
        if self.is_playing:
            current_pos = self.get_current_position()
            if current_pos >= self.duration:
                pygame.mixer.music.stop()
                self.is_playing = False
                self.position = self.duration
                self.progress_slider.setValue(int(self.duration * 1000))
                self.time_label.setText(f"{self.parent.format_time(self.duration)} / {self.parent.format_time(self.duration)}")
                self.play_pause_button.setText("Play")
                self.parent.log_info(f"Audio {self.filepath} finished playing")
            else:
                self.progress_slider.setValue(int(current_pos * 1000))
                self.time_label.setText(f"{self.parent.format_time(current_pos)} / {self.parent.format_time(self.duration)}")

    def stop(self):
        self.active = False
        if self.is_playing:
            self.position = self.get_current_position()
            pygame.mixer.music.stop()
        self.timer.stop()
        self.parent.log_info(f"Audio player stopped for {self.filepath}")

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        pygame.mixer.init()
        self.setWindowTitle("Fleasion")
        self.setGeometry(100, 100, 1200, 800)
        self.persistent_volume = 1.0
        self.preview_pane_added = True
        self.current_mode = "json"
        self.live_update_running = False
        self.cache_files = []
        self.audio_players = {}
        self.temp_files = {}
        self.cache_populated = False
        
        self.save_raw_on_name_change_var = False
        self.save_converted_on_name_change_var = False

        self.setup_ui()
        self.show()

        self.log_info("Application initialized successfully")

    def setup_ui(self):
        self.setup_menu_bar()
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.central_layout = QVBoxLayout(self.central_widget)
        
        self.setup_json_mode()
        self.central_layout.addWidget(self.outer_splitter)
        
        self.setup_cache_mode()
        self.central_layout.addWidget(self.cache_frame)
        self.cache_frame.hide()
        
        self.setup_command_line()
        self.central_layout.addWidget(self.cmd_line)

    def setup_menu_bar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        file_menu.addAction(QAction("Load JSON (Left)", self, triggered=lambda: self.load_json("left")))
        file_menu.addAction(QAction("Load JSON (Right)", self, triggered=lambda: self.load_json("right")))
        file_menu.addSeparator()
        file_menu.addAction(QAction("Exit", self, triggered=self.close))

        mode_menu = menubar.addMenu("Mode")
        mode_menu.addAction(QAction("JSON Tree Mode", self, triggered=self.set_json_mode))
        mode_menu.addAction(QAction("Cache Previewer", self, triggered=self.set_cache_mode))

        self.stay_on_top_cb = QCheckBox("Stay on Top")
        self.stay_on_top_cb.setChecked(False)
        self.stay_on_top_cb.toggled.connect(self.toggle_stay_on_top)
        menubar.setCornerWidget(self.stay_on_top_cb, Qt.TopRightCorner)

    def toggle_stay_on_top(self, checked):
        if checked:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
            self.log_info("Window set to stay on top")
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
            self.log_info("Window set to normal layering")
        self.show()

    def setup_json_mode(self):
        self.outer_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.outer_splitter.addWidget(self.main_splitter)
        
        self.json_frame_left = QFrame()
        self.json_frame_right = QFrame()
        self.main_splitter.addWidget(self.json_frame_left)
        self.main_splitter.addWidget(self.json_frame_right)
        
        self.preview_splitter = QSplitter(Qt.Vertical)
        self.json_preview_left = QFrame()
        self.json_preview_right = QFrame()
        self.preview_splitter.addWidget(self.json_preview_left)
        self.preview_splitter.addWidget(self.json_preview_right)
        self.outer_splitter.addWidget(self.preview_splitter)
        self.preview_splitter.hide()
        
        self.setup_json_trees()

    def setup_json_trees(self):
        self.json_trees = {
            "left": {
                "tree": QTreeWidget(self.json_frame_left),
                "search_entry": QLineEdit(),
                "data": {},
                "file_path": None,
                "preview": self.json_preview_left,
                "state": {"last_query": None, "matches": [], "current_index": -1}
            },
            "right": {
                "tree": QTreeWidget(self.json_frame_right),
                "search_entry": QLineEdit(),
                "data": {},
                "file_path": None,
                "preview": self.json_preview_right,
                "state": {"last_query": None, "matches": [], "current_index": -1}
            }
        }
        for side, config in self.json_trees.items():
            tree = config["tree"]
            tree.setHeaderLabels(["Key", "Value"])
            tree.setColumnWidth(0, 200)
            tree.setSelectionMode(QTreeWidget.ExtendedSelection if side == "left" else QTreeWidget.SingleSelection)
            tree.setEditTriggers(QTreeWidget.DoubleClicked)
            tree.setItemDelegate(NonEditableDelegate())
            tree.itemChanged.connect(lambda item, col, c=config: self.handle_edit(item, col, c))
            tree.itemSelectionChanged.connect(self.update_json_previews)
            
            search_frame = QFrame()
            search_layout = QHBoxLayout(search_frame)
            search_layout.addWidget(QLabel("Search Keys:"))
            search_layout.addWidget(config["search_entry"])
            config["search_entry"].returnPressed.connect(lambda t=tree, e=config["search_entry"]: self.search_json(t, e))
            
            layout = QVBoxLayout(config["tree"].parent())
            layout.addWidget(search_frame)
            layout.addWidget(tree)
        
        self.editing_item = None
        self.original_value = None

    def setup_cache_mode(self):
        self.cache_frame = QFrame()
        cache_layout = QVBoxLayout(self.cache_frame)
        
        self.cache_search_frame = QFrame()
        self.cache_search_frame.setMaximumHeight(40)
        search_layout = QHBoxLayout(self.cache_search_frame)
        search_layout.setSpacing(5)
        search_layout.setContentsMargins(2, 2, 2, 2)
        
        search_label = QLabel("Search:")
        search_label.setFixedHeight(20)
        search_layout.addWidget(search_label)
        self.cache_search_entry = QLineEdit()
        self.cache_search_entry.setFixedHeight(25)
        self.cache_search_entry.returnPressed.connect(self.search_cache)
        search_layout.addWidget(self.cache_search_entry)
        
        self.cache_filter_vars = {
            "Name": QCheckBox("Name"),
            "Type": QCheckBox("Type", checked=True),
            "Size": QCheckBox("Size"),
            "Date": QCheckBox("Date")
        }
        for cb in self.cache_filter_vars.values():
            cb.setFixedHeight(20)
            search_layout.addWidget(cb)
        
        self.live_update_cb = QCheckBox("Live Update", toggled=self.toggle_live_update)
        self.live_update_cb.setFixedHeight(20)
        search_layout.addWidget(self.live_update_cb)
        
        self.settings_button = QPushButton("Settings")
        self.settings_button.setFixedHeight(25)
        self.settings_menu = QMenu(self)
        self.settings_menu.addAction(QAction("Save Raw on Name Change", self, checkable=True, 
                                           toggled=lambda v: setattr(self, "save_raw_on_name_change_var", v)))
        self.settings_menu.addAction(QAction("Save Converted on Name Change", self, checkable=True, 
                                           toggled=lambda v: setattr(self, "save_converted_on_name_change_var", v)))
        self.settings_button.setMenu(self.settings_menu)
        search_layout.addWidget(self.settings_button)
        
        self.filter_button = QPushButton("Filter")
        self.filter_button.setFixedHeight(25)
        self.filter_menu = QMenu(self)
        
        filter_types = ["Unknown", "Redirect", "Error", "PNG", "GIF", "JFIF", "OGG", "MP3", 
                       "KTX", "Mesh", "JSON", "XML", "RBXM Animation", "EXTM3U (VideoFrame)", 
                       "TTF (JSON)", "Translation (JSON)", "JSON/VideoFrame"]
        
        self.show_filters = QMenu("Show", self)
        self.exclude_filters = QMenu("Exclude", self)
        
        self.show_filter_actions = {}
        self.exclude_filter_actions = {}
        
        for ftype in filter_types:
            show_action = QAction(ftype, self, checkable=True, 
                                triggered=lambda checked, t=ftype: self.apply_filter(t, "show", checked))
            exclude_action = QAction(ftype, self, checkable=True, 
                                  triggered=lambda checked, t=ftype: self.apply_filter(t, "exclude", checked))
            self.show_filters.addAction(show_action)
            self.exclude_filters.addAction(exclude_action)
            self.show_filter_actions[ftype] = show_action
            self.exclude_filter_actions[ftype] = exclude_action
        
        self.filter_menu.addMenu(self.show_filters)
        self.filter_menu.addMenu(self.exclude_filters)
        self.filter_button.setMenu(self.filter_menu)
        search_layout.addWidget(self.filter_button)
        
        refresh_button = QPushButton("Refresh")
        refresh_button.setFixedHeight(25)
        refresh_button.clicked.connect(self.refresh_cache)
        search_layout.addWidget(refresh_button)
        
        clear_cache_button = QPushButton("Clear Cache")
        clear_cache_button.setFixedHeight(25)
        clear_cache_button.clicked.connect(self.clear_cache)
        search_layout.addWidget(clear_cache_button)
        
        cache_layout.addWidget(self.cache_search_frame)
        
        self.cache_splitter = QSplitter(Qt.Horizontal)
        cache_layout.addWidget(self.cache_splitter)
        
        tree_frame = QFrame()
        tree_layout = QVBoxLayout(tree_frame)
        self.cache_tree = QTreeWidget()
        self.cache_tree.setHeaderLabels(["Name", "Type", "Size", "Date"])
        for i, width in enumerate([200, 100, 100, 150]):
            self.cache_tree.setColumnWidth(i, width)
        self.cache_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.cache_tree.itemSelectionChanged.connect(lambda: self.preview_cache_content(self.cache_tree.selectedItems()[0].text(0) if self.cache_tree.selectedItems() else "", self.cache_preview))
        self.cache_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cache_tree.customContextMenuRequested.connect(self.show_cache_menu)
        
        self.cache_tree.setSortingEnabled(True)
        self.cache_tree.header().setSectionsClickable(True)
        
        scroll_bar = QScrollBar(Qt.Vertical)
        self.cache_tree.setVerticalScrollBar(scroll_bar)
        tree_layout.addWidget(self.cache_tree)
        self.cache_splitter.addWidget(tree_frame)
        
        self.cache_preview = QFrame()
        self.cache_splitter.addWidget(self.cache_preview)
        self.cache_splitter.setSizes([800, 400])

    def setup_command_line(self):
        self.cmd_line = QTextEdit()
        self.cmd_line.setReadOnly(True)
        self.cmd_line.setFixedHeight(150)

    def handle_edit(self, item, column, config):
        if self.editing_item != item:
            return
        
        new_text = item.text(column)
        if new_text == self.original_value:
            self.editing_item = None
            self.original_value = None
            return
        
        try:
            path = self.get_path(config["tree"], item)
            if column == 0:
                if not new_text:
                    item.setText(0, self.original_value)
                    self.log_error("Key cannot be empty")
                    return
                
                current = config["data"]
                for key in path[:-1]:
                    current = current[key]
                if new_text in current and new_text != self.original_value:
                    item.setText(0, self.original_value)
                    self.log_error(f"Duplicate key '{new_text}' detected at the same level.")
                    return
                
                new_dict = {}
                for k, v in current.items():
                    if k == self.original_value:
                        new_dict[new_text] = v
                    else:
                        new_dict[k] = v
                current.clear()
                current.update(new_dict)
                self.log_info(f"Changed key from '{self.original_value}' to '{new_text}' at path {path}")
                
            elif column == 1 and item.childCount() == 0:
                parsed_value = self.parse_value(new_text)
                current = config["data"]
                for key in path[:-1]:
                    current = current[key]
                current[path[-1]] = parsed_value
                item.setText(1, str(parsed_value))
                self.log_info(f"Changed value from '{self.original_value}' to '{new_text}' at path {path}")
            
            self.save_json(config)
            
            other_side = "right" if config == self.json_trees["left"] else "left"
            other_config = self.json_trees[other_side]
            if config["file_path"] and config["file_path"] == other_config["file_path"]:
                other_config["data"] = config["data"]
                self.update_tree_item(other_config["tree"], path, column, new_text, config["data"])
                self.log_info(f"Synchronized changes to {other_side} tree for file {config['file_path']}")
            
            self.update_json_previews()
            
        except Exception as e:
            self.log_error(f"Failed to update JSON: {e}")
            item.setText(column, self.original_value)
        
        self.editing_item = None
        self.original_value = None

    def update_tree_item(self, tree, path, column, new_text, data):
        current_item = tree.invisibleRootItem()
        
        for key in path[:-1]:
            found = False
            for i in range(current_item.childCount()):
                child = current_item.child(i)
                if child.text(0) == str(key):
                    current_item = child
                    found = True
                    break
            if not found:
                self.log_error(f"Could not find path segment '{key}' in tree")
                return
        
        last_key = path[-1]
        for i in range(current_item.childCount()):
            child = current_item.child(i)
            if column == 0 and child.text(0) == str(self.original_value):
                child.setText(0, new_text)
                if child.childCount() > 0:
                    return
                current_data = data
                for k in path[:-1]:
                    current_data = current_data[k]
                child.setText(1, str(current_data[new_text]) if not isinstance(current_data[new_text], (dict, list)) else f"<{type(current_data[new_text]).__name__}>")
                return
            elif column == 1 and child.text(0) == str(last_key):
                parsed_value = self.parse_value(new_text)
                child.setText(1, str(parsed_value))
                return
        
        self.log_error(f"Could not find item to update for path {path} in tree")

    def start_edit_tracking(self, item, column):
        self.editing_item = item
        self.original_value = item.text(column)

    def parse_value(self, value):
        value = value.strip()
        if value.lower() in ('true', 'false'):
            return value.lower() == 'true'
        try:
            if value.isdigit():
                return int(value)
            if value.replace('.', '').isdigit() and value.count('.') <= 1:
                return float(value)
        except ValueError:
            pass
        return value

    def get_path(self, tree, item):
        path = []
        current = item
        while current:
            text = current.text(0)
            try:
                key = int(text)
            except ValueError:
                key = text
            path.insert(0, key)
            current = current.parent()
        return path

    def load_json(self, side):
        file_path = QFileDialog.getOpenFileName(self, "Open JSON File", "", "JSON files (*.json)")[0]
        if file_path:
            try:
                with open(file_path, 'r') as f:
                    json_data = json.load(f)
                config = self.json_trees[side]
                config["data"] = json_data
                config["file_path"] = file_path
                self.populate_tree(config["tree"], json_data)
                config["state"].update({"last_query": None, "matches": [], "current_index": -1})
                self.log_info(f"Loaded JSON ({side}) from {file_path}")
            except Exception as e:
                self.log_error(f"Failed to load JSON ({side}): {e}")

    def save_json(self, config):
        if not config["file_path"]:
            self.log_error("No file path specified for saving JSON")
            return
        
        try:
            with open(config["file_path"], 'w', encoding='utf-8') as f:
                json.dump(config["data"], f, indent=4, ensure_ascii=False)
            self.log_info(f"Saved changes to {config['file_path']}")
        except PermissionError:
            self.log_error(f"Permission denied when saving to {config['file_path']}")
        except Exception as e:
            self.log_error(f"Failed to save JSON to {config['file_path']}: {e}")

    def search_json(self, tree, search_entry):
        query = search_entry.text().strip().lower()
        if not query:
            self.log_info("Search query is empty")
            return
        config = next(c for c in self.json_trees.values() if c["tree"] == tree)
        state = config["state"]
        if query != state["last_query"] or not state["matches"]:
            state["matches"] = tree.findItems(query, Qt.MatchContains | Qt.MatchRecursive, 0)
            state["last_query"] = query
            state["current_index"] = 0 if state["matches"] else -1
            if state["matches"]:
                tree.setCurrentItem(state["matches"][0])
                tree.scrollToItem(state["matches"][0])
                self._expand_parents(tree, state["matches"][0])
                self.log_info(f"Found {len(state['matches'])} matches for '{query}'")
            else:
                self.log_info(f"No matches found for '{query}'")
        elif state["matches"]:
            state["current_index"] = (state["current_index"] + 1) % len(state["matches"])
            tree.setCurrentItem(state["matches"][state["current_index"]])
            tree.scrollToItem(state["matches"][state["current_index"]])
            self._expand_parents(tree, state["matches"][state["current_index"]])
            self.log_info(f"Showing match {state['current_index'] + 1} of {len(state['matches'])} for '{query}'")

    def _expand_parents(self, tree, item):
        parent = item.parent()
        while parent:
            tree.expandItem(parent)
            parent = parent.parent()

    def populate_tree(self, tree, data):
        tree.clear()
        def add_items(parent, data):
            if isinstance(data, dict):
                for key, value in data.items():
                    item = QTreeWidgetItem(parent, [str(key), "" if isinstance(value, (dict, list)) else str(value)])
                    flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
                    item.setFlags(flags)
                    if isinstance(value, dict):
                        item.setText(1, "<dict>")
                        add_items(item, value)
                    elif isinstance(value, list):
                        item.setText(1, "<list>")
                        add_items(item, value)
                    else:
                        item.setText(1, str(value))
            elif isinstance(data, list):
                for i, value in enumerate(data):
                    item = QTreeWidgetItem(parent, [str(i), "" if isinstance(value, (dict, list)) else str(value)])
                    flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
                    item.setFlags(flags | Qt.ItemIsEditable if not isinstance(value, (dict, list)) else flags)
                    if isinstance(value, dict):
                        item.setText(1, "<dict>")
                        add_items(item, value)
                    elif isinstance(value, list):
                        item.setText(1, "<list>")
                        add_items(item, value)
                    else:
                        item.setText(1, str(value))
        add_items(tree.invisibleRootItem(), data)
        tree.itemDoubleClicked.connect(lambda item, col: self.start_edit_tracking(item, col))

    def update_json_previews(self):
        pygame.mixer.music.stop()
        for config in self.json_trees.values():
            self.close_preview(config["preview"])
        
        left_selected = self.json_trees["left"]["tree"].selectedItems()
        right_selected = self.json_trees["right"]["tree"].selectedItems()
        has_valid_preview = False
        
        for item in left_selected:
            if item.childCount() == 0 and item.text(1) != "<object>":
                self.preview_json_content(self.json_trees["left"]["tree"], item, self.json_trees["left"]["preview"])
                has_valid_preview = True
        
        for item in right_selected:
            if item.childCount() == 0 and item.text(1) != "<object>":
                self.preview_json_content(self.json_trees["right"]["tree"], item, self.json_trees["right"]["preview"])
                has_valid_preview = True
        
        if has_valid_preview:
            self.preview_splitter.show()
            for config in self.json_trees.values():
                if config["preview"].layout() and config["preview"].layout().count():
                    if config["preview"] not in self.preview_splitter.children():
                        self.preview_splitter.addWidget(config["preview"])
        else:
            self.preview_splitter.hide()

    def preview_json_content(self, tree, item, preview_widget):
        value = item.text(1)
        filepath = os.path.join("cached_files", value)
        self.display_preview(filepath, preview_widget)

    def preview_cache_content(self, file_name, preview_widget):
        self.close_preview(preview_widget)
        if not file_name:
            self.log_info("No file selected for preview")
            return
        filepath = os.path.join(tempfile.gettempdir(), 'Roblox', 'http', file_name)
        self.log_info(f"Previewing {file_name} at {filepath}")
        self.display_preview(filepath, preview_widget)
        self.cache_splitter.setSizes([800, 400])

    def convert_ktx_to_png(self, ktx_path):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_png:
            png_path = tmp_png.name
        
        cmd = ['pvrtextoolcli', '-i', ktx_path, '-noout', '-shh', '-d', png_path]
        try:
            subprocess.run(cmd, check=True)
            convert(png_path)
            return png_path
        except Exception as e:
            self.log_error(f"Failed to convert KTX to PNG: {e}")
            return None

    def convert_mesh_to_obj(self, data):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.obj') as tmp_obj:
            obj_path = tmp_obj.name
            try:
                mesh_processing.convert(data, output_path=obj_path)
                if os.path.exists(obj_path) and os.path.getsize(obj_path) > 0:
                    self.log_info(f"Successfully created OBJ file: {obj_path}")
                    return obj_path
                else:
                    self.log_error(f"OBJ file was not created or is empty: {obj_path}")
                    return None
            except Exception as e:
                self.log_error(f"Failed to convert mesh to OBJ: {e}")
                return None

    def display_3d_preview(self, obj_path, original_filepath, preview_frame):
        if not obj_path or not os.path.exists(obj_path):
            self.log_error(f"OBJ path invalid or file does not exist: {obj_path}")
            return self.display_file_info(original_filepath, self.temp_files[preview_frame][0], preview_frame)

        if preview_frame.layout():
            while preview_frame.layout().count():
                child = preview_frame.layout().takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
        else:
            layout = QVBoxLayout(preview_frame)

        layout = preview_frame.layout()

        file_name = os.path.basename(original_filepath)
        size = os.path.getsize(original_filepath)
        formatted_size = self.format_size(size)
        date = time.ctime(os.path.getmtime(original_filepath))
        ftype = self.get_file_type(original_filepath)

        layout.addWidget(QLabel(f"File Name: {file_name}"))
        layout.addWidget(QLabel(f"File Type: {ftype}"))
        layout.addWidget(QLabel(f"File Size: {formatted_size}"))
        layout.addWidget(QLabel(f"Modification Date: {date}"))

        try:
            plotter = QtInteractor(preview_frame)
            layout.addWidget(plotter.interactor)
            
            model = pv.read(obj_path)
            plotter.add_mesh(model)
            plotter.reset_camera()
            plotter.set_background('white')
            
            self.temp_files[preview_frame].append(plotter)
            
        except Exception as e:
            self.log_error(f"Error loading 3D model {obj_path}: {str(e)}")
            layout.addWidget(QLabel(f"Error loading 3D model: {str(e)}"))

        button_frame = QFrame()
        button_layout = QHBoxLayout(button_frame)
        button_layout.addWidget(QPushButton("Close Preview", clicked=self.get_close_command(preview_frame)))
        
        obj_button = QPushButton("Open OBJ Externally")
        obj_menu = QMenu(self)
        obj_menu.addAction(QAction("Open with Default", self, triggered=lambda: self.open_externally(obj_path)))
        obj_menu.addAction(QAction("Select Program", self, triggered=lambda: self.select_program_to_open(obj_path)))
        obj_button.setMenu(obj_menu)
        button_layout.addWidget(obj_button)
        
        layout.addWidget(button_frame)
        
        preview_frame.show()
        preview_frame.update()
        self.log_info(f"Displaying 3D preview for {file_name}")

    def select_program_to_open(self, filepath):
        if not os.path.exists(filepath):
            self.log_error(f"File does not exist: {filepath}")
            return
        
        program = QFileDialog.getOpenFileName(self, "Select Program to Open File", "", "Executable files (*.exe);;All files (*.*)")[0]
        if program:
            try:
                subprocess.run([program, filepath], check=True)
                self.log_info(f"Opened {filepath} with {program}")
            except Exception as e:
                self.log_error(f"Failed to open {filepath} with selected program: {e}")

    def display_preview(self, filepath, preview_widget):
        self.close_preview(preview_widget)
        if not filepath:
            self.log_info("No file selected for preview")
            return
        tempfile_name, ftype = self.export(filepath)
        if not tempfile_name or not ftype:
            self.log_error(f"Failed to export or determine type for {filepath}")
            return
        self.temp_files[preview_widget] = [tempfile_name]
        if preview_widget.layout():
            while preview_widget.layout().count():
                child = preview_widget.layout().takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
        else:
            layout = QVBoxLayout(preview_widget)
        layout = preview_widget.layout()
        
        if ftype in ["PNG", "GIF", "JFIF"]:
            self.display_image_preview(tempfile_name, filepath, preview_widget)
        elif ftype in ["OGG", "MP3"]:
            self._handle_audio(tempfile_name, preview_widget)
        elif ftype == "KTX":
            png_path = self.convert_ktx_to_png(tempfile_name)
            if png_path:
                self.temp_files[preview_widget].append(png_path)
                self.display_image_preview(png_path, filepath, preview_widget)
            else:
                self.display_file_info(filepath, tempfile_name, preview_widget)
        elif ftype.startswith("Mesh"):
            with open(tempfile_name, 'rb') as f:
                data = f.read()
            obj_path = self.convert_mesh_to_obj(data)
            if obj_path:
                self.temp_files[preview_widget].append(obj_path)
                self.display_3d_preview(obj_path, filepath, preview_widget)
            else:
                self.display_file_info(filepath, tempfile_name, preview_widget)
        elif ftype in ["Translation (JSON)", "TTF (JSON)", "JSON/VideoFrame"]:
            self.display_json_preview(tempfile_name, filepath, preview_widget)
        elif ftype in ["XML", "EXTM3U (VideoFrame)"]:
            self.display_text_preview(tempfile_name, filepath, preview_widget)
        else:
            self.display_file_info(filepath, tempfile_name, preview_widget)
        
        preview_widget.show()
        preview_widget.update()
        self.log_info(f"Cache preview visible: {preview_widget.isVisible()}")

    def _handle_audio(self, tempfile_name, preview_widget):
        player = AudioPlayer(self, tempfile_name, preview_widget)
        self.audio_players[preview_widget] = player

    def display_image_preview(self, tempfile_name, original_filepath, preview_frame):
        pixmap = QPixmap(tempfile_name).scaled(300, 300, Qt.KeepAspectRatio)
        layout = preview_frame.layout()
        layout.addWidget(QLabel(pixmap=pixmap))
        size = os.path.getsize(original_filepath)
        width = pixmap.width()
        height = pixmap.height()
        ftype = self.get_file_type(original_filepath)
        layout.addWidget(QLabel(f"Type: {ftype}, Size: {self.format_size(size)}, Dimensions: {width}x{height}"))
        
        button_frame = QFrame()
        button_layout = QHBoxLayout(button_frame)
        button_layout.addWidget(QPushButton("Close Preview", clicked=self.get_close_command(preview_frame)))
        button_layout.addWidget(QPushButton("Open Externally", clicked=lambda: self.open_externally(tempfile_name)))
        layout.addWidget(button_frame)
        preview_frame.show()
        preview_frame.update()

    def display_file_info(self, filepath, tempfile_name, preview_frame):
        file_name = os.path.basename(filepath)
        size = os.path.getsize(filepath)
        formatted_size = self.format_size(size)
        date = time.ctime(os.path.getmtime(filepath))
        ftype = self.get_file_type(filepath)
        
        layout = preview_frame.layout()
        layout.addWidget(QLabel(f"File Name: {file_name}"))
        layout.addWidget(QLabel(f"File Type: {ftype}"))
        layout.addWidget(QLabel(f"File Size: {formatted_size}"))
        layout.addWidget(QLabel(f"Modification Date: {date}"))
        
        button_frame = QFrame()
        button_layout = QHBoxLayout(button_frame)
        button_layout.addWidget(QPushButton("Close Preview", clicked=self.get_close_command(preview_frame)))
        button_layout.addWidget(QPushButton("Open Externally", clicked=lambda: self.open_externally(tempfile_name)))
        layout.addWidget(button_frame)
        preview_frame.show()
        preview_frame.update()

    def display_text_preview(self, tempfile_name, original_filepath, preview_frame):
        with open(tempfile_name, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        layout = preview_frame.layout()
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(content)
        layout.addWidget(text_edit)
        file_name = os.path.basename(original_filepath)
        size = os.path.getsize(original_filepath)
        formatted_size = self.format_size(size)
        date = time.ctime(os.path.getmtime(original_filepath))
        ftype = self.get_file_type(original_filepath)
        layout.addWidget(QLabel(f"File Name: {file_name}"))
        layout.addWidget(QLabel(f"File Type: {ftype}"))
        layout.addWidget(QLabel(f"File Size: {formatted_size}"))
        layout.addWidget(QLabel(f"Modification Date: {date}"))
        button_frame = QFrame()
        button_layout = QHBoxLayout(button_frame)
        button_layout.addWidget(QPushButton("Close Preview", clicked=self.get_close_command(preview_frame)))
        button_layout.addWidget(QPushButton("Open Externally", clicked=lambda: self.open_externally(tempfile_name)))
        layout.addWidget(button_frame)
        preview_frame.show()
        preview_frame.update()

    def display_json_preview(self, tempfile_name, original_filepath, preview_frame):
        try:
            with open(tempfile_name, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            formatted_json = json.dumps(json_data, indent=4)
        except Exception as e:
            formatted_json = f"Error loading JSON: {e}"
        layout = preview_frame.layout()
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(formatted_json)
        layout.addWidget(text_edit)
        file_name = os.path.basename(original_filepath)
        size = os.path.getsize(original_filepath)
        formatted_size = self.format_size(size)
        date = time.ctime(os.path.getmtime(original_filepath))
        ftype = self.get_file_type(original_filepath)
        layout.addWidget(QLabel(f"File Name: {file_name}"))
        layout.addWidget(QLabel(f"File Type: {ftype}"))
        layout.addWidget(QLabel(f"File Size: {formatted_size}"))
        layout.addWidget(QLabel(f"Modification Date: {date}"))
        button_frame = QFrame()
        button_layout = QHBoxLayout(button_frame)
        button_layout.addWidget(QPushButton("Close Preview", clicked=self.get_close_command(preview_frame)))
        button_layout.addWidget(QPushButton("Open Externally", clicked=lambda: self.open_externally(tempfile_name)))
        layout.addWidget(button_frame)
        preview_frame.show()
        preview_frame.update()

    def close_preview(self, preview_frame, deselect=False):
        if preview_frame in self.audio_players:
            self.audio_players[preview_frame].stop()
            del self.audio_players[preview_frame]
        if preview_frame.layout():
            while preview_frame.layout().count():
                child = preview_frame.layout().takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
        preview_frame.hide()
        if preview_frame in self.temp_files:
            for temp_item in self.temp_files[preview_frame]:
                if isinstance(temp_item, QtInteractor):
                    continue
                if os.path.exists(temp_item) and os.path.dirname(temp_item) == tempfile.gettempdir():
                    try:
                        os.remove(temp_item)
                        self.log_info(f"Deleted temporary file: {temp_item}")
                    except PermissionError as e:
                        self.log_error(f"Failed to delete temporary file {temp_item}: {e}")
                    except FileNotFoundError:
                        self.log_info(f"Temporary file {temp_item} already deleted")
                    except Exception as e:
                        self.log_error(f"Failed to delete temporary file {temp_item}: {e}")
            del self.temp_files[preview_frame]
        if deselect and self.current_mode == "cache":
            self.cache_tree.clearSelection()

    def get_close_command(self, preview_frame):
        if preview_frame in [self.json_preview_left, self.json_preview_right]:
            tree = self.json_trees["left"]["tree"] if preview_frame == self.json_preview_left else self.json_trees["right"]["tree"]
            return lambda: self.close_json_preview(tree)
        return lambda: self.close_preview(preview_frame, deselect=True)

    def close_json_preview(self, tree):
        tree.clearSelection()
        self.update_json_previews()

    def export(self, filepath):
        try:
            with open(filepath, 'rb') as f:
                data = f.read()
            
            reader = ByteReader(data)
            ident = reader.read_string(4)
            if ident != "RBXH":
                return None, "Unknown"
            
            reader.skip(4)
            link_len = reader.read_uint32()
            link = reader.read_string(link_len)
            reader.skip(1)
            req_status_code = reader.read_uint32()
            
            header_data_len = reader.read_uint32()
            reader.skip(4)
            file_size = reader.read_uint32()
            reader.skip(8 + header_data_len)
            cont = reader.read_bytes(file_size)
            
            begin = cont[:min(48, len(cont))].decode('utf-8', errors='ignore')
            if "<roblox!" in begin:
                ftype = "RBXM Animation"
                ext = ".rbxm"
            elif "<roblox xml" in begin:
                ftype = "XML"
                ext = ".xml"
            elif '"version' not in begin and "version" in begin:
                mesh_version = cont[:12].decode('utf-8')
                num_only_ver = mesh_version[8:]
                ftype = f"Mesh (v{num_only_ver})"
                ext = ".mesh"
            elif '{"locale":"' in begin:
                ftype = "Translation (JSON)"
                ext = ".json"
            elif "PNG\r\n" in begin:
                ftype = "PNG"
                ext = ".png"
            elif begin.startswith("GIF8"):
                ftype = "GIF"
                ext = ".gif"
            elif "JFIF" in begin:
                ftype = "JFIF"
                ext = ".jpg"
            elif "OggS" in begin:
                ftype = "OGG"
                ext = ".ogg"
            elif any(x in begin for x in ["TSSE", "Lavf", "matroska"]):
                ftype = "MP3"
                ext = ".mp3"
            elif "KTX " in begin:
                ftype = "KTX"
                ext = ".ktx"
            elif begin.startswith("#EXTM3U"):
                ftype = "EXTM3U (VideoFrame)"
                ext = ".m3u"
            elif '"name": "' in begin:
                ftype = "TTF (JSON)"
                ext = ".json"
            elif '{"applicationSettings' in begin or '{"version' in begin or "webmB" in begin:
                ftype = "JSON/VideoFrame"
                ext = ".json"
            else:
                ftype = "Unknown"
                ext = ".bin"
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
                tmp_file.write(cont)
                tempfile_name = tmp_file.name
                self.log_info(f"Exported {os.path.basename(filepath)} to {tempfile_name} as {ftype}")
                return tempfile_name, ftype
        except Exception as e:
            self.log_error(f"Failed to export {filepath}: {e}")
            return None, "Unknown"

    def get_file_type(self, filepath):
        try:
            with open(filepath, 'rb') as f:
                data = f.read()
            
            reader = ByteReader(data)
            ident = reader.read_string(4)
            if ident != "RBXH":
                return "Unknown"
            
            reader.skip(4)
            link_len = reader.read_uint32()
            link = reader.read_string(link_len)
            reader.skip(1)
            req_status_code = reader.read_uint32()
            
            if req_status_code in {301, 302, 303, 307, 308}:
                return f"Redirect ({req_status_code})"
            elif req_status_code != 200:
                return f"Error ({req_status_code})"
            
            header_data_len = reader.read_uint32()
            reader.skip(4)
            file_size = reader.read_uint32()
            reader.skip(8 + header_data_len)
            cont = reader.read_bytes(file_size)
            begin = cont[:min(48, len(cont))].decode('utf-8', errors='ignore')
            
            if "<roblox!" in begin:
                return "RBXM Animation"
            elif "<roblox xml" in begin:
                return "XML"
            elif '"version' not in begin and "version" in begin:
                mesh_version = cont[:12].decode('utf-8')
                num_only_ver = mesh_version[8:]
                return f"Mesh (v{num_only_ver})"
            elif '{"locale":"' in begin:
                return "Translation (JSON)"
            elif "PNG\r\n" in begin:
                return "PNG"
            elif begin.startswith("GIF8"):
                return "GIF"
            elif "JFIF" in begin:
                return "JFIF"
            elif "OggS" in begin:
                return "OGG"
            elif any(x in begin for x in ["TSSE", "Lavf", "matroska"]):
                return "MP3"
            elif "KTX " in begin:
                return "KTX"
            elif begin.startswith("#EXTM3U"):
                return "EXTM3U (VideoFrame)"
            elif '"name": "' in begin:
                return "TTF (JSON)"
            elif '{"applicationSettings' in begin or '{"version' in begin or "webmB" in begin:
                return "JSON/VideoFrame"
            else:
                return "Unknown"
        except Exception as e:
            self.log_error(f"File type error: {e}")
            return "Unknown"

    def format_size(self, size_in_bytes):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_in_bytes < 1024.0:
                return f"{size_in_bytes:.2f} {unit}"
            size_in_bytes /= 1024.0
        return f"{size_in_bytes:.2f} TB"

    def format_time(self, seconds):
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    def open_externally(self, filepath):
        self.log_info(f"Attempting to open externally: {filepath}")
        if not os.path.exists(filepath):
            self.log_error(f"File does not exist: {filepath}")
            return
        try:
            os.startfile(filepath)
            self.log_info(f"Successfully opened {filepath} externally")
        except Exception as e:
            self.log_error(f"Failed to open {filepath} externally: {e}")

    def set_json_mode(self):
        self.cache_frame.hide()
        self.outer_splitter.show()
        self.current_mode = "json"
        self.live_update_running = False
        self.update_json_previews()
        self.log_info("Switched to JSON Tree Mode")

    def set_cache_mode(self):
        self.outer_splitter.hide()
        self.cache_frame.show()
        self.current_mode = "cache"
        self.close_preview(self.cache_preview, deselect=True)
        self.log_info("Switched to Cache Previewer Mode")
        if not self.cache_populated:
            self.start_cache_loader()
        self.toggle_live_update()

    def start_cache_loader(self):
        cache_dir = os.path.join(tempfile.gettempdir(), 'Roblox', 'http')
        if not os.path.exists(cache_dir):
            self.log_error(f"Cache directory {cache_dir} does not exist")
            return
        self.cache_loader = CacheLoader(cache_dir)
        self.cache_loader.update_tree.connect(self.update_cache_tree)
        self.cache_loader.finished.connect(self.on_cache_loader_finished)
        self.cache_loader.start()

    def apply_filter(self, filter_type, mode, checked):
        self.update_cache_tree(self.cache_files)
        self.log_info(f"{'Added' if checked else 'Removed'} {mode} filter for {filter_type}")

    def update_cache_tree(self, cache_files):
        self.cache_tree.clear()
        self.cache_files = cache_files
        
        show_filters = {ftype for ftype, action in self.show_filter_actions.items() if action.isChecked()}
        exclude_filters = {ftype for ftype, action in self.exclude_filter_actions.items() if action.isChecked()}
        
        for file_data in cache_files:
            ftype = file_data[1]
            should_show = True
            
            if show_filters:
                should_show = any(f in ftype or ftype == f for f in show_filters)
            
            if should_show and exclude_filters:
                should_show = not any(f in ftype or ftype == f for f in exclude_filters)
            
            if should_show:
                self.cache_tree.addTopLevelItem(CacheTreeItem(list(file_data)))
        
        sort_column = self.cache_tree.sortColumn()
        sort_order = self.cache_tree.header().sortIndicatorOrder()
        self.cache_tree.sortItems(sort_column, sort_order)
        if self.cache_search_entry.text().strip():
            self.search_cache()

    def on_cache_loader_finished(self):
        self.cache_populated = True
        self.log_info(f"Loaded {len(self.cache_files)} cache files")

    def load_cache_files(self):
        temp_dir = tempfile.gettempdir()
        cache_dir = os.path.join(temp_dir, 'Roblox', 'http')
        if not os.path.exists(cache_dir):
            self.log_error(f"Cache directory {cache_dir} does not exist")
            return
        files = os.listdir(cache_dir)
        self.cache_tree.clear()
        self.cache_files = []
        for file in files:
            filepath = os.path.join(cache_dir, file)
            try:
                size = os.path.getsize(filepath)
                formatted_size = self.format_size(size)
                date = time.ctime(os.path.getmtime(filepath))
                ftype = self.get_file_type(filepath)
                file_data = (file, ftype, formatted_size, date)
                self.cache_files.append(file_data)
                self.cache_tree.addTopLevelItem(CacheTreeItem([file, ftype, formatted_size, date]))
            except FileNotFoundError:
                self.log_info(f"Skipping file {file} as it no longer exists")
                continue
            except Exception as e:
                self.log_error(f"Error processing file {file}: {e}")
                continue
        sort_column = self.cache_tree.sortColumn()
        sort_order = self.cache_tree.header().sortIndicatorOrder()
        self.cache_tree.sortItems(sort_column, sort_order)
        self.log_info(f"Loaded {len(self.cache_files)} cache files from {cache_dir}")
        if self.cache_search_entry.text().strip():
            self.search_cache()
        self.cache_populated = True

    def search_cache(self):
        query = self.cache_search_entry.text().strip().lower()
        if not query:
            self.update_cache_tree(self.cache_files)
            self.log_info("Search query is empty, showing all filtered cache files")
            return
        
        active_filters = [col for col, cb in self.cache_filter_vars.items() if cb.isChecked()]
        if not active_filters:
            self.log_info("No columns selected for search")
            return
        
        self.cache_tree.clear()
        matches = 0
        
        show_filters = {ftype for ftype, action in self.show_filter_actions.items() if action.isChecked()}
        exclude_filters = {ftype for ftype, action in self.exclude_filter_actions.items() if action.isChecked()}
        
        for file_data in self.cache_files:
            ftype = file_data[1]
            should_show = True
            if show_filters:
                should_show = any(f in ftype or ftype == f for f in show_filters)
            if should_show and exclude_filters:
                should_show = not any(f in ftype or ftype == f for f in exclude_filters)
            
            if should_show:
                for col in active_filters:
                    col_idx = {"Name": 0, "Type": 1, "Size": 2, "Date": 3}[col]
                    if query in str(file_data[col_idx]).lower():
                        self.cache_tree.addTopLevelItem(CacheTreeItem(list(file_data)))
                        matches += 1
                        break
        
        self.log_info(f"Found {matches} matches for '{query}' in {', '.join(active_filters)}")
        sort_column = self.cache_tree.sortColumn()
        sort_order = self.cache_tree.header().sortIndicatorOrder()
        self.cache_tree.sortItems(sort_column, sort_order)

    def refresh_cache(self):
        self.load_cache_files()
        self.log_info("Refreshed cache, reloaded all files")

    def clear_cache(self):
        temp_dir = tempfile.gettempdir()
        cache_dir = os.path.join(temp_dir, 'Roblox', 'http')
        if not os.path.exists(cache_dir):
            self.log_info("Cache directory does not exist")
            return
        try:
            for file in os.listdir(cache_dir):
                file_path = os.path.join(cache_dir, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            self.log_info("Cache cleared successfully")
            self.load_cache_files()
        except Exception as e:
            self.log_error(f"Failed to clear cache: {e}")

    def toggle_live_update(self):
        if self.current_mode == "cache" and self.live_update_cb.isChecked() and not self.live_update_running:
            self.live_update_running = True
            self.log_info("Live update started")
            QTimer.singleShot(2000, self.update_cache_live)
        elif not self.live_update_cb.isChecked():
            self.live_update_running = False
            self.log_info("Live update stopped")

    def update_cache_live(self):
        if self.current_mode != "cache" or not self.live_update_cb.isChecked():
            self.live_update_running = False
            return
        cache_dir = os.path.join(tempfile.gettempdir(), 'Roblox', 'http')
        if not os.path.exists(cache_dir):
            self.log_error(f"Cache directory {cache_dir} does not exist")
            self.live_update_running = False
            return
        try:
            current_files = {file_data[0] for file_data in self.cache_files}
            new_files = set(os.listdir(cache_dir)) - current_files
            if not new_files:
                QTimer.singleShot(2000, self.update_cache_live)
                return
            query = self.cache_search_entry.text().strip().lower()
            active_filters = [col for col, cb in self.cache_filter_vars.items() if cb.isChecked()]
            show_filters = {ftype for ftype, action in self.show_filter_actions.items() if action.isChecked()}
            exclude_filters = {ftype for ftype, action in self.exclude_filter_actions.items() if action.isChecked()}
            new_entries_added = 0
            added_file_names = []
            for file in new_files:
                filepath = os.path.join(cache_dir, file)
                try:
                    if not os.path.exists(filepath):
                        self.log_info(f"Skipping file {file} as it no longer exists")
                        continue
                    size = os.path.getsize(filepath)
                    formatted_size = self.format_size(size)
                    date = time.ctime(os.path.getmtime(filepath))
                    ftype = self.get_file_type(filepath)
                    file_data = (file, ftype, formatted_size, date)
                    self.cache_files.append(file_data)
                    should_show = True
                    if show_filters:
                        should_show = any(f in ftype or ftype == f for f in show_filters)
                    if should_show and exclude_filters:
                        should_show = not any(f in ftype or ftype == f for f in exclude_filters)
                    if should_show and (not query or not active_filters or any(query in str(file_data[{"Name": 0, "Type": 1, "Size": 2, "Date": 3}[col]]).lower() for col in active_filters)):
                        self.cache_tree.addTopLevelItem(CacheTreeItem(list(file_data)))
                        new_entries_added += 1
                        added_file_names.append(file)
                except FileNotFoundError as e:
                    self.log_info(f"Skipping file {file} due to FileNotFoundError: {e}")
                    continue
                except Exception as e:
                    self.log_error(f"Error processing file {file}: {e}")
                    continue
            if new_entries_added:
                sort_column = self.cache_tree.sortColumn()
                sort_order = self.cache_tree.header().sortIndicatorOrder()
                self.cache_tree.sortItems(sort_column, sort_order)
                self.log_info(f"Live update added {new_entries_added} new filtered cache entries: {', '.join(added_file_names)}")
        except Exception as e:
            self.log_error(f"Live update error: {e}")
        QTimer.singleShot(2000, self.update_cache_live)

    def log_error(self, message):
        self.cmd_line.append(f"ERROR: {message}")

    def log_info(self, message):
        self.cmd_line.append(f"INFO: {message}")

    def show_cache_menu(self, pos):
        selected = self.cache_tree.selectedItems()
        if not selected:
            return
        menu = QMenu(self)
        download_menu = menu.addMenu("Download")
        download_menu.addAction(QAction("Raw", self, triggered=lambda: self.download("raw")))
        download_menu.addAction(QAction("Converted", self, triggered=lambda: self.download("converted")))
        
        copy_menu = menu.addMenu("Copy")
        copy_menu.addAction(QAction("Copy Name", self, triggered=self.copy_name_to_clipboard))
        copy_menu.addAction(QAction("Copy File", self, triggered=self.copy_file_to_clipboard))
        copy_menu.addAction(QAction("Copy Path", self, triggered=self.copy_path_to_clipboard))
        
        menu.addAction(QAction("Go To", self, triggered=self.explore_here))
        
        delete_menu = menu.addMenu("Delete")
        delete_menu.addAction(QAction("Delete File" if len(selected) == 1 else "Delete Files", self, triggered=self.delete_selected_files))
        delete_menu.addAction(QAction("Delete Ingame", self, triggered=self.delete_ingame))
        
        menu.exec_(self.cache_tree.mapToGlobal(pos))

    def copy_name_to_clipboard(self):
        selected = self.cache_tree.selectedItems()
        if selected:
            file_name = selected[0].text(0)
            QApplication.clipboard().setText(file_name)
            self.log_info(f"Copied name '{file_name}' to clipboard")

    def copy_file_to_clipboard(self):
        selected = self.cache_tree.selectedItems()
        if not selected:
            self.log_info("No file selected to copy to clipboard")
            return
        file_name = selected[0].text(0)
        cache_dir = os.path.join(tempfile.gettempdir(), 'Roblox', 'http')
        file_path = os.path.join(cache_dir, file_name)
        
        if not os.path.exists(file_path):
            self.log_error(f"File does not exist: {file_path}")
            return
        
        try:
            command = f"powershell.exe -Command \"Set-Clipboard -LiteralPath '{file_path}'\""
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                self.log_info(f"Copied file '{file_name}' to clipboard")
            else:
                self.log_error(f"Failed to copy file to clipboard: {result.stderr}")
        except Exception as e:
            self.log_error(f"Error copying file to clipboard: {str(e)}")

    def copy_path_to_clipboard(self):
        selected = self.cache_tree.selectedItems()
        if selected:
            file_name = selected[0].text(0)
            cache_dir = os.path.join(tempfile.gettempdir(), 'Roblox', 'http')
            file_path = os.path.join(cache_dir, file_name)
            QApplication.clipboard().setText(file_path)
            self.log_info(f"Copied file path '{file_path}' to clipboard")

    def explore_here(self):
        selected = self.cache_tree.selectedItems()
        if selected:
            file_name = selected[0].text(0)
            cache_dir = os.path.join(tempfile.gettempdir(), 'Roblox', 'http')
            file_path = os.path.join(cache_dir, file_name)
            subprocess.Popen(['explorer', '/select,', file_path])
            self.log_info(f"Opened explorer and selected '{file_name}' at '{file_path}'")

    def download(self, mode):
        selected = self.cache_tree.selectedItems()
        if not selected:
            self.log_info("No files selected for download")
            return
        temp_dir = tempfile.gettempdir()
        cache_dir = os.path.join(temp_dir, 'Roblox', 'http')
        downloaded_files = []
        
        for item in selected:
            file_name = item.text(0)
            filepath = os.path.join(cache_dir, file_name)
            if mode == "raw":
                save_path = QFileDialog.getSaveFileName(self, "Save Raw File", file_name)[0]
                if save_path:
                    try:
                        shutil.copy(filepath, save_path)
                        downloaded_files.append(save_path)
                    except Exception as e:
                        self.log_error(f"Failed to download raw file '{file_name}': {e}")
            elif mode == "converted":
                tempfile_name, ftype = self.export(filepath)
                if tempfile_name and ftype:
                    if ftype.startswith("Mesh"):
                        with open(tempfile_name, 'rb') as f:
                            data = f.read()
                        obj_path = self.convert_mesh_to_obj(data)
                        if obj_path:
                            save_path = QFileDialog.getSaveFileName(self, "Save Converted File", f"{file_name}.obj", "OBJ files (*.obj)")[0]
                            if save_path:
                                shutil.copy(obj_path, save_path)
                                downloaded_files.append(save_path)
                                os.remove(obj_path)
                            os.remove(tempfile_name)
                    elif ftype == "KTX":
                        png_path = self.convert_ktx_to_png(tempfile_name)
                        if png_path:
                            save_path = QFileDialog.getSaveFileName(self, "Save Converted File", f"{file_name}.png", "PNG files (*.png)")[0]
                            if save_path:
                                shutil.copy(png_path, save_path)
                                downloaded_files.append(save_path)
                                os.remove(png_path)
                            os.remove(tempfile_name)
                    else:
                        ext = os.path.splitext(tempfile_name)[1]
                        save_path = QFileDialog.getSaveFileName(self, "Save Converted File", f"{file_name}{ext}")[0]
                        if save_path:
                            shutil.copy(tempfile_name, save_path)
                            downloaded_files.append(save_path)
                            os.remove(tempfile_name)
        
        if downloaded_files:
            self.log_info(f"Downloaded {len(downloaded_files)} files: {', '.join(downloaded_files)}")

    def delete_selected_files(self):
        selected = self.cache_tree.selectedItems()
        if not selected:
            return
        cache_dir = os.path.join(tempfile.gettempdir(), 'Roblox', 'http')
        deleted_files = []
        if self.cache_preview in self.audio_players:
            previewed_file = os.path.basename(self.audio_players[self.cache_preview].filepath)
            for item in selected:
                file_name = item.text(0)
                if file_name == previewed_file or os.path.join(cache_dir, file_name) == self.audio_players[self.cache_preview].filepath:
                    self.audio_players[self.cache_preview].stop()
                    del self.audio_players[self.cache_preview]
                    break
        for item in selected:
            file_name = item.text(0)
            filepath = os.path.join(cache_dir, file_name)
            try:
                os.remove(filepath)
                deleted_files.append(file_name)
            except Exception as e:
                self.log_error(f"Failed to delete {file_name}: {e}")
        for item in selected[:]:
            self.cache_tree.takeTopLevelItem(self.cache_tree.indexOfTopLevelItem(item))
        self.cache_files = [file_data for file_data in self.cache_files if file_data[0] not in deleted_files]
        self.log_info(f"Deleted {len(deleted_files)} files from disk")

    def delete_ingame(self):
        selected = self.cache_tree.selectedItems()
        if not selected:
            return
        cache_dir = os.path.join(tempfile.gettempdir(), 'Roblox', 'http')
        source_path = os.path.join("cached_files", "5873cfba79134ecfec6658f559d8f320")
        source_name = os.path.basename(source_path)
        if not os.path.exists(source_path):
            self.log_error(f"Source file {source_path} does not exist for ingame deletion")
            return
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        try:
            replaced_count = 0
            for item in selected:
                target_file_name = item.text(0)
                dest_path = os.path.join(cache_dir, target_file_name)
                shutil.copy2(source_path, dest_path)
                self.log_info(f"Replaced '{target_file_name}' with '{source_name}' for ingame deletion")
                replaced_count += 1
            self.log_info(f"Deleted {replaced_count} files ingame by replacing with '{source_name}'")
        except Exception as e:
            self.log_error(f"Failed to delete ingame: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    sys.exit(app.exec())