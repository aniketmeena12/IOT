"""
=============================================================================
 CORRUGATED MANUFACTURING MACHINE
 SMART SENSOR SYSTEM — ALERT ON REAL SENSOR FAILURE
=============================================================================

 BEHAVIOR:
 ─────────────────────────────────────────────────────────────────────────
 MODE 1 — No real sensor registered:
   → Runs full simulation (physics + PID)
   → Normal operation, no alerts

 MODE 2 — Real sensor registered + reading OK:
   → Uses REAL sensor data
   → PID controls heater normally

 MODE 3 — Real sensor registered + reading FAILS:
   → 🚨 SENSOR FAULT ALARM raised immediately
   → Heater power set to 0% (safe state)
   → Simulation does NOT take over
   → System holds in FAULT state
   → Alarm logged with timestamp + fault count
   → Auto-retry every SENSOR_RETRY_STEPS steps
   → If sensor recovers → alarm cleared, normal operation resumes
   → If sensor stays failed → alarm repeats every retry cycle

 WHY THIS IS CORRECT FOR INDUSTRIAL USE:
   Silently substituting simulated data when a real sensor fails is
   dangerous in a real corrugated plant — the machine could overheat
   because the controller thinks it knows the temperature when it does not.
   The safe action is: STOP heater, RAISE alarm, WAIT for engineer.

 INFLUXDB / GRAFANA INTEGRATION:
   Every tick writes one Point to InfluxDB → Grafana reads live.
   Set env vars before running:
     export INFLUXDB_URL="http://localhost:8086"
     export INFLUXDB_TOKEN="your-token"
     export INFLUXDB_ORG="your-org"
     export INFLUXDB_BUCKET="spring_data"
     export MACHINE_NAME="smart_heater_01"
   Install: pip install influxdb-client

=============================================================================
"""

import os
import random
import time
import math
import csv
import json
import logging
import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, List
from enum import Enum

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s  %(levelname)-8s %(name)s — %(message)s",
    datefmt= "%H:%M:%S"
)
log = logging.getLogger("SmartSensor")


# =============================================================================
#  CONFIGURATION
# =============================================================================
@dataclass
class Config:
    # Process temperature limits (°C)
    TEMP_MIN:           float = 80.0
    TEMP_MAX:           float = 180.0

    # Base operating target — auto-adjusted by environment
    BASE_TARGET_TEMP:   float = 140.0

    # Idle / standby setpoint
    IDLE_TEMP:          float = 100.0

    # K-type thermocouple noise simulation
    NOISE_AMPLITUDE:    float = 1.5

    # Heater thermal model
    HEAT_RATE_PER_PCT:  float = 0.08   # °C per step per 1% power
    COOL_RATE_BASE:     float = 0.04   # Newton cooling coefficient

    # Auto target setter environmental limits
    HUMIDITY_BOOST_MAX: float = 10.0   # max °C boost at 80% humidity
    AMBIENT_BOOST_MAX:  float = 8.0    # max °C boost at 20°C ambient

    # PID gains
    KP:  float = 2.2
    KI:  float = 0.04
    KD:  float = 1.2
    DERIV_FILTER_ALPHA: float = 0.20   # derivative low-pass filter

    # Power output limits
    POWER_MIN: float = 0.0
    POWER_MAX: float = 100.0

    # Simulation timing
    STEP_DELAY: float = 1.0    # seconds between steps (real-time)
    MAX_STEPS:  int   = 0      # 0 = run forever (requires Ctrl+C to stop)

    # Real sensor auto-retry interval (steps)
    SENSOR_RETRY_STEPS: int = 10

    # Display parameters
    SHOW_CURRENT_TEMP:  bool = True   # Display current temp during execution
    SHOW_TEMP_VERBOSE:  bool = False  # Extra detailed temperature info
    TEMP_DISPLAY_UNIT:  str  = "celsius"  # celsius or fahrenheit


CFG = Config()


# =============================================================================
#  INFLUXDB SETTINGS  (read from environment variables)
# =============================================================================
INFLUXDB_URL    = os.getenv("INFLUXDB_URL",    "http://localhost:8086")
INFLUXDB_TOKEN  = os.getenv("INFLUXDB_TOKEN",  "my-super-secret-token")
INFLUXDB_ORG    = os.getenv("INFLUXDB_ORG",    "spring_factory")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "spring_data")
MACHINE_NAME    = os.getenv("MACHINE_NAME",    "smart_heater_01")


def write_heating_point(write_api, data: dict) -> None:
    """
    Write one tick's data to InfluxDB.
    Called every step from SmartHeatingController.run().
    Grafana reads these points to build the live dashboard.
    """
    try:
        from influxdb_client import Point, WritePrecision
        point = (
            Point("spring_heating")
            .tag("machine", MACHINE_NAME)
            # ── process values ──────────────────────────────────────────────
            .field("step",         int(data.get("step", 0)))
            .field("sensor_temp",  float(data.get("sensor_temp", 0.0)))
            .field("target",       float(data.get("target", 0.0)))
            .field("hum_boost",    float(data.get("hum_boost",  0.0)))
            .field("amb_boost",    float(data.get("amb_boost",  0.0)))
            .field("humidity",     float(data.get("humidity",   0.0)))
            .field("ambient",      float(data.get("ambient",    0.0)))
            # ── heater / PID ────────────────────────────────────────────────
            .field("power",        float(data.get("power",   0.0)))
            .field("p_term",       float(data.get("p_term",  0.0)))
            .field("i_term",       float(data.get("i_term",  0.0)))
            .field("d_term",       float(data.get("d_term",  0.0)))
            .field("error",        float(data.get("error",   0.0)
                                         if data.get("error") is not None else 0.0))
            # ── fault / alarm ───────────────────────────────────────────────
            .field("system_fault", int(bool(data.get("system_fault", False))))
            .field("alarm_count",  int(data.get("alarm_count", 0)))
            .field("fault_keys",   str(",".join(data.get("fault_keys", []))
                                        if data.get("fault_keys") else ""))
            # ── sensor source tags ──────────────────────────────────────────
            .field("state",        str(data.get("state",     "")))
            .field("temp_mode",    str(data.get("temp_mode", "")))
            .field("hum_mode",     str(data.get("hum_mode",  "")))
            .field("amb_mode",     str(data.get("amb_mode",  "")))
            .time(datetime.now(timezone.utc), WritePrecision.NS)
        )
        write_api.write(bucket=INFLUXDB_BUCKET, record=point)
    except Exception as e:
        log.warning(f"InfluxDB write failed: {e}")


# =============================================================================
#  SENSOR MODE
# =============================================================================
class SensorMode(Enum):
    SIMULATION = "SIMULATION"   # no real sensor → physics model
    REAL       = "REAL"         # real sensor reading OK
    FAULT      = "FAULT"        # real sensor registered but FAILING → ALARM


# =============================================================================
#  ALARM RECORD
# =============================================================================
@dataclass
class AlarmRecord:
    timestamp:   str
    sensor_key:  str
    sensor_name: str
    fault_count: int
    error_msg:   str
    step:        int

    def __str__(self):
        return (f"[ALARM #{self.fault_count}] Step {self.step} | "
                f"{self.timestamp} | Sensor: {self.sensor_key} "
                f"({self.sensor_name}) | Error: {self.error_msg}")


# =============================================================================
#  SENSOR READING RESULT
# =============================================================================
@dataclass
class SensorReading:
    value:   float
    mode:    SensorMode
    sensor:  str
    ok:      bool
    error:   str = ""


# =============================================================================
#  BASE SENSOR
# =============================================================================
class BaseSensor(ABC):
    def __init__(self, name: str, unit: str):
        self.name = name
        self.unit = unit
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    @abstractmethod
    def read(self) -> float:
        """Read sensor value. MUST raise exception on failure."""

    def __repr__(self):
        return f"{self.__class__.__name__}('{self.name}')"


# =============================================================================
#  SIMULATED SENSORS  (used ONLY when no real sensor is registered)
# =============================================================================

class SimulatedTempSensor(BaseSensor):
    """
    K-type thermocouple simulation with realistic thermal physics + Gaussian noise.
    Only active when no real sensor is registered.
    """
    def __init__(self, initial_temp: float = 30.0):
        super().__init__("Simulated K-Type Thermocouple", "°C")
        self._true_temp = initial_temp

    def update_physics(self, power_pct: float, ambient_temp: float) -> None:
        """Advance thermal model one step (called by HeaterSystem)."""
        heat_in  = power_pct * CFG.HEAT_RATE_PER_PCT
        cool_out = CFG.COOL_RATE_BASE * (self._true_temp - ambient_temp)
        self._true_temp += heat_in - cool_out
        self._true_temp  = max(CFG.TEMP_MIN - 20, min(CFG.TEMP_MAX, self._true_temp))

    def read(self) -> float:
        noise = random.gauss(0, CFG.NOISE_AMPLITUDE / 2)
        return round(max(CFG.TEMP_MIN, min(CFG.TEMP_MAX, self._true_temp + noise)), 2)

    @property
    def true_temperature(self) -> float:
        return self._true_temp


class SimulatedHumiditySensor(BaseSensor):
    """DHT22-style humidity simulation (30–80% RH, slow sine drift)."""
    def __init__(self):
        super().__init__("Simulated Humidity", "%RH")
        self._t = 0

    def read(self) -> float:
        self._t += 1
        return round(max(30.0, min(80.0,
            55.0 + 25.0 * math.sin(self._t / 90.0 + 1.0)
            + random.uniform(-1.0, 1.0))), 1)


class SimulatedAmbientSensor(BaseSensor):
    """Ambient temperature simulation (20–40°C, slow sine drift)."""
    def __init__(self):
        super().__init__("Simulated Ambient Temp", "°C")
        self._t = 0

    def read(self) -> float:
        self._t += 1
        return round(max(20.0, min(40.0,
            30.0 + 10.0 * math.sin(self._t / 60.0)
            + random.uniform(-0.5, 0.5))), 1)


# =============================================================================
#  REAL SENSOR IMPLEMENTATIONS
#  Each raises an exception on read failure — the SmartSensorSlot catches this
#  and raises a FAULT alarm. Never silently return a wrong value.
# =============================================================================

class ModbusTCPSensor(BaseSensor):
    """
    Modbus TCP — Siemens S7, Allen-Bradley, Schneider PLC.
    pip install pymodbus

    Usage:
        ModbusTCPSensor(host="192.168.1.10", port=502, register=100, scale=0.1)
    """
    def __init__(self, host: str, port: int = 502, register: int = 100,
                 unit_id: int = 1, scale: float = 0.1,
                 name: str = "Modbus TCP", unit: str = "°C"):
        super().__init__(name, unit)
        self.host = host; self.port = port
        self.register = register; self.unit_id = unit_id; self.scale = scale
        self._client = None

    def connect(self) -> bool:
        try:
            from pymodbus.client import ModbusTcpClient
            self._client = ModbusTcpClient(host=self.host, port=self.port)
            ok = self._client.connect()
            self._connected = ok
            return ok
        except Exception as e:
            log.warning(f"ModbusTCP connect failed: {e}")
            return False

    def disconnect(self) -> None:
        if self._client:
            self._client.close()
        self._connected = False

    def read(self) -> float:
        if not self._connected:
            raise ConnectionError("Modbus not connected")
        result = self._client.read_holding_registers(
            address=self.register, count=1, slave=self.unit_id)
        if result.isError():
            raise IOError(f"Modbus register error: {result}")
        return round(result.registers[0] * self.scale, 2)


class ModbusRTUSensor(BaseSensor):
    """
    Modbus RTU over RS485 serial line.
    pip install minimalmodbus

    Usage:
        ModbusRTUSensor(port="/dev/ttyUSB0", slave_id=1, register=0, baudrate=9600)
    """
    def __init__(self, port: str, slave_id: int = 1, register: int = 0,
                 baudrate: int = 9600, scale: float = 0.1,
                 name: str = "Modbus RTU", unit: str = "°C"):
        super().__init__(name, unit)
        self.port = port; self.slave_id = slave_id
        self.register = register; self.baudrate = baudrate; self.scale = scale
        self._instr = None

    def connect(self) -> bool:
        try:
            import minimalmodbus
            self._instr = minimalmodbus.Instrument(self.port, self.slave_id)
            self._instr.serial.baudrate = self.baudrate
            self._connected = True
            return True
        except Exception as e:
            log.warning(f"ModbusRTU connect failed: {e}")
            return False

    def read(self) -> float:
        if not self._connected:
            raise ConnectionError("RTU not connected")
        return round(self._instr.read_register(self.register, 0) * self.scale, 2)


class AnalogSensor_4_20mA(BaseSensor):
    """
    4-20mA analog sensor via ADS1115 ADC (Raspberry Pi / SBC).
    pip install adafruit-circuitpython-ads1x15

    Usage:
        AnalogSensor_4_20mA(channel=0, eng_min=80.0, eng_max=200.0)
    """
    def __init__(self, channel: int = 0, eng_min: float = 80.0,
                 eng_max: float = 200.0,
                 name: str = "4-20mA Sensor", unit: str = "°C"):
        super().__init__(name, unit)
        self.channel = channel; self.eng_min = eng_min; self.eng_max = eng_max
        self._chan = None

    def connect(self) -> bool:
        try:
            import board, busio
            import adafruit_ads1x15.ads1115 as ADS
            from adafruit_ads1x15.analog_in import AnalogIn
            i2c = busio.I2C(board.SCL, board.SDA)
            ads = ADS.ADS1115(i2c)
            ch  = [ADS.P0, ADS.P1, ADS.P2, ADS.P3][self.channel]
            self._chan = AnalogIn(ads, ch)
            self._connected = True
            return True
        except Exception as e:
            log.warning(f"4-20mA connect failed: {e}")
            return False

    def read(self) -> float:
        if not self._connected:
            raise ConnectionError("ADC not connected")
        voltage  = self._chan.voltage
        fraction = max(0.0, min(1.0, (voltage - 1.0) / 4.0))
        return round(self.eng_min + fraction * (self.eng_max - self.eng_min), 2)


class OPCUASensor(BaseSensor):
    """
    OPC-UA sensor — Siemens WinCC, Ignition, Wonderware SCADA.
    pip install opcua

    Usage:
        OPCUASensor(url="opc.tcp://192.168.1.20:4840", node_id="ns=2;i=1001")
    """
    def __init__(self, url: str, node_id: str,
                 name: str = "OPC-UA Sensor", unit: str = "°C"):
        super().__init__(name, unit)
        self.url = url; self.node_id = node_id
        self._client = None; self._node = None

    def connect(self) -> bool:
        try:
            from opcua import Client
            self._client = Client(self.url)
            self._client.connect()
            self._node = self._client.get_node(self.node_id)
            self._connected = True
            return True
        except Exception as e:
            log.warning(f"OPC-UA connect failed: {e}")
            return False

    def disconnect(self) -> None:
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
        self._connected = False

    def read(self) -> float:
        if not self._connected:
            raise ConnectionError("OPC-UA not connected")
        return round(float(self._node.get_value()), 2)


class SerialSensor(BaseSensor):
    """
    ASCII serial sensor — Eurotherm, RKC, Autonics temperature controllers.
    pip install pyserial

    Usage:
        SerialSensor(port="/dev/ttyUSB0", baudrate=9600, command="T\\r")
    """
    def __init__(self, port: str, baudrate: int = 9600,
                 command: str = "T\r", timeout: float = 1.0,
                 name: str = "Serial Sensor", unit: str = "°C"):
        super().__init__(name, unit)
        self.port = port; self.baudrate = baudrate
        self.command = command; self.timeout = timeout
        self._serial = None

    def connect(self) -> bool:
        try:
            import serial
            self._serial = serial.Serial(
                self.port, self.baudrate, timeout=self.timeout)
            self._connected = True
            return True
        except Exception as e:
            log.warning(f"Serial connect failed: {e}")
            return False

    def disconnect(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._connected = False

    def read(self) -> float:
        if not self._connected:
            raise ConnectionError("Serial not connected")
        self._serial.write(self.command.encode())
        return round(float(self._serial.readline().decode().strip()), 2)


class MQTTSensor(BaseSensor):
    """
    MQTT broker sensor — Node-RED, AWS IoT, local MQTT broker.
    pip install paho-mqtt

    Usage:
        MQTTSensor(broker="192.168.1.5", topic="factory/zone1/temp")
    Payload accepted: plain float "142.5"  or JSON {"value": 142.5}
    """
    def __init__(self, broker: str, topic: str, port: int = 1883,
                 name: str = "MQTT Sensor", unit: str = "°C"):
        super().__init__(name, unit)
        self.broker = broker; self.topic = topic; self.port = port
        self._client = None; self._last: Optional[float] = None

    def connect(self) -> bool:
        try:
            import paho.mqtt.client as mqtt

            def on_message(client, userdata, msg):
                try:
                    payload = msg.payload.decode()
                    try:
                        self._last = float(json.loads(payload).get("value", payload))
                    except Exception:
                        self._last = float(payload)
                except Exception:
                    pass

            self._client = mqtt.Client()
            self._client.on_message = on_message
            self._client.connect(self.broker, self.port, keepalive=60)
            self._client.subscribe(self.topic)
            self._client.loop_start()
            self._connected = True
            return True
        except Exception as e:
            log.warning(f"MQTT connect failed: {e}")
            return False

    def disconnect(self) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
        self._connected = False

    def read(self) -> float:
        if not self._connected or self._last is None:
            raise ConnectionError("MQTT not ready — no message received yet")
        return round(self._last, 2)


class CSVReplaySensor(BaseSensor):
    """
    Replay recorded plant data from a CSV file row by row.
    No hardware needed — great for testing PID with real historical data.

    Usage:
        CSVReplaySensor(filepath="plant_data.csv", column="temperature")

    CSV format:
        timestamp,temperature,humidity,ambient_temp
        2024-01-01 08:00:00,141.2,65.3,28.4
    """
    def __init__(self, filepath: str, column: str = "temperature",
                 name: str = "CSV Replay", unit: str = "°C"):
        super().__init__(name, unit)
        self.filepath = filepath; self.column = column
        self._data: List[float] = []; self._idx = 0

    def connect(self) -> bool:
        try:
            with open(self.filepath, newline="") as f:
                self._data = [float(r[self.column]) for r in csv.DictReader(f)]
            self._connected = bool(self._data)
            if self._connected:
                log.info(f"CSV replay loaded: {len(self._data)} rows from {self.filepath}")
            return self._connected
        except Exception as e:
            log.warning(f"CSV load failed: {e}")
            return False

    def read(self) -> float:
        if not self._connected or not self._data:
            raise IOError("CSV not loaded")
        val = self._data[self._idx % len(self._data)]
        self._idx += 1
        return round(val, 2)


# =============================================================================
#  SMART SENSOR SLOT — core fault detection logic
# =============================================================================
class SmartSensorSlot:
    """
    Single sensor slot with three-mode behavior:

    ┌──────────────────────────────────────────────────────────────────────┐
    │  Real sensor registered?                                             │
    │    NO  → SIMULATION → physics model runs, PID continues normally    │
    │    YES → Try real sensor read()                                      │
    │          ┌─ OK   → REAL → use live value, PID continues             │
    │          └─ FAIL → FAULT → 🚨 ALARM raised                          │
    │                    heater.emergency_off() → power = 0%              │
    │                    PID suspended                                     │
    │                    Simulation does NOT take over                     │
    │                    Auto-retry every SENSOR_RETRY_STEPS steps         │
    │                    Recovery → REAL, alarm cleared                    │
    └──────────────────────────────────────────────────────────────────────┘
    """

    def __init__(self, key: str, sim_sensor: BaseSensor,
                 real_sensor: Optional[BaseSensor] = None):
        self.key          = key
        self.sim_sensor   = sim_sensor
        self.real_sensor  = real_sensor
        self.mode         = SensorMode.SIMULATION
        self.alarms:      List[AlarmRecord] = []
        self._fault_count    = 0
        self._retry_counter  = 0
        self._last_good_val  = 0.0
        self._step           = 0

        # Always connect simulation
        self.sim_sensor.connect()

        # Connect real sensor if provided
        if self.real_sensor:
            self._connect_real()

    def _connect_real(self) -> bool:
        try:
            ok = self.real_sensor.connect()
            if ok:
                self.mode = SensorMode.REAL
                log.info(f"[{self.key}] Real sensor CONNECTED: {self.real_sensor}")
            else:
                self.mode = SensorMode.FAULT
                self._raise_alarm("Connection failed at startup", step=0)
            return ok
        except Exception as e:
            self.mode = SensorMode.FAULT
            self._raise_alarm(str(e), step=0)
            return False

    def _raise_alarm(self, error_msg: str, step: int) -> AlarmRecord:
        self._fault_count += 1
        alarm = AlarmRecord(
            timestamp   = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            sensor_key  = self.key,
            sensor_name = self.real_sensor.name if self.real_sensor else "unknown",
            fault_count = self._fault_count,
            error_msg   = error_msg,
            step        = step
        )
        self.alarms.append(alarm)
        log.error(f"🚨 SENSOR FAULT ALARM | {alarm}")
        return alarm

    def _try_retry(self, step: int) -> bool:
        log.info(f"[{self.key}] Retrying real sensor (step {step})...")
        return self._connect_real()

    def read(self, step: int,
             sim_power: float = 0.0,
             sim_ambient: float = 30.0) -> Tuple[SensorReading, Optional[AlarmRecord]]:
        """
        Read from best available source.
        Returns (SensorReading, AlarmRecord or None).
        AlarmRecord is non-None only when a NEW alarm fires this step.
        """
        self._step = step
        alarm_this_step: Optional[AlarmRecord] = None

        # ── SIMULATION mode (no real sensor registered) ───────────────────────
        if self.real_sensor is None:
            if isinstance(self.sim_sensor, SimulatedTempSensor):
                self.sim_sensor.update_physics(sim_power, sim_ambient)
            try:
                val = self.sim_sensor.read()
                self._last_good_val = val
                return SensorReading(val, SensorMode.SIMULATION,
                                     self.sim_sensor.name, True), None
            except Exception as e:
                return SensorReading(self._last_good_val, SensorMode.FAULT,
                                     "sim-error", False, str(e)), None

        # ── FAULT mode: try periodic retry ────────────────────────────────────
        if self.mode == SensorMode.FAULT:
            self._retry_counter += 1
            if self._retry_counter >= CFG.SENSOR_RETRY_STEPS:
                self._retry_counter = 0
                recovered = self._try_retry(step)
                if recovered:
                    log.info(f"[{self.key}] Sensor RECOVERED at step {step}")

        # ── REAL mode: attempt hardware read ──────────────────────────────────
        if self.mode == SensorMode.REAL:
            try:
                val = self.real_sensor.read()
                self._last_good_val = val
                return SensorReading(val, SensorMode.REAL,
                                     self.real_sensor.name, True), None
            except Exception as e:
                # Real sensor just failed → FAULT
                self.mode           = SensorMode.FAULT
                self._retry_counter = 0
                alarm_this_step     = self._raise_alarm(str(e), step)
                return SensorReading(
                    value  = self._last_good_val,
                    mode   = SensorMode.FAULT,
                    sensor = self.real_sensor.name,
                    ok     = False,
                    error  = str(e)
                ), alarm_this_step

        # ── FAULT mode: still broken, hold last value ─────────────────────────
        if self.mode == SensorMode.FAULT:
            return SensorReading(
                value  = self._last_good_val,
                mode   = SensorMode.FAULT,
                sensor = self.real_sensor.name,
                ok     = False,
                error  = "Sensor in FAULT — awaiting recovery"
            ), None

        # Safety fallback (should never reach here)
        return SensorReading(0.0, SensorMode.FAULT, "unknown",
                             False, "unknown state"), None

    def plug_in(self, real_sensor: BaseSensor, step: int = 0) -> bool:
        """Hot-plug a real sensor at runtime — no restart needed."""
        log.info(f"[{self.key}] Hot-plugging: {real_sensor}")
        if self.real_sensor:
            try:
                self.real_sensor.disconnect()
            except Exception:
                pass
        self.real_sensor = real_sensor
        return self._connect_real()

    def remove_real_sensor(self) -> None:
        """Remove real sensor → slot reverts to SIMULATION mode."""
        if self.real_sensor:
            try:
                self.real_sensor.disconnect()
            except Exception:
                pass
        self.real_sensor = None
        self.mode = SensorMode.SIMULATION
        log.info(f"[{self.key}] Real sensor removed → SIMULATION mode")

    def disconnect(self) -> None:
        try:
            self.sim_sensor.disconnect()
        except Exception:
            pass
        if self.real_sensor:
            try:
                self.real_sensor.disconnect()
            except Exception:
                pass

    @property
    def has_active_alarm(self) -> bool:
        return self.mode == SensorMode.FAULT and self.real_sensor is not None

    @property
    def fault_count(self) -> int:
        return self._fault_count


# =============================================================================
#  ALARM MANAGER
# =============================================================================
class AlarmManager:
    """
    Central alarm registry.
    In a real plant: also trigger buzzer, send SMS, write to PLC alarm register.
    """

    def __init__(self):
        self.active_alarms:   Dict[str, AlarmRecord] = {}
        self.alarm_history:   List[AlarmRecord]       = []
        self.system_in_fault: bool                    = False

    def raise_alarm(self, key: str, alarm: AlarmRecord) -> None:
        self.active_alarms[key] = alarm
        self.alarm_history.append(alarm)
        self.system_in_fault = True
        self._print_alarm_banner(alarm)

    def clear_alarm(self, key: str) -> None:
        if key in self.active_alarms:
            del self.active_alarms[key]
            log.info(f"ALARM CLEARED for sensor [{key}]")
        if not self.active_alarms:
            self.system_in_fault = False

    def _print_alarm_banner(self, alarm: AlarmRecord) -> None:
        print("\n" + "!" * 70)
        print(f"  SENSOR FAULT ALARM #{alarm.fault_count}")
        print(f"  Sensor  : {alarm.sensor_key} — {alarm.sensor_name}")
        print(f"  Time    : {alarm.timestamp}  (Sim Step: {alarm.step})")
        print(f"  Error   : {alarm.error_msg}")
        print(f"  Action  : HEATER POWER → 0%  |  PID → SUSPENDED")
        print(f"  Recovery: Auto-retry every {CFG.SENSOR_RETRY_STEPS} steps")
        print("!" * 70 + "\n")

    def print_summary(self) -> None:
        print("\n── ALARM HISTORY ─────────────────────────────────────────────────────")
        if not self.alarm_history:
            print("  No alarms recorded.")
        for a in self.alarm_history:
            print(f"  {a}")
        print(f"\n  Total alarms: {len(self.alarm_history)}")
        print("──────────────────────────────────────────────────────────────────────\n")

    @property
    def alarm_count(self) -> int:
        return len(self.alarm_history)


# =============================================================================
#  SMART SENSOR MANAGER
# =============================================================================
class SmartSensorManager:
    """
    Central registry for all sensor slots.
    The controller only ever calls manager.read() — never touches sensors directly.
    """

    def __init__(self):
        self._slots: Dict[str, SmartSensorSlot] = {}
        self.alarms: AlarmManager               = AlarmManager()

    def register(self, key: str,
                 sim_sensor: BaseSensor,
                 real_sensor: Optional[BaseSensor] = None) -> None:
        """
        Register a sensor slot.
          sim_sensor  → always required (simulation for no-hardware mode)
          real_sensor → optional real hardware (if fails → FAULT alarm)
        """
        self._slots[key] = SmartSensorSlot(key, sim_sensor, real_sensor)
        mode = "REAL" if real_sensor else "SIMULATION"
        log.info(f"Registered [{key}] → {mode} mode")

    def read(self, key: str, step: int,
             sim_power: float   = 0.0,
             sim_ambient: float = 30.0) -> SensorReading:
        """Read sensor by key. Alarm fired here if real sensor fails."""
        slot = self._slots.get(key)
        if not slot:
            log.error(f"No sensor registered as '{key}'")
            return SensorReading(0.0, SensorMode.FAULT, "missing",
                                 False, "not registered")

        reading, new_alarm = slot.read(step, sim_power, sim_ambient)

        if new_alarm:
            self.alarms.raise_alarm(key, new_alarm)

        # Sensor recovered → clear alarm
        if key in self.alarms.active_alarms and reading.mode == SensorMode.REAL:
            self.alarms.clear_alarm(key)

        return reading

    def plug_in(self, key: str, real_sensor: BaseSensor, step: int = 0) -> bool:
        """Hot-plug a real sensor at runtime."""
        slot = self._slots.get(key)
        if not slot:
            log.error(f"Cannot plug in: no slot registered as '{key}'")
            return False
        return slot.plug_in(real_sensor, step)

    def remove_sensor(self, key: str) -> None:
        """Remove real sensor → slot reverts to SIMULATION."""
        slot = self._slots.get(key)
        if slot:
            slot.remove_real_sensor()
            self.alarms.clear_alarm(key)

    def any_fault(self) -> bool:
        return any(s.has_active_alarm for s in self._slots.values())

    def fault_keys(self) -> List[str]:
        return [k for k, s in self._slots.items() if s.has_active_alarm]

    def all_modes(self) -> Dict[str, str]:
        return {k: s.mode.value for k, s in self._slots.items()}

    def disconnect_all(self) -> None:
        for slot in self._slots.values():
            slot.disconnect()


# =============================================================================
#  AUTO TARGET SETTER
# =============================================================================
class AutoTargetSetter:
    """
    Automatically computes PID setpoint from environmental readings.
    Operator sets only the BASE target — boosts are applied automatically.
    """

    def compute(self, base: float, humidity: float,
                ambient: float, idle: bool) -> Tuple[float, float, float]:
        if idle:
            return CFG.IDLE_TEMP, 0.0, 0.0
        hf = max(0.0, min(1.0, (humidity - 30.0) / 50.0))
        af = max(0.0, min(1.0, (40.0 - ambient) / 20.0))
        hb = round(hf * CFG.HUMIDITY_BOOST_MAX, 2)
        ab = round(af * CFG.AMBIENT_BOOST_MAX,  2)
        return round(base + hb + ab, 1), hb, ab


# =============================================================================
#  PID CONTROLLER
# =============================================================================
class PIDController:
    """
    Discrete PID with anti-windup integral clamping and derivative filtering.
    Suspended automatically when sensor is in FAULT state.
    """

    def __init__(self):
        self.kp = CFG.KP
        self.ki = CFG.KI
        self.kd = CFG.KD
        self._i  = 0.0
        self._pe = 0.0
        self._pd = 0.0

    def reset(self) -> None:
        self._i = self._pe = self._pd = 0.0

    def compute(self, setpoint: float, measured: float,
                dt: float = 1.0) -> Tuple[float, float, float, float]:
        dt  = max(dt, 1e-6)
        err = setpoint - measured

        p   = self.kp * err

        self._i += err * dt
        wl  = (CFG.POWER_MAX - CFG.POWER_MIN) / (self.ki + 1e-9)
        self._i = max(-wl, min(wl, self._i))
        i   = self.ki * self._i

        rd  = (err - self._pe) / dt
        df  = CFG.DERIV_FILTER_ALPHA * rd + (1 - CFG.DERIV_FILTER_ALPHA) * self._pd
        d   = self.kd * df

        self._pe = err
        self._pd = df

        total = max(CFG.POWER_MIN, min(CFG.POWER_MAX, round(p + i + d, 2)))
        return total, round(p, 2), round(i, 2), round(d, 2)


# =============================================================================
#  HEATER SYSTEM
# =============================================================================
class HeaterSystem:
    """Represents the physical heater output (electric SCR or steam valve)."""

    def __init__(self):
        self.power_pct: float = 0.0

    def set_power(self, pct: float) -> None:
        self.power_pct = max(CFG.POWER_MIN, min(CFG.POWER_MAX, pct))

    def emergency_off(self) -> None:
        """Immediately cut heater power — called on any sensor FAULT."""
        self.power_pct = 0.0

    def state_label(self, target: float, temp: float, fault: bool) -> str:
        if fault:                        return "SENSOR FAULT"
        if self.power_pct >= 65:         return "HEATING"
        if abs(temp - target) <= 2.0:    return "STABLE"
        if temp > target + 2.0:          return " COOLING"
        return "⬆️  WARMING"


# =============================================================================
#  MASTER SMART HEATING CONTROLLER
# =============================================================================
class SmartHeatingController:
    """
    Fully automatic controller using SmartSensorManager.

    On sensor FAULT:
      → heater.emergency_off()  — power = 0% immediately
      → PID suspended            — no computation, no output
      → tick() returns fault dict — logged + written to InfluxDB
      → Auto-retry sensor every SENSOR_RETRY_STEPS steps

    On sensor recovery:
      → alarm cleared
      → PID resumes from held state
      → heater power returns to PID output
    """

    def __init__(self, manager: SmartSensorManager,
                 base_target: float = CFG.BASE_TARGET_TEMP,
                 idle_mode:   bool  = False,
                 write_api           = None):
        self.manager       = manager
        self.base_target   = base_target
        self.idle_mode     = idle_mode
        self.write_api     = write_api   # InfluxDB write_api or None
        self.heater        = HeaterSystem()
        self.target_setter = AutoTargetSetter()
        self.pid           = PIDController()
        self._step         = 0
        self._last_temp    = 30.0
        self._last_amb     = 30.0

    def tick(self) -> dict:
        self._step += 1

        # ── Read environment sensors ──────────────────────────────────────────
        amb_r = self.manager.read("ambient", self._step,
                                  sim_ambient=self._last_amb)
        hum_r = self.manager.read("humidity", self._step,
                                  sim_ambient=self._last_amb)

        if amb_r.ok:
            self._last_amb = amb_r.value

        # ── Read process temperature ───────────────────────────────────────────
        temp_r = self.manager.read("temp", self._step,
                                   sim_power   = self.heater.power_pct,
                                   sim_ambient = self._last_amb)
        if temp_r.ok:
            self._last_temp = temp_r.value

        # ── Check system fault ────────────────────────────────────────────────
        system_fault = self.manager.any_fault()
        fault_keys   = self.manager.fault_keys()

        if system_fault:
            # FAULT: cut heater, suspend PID — do NOT simulate further
            self.heater.emergency_off()
            return {
                "step":        self._step,
                "sensor_temp": self._last_temp,
                "target":      self.base_target,
                "hum_boost":   0.0,
                "amb_boost":   0.0,
                "humidity":    hum_r.value if hum_r.ok else 0.0,
                "ambient":     amb_r.value if amb_r.ok else 0.0,
                "power":       0.0,
                "p_term":      0.0,
                "i_term":      0.0,
                "d_term":      0.0,
                "error":       None,
                "state":       "🚨 SENSOR FAULT",
                "system_fault":True,
                "fault_keys":  fault_keys,
                "modes":       self.manager.all_modes(),
                "temp_mode":   temp_r.mode.value,
                "hum_mode":    hum_r.mode.value,
                "amb_mode":    amb_r.mode.value,
                "alarm_count": self.manager.alarms.alarm_count,
            }

        # ── NORMAL: auto target + PID ─────────────────────────────────────────
        target, hb, ab = self.target_setter.compute(
            self.base_target, hum_r.value, amb_r.value, self.idle_mode
        )
        power, p, i, d = self.pid.compute(
            target, self._last_temp, dt=max(CFG.STEP_DELAY, 1.0)
        )
        self.heater.set_power(power)

        return {
            "step":        self._step,
            "sensor_temp": self._last_temp,
            "target":      target,
            "hum_boost":   hb,
            "amb_boost":   ab,
            "humidity":    round(hum_r.value, 1),
            "ambient":     round(amb_r.value, 1),
            "power":       power,
            "p_term":      p,
            "i_term":      i,
            "d_term":      d,
            "error":       round(target - self._last_temp, 2),
            "state":       self.heater.state_label(target, self._last_temp, False),
            "system_fault":False,
            "fault_keys":  [],
            "modes":       self.manager.all_modes(),
            "temp_mode":   temp_r.mode.value,
            "hum_mode":    hum_r.mode.value,
            "amb_mode":    amb_r.mode.value,
            "alarm_count": self.manager.alarms.alarm_count,
        }

    @staticmethod
    def log(d: dict) -> None:
        modes = f"[T:{d['temp_mode']} H:{d['hum_mode']} A:{d['amb_mode']}]"
        if d["system_fault"]:
            faults = ",".join(d["fault_keys"])
            print(
                f"[{d['step']:>4}] "
                f" FAULT — Sensors:[{faults}] | "
                f"LastTemp:{d['sensor_temp']:>7.2f}°C | "
                f"Power:  0.0% (HEATER OFF) | "
                f"Alarms:{d['alarm_count']}  {modes}"
            )
        else:
            print(
                f"[{d['step']:>4}] "
                f"Temp:{d['sensor_temp']:>7.2f}°C  "
                f"Tgt:{d['target']:>6.1f}°C  "
                f"Err:{d['error']:>+6.2f}  "
                f"Pwr:{d['power']:>5.1f}%  "
                f"P:{d['p_term']:>7.2f} "
                f"I:{d['i_term']:>6.2f} "
                f"D:{d['d_term']:>6.2f}  "
                f"Hum:{d['humidity']:>4.1f}%  "
                f"Amb:{d['ambient']:>4.1f}°C  "
                f"{modes}  {d['state']}"
            )

    def run(self, max_steps: int = CFG.MAX_STEPS,
                  step_delay: float = CFG.STEP_DELAY,
                  show_temp: bool = CFG.SHOW_CURRENT_TEMP,
                  show_temp_verbose: bool = CFG.SHOW_TEMP_VERBOSE) -> None:
        W = 115
        print("=" * W)
        print("  CORRUGATED MANUFACTURING — SMART SENSOR SYSTEM WITH FAULT ALARM")
        print("=" * W)
        print(f"  Base target       : {self.base_target}°C")
        print(f"  PID gains         : Kp={CFG.KP}  Ki={CFG.KI}  Kd={CFG.KD}")
        print(f"  Sensor modes      : {self.manager.all_modes()}")
        print(f"  Display temp      : {'✓ ENABLED' if show_temp else '✗ DISABLED'}")
        print(f"  On fault          : Heater → 0%  |  PID suspended  |  Alarm raised")
        print(f"  On recovery       : Alarm cleared  |  Normal operation resumes")
        print(f"  InfluxDB          : {'ENABLED → ' + INFLUXDB_URL if self.write_api else 'DISABLED'}")
        print("=" * W)

        try:
            step = 0
            while True:
                step += 1
                data = self.tick()
                self.log(data)

                # Display current temperature if enabled
                if show_temp:
                    current_temp = data.get("sensor_temp", 0.0)
                    target_temp = data.get("target", 0.0)
                    power = data.get("power", 0.0)
                    state = data.get("state", "UNKNOWN")
                    if show_temp_verbose:
                        print(f"  [TEMP] Current: {current_temp:.1f}°C | Target: {target_temp:.1f}°C | Power: {power:.1f}% | State: {state}")
                    else:
                        print(f"  [TEMP] {current_temp:.1f}°C → {target_temp:.1f}°C (Power: {power:.0f}%)")

                # Write to InfluxDB → Grafana reads this
                if self.write_api is not None:
                    write_heating_point(self.write_api, data)

                if max_steps and step >= max_steps:
                    print("-" * W)
                    print(f" Complete — {step} steps.")
                    self.manager.alarms.print_summary()
                    break

                time.sleep(step_delay)

        except KeyboardInterrupt:
            print(f"\n  [!] Stopped at step {self._step}.")
            self.manager.alarms.print_summary()
        finally:
            self.manager.disconnect_all()


# =============================================================================
#  ENTRY POINT
# =============================================================================
if __name__ == "__main__":

    # ── Parse command-line arguments ──────────────────────────────────────────
    parser = argparse.ArgumentParser(
        description="Smart Heating Controller Simulation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 heating_simulation.py --show-temp
  python3 heating_simulation.py --show-temp-verbose --steps 60
  python3 heating_simulation.py --no-show-temp
        """
    )
    parser.add_argument(
        "--show-temp",
        action="store_true",
        default=CFG.SHOW_CURRENT_TEMP,
        help="Display current temperature during execution (default: enabled)"
    )
    parser.add_argument(
        "--no-show-temp",
        action="store_false",
        dest="show_temp",
        help="Disable temperature display"
    )
    parser.add_argument(
        "--show-temp-verbose",
        action="store_true",
        default=CFG.SHOW_TEMP_VERBOSE,
        help="Display verbose temperature info (current, target, power, state)"
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=CFG.MAX_STEPS,
        help=f"Number of simulation steps (default: {CFG.MAX_STEPS}, 0=infinite)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=CFG.STEP_DELAY,
        help=f"Delay between steps in seconds (default: {CFG.STEP_DELAY})"
    )
    parser.add_argument(
        "--target-temp",
        type=float,
        default=140.0,
        help="Base target temperature in Celsius (default: 140.0)"
    )

    args = parser.parse_args()

    # ── Build sensor manager ───────────────────────────────────────────────────
    manager = SmartSensorManager()

    # ─────────────────────────────────────────────────────────────────────────
    #  OPTION A — Pure simulation (no hardware needed)
    #  Runs full physics model, no alarms possible.
    # ─────────────────────────────────────────────────────────────────────────
    manager.register("temp",     SimulatedTempSensor(initial_temp=30.0))
    manager.register("humidity", SimulatedHumiditySensor())
    manager.register("ambient",  SimulatedAmbientSensor())

    # ─────────────────────────────────────────────────────────────────────────
    #  OPTION B — With real sensors (uncomment, keep sim as first arg)
    #  If real sensor fails → ALARM + heater OFF. No simulation fallback.
    # ─────────────────────────────────────────────────────────────────────────

    # Modbus TCP (Siemens S7, Allen-Bradley, Schneider):
    # manager.register("temp",
    #     SimulatedTempSensor(initial_temp=30.0),
    #     real_sensor = ModbusTCPSensor(host="192.168.1.10", register=100, scale=0.1)
    # )

    # Modbus RTU over RS485:
    # manager.register("temp",
    #     SimulatedTempSensor(initial_temp=30.0),
    #     real_sensor = ModbusRTUSensor(port="/dev/ttyUSB0", slave_id=1, register=0)
    # )

    # 4-20mA via ADS1115 ADC (Raspberry Pi):
    # manager.register("temp",
    #     SimulatedTempSensor(initial_temp=30.0),
    #     real_sensor = AnalogSensor_4_20mA(channel=0, eng_min=80.0, eng_max=200.0)
    # )

    # OPC-UA (Siemens WinCC / Ignition SCADA):
    # manager.register("temp",
    #     SimulatedTempSensor(initial_temp=30.0),
    #     real_sensor = OPCUASensor(
    #         url="opc.tcp://192.168.1.20:4840", node_id="ns=2;i=1001")
    # )

    # Serial RS485 (Eurotherm / RKC / Autonics):
    # manager.register("temp",
    #     SimulatedTempSensor(initial_temp=30.0),
    #     real_sensor = SerialSensor(port="/dev/ttyUSB0", baudrate=9600, command="T\r")
    # )

    # MQTT (Node-RED / AWS IoT / local broker):
    # manager.register("temp",
    #     SimulatedTempSensor(initial_temp=30.0),
    #     real_sensor = MQTTSensor(broker="192.168.1.5", topic="factory/zone1/temp")
    # )

    # CSV replay (test with real historical plant data):
    # manager.register("temp",
    #     SimulatedTempSensor(initial_temp=30.0),
    #     real_sensor = CSVReplaySensor(filepath="plant_data.csv", column="temperature")
    # )

    # ── InfluxDB connection (optional — set env vars or set write_api=None) ───
    write_api    = None
    influx_client = None
    try:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS
        influx_client = InfluxDBClient(
            url   = INFLUXDB_URL,
            token = INFLUXDB_TOKEN,
            org   = INFLUXDB_ORG
        )
        write_api = influx_client.write_api(write_options=SYNCHRONOUS)
        log.info(f"InfluxDB connected → {INFLUXDB_URL}")
    except ImportError:
        log.warning("influxdb-client not installed — running without InfluxDB. "
                    "Install: pip install influxdb-client")
    except Exception as e:
        log.warning(f"InfluxDB connection failed: {e} — running without InfluxDB")

    # ── Run controller ────────────────────────────────────────────────────────
    controller = SmartHeatingController(
        manager     = manager,
        base_target = args.target_temp,
        idle_mode   = False,
        write_api   = write_api
    )

    try:
        controller.run(
            max_steps           = args.steps,
            step_delay          = args.delay,
            show_temp           = args.show_temp,
            show_temp_verbose   = args.show_temp_verbose
        )
    finally:
        if write_api:
            write_api.close()
        if influx_client:
            influx_client.close()