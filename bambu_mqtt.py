#!/usr/bin/env python3
"""
Bambu MQTT Client - Modular MQTT connection for Bambu Lab printers.

Provides a reusable MQTT client that connects to Bambu Cloud, tracks printer
state, and fires callbacks when data arrives. Used as a shared dependency by
both the Bambu Progress Notification server and the Filament Tracker.

Usage:
    from bambu_mqtt import BambuMQTTClient, PrinterState, PREPARATION_STAGES

    mqtt = BambuMQTTClient(server, port, user_id, token, serial)
    mqtt.on_print_update(my_handler)
    mqtt.on_ams_data(my_ams_handler)
    mqtt.run()  # blocks
"""

import json
import ssl
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Callable, List

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

# =============================================================================
# PREPARATION STAGE MAPPING - maps stg_cur numeric values to human-readable names
# Source: ha-bambulab Home Assistant integration (https://github.com/greghesp/ha-bambulab)
#         CURRENT_STAGE_IDS in custom_components/bambu_lab/pybambu/const.py
# =============================================================================
PREPARATION_STAGES = {
    -1: None,                           # idle
    0: None,                            # printing (not a prep stage)
    1: "Auto bed leveling",
    2: "Preheating heatbed",
    3: "Vibration compensation",
    4: "Changing filament",
    5: "M400 pause",
    6: "Filament runout pause",
    7: "Heating hotend",
    8: "Calibrating extrusion",
    9: "Scanning bed surface",
    10: "Inspecting first layer",
    11: "Identifying build plate",
    12: "Calibrating micro lidar",
    13: "Homing toolhead",
    14: "Cleaning nozzle tip",
    15: "Checking extruder temp",
    16: "Paused by user",
    17: "Front cover falling",
    18: "Calibrating micro lidar",
    19: "Calibrating extrusion flow",
    20: "Nozzle temp malfunction",
    21: "Heatbed temp malfunction",
    22: "Filament unloading",
    23: "Paused: skipped step",
    24: "Filament loading",
    25: "Calibrating motor noise",
    26: "Paused: AMS lost",
    27: "Paused: low fan speed",
    28: "Chamber temp control error",
    29: "Cooling chamber",
    30: "Paused by G-code",
    31: "Motor noise calibration",
    32: "Paused: nozzle filament covered",
    33: "Paused: cutter error",
    34: "Paused: first layer error",
    35: "Paused: nozzle clog",
    36: "Checking absolute accuracy",
    37: "Absolute accuracy calibration",
    38: "Checking absolute accuracy",
    39: "Calibrating nozzle offset",
    40: "Bed leveling (high temp)",
    41: "Checking quick release",
    42: "Checking door and cover",
    43: "Laser calibration",
    44: "Checking platform",
    45: "Checking camera position",
    46: "Calibrating camera",
    47: "Bed leveling phase 1",
    48: "Bed leveling phase 2",
    49: "Heating chamber",
    50: "Cooling heatbed",
    51: "Printing calibration lines",
    52: "Checking material",
    53: "Live view camera calibration",
    54: "Waiting for heatbed temp",
    55: "Checking material position",
    56: "Cutting module offset calibration",
    57: "Measuring surface",
    58: "Thermal preconditioning",
    59: "Homing blade holder",
    60: "Calibrating camera offset",
    61: "Calibrating blade holder",
    62: "Hotend pick and place test",
    63: "Waiting for chamber temp",
    64: "Preparing hotend",
    65: "Calibrating nozzle clump detection",
    66: "Purifying chamber air",
    77: "Preparing AMS",
    255: None,                          # idle
}

# Stage categories - groups stg_cur values by semantic meaning.
# Combined with layer_num to distinguish pre-print stages from mid-print interruptions.
STAGE_CATEGORIES = {
    # prepare - normal pre-print setup
    1: "prepare", 2: "prepare", 3: "prepare", 7: "prepare", 9: "prepare",
    11: "prepare", 13: "prepare", 14: "prepare", 15: "prepare", 29: "prepare",
    40: "prepare", 41: "prepare", 42: "prepare", 47: "prepare", 48: "prepare",
    49: "prepare", 50: "prepare", 51: "prepare", 52: "prepare", 54: "prepare",
    55: "prepare", 57: "prepare", 58: "prepare", 59: "prepare", 63: "prepare",
    64: "prepare", 66: "prepare", 77: "prepare",
    # calibrate - calibration/scanning steps
    8: "calibrate", 10: "calibrate", 12: "calibrate", 18: "calibrate",
    19: "calibrate", 25: "calibrate", 31: "calibrate", 36: "calibrate",
    37: "calibrate", 38: "calibrate", 39: "calibrate", 43: "calibrate",
    44: "calibrate", 45: "calibrate", 46: "calibrate", 53: "calibrate",
    56: "calibrate", 60: "calibrate", 61: "calibrate", 62: "calibrate",
    65: "calibrate",
    # paused - expected interruptions
    5: "paused", 16: "paused", 30: "paused",
    # filament - filament operations (context-dependent on layer_num)
    4: "filament", 22: "filament", 24: "filament",
    # issue - errors/malfunctions requiring attention
    6: "issue", 17: "issue", 20: "issue", 21: "issue", 23: "issue",
    26: "issue", 27: "issue", 28: "issue", 32: "issue", 33: "issue",
    34: "issue", 35: "issue",
}


class PrinterState:
    """Tracks current printer state from MQTT messages."""
    def __init__(self):
        self.gcode_state: str = "UNKNOWN"
        self.stg_cur: int = -1          # current preparation stage
        self.progress: int = 0
        self.remaining_time_minutes: int = 0  # Bambu sends minutes
        self.job_name: str = ""
        self.layer_num: int = 0
        self.total_layers: int = 0
        self.nozzle_temp: int = 0
        self.nozzle_target_temp: int = 0
        self.bed_temp: int = 0
        self.bed_target_temp: int = 0
        self.chamber_temp: int = 0
        self.last_update_at: str = ""
        self.last_error_code: str = ""
        self.last_failure_reason: str = ""


class BambuMQTTClient:
    """Modular MQTT client for Bambu Lab printers.

    Connects to Bambu Cloud MQTT, parses printer state, and fires callbacks
    when print data or AMS data arrives. Consumers register callbacks to
    react to data without managing the MQTT connection themselves.
    """

    def __init__(self, mqtt_server: str, mqtt_port: int, user_id: str,
                 access_token: str, printer_serial: str):
        self.state = PrinterState()
        self.mqtt_client: Optional[mqtt.Client] = None
        self.printer_serial = printer_serial
        self._mqtt_server = mqtt_server
        self._mqtt_port = mqtt_port
        self._user_id = user_id
        self._access_token = access_token

        # Callback lists
        self._on_print_update_cbs: List[Callable] = []
        self._on_ams_data_cbs: List[Callable] = []
        self._on_connect_cbs: List[Callable] = []
        self._on_disconnect_cbs: List[Callable] = []

    # -- Callback registration --

    def on_print_update(self, callback: Callable):
        """Register callback: called with (print_data_dict) when print fields change."""
        self._on_print_update_cbs.append(callback)

    def on_ams_data(self, callback: Callable):
        """Register callback: called with (ams_payload_dict) when AMS data arrives."""
        self._on_ams_data_cbs.append(callback)

    def on_connect(self, callback: Callable):
        """Register callback: called with no args when MQTT connects successfully."""
        self._on_connect_cbs.append(callback)

    def on_disconnect(self, callback: Callable):
        """Register callback: called with (reason_code) on MQTT disconnect."""
        self._on_disconnect_cbs.append(callback)

    # -- MQTT handlers --

    def _handle_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0 or str(reason_code) == "Success":
            logger.info("=" * 50)
            logger.info("Connected to Bambu MQTT successfully!")
            logger.info("=" * 50)

            topic = f"device/{self.printer_serial}/report"
            client.subscribe(topic)
            logger.info(f"Subscribed to: {topic}")

            self.request_push_all()

            for cb in self._on_connect_cbs:
                try:
                    cb()
                except Exception as e:
                    logger.error(f"on_connect callback error: {e}")
        else:
            logger.error(f"Bambu MQTT connection failed with code: {reason_code}")

    def _handle_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if reason_code != 0:
            logger.warning(f"Disconnected from Bambu MQTT (rc={reason_code}). Will reconnect...")
        for cb in self._on_disconnect_cbs:
            try:
                cb(reason_code)
            except Exception as e:
                logger.error(f"on_disconnect callback error: {e}")

    def _handle_subscribe(self, client, userdata, mid, reason_codes, properties):
        logger.info(f"Subscription confirmed (QoS: {reason_codes[0]})")
        logger.info("")
        logger.info("=" * 50)
        logger.info("WAITING FOR PRINTER UPDATES...")
        logger.info("=" * 50)
        logger.info("If printer is printing, you should see updates below.")
        logger.info("Press Ctrl+C to exit.")
        logger.info("=" * 50)
        logger.info("")

    def _handle_message(self, client, userdata, msg):
        try:
            raw = msg.payload.decode()
            if len(raw) < 10:
                return

            payload = json.loads(raw)

            if "print" in payload:
                print_data = payload["print"]
                updated = self._parse_print_data(print_data)

                # Fire AMS callbacks
                if "ams" in print_data:
                    for cb in self._on_ams_data_cbs:
                        try:
                            cb(print_data["ams"])
                        except Exception as e:
                            logger.error(f"AMS callback error: {e}")

                # Fire print update callbacks
                if updated:
                    self.print_status()
                    for cb in self._on_print_update_cbs:
                        try:
                            cb(print_data)
                        except Exception as e:
                            logger.error(f"Print update callback error: {e}")

        except json.JSONDecodeError:
            logger.error("Failed to parse MQTT message")
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    # -- State parsing --

    def _parse_print_data(self, print_data: dict) -> bool:
        """Parse print fields from MQTT message into self.state. Returns True if any field updated."""
        updated = False

        if "gcode_state" in print_data:
            self.state.gcode_state = print_data["gcode_state"]
            updated = True

        if "mc_percent" in print_data:
            self.state.progress = print_data["mc_percent"]
            updated = True

        if "mc_remaining_time" in print_data:
            self.state.remaining_time_minutes = print_data["mc_remaining_time"]
            updated = True

        if "nozzle_temper" in print_data:
            self.state.nozzle_temp = print_data["nozzle_temper"]
            updated = True

        if "nozzle_target_temper" in print_data:
            self.state.nozzle_target_temp = print_data["nozzle_target_temper"]
            updated = True

        if "bed_temper" in print_data:
            self.state.bed_temp = print_data["bed_temper"]
            updated = True

        if "bed_target_temper" in print_data:
            self.state.bed_target_temp = print_data["bed_target_temper"]
            updated = True

        # Chamber temp: newer firmware (P2S, H2, X1E) uses CTC path
        ctc_temp = print_data.get("device", {}).get("ctc", {}).get("info", {}).get("temp", None)
        if ctc_temp is not None:
            self.state.chamber_temp = ctc_temp & 0xFFFF
            updated = True
        elif "chamber_temper" in print_data:
            self.state.chamber_temp = round(print_data["chamber_temper"])
            updated = True

        if "stg_cur" in print_data:
            self.state.stg_cur = print_data["stg_cur"]
            updated = True

        # Optional error/failure fields (firmware/model dependent).
        # We only store values when they are present in payload.
        error_code = (
            print_data.get("print_error")
            or print_data.get("error_code")
            or print_data.get("err_code")
        )
        if error_code not in (None, ""):
            self.state.last_error_code = str(error_code)
            updated = True

        fail_reason = (
            print_data.get("fail_reason")
            or print_data.get("cancel_reason")
            or print_data.get("completion_reason")
            or print_data.get("reason")
        )
        if fail_reason not in (None, ""):
            self.state.last_failure_reason = str(fail_reason)
            updated = True

        hms_value = print_data.get("hms")
        if isinstance(hms_value, list) and hms_value:
            first_hms = hms_value[0]
            if isinstance(first_hms, dict):
                hms_code = first_hms.get("code") or first_hms.get("err_code")
                hms_msg = first_hms.get("msg") or first_hms.get("desc")
                if hms_code not in (None, ""):
                    self.state.last_error_code = str(hms_code)
                    updated = True
                if hms_msg not in (None, ""):
                    self.state.last_failure_reason = str(hms_msg)
                    updated = True

        if "subtask_name" in print_data:
            self.state.job_name = print_data["subtask_name"]
            updated = True

        if "layer_num" in print_data:
            self.state.layer_num = print_data["layer_num"]
            updated = True

        if "total_layer_num" in print_data:
            self.state.total_layers = print_data["total_layer_num"]
            updated = True

        # Also check nested "3D" object for layer info
        if "3D" in print_data:
            if "layer_num" in print_data["3D"]:
                self.state.layer_num = print_data["3D"]["layer_num"]
                updated = True
            if "total_layer_num" in print_data["3D"]:
                self.state.total_layers = print_data["3D"]["total_layer_num"]
                updated = True

        if updated:
            self.state.last_update_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        return updated

    # -- Utility --

    def print_status(self):
        """Print formatted status update to console."""
        timestamp = datetime.now().strftime("%H:%M:%S")

        remaining = self.state.remaining_time_minutes
        hours = remaining // 60
        mins = remaining % 60
        time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

        stg_name = PREPARATION_STAGES.get(self.state.stg_cur)
        stage_str = f" [{stg_name}]" if stg_name else ""

        print(f"[{timestamp}] {self.state.job_name or 'Unknown'} | "
              f"{self.state.gcode_state}{stage_str} | "
              f"{self.state.progress}% | "
              f"Layer {self.state.layer_num}/{self.state.total_layers} | "
              f"ETA: {time_str} | "
              f"Nozzle: {self.state.nozzle_temp} C | "
              f"Bed: {self.state.bed_temp} C | "
              f"Chamber: {self.state.chamber_temp} C")

    def request_push_all(self):
        """Request full state dump from printer."""
        if not self.mqtt_client:
            return
        topic = f"device/{self.printer_serial}/request"
        payload = json.dumps({
            "pushing": {
                "sequence_id": "0",
                "command": "pushall"
            }
        })
        self.mqtt_client.publish(topic, payload)
        logger.info("Requested full state from printer")

    # -- Connection --

    def run(self):
        """Connect to Bambu MQTT and run the event loop (blocks)."""
        client_id = f"bambu_mqtt_{int(time.time())}"
        self.mqtt_client = mqtt.Client(
            client_id=client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )

        username = f"u_{self._user_id}"
        self.mqtt_client.username_pw_set(username, self._access_token)
        logger.info(f"Username: {username}")
        logger.info(f"Client ID: {client_id}")

        self.mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

        self.mqtt_client.on_connect = self._handle_connect
        self.mqtt_client.on_disconnect = self._handle_disconnect
        self.mqtt_client.on_message = self._handle_message
        self.mqtt_client.on_subscribe = self._handle_subscribe

        logger.info(f"Connecting to {self._mqtt_server}:{self._mqtt_port}...")
        self.mqtt_client.connect(self._mqtt_server, self._mqtt_port, keepalive=60)
        self.mqtt_client.loop_forever()

    def disconnect(self):
        """Disconnect from MQTT."""
        if self.mqtt_client:
            self.mqtt_client.disconnect()
