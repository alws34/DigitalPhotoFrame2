import json
import logging
import platform
import socket
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any, Optional

import psutil

try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None

if TYPE_CHECKING:
    from paho.mqtt.client import Client as MqttClient
else:
    MqttClient = Any

try:
    from Utilities.network_utils import get_local_ip as _get_local_ip_util
except Exception:
    _get_local_ip_util = None


# Dotted paths that must never be exposed as MQTT entities
MQTT_SKIP_PATHS = {
    "mqtt.base_topic", "mqtt.client_id", "mqtt.discovery_prefix",
    "mqtt.enabled", "mqtt.host", "mqtt.password", "mqtt.port",
    "mqtt.retain_config", "mqtt.tls", "mqtt.username", "mqtt.discovery",
    "mqtt.interval_seconds",
    "backend_configs.supersecretkey", "backend_configs.host",
    "backend_configs.server_port", "backend_configs.stream_height",
    "backend_configs.stream_width", "backend_configs.idle_fps",
    "system.image_dir", "system.log_file_path", "system.service_name",
    "system.image_quality_encoding",
    "autoupdate.repo_path", "autoupdate.remote", "autoupdate.branch",
    "autoupdate.hour", "autoupdate.minute", "autoupdate.shallow_ok",
    "albums.active_album_id",  # handled by the dedicated album select entity
    "about.image_path", "about.text", "about.version",
    "ui.font_name", "ui.date_format",
    "open_meteo.timeformat", "open_meteo.timezone",
    "open_meteo.latitude", "open_meteo.longitude",
    # brightness has its own dedicated entity with custom topic
    "screen.brightness",
}


def _iter_schema_leaves(schema: dict, prefix: str = "") -> "list[tuple[str, dict]]":
    """Recursively yield (dotted_path, leaf_descriptor) for all typed leaves."""
    results = []
    for key, value in schema.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            if "type" in value:
                results.append((path, value))
            else:
                results.extend(_iter_schema_leaves(value, path))
    return results


class MqttBridge:
    """
    Publishes (state topic JSON):
      ip, cpu_usage, cpu_temp_c, ram_percent, ram_used_mb, ram_total_mb,
      wifi_ssid, uptime_s, brightness

    Home Assistant discovery entities:
      - Sensors for all stats above
      - number: Screen Brightness (10..100)
      - switch: Screen Power
      - button: Pull Update
      - switch: Service (ON=start, OFF=stop)
      - button: Restart Service
      - Schema-driven entities for all exposed settings
      - select: Active Album (dynamic, from AlbumManager)

    Settings (settings['mqtt']):
      enabled: true|false
      host: "192.168.1.10"
      port: 1883
      username: ""         (optional)
      password: ""         (optional)
      tls: false           (optional)
      client_id: "photoframe-<hostname>" (optional)
      base_topic: "photoframe"           (optional)
      discovery: true
      discovery_prefix: "homeassistant"  (optional)
      interval_seconds: 1
      retain_config: true
    """

    # ---------- lifecycle ----------
    def __init__(self, view, settings: dict, album_manager=None):
        self.view = view
        self.settings = settings or {}
        self.cfg = (self.settings.get("mqtt") or {}).copy()
        self._album_manager = album_manager

        self.enabled = bool(self.cfg.get("enabled", False))
        self.host = self.cfg.get("host", "127.0.0.1")
        self.port = int(self.cfg.get("port", 1883))
        self.username = (self.cfg.get("username") or None)
        self.password = (self.cfg.get("password") or None)
        self.tls = bool(self.cfg.get("tls", False))

        node = (platform.node() or "device").lower()
        self.client_id = self.cfg.get("client_id") or f"photoframe-{node}"
        self.base_topic = self._clean_topic(self.cfg.get("base_topic") or "photoframe")
        self.discovery = bool(self.cfg.get("discovery", True))
        self.discovery_prefix = self._clean_topic(self.cfg.get("discovery_prefix") or "homeassistant")
        self.retain_config = bool(self.cfg.get("retain_config", True))
        self.interval = int(self.cfg.get("interval_seconds", 10))

        self.device_name = "Digital Photo Frame"
        self.service_name = self.settings.get("service_name", "photoframe")
        self.device_id = self.client_id

        # topics
        self.t_avail = f"{self.base_topic}/{self.device_id}/status"
        self.t_state = f"{self.base_topic}/{self.device_id}/state"
        self.t_brightness_state = f"{self.base_topic}/{self.device_id}/brightness"
        self.t_service_state = f"{self.base_topic}/{self.device_id}/service_state"
        self.t_cmd_brightness = f"{self.base_topic}/{self.device_id}/cmd/brightness"
        self.t_cmd_update   = f"{self.base_topic}/{self.device_id}/cmd/update"
        self.t_cmd_restart  = f"{self.base_topic}/{self.device_id}/cmd/restart"
        self.t_cmd_service  = f"{self.base_topic}/{self.device_id}/cmd/service"
        self.t_cmd_screen   = f"{self.base_topic}/{self.device_id}/cmd/screen"

        self._ext_ip_cache = (None, 0)

        self._last_nonzero_brightness = 60

        self.client: Optional[MqttClient] = None
        self.connected = False
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self._start_ts = time.time()

        # Watchdog
        self.last_connect_time = 0
        self.last_disconnect_time = 0
        self.WATCHDOG_TIMEOUT = 60  # seconds


    def start(self):
        if not self.enabled:
            self._log("MQTT disabled; not starting.", logging.INFO)
            return
        if mqtt is None:
            self._log("paho-mqtt not installed. Run: pip install paho-mqtt", logging.ERROR)
            return
        if self.thread and self.thread.is_alive():
            return

        try:
            self.client = mqtt.Client(client_id=self.client_id, clean_session=True)
        except TypeError:
            self.client = mqtt.Client(client_id=self.client_id)

        if self.username:
            self.client.username_pw_set(self.username, self.password or None)
        if self.tls:
            try:
                self.client.tls_set()
            except Exception as e:
                self._log(f"TLS setup failed: {e}", logging.ERROR)

        # LWT
        self.client.will_set(self.t_avail, payload="offline", qos=1, retain=True)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        try:
            self.client.connect_async(self.host, self.port, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            self._log(f"MQTT connect failed: {e}", logging.ERROR)
            return

        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._log("MQTT bridge started.", logging.INFO)

    def stop(self):
        self.stop_event.set()
        try:
            if self.client:
                self._publish(self.t_avail, "offline", retain=True)
                self.client.loop_stop()
                self.client.disconnect()
        except Exception:
            pass

    def _async(self, fn, *args, **kwargs):
        threading.Thread(target=lambda: fn(*args, **kwargs), daemon=True).start()

    # ---------- callbacks ----------
    def _on_connect(self, _c, _u, _f, rc):
        self.connected = (rc == 0)
        self.last_connect_time = time.time()
        if not self.connected:
            self._log(f"MQTT connect rc={rc}", logging.ERROR)
            return

        self._log("Connected to MQTT broker.", logging.INFO)
        self._publish(self.t_avail, "online", retain=True)

        # subscribe commands
        self.client.subscribe(self.t_cmd_brightness, qos=1)
        self.client.subscribe(self.t_cmd_update, qos=1)
        self.client.subscribe(self.t_cmd_restart, qos=1)
        self.client.subscribe(self.t_cmd_service, qos=1)
        self.client.subscribe(self.t_cmd_screen, qos=1)
        # subscribe schema-driven settings commands
        self.client.subscribe(f"{self.base_topic}/{self.device_id}/cmd/settings/#", qos=1)
        # subscribe album command
        self.client.subscribe(f"{self.base_topic}/{self.device_id}/cmd/albums/active", qos=1)

        if self.discovery:
            self._publish_discovery_all()

        # initial states
        self._publish_immediate_states()

    def _on_disconnect(self, _c, _u, rc):
        self.connected = False
        self.last_disconnect_time = time.time()
        if rc != 0:
            self._log(f"Unexpected disconnect rc={rc}", logging.WARNING)

    def _on_message(self, _c, _u, msg):
        topic = msg.topic
        payload = (msg.payload or b"").decode("utf-8", "ignore").strip()
        self._log(f"RX {topic} = {payload}", logging.INFO)

        if topic == self.t_cmd_brightness:
            self._async(self._handle_cmd_brightness, payload)
        elif topic == self.t_cmd_update:
            self._async(self._handle_cmd_update)
        elif topic == self.t_cmd_restart:
            self._async(self._handle_cmd_restart)
        elif topic == self.t_cmd_service:
            self._async(self._handle_cmd_service, payload)
        elif topic == self.t_cmd_screen:
            self._async(self._handle_cmd_screen, payload)
        elif topic == f"{self.base_topic}/{self.device_id}/cmd/albums/active":
            self._async(self._handle_album_cmd, payload)
        elif topic.startswith(f"{self.base_topic}/{self.device_id}/cmd/settings/"):
            dotted_path = topic.split("/cmd/settings/", 1)[1]
            self._async(self._handle_setting_cmd, dotted_path, payload)

    def _handle_cmd_screen(self, payload: str):
        target = (payload or "").strip().lower()
        if target not in ("on", "off"):
            self._log(f"Unknown screen payload: {payload!r}", logging.WARNING)
            return

        if target == "off":
            # go to 0%
            self._handle_cmd_brightness("0")
        else:
            # restore last nonzero brightness (fall back to 60)
            pct = max(10, min(100, int(self._last_nonzero_brightness or 60)))
            self._handle_cmd_brightness(str(pct))

    # ---------- schema-driven settings command handler ----------
    def _handle_setting_cmd(self, dotted_path: str, payload: str) -> None:
        from Utilities.config_events import notify_settings_changed
        from Utilities.config_store import (
            get_field_schema,
            load_settings,
            save_settings,
        )

        schema = get_field_schema(dotted_path)
        if schema is None:
            self._log(f"Unknown settings path: {dotted_path!r}", logging.WARNING)
            return

        if dotted_path in MQTT_SKIP_PATHS:
            self._log(f"Settings path {dotted_path!r} is on skip list; ignoring.", logging.WARNING)
            return

        stype = schema["type"]
        # parse value
        if stype == "bool":
            value = payload.lower() in ("true", "on", "1", "yes")
        elif stype == "int":
            try:
                value = int(payload)
            except ValueError:
                self._log(f"Invalid int payload for {dotted_path}: {payload!r}", logging.WARNING)
                return
        elif stype == "float":
            try:
                value = float(payload)
            except ValueError:
                self._log(f"Invalid float payload for {dotted_path}: {payload!r}", logging.WARNING)
                return
        elif stype in ("enum", "color"):
            if payload not in schema.get("choices", []):
                self._log(
                    f"Invalid choice {payload!r} for {dotted_path}; "
                    f"valid: {schema.get('choices', [])}", logging.WARNING
                )
                return
            value = payload
        else:
            self._log(f"Settings type {stype!r} for {dotted_path} is not writable via MQTT.", logging.WARNING)
            return

        # apply
        parts = dotted_path.split(".")
        settings = load_settings()
        node = settings
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
        save_settings(settings)

        # hot-reload
        notify_settings_changed(settings)

        # publish updated state back
        state_topic = f"{self.base_topic}/{self.device_id}/settings/{dotted_path}"
        self._publish(state_topic, payload, retain=True)
        self._log(f"Setting {dotted_path} updated to {value!r}", logging.INFO)

    # ---------- album command handlers ----------
    def _get_albums(self) -> list:
        if self._album_manager is None:
            return []
        try:
            return self._album_manager.get_albums()
        except Exception as e:
            self._log(f"get_albums() failed: {e}", logging.ERROR)
            return []

    def _handle_album_cmd(self, album_name: str) -> None:
        if self._album_manager is None:
            return
        if album_name == "All Photos":
            self._album_manager.set_active_album("all")
        else:
            albums = self._get_albums()
            match = next((a for a in albums if a["name"] == album_name), None)
            if match:
                self._album_manager.set_active_album(match["id"])
            else:
                self._log(f"Album not found: {album_name!r}", logging.WARNING)
                return
        self._publish_album_state()

    def _publish_album_state(self) -> None:
        if self._album_manager is None:
            return
        try:
            album_id = self._album_manager.get_active_album_id()
            if album_id == "all" or not album_id:
                name = "All Photos"
            else:
                albums = self._get_albums()
                match = next((a for a in albums if a["id"] == album_id), None)
                name = match["name"] if match else "All Photos"
            self._publish(
                f"{self.base_topic}/{self.device_id}/albums/active",
                name,
                retain=True,
            )
        except Exception as e:
            self._log(f"_publish_album_state failed: {e}", logging.ERROR)

    # ---------- worker ----------
    def _run(self):
        while not self.stop_event.is_set():
            try:
                # Watchdog check
                if not self.connected:
                    now = time.time()
                    ref_time = max(self.last_disconnect_time, self._start_ts)
                    if (now - ref_time) > self.WATCHDOG_TIMEOUT:
                        self._log(
                            f"Watchdog: Disconnected for > {self.WATCHDOG_TIMEOUT}s. Forcing reconnect...",
                            logging.WARNING,
                        )
                        try:
                            self.client.reconnect()
                            self.last_disconnect_time = now
                        except Exception as e:
                            self._log(f"Watchdog reconnect failed: {e}", logging.ERROR)
                            self.last_disconnect_time = now

                if self.connected:
                    self._publish_stats()
                    self._publish_brightness_state()
                    self._publish_service_state()
            except Exception as e:
                self._log(f"tick failed: {e}", logging.ERROR)

            self.stop_event.wait(self.interval)

    # ---------- discovery ----------
    def _publish_discovery_all(self):
        dev = self._device_payload()
        dp = self.discovery_prefix
        did = self.device_id

        def pub(topic, payload):
            body = json.dumps(payload)
            self._log(f"Publish discovery: {topic} -> {body}", logging.INFO)
            self._publish(topic, body, retain=self.retain_config)

        # Helper: only include state_class if not None
        def sensor(object_id, name, vt, unit=None, device_class=None, icon=None, state_class=None, extra=None, unique_suffix=""):
            uid = f"{did}_{object_id}{unique_suffix}"
            cfg = {
                "name": name,
                "unique_id": uid,
                "state_topic": self.t_state,
                "availability_topic": self.t_avail,
                "value_template": vt,
                "device": dev,
            }
            if unit is not None:
                cfg["unit_of_measurement"] = unit
            if device_class:
                cfg["device_class"] = device_class
            if state_class:  # only add for numeric sensors
                cfg["state_class"] = state_class
            if icon:
                cfg["icon"] = icon
            if isinstance(extra, dict):
                cfg.update(extra)
            pub(f"{dp}/sensor/{did}/{object_id}/config", cfg)

        # --- Screen brightness slider (dedicated topic, keep as-is) ---
        number_brightness = {
            "name": "Screen Brightness",
            "unique_id": f"{did}_screen_brightness",
            "state_topic": self.t_brightness_state,
            "command_topic": self.t_cmd_brightness,
            "availability_topic": self.t_avail,
            "min": 0,
            "max": 100,
            "step": 1,
            "unit_of_measurement": "%",
            "mode": "slider",
            "device": dev,
            "icon": "mdi:brightness-6",
        }
        pub(f"{dp}/number/{did}/screen_brightness/config", number_brightness)

        # --- Screen power switch ---
        switch_screen = {
            "name": "Screen Power",
            "unique_id": f"{did}_screen_power",
            "state_topic": self.t_brightness_state,
            "command_topic": self.t_cmd_screen,
            "availability_topic": self.t_avail,
            "payload_on": "on",
            "payload_off": "off",
            "value_template": "{{ 'on' if (value|int(0)) > 0 else 'off' }}",
            "device": dev,
            "icon": "mdi:monitor",
        }
        pub(f"{dp}/switch/{did}/screen_power/config", switch_screen)

        # --- Update / restart / service ---
        button_update = {
            "name": "Pull Update",
            "unique_id": f"{did}_pull_update",
            "command_topic": self.t_cmd_update,
            "availability_topic": self.t_avail,
            "device": dev,
            "icon": "mdi:update",
        }
        pub(f"{dp}/button/{did}/pull_update/config", button_update)

        button_restart = {
            "name": "Restart Service",
            "unique_id": f"{did}_restart_service",
            "command_topic": self.t_cmd_restart,
            "availability_topic": self.t_avail,
            "device": dev,
            "icon": "mdi:restart",
        }
        pub(f"{dp}/button/{did}/restart_service/config", button_restart)

        switch_service = {
            "name": "Service",
            "unique_id": f"{did}_service",
            "state_topic": self.t_service_state,
            "command_topic": self.t_cmd_service,
            "availability_topic": self.t_avail,
            "payload_on": "ON",
            "payload_off": "OFF",
            "device": dev,
            "icon": "mdi:application-cog",
        }
        pub(f"{dp}/switch/{did}/service/config", switch_service)

        # --- Diagnostic sensors ---
        sensor(
            "external_ip", "External IP", "{{ value_json.external_ip }}",
            icon="mdi:web",
            extra={"entity_category": "diagnostic"},
            unique_suffix="_v2",
        )
        sensor("cpu", "CPU Usage", "{{ value_json.cpu_usage }}", unit="%", device_class="power_factor")
        sensor("cputemp", "CPU Temperature", "{{ value_json.cpu_temp_c }}", unit="°C", device_class="temperature")
        sensor("ram_pct", "RAM Usage", "{{ value_json.ram_percent }}", unit="%", device_class="power_factor")
        sensor("ram_used", "RAM Used", "{{ value_json.ram_used_mb }}", unit="MB", icon="mdi:memory")
        sensor("ram_total", "RAM Total", "{{ value_json.ram_total_mb }}", unit="MB", icon="mdi:memory")
        sensor(
            "ssid", "Wi-Fi SSID", "{{ value_json.wifi_ssid }}",
            icon="mdi:wifi",
            extra={"entity_category": "diagnostic"},
            unique_suffix="_v2",
        )
        sensor("uptime", "Uptime", "{{ value_json.uptime_s }}", unit="s", icon="mdi:timer-outline")
        sensor("brightness", "Screen Brightness", "{{ value_json.brightness }}", unit="%", icon="mdi:brightness-6")

        # --- Schema-driven settings entities ---
        self._publish_schema_discovery(pub, dev)

        # --- Dynamic album select entity ---
        self._publish_album_select_discovery(pub, dev)

    def _publish_schema_discovery(self, pub, dev) -> None:
        """Publish HA discovery for all SETTINGS_SCHEMA leaves that aren't skipped."""
        from Utilities.config_store import SETTINGS_SCHEMA

        dp = self.discovery_prefix
        did = self.device_id

        for dotted_path, leaf in _iter_schema_leaves(SETTINGS_SCHEMA):
            if dotted_path in MQTT_SKIP_PATHS:
                continue

            stype = leaf.get("type", "")
            if stype in ("str", "password", "numeric_string"):
                continue  # not safe/useful via MQTT

            label = leaf.get("label", dotted_path)
            # Use dotted_path as object_id (replace dots with underscores for safety)
            obj_id = dotted_path.replace(".", "_")
            unique_id = f"{did}_setting_{obj_id}"
            state_topic = f"{self.base_topic}/{did}/settings/{dotted_path}"
            cmd_topic = f"{self.base_topic}/{did}/cmd/settings/{dotted_path}"

            base_cfg = {
                "name": label,
                "unique_id": unique_id,
                "state_topic": state_topic,
                "command_topic": cmd_topic,
                "availability_topic": self.t_avail,
                "device": dev,
                "retain": True,
            }

            if stype == "bool":
                cfg = dict(base_cfg)
                cfg["payload_on"] = "true"
                cfg["payload_off"] = "false"
                pub(f"{dp}/switch/{did}/setting_{obj_id}/config", cfg)

            elif stype in ("int", "float"):
                cfg = dict(base_cfg)
                if "min" in leaf:
                    cfg["min"] = leaf["min"]
                if "max" in leaf:
                    cfg["max"] = leaf["max"]
                if "step" in leaf:
                    cfg["step"] = leaf["step"]
                pub(f"{dp}/number/{did}/setting_{obj_id}/config", cfg)

            elif stype in ("enum", "color"):
                cfg = dict(base_cfg)
                cfg["options"] = leaf.get("choices", [])
                pub(f"{dp}/select/{did}/setting_{obj_id}/config", cfg)

    def _publish_album_select_discovery(self, pub, dev) -> None:
        """Publish HA select entity for the active album (dynamic options)."""
        did = self.device_id
        dp = self.discovery_prefix

        albums = self._get_albums()
        options = ["All Photos"] + [a["name"] for a in albums]

        cfg = {
            "name": "Active Album",
            "unique_id": f"{did}_active_album",
            "state_topic": f"{self.base_topic}/{did}/albums/active",
            "command_topic": f"{self.base_topic}/{did}/cmd/albums/active",
            "availability_topic": self.t_avail,
            "options": options,
            "device": dev,
            "icon": "mdi:image-album",
        }
        pub(f"{dp}/select/{did}/active_album/config", cfg)

    def _device_payload(self):
        sw = "PhotoFrame 2.0"
        try:
            v = self.settings.get("version") or ""
            if v:
                sw = f"{sw} ({v})"
        except Exception:
            pass
        return {
            "identifiers": [self.device_id],
            "name": self.device_name,
            "manufacturer": "PhotoFrame",
            "model": "Digital Photo Frame",
            "sw_version": sw,
        }

    # ---------- state + stats ----------
    def _publish_immediate_states(self):
        self._publish_brightness_state()
        self._publish_service_state()
        self._publish_stats()
        self._publish_all_settings_states()
        self._publish_album_state()

    def _publish_all_settings_states(self) -> None:
        """Publish current value of every schema-driven setting entity."""
        from Utilities.config_store import SETTINGS_SCHEMA, load_settings

        try:
            current_settings = load_settings()
        except Exception as e:
            self._log(f"_publish_all_settings_states: load_settings failed: {e}", logging.ERROR)
            return

        for dotted_path, leaf in _iter_schema_leaves(SETTINGS_SCHEMA):
            if dotted_path in MQTT_SKIP_PATHS:
                continue
            stype = leaf.get("type", "")
            if stype in ("str", "password", "numeric_string"):
                continue

            # Resolve value from settings dict
            parts = dotted_path.split(".")
            node = current_settings
            try:
                for p in parts:
                    node = node[p]
                value = node
            except (KeyError, TypeError):
                continue

            # Serialize to MQTT payload
            if stype == "bool":
                payload = "true" if bool(value) else "false"
            else:
                payload = str(value)

            state_topic = f"{self.base_topic}/{self.device_id}/settings/{dotted_path}"
            self._publish(state_topic, payload, retain=True)

    def _publish_stats(self):
        self._publish(self.t_state, json.dumps(self._stats()), qos=1, retain=False)

    def _stats(self):
        ip = str(self._get_local_ip() or "")
        ext_ip = self._get_external_ip() or ""
        cpu_pct = int(psutil.cpu_percent(interval=0))
        ram = psutil.virtual_memory()
        ram_used = ram.used // (1024 * 1024)
        ram_total = ram.total // (1024 * 1024)
        ram_percent = int(ram.percent)

        # CPU temperature
        try:
            temps = psutil.sensors_temperatures() or {}
            cand = None
            for k in ("cpu_thermal", "coretemp", "soc_thermal"):
                if k in temps and temps[k]:
                    cand = temps[k][0]
                    break
            cpu_temp = round(float(cand.current), 1) if cand else None
        except Exception:
            cpu_temp = None

        ssid = str(self._get_current_ssid() or "")
        brightness = self._read_brightness_percent()

        return {
            "ip": ip,
            "external_ip": ext_ip,
            "cpu_usage": cpu_pct,
            "cpu_temp_c": cpu_temp,
            "ram_percent": ram_percent,
            "ram_used_mb": ram_used,
            "ram_total_mb": ram_total,
            "wifi_ssid": ssid,
            "uptime_s": int(time.time() - self._start_ts),
            "brightness": brightness,
        }

    # ---------- brightness ----------
    def _resolve_screen_ctrl(self):
        """Return an object with set_brightness_percent for the current view.

        Priority:
          1. view.screen_ctrl  — Qt mode (ScreenController instance)
          2. view itself       — pygame mode (PhotoFramePygame has the method directly)
          3. view.screen       — future-proof fallback
        """
        sc = getattr(self.view, "screen_ctrl", None)
        if sc is not None and hasattr(sc, "set_brightness_percent"):
            return sc
        if self.view is not None and hasattr(self.view, "set_brightness_percent"):
            return self.view
        sc = getattr(self.view, "screen", None)
        if sc is not None and hasattr(sc, "set_brightness_percent"):
            return sc
        return None

    def _read_brightness_percent(self) -> Optional[int]:
        # Prefer ScreenController if it exposes a reader
        try:
            sc = self._resolve_screen_ctrl()
            if sc and hasattr(sc, "read_brightness_percent"):
                pct = sc.read_brightness_percent()
                if pct is not None:
                    return int(pct)
        except Exception:
            pass
        # Fallback: read from /sys/class/backlight
        try:
            dev = self._pick_default_backlight()
            if not dev:
                return None
            cur, maxb = self._read_brightness_values(dev)
            if not maxb:
                return None
            pct = int(round(cur * 100.0 / maxb))
            pct = max(0, min(100, pct))
            if pct and pct < 10:
                pct = 10
            return pct
        except Exception:
            return None

    def _publish_brightness_state(self):
        pct = self._read_brightness_percent()
        if pct is not None:
            self._publish(self.t_brightness_state, str(pct), qos=1, retain=True)

    def _handle_cmd_brightness(self, payload: str):
        try:
            pct = int(float(payload))
        except Exception:
            self._log(f"Bad brightness payload: {payload!r}", logging.WARNING)
            return

        pct = max(0, min(100, pct))  # allow 0 now

        # remember last nonzero brightness so we can restore after screen on
        if pct > 0:
            self._last_nonzero_brightness = pct

        try:
            sc = self._resolve_screen_ctrl()
            if sc is not None:
                # Read previous value before changing so we know whether to toggle hw power
                prev_pct = None
                try:
                    if hasattr(sc, "read_brightness_percent"):
                        prev_pct = sc.read_brightness_percent()
                except Exception:
                    pass
                ok = bool(sc.set_brightness_percent(pct, allow_zero=True))
                if not ok:
                    self._log("ScreenController refused brightness change.", logging.WARNING)
                # Hardware screen power: same logic as _on_settings_changed_pygame
                try:
                    from Utilities.brightness import set_screen_power  # noqa: PLC0415
                    if pct == 0:
                        set_screen_power(False)
                    elif prev_pct == 0:
                        set_screen_power(True)
                except Exception:
                    pass
                self._publish_brightness_state()
                return
        except Exception as e:
            self._log(f"ScreenController set_brightness failed: {e}", logging.ERROR)

        # Fallback direct write (headless / no view)
        try:
            from Utilities.brightness import set_screen_power  # noqa: PLC0415
            if pct == 0:
                set_screen_power(False)
        except Exception:
            pass
        try:
            dev = self._pick_default_backlight()
            if not dev:
                self._log("No backlight device found.", logging.ERROR)
                return
            self._write_brightness_percent(dev, pct, allow_zero=True)
            self._publish_brightness_state()
        except Exception as e:
            self._log(f"Fallback brightness write failed: {e}", logging.ERROR)


    # ---------- update / service ----------
    def _handle_cmd_update(self):
        try:
            au = getattr(self.view, "autoupdater", None)
            if au and hasattr(au, "pull_now"):
                ok, msg = au.pull_now()
                lvl = logging.INFO if ok else logging.WARNING
                self._log(f"Pull update {'OK' if ok else 'FAILED'}: {msg[:300]}", lvl)
                return
        except Exception as e:
            self._log(f"AutoUpdater pull_now error: {e}", logging.ERROR)

        # Fallback: try git pull in app dir
        try:
            res = subprocess.run(
                ["git", "-C", self._repo_path(), "pull", "--ff-only"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120
            )
            ok = (res.returncode == 0)
            msg = res.stdout.strip()
            lvl = logging.INFO if ok else logging.WARNING
            self._log(f"git pull {'OK' if ok else 'FAILED'}: {msg[:300]}", lvl)
        except Exception as e:
            self._log(f"git pull fallback error: {e}", logging.ERROR)

    def _handle_cmd_restart(self):
        try:
            self._restart_service(self.service_name)
        except Exception as e:
            self._log(f"Restart service error: {e}", logging.ERROR)

    def _handle_cmd_service(self, payload: str):
        target = (payload or "").strip().upper()
        if target not in ("ON", "OFF"):
            self._log(f"Unknown service payload: {payload!r}", logging.WARNING)
            return

        name = self.service_name

        if target == "OFF":
            # 1) tell HA we're going offline
            try:
                mi = self.client.publish(self.t_avail, "offline", qos=1, retain=True)
                mi.wait_for_publish(timeout=1.0)
            except Exception:
                pass

            # 2) disconnect cleanly
            try:
                self.client.disconnect()
                self.client.loop_stop()
            except Exception:
                pass

            # 3) now actually stop the unit
            try:
                self._stop_service(name)
            except Exception as e:
                self._log(f"Stop service error: {e}", logging.ERROR)
            return

        # target == "ON"
        try:
            if not self._is_service_running(name):
                self._start_service(name)
            self._delayed_publish_service_state()
        except Exception as e:
            self._log(f"Start service error: {e}", logging.ERROR)

    def _delayed_publish_service_state(self, delay: float = 1.5):
        def _w():
            time.sleep(delay)
            try:
                self._publish_service_state()
            except Exception:
                pass
        threading.Thread(target=_w, daemon=True).start()

    def _publish_service_state(self):
        state = "ON" if self._is_service_running(self.service_name) else "OFF"
        self._publish(self.t_service_state, state, qos=1, retain=True)

    # ---------- helpers: backlight ----------
    def _list_backlights(self):
        import os
        base = "/sys/class/backlight"
        if not os.path.isdir(base):
            return []
        devs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        # prefer rpi-like first
        devs.sort(key=lambda n: (0 if ("rpi" in n or "rasp" in n) else 1, n))
        return devs

    def _pick_default_backlight(self) -> Optional[str]:
        devs = self._list_backlights()
        return devs[0] if devs else None

    def _read_brightness_values(self, dev):
        import os
        base = f"/sys/class/backlight/{dev}"
        try:
            with open(os.path.join(base, "max_brightness"), "r") as f:
                maxb = int(f.read().strip())
            with open(os.path.join(base, "brightness"), "r") as f:
                cur = int(f.read().strip())
            return cur, maxb
        except Exception:
            return None, None

    def _write_brightness_percent(self, dev, pct: int, allow_zero: bool = False) -> bool:
        import os
        base = f"/sys/class/backlight/{dev}"
        try:
            with open(os.path.join(base, "max_brightness"), "r") as f:
                maxb = int(f.read().strip())
        except Exception:
            return False
        if allow_zero:
            pct = max(0, min(100, int(pct)))
        else:
            pct = max(10, min(100, int(pct)))
        value = int(round(pct * maxb / 100.0))
        path = os.path.join(base, "brightness")
        try:
            with open(path, "w") as f:
                f.write(str(value))
            return True
        except PermissionError:
            cmd = f"echo {value} | sudo tee {path}"
            try:
                subprocess.run(cmd, shell=True, check=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except subprocess.CalledProcessError:
                return False
        except Exception:
            return False

    # ---------- helpers: system ----------
    def _repo_path(self):
        import os
        return self.settings.get("autoupdate", {}).get("repo_path", os.path.dirname(__file__))

    def _is_user_service(self, name) -> bool:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "status", f"{name}.service"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2
            )
            return r.returncode in (0, 3)
        except Exception:
            return False

    def _is_service_running(self, name) -> bool:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", f"{name}.service"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3
            )
            if r.returncode == 0 and (r.stdout or "").strip() == "active":
                return True
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["systemctl", "is-active", f"{name}.service"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3
            )
            return r.returncode == 0 and (r.stdout or "").strip() == "active"
        except Exception:
            return False

    def _restart_service(self, name):
        if self._is_user_service(name):
            subprocess.run(["systemctl", "--user", "restart", f"{name}.service"], check=False)
        else:
            subprocess.run(["sudo", "systemctl", "restart", f"{name}.service"], check=False)

    def _start_service(self, name):
        if self._is_user_service(name):
            subprocess.run(["systemctl", "--user", "start", f"{name}.service"], check=False)
        else:
            subprocess.run(["sudo", "systemctl", "start", f"{name}.service"], check=False)

    def _stop_service(self, name):
        if self._is_user_service(name):
            subprocess.run(["systemctl", "--user", "stop", f"{name}.service"], check=False)
        else:
            subprocess.run(["sudo", "systemctl", "stop", f"{name}.service"], check=False)

    def _get_local_ip(self) -> str:
        if _get_local_ip_util:
            try:
                return _get_local_ip_util()
            except Exception:
                pass
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    def _get_current_ssid(self) -> str:
        # Prefer: nmcli shows the active Wi-Fi row as "yes:<SSID>"
        try:
            out = subprocess.check_output(
                ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
                text=True, stderr=subprocess.DEVNULL, timeout=4
            )
            for line in out.splitlines():
                parts = line.split(":")
                if len(parts) >= 2 and parts[0] == "yes":
                    ssid = parts[1].strip()
                    if ssid:
                        return ssid
        except Exception:
            pass

        # Fallback: iwgetid -r
        try:
            out = subprocess.check_output(
                ["iwgetid", "-r"], text=True, stderr=subprocess.DEVNULL, timeout=3
            )
            ssid = out.strip()
            if ssid:
                return ssid
        except Exception:
            pass

        # Last resort: active connections filtered to Wi-Fi type
        try:
            out = subprocess.check_output(
                ["nmcli", "-t", "-f", "TYPE,NAME", "connection", "show", "--active"],
                text=True, stderr=subprocess.DEVNULL, timeout=4
            )
            for line in out.splitlines():
                typ, name = (line.split(":") + ["", ""])[:2]
                if typ == "wifi" and name:
                    return name
        except Exception:
            pass

        return "N/A"

    def _get_external_ip(self) -> Optional[str]:
        # return cached value for 5 minutes
        try:
            ip, ts = self._ext_ip_cache
            if ip and (time.time() - ts) < 300:
                return ip
        except Exception:
            pass

        try:
            out = subprocess.check_output(
                ["curl", "-sS", "--max-time", "3", "https://ipinfo.io/ip"],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
            if out and len(out) < 64:
                self._ext_ip_cache = (out, time.time())
                return out
        except Exception:
            pass
        return None


    # ---------- mqtt helpers ----------
    def _publish(self, topic, payload, qos=1, retain=False):
        if not self.client:
            return
        try:
            self.client.publish(topic, payload=payload, qos=qos, retain=retain)
        except Exception as e:
            self._log(f"Publish error {topic}: {e}", logging.ERROR)

    @staticmethod
    def _clean_topic(t: str) -> str:
        return "/".join(p for p in (t or "").split("/") if p)

    def _log(self, msg, lvl=logging.INFO):
        try:
            self.view.send_log_message(f"[MQTT] {msg}", lvl)
        except Exception:
            logging.log(lvl, f"[MQTT] {msg}")
