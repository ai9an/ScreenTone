import sys
import json
import os
import threading
from functools import partial
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSlider, QPushButton, QComboBox, QFileDialog, QSizePolicy
)
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPoint
import screen_brightness_control as sbc
import pystray
from PIL import Image, ImageDraw
import shutil

class BrightnessWorker(QThread):
    update_complete = pyqtSignal()
    def __init__(self, monitor_index, value):
        super().__init__()
        self.monitor_index = monitor_index
        self.value = value
    def run(self):
        try:
            sbc.set_brightness(self.value, display=self.monitor_index)
        except Exception as e:
            print(f"Failed to set brightness on monitor {self.monitor_index}: {e}")
        self.update_complete.emit()

def create_pystray_icon(app):
    size = 64
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill='white')
    def on_quit(icon, item):
        icon.stop()
        app.force_exit = True
        QApplication.quit()
    def on_show(icon, item):
        app.show()
        app.activateWindow()
    menu = pystray.Menu(
        pystray.MenuItem('Show', on_show),
        pystray.MenuItem('Exit', on_quit)
    )
    icon = pystray.Icon("ScreenTone", image, "ScreenTone", menu)
    return icon

class ScreenToneApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ScreenTone")
        self.setFixedSize(400, 210)
        self.setStyleSheet("background-color: #1e1e1e; color: white; font-size: 12px;")
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout()
        self.central_widget.setLayout(self.main_layout)
        self.monitor_sliders = []
        self.brightness_threads = []
        self.appdata_path = os.path.join(os.getenv("LOCALAPPDATA"), "ScreenTone")
        self.prefs_file = os.path.join(self.appdata_path, "user_prefs.json")
        self.presets_dir = os.path.join(self.appdata_path, "presets")
        self.preset_restore_dir = os.path.join(self.appdata_path, "presetrestore")
        os.makedirs(self.presets_dir, exist_ok=True)
        os.makedirs(self.preset_restore_dir, exist_ok=True)
        self.is_preset_saved = True
        self.force_exit = False
        self.brightness_update_timers = {}
        self.init_ui()
        self.loading_label = QLabel("Loading monitors...")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_layout.addWidget(self.loading_label)
        QTimer.singleShot(50, self.load_monitors)
        self.restore_window_position()
        self.show()
        self.init_pystray()

    def init_ui(self):
        self.dropdown = QComboBox()
        self.dropdown.currentIndexChanged.connect(self.apply_selected_preset)
        self.main_layout.insertWidget(0, self.dropdown)
        self.buttons_layout = QHBoxLayout()
        delete_button = QPushButton("Delete Preset")
        delete_button.clicked.connect(self.delete_selected_preset)
        save_button = QPushButton("Save Preset")
        save_button.clicked.connect(self.save_current_preset)
        for btn in (delete_button, save_button):
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setFixedHeight(28)
            self.buttons_layout.addWidget(btn)
        self.main_layout.insertLayout(1, self.buttons_layout)

    def init_pystray(self):
        self.pystray_icon = create_pystray_icon(self)
        threading.Thread(target=self.pystray_icon.run, daemon=True).start()

    def load_monitors(self):
        try:
            self.monitors = sbc.list_monitors()
            self.loading_label.hide()
            self.load_presets()
            # Clear any existing sliders
            for i in reversed(range(self.main_layout.count())):
                widget = self.main_layout.itemAt(i).widget()
                if widget and widget not in (self.dropdown,):
                    self.main_layout.removeWidget(widget)
                    widget.deleteLater()
            self.monitor_sliders.clear()
            for index, monitor in enumerate(self.monitors):
                label = QLabel(f"Monitor {index + 1}")
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setMinimum(0)
                slider.setMaximum(100)
                slider.setValue(sbc.get_brightness(display=index)[0])
                slider.valueChanged.connect(partial(self.monitor_slider_changed, index))
                self.main_layout.addWidget(label)
                self.main_layout.addWidget(slider)
                self.monitor_sliders.append(slider)
            QTimer.singleShot(50, self.load_user_prefs)
        except Exception as e:
            self.loading_label.setText(f"Error loading monitors: {e}")

    def monitor_slider_changed(self, monitor_index, value):
        if self.is_preset_saved:
            self.is_preset_saved = False
            self.dropdown.blockSignals(True)
            self.dropdown.clear()
            self.dropdown.addItem("UnsavedPreset")
            for preset in self.presets_list:
                self.dropdown.addItem(preset)
            self.dropdown.setCurrentIndex(0)
            self.dropdown.blockSignals(False)
        if monitor_index in self.brightness_update_timers:
            self.brightness_update_timers[monitor_index].stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda idx=monitor_index, val=value: self.set_brightness(idx, val))
        timer.start(50)
        self.brightness_update_timers[monitor_index] = timer

    def delete_selected_preset(self):
        preset_name = self.dropdown.currentText()
        if preset_name and preset_name != "UnsavedPreset":
            preset_path = os.path.join(self.presets_dir, preset_name)
            if os.path.exists(preset_path):
                try:
                    shutil.move(preset_path, os.path.join(self.preset_restore_dir, preset_name))
                except:
                    pass
                self.load_presets()
                self.reset_sliders_to_default()
                self.dropdown.blockSignals(True)
                self.dropdown.clear()
                self.dropdown.addItem("UnsavedPreset")
                for preset in self.presets_list:
                    self.dropdown.addItem(preset)
                self.dropdown.setCurrentIndex(0)
                self.dropdown.blockSignals(False)
                self.is_preset_saved = False

    def reset_sliders_to_default(self):
        default_value = 50
        for slider in self.monitor_sliders:
            slider.blockSignals(True)
            slider.setValue(default_value)
            slider.blockSignals(False)
        for idx in range(len(self.monitor_sliders)):
            self.set_brightness(idx, default_value)

    def set_brightness(self, monitor_index, value):
        worker = BrightnessWorker(monitor_index, value)
        worker.update_complete.connect(lambda: self.cleanup_thread(worker))
        self.brightness_threads.append(worker)
        worker.start()

    def cleanup_thread(self, thread):
        if thread in self.brightness_threads:
            self.brightness_threads.remove(thread)

    def load_presets(self):
        os.makedirs(self.presets_dir, exist_ok=True)
        self.presets_list = []
        self.dropdown.clear()
        for file in os.listdir(self.presets_dir):
            if file.endswith(".json"):
                self.presets_list.append(file)
        self.presets_list.sort()
        for preset in self.presets_list:
            self.dropdown.addItem(preset)

    def apply_selected_preset(self):
        preset_name = self.dropdown.currentText()
        if not preset_name or preset_name == "UnsavedPreset":
            return
        try:
            with open(os.path.join(self.presets_dir, preset_name), "r") as f:
                values = json.load(f)
            for i, value in enumerate(values):
                if i < len(self.monitor_sliders):
                    self.monitor_sliders[i].blockSignals(True)
                    self.monitor_sliders[i].setValue(value)
                    self.monitor_sliders[i].blockSignals(False)
                    self.set_brightness(i, value)
            self.is_preset_saved = True
        except:
            pass

    def save_current_preset(self):
        preset_name, _ = QFileDialog.getSaveFileName(self, "Save Preset", self.presets_dir, "JSON Files (*.json)")
        if preset_name:
            data = [slider.value() for slider in self.monitor_sliders]
            with open(preset_name if preset_name.endswith(".json") else preset_name + ".json", "w") as f:
                json.dump(data, f)
            self.is_preset_saved = True
            self.load_presets()
            base = os.path.basename(preset_name)
            self.dropdown.blockSignals(True)
            self.dropdown.setCurrentText(base)
            self.dropdown.blockSignals(False)

    def load_user_prefs(self):
        if os.path.exists(self.prefs_file):
            try:
                with open(self.prefs_file, "r") as f:
                    prefs = json.load(f)
                    levels = prefs.get("brightness_levels", [])
                    matched_preset = None
                    for preset_file in self.presets_list:
                        preset_path = os.path.join(self.presets_dir, preset_file)
                        try:
                            with open(preset_path, "r") as pf:
                                preset_values = json.load(pf)
                            if preset_values == levels:
                                matched_preset = preset_file
                                break
                        except:
                            pass
                    for i, value in enumerate(levels):
                        if i < len(self.monitor_sliders):
                            self.monitor_sliders[i].blockSignals(True)
                            self.monitor_sliders[i].setValue(value)
                            self.monitor_sliders[i].blockSignals(False)
                            self.set_brightness(i, value)
                    self.dropdown.blockSignals(True)
                    if matched_preset:
                        self.dropdown.setCurrentText(matched_preset)
                        self.is_preset_saved = True
                    else:
                        self.dropdown.clear()
                        self.dropdown.addItem("UnsavedPreset")
                        for preset in self.presets_list:
                            self.dropdown.addItem(preset)
                        self.dropdown.setCurrentIndex(0)
                        self.is_preset_saved = False
                    self.dropdown.blockSignals(False)
            except:
                pass

    def restore_window_position(self):
        if os.path.exists(self.prefs_file):
            try:
                with open(self.prefs_file, "r") as f:
                    data = json.load(f)
                    x, y = data.get("window_position", [None, None])
                    if x is not None and y is not None:
                        self.move(QPoint(x, y))
                    else:
                        self.position_bottom_right_with_margin()
            except:
                self.position_bottom_right_with_margin()
        else:
            self.position_bottom_right_with_margin()

    def position_bottom_right_with_margin(self):
        screen = QApplication.primaryScreen().geometry()
        x = screen.width() - self.width() - 20
        y = screen.height() - self.height() - 60
        self.move(x, y)

    def save_user_prefs(self):
        try:
            prefs = {
                "window_position": [self.x(), self.y()],
                "brightness_levels": [s.value() for s in self.monitor_sliders]
            }
            with open(self.prefs_file, "w") as f:
                json.dump(prefs, f)
        except:
            pass

    def closeEvent(self, event):
        self.save_user_prefs()
        if not self.force_exit:
            event.ignore()
            self.hide()
        else:
            event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ScreenToneApp()
    sys.exit(app.exec())
