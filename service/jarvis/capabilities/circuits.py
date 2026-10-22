"""Wiring diagrams for hobby electronics: which wire goes where, with warnings and a starter sketch.

A built-in parts library (boards and common sensors/actuators, every pin with its role, plus each
part's supply range) wires any known board + part combination deterministically: power at the
right voltage, ground, and data on a free suitable pin (I2C on the board's SDA/SCL, PWM for a
servo, an ADC pin for a potentiometer). Level, current and pull-up pitfalls become warnings, and
helper parts (LED resistor, voltage divider, 3.3 V regulator, external 5 V supply, breadboard
rails) are drawn into the diagram.

Requests with parts outside the library go to the language model with a JSON schema. Its answer is
untrusted: it is validated against the library (a wire to a pin a known part does not have is
dropped), parts the library does not know are flagged, and if the model fails the deterministic
wiring of the known parts stands.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from jarvis import i18n
from jarvis.tools import Context, tool

log = logging.getLogger("jarvis.circuits")

COLORS = {"power": "#f97316", "ground": "#ffffff", "signal": "#38bdf8", "i2c": "#a78bfa", "pwm": "#22c55e"}
MUTED = "#64748b"
MAX_INSTANCES = 8
LANG_NAMES = {"ru": "Russian", "en": "English", "pt": "Brazilian Portuguese"}


# ================================================================ parts library

@dataclass(frozen=True)
class Board:
    key: str
    name: str
    logic: float                       # I/O voltage
    language: str                      # arduino | micropython
    family: str                        # arduino | esp32 | esp8266 | pico (pin naming in code)
    pins: dict                         # pin -> frozenset(roles)
    supply: dict                       # output volts -> pin
    gpio: tuple                        # preferred free digital pins, in order
    pwm: tuple
    analog: tuple
    sda: str
    scl: str
    aliases: tuple
    weak: tuple = ()
    pin_alias: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Part:
    key: str
    name: str
    subtitle: str
    pins: tuple                        # ((pin, need), ...) in physical order
    vcc: tuple | None                  # supply range (min, max) volts
    category: str
    aliases: tuple
    weak: tuple = ()
    i2c: int | None = None
    pin_alias: dict = field(default_factory=dict)


# Pin needs: power | power-adc (supply = ADC full scale) | ground | io | in | in-vcc (the part drives it
# at its own VCC) | out | pullup-in | pwm | analog | sda | scl | anode (through a resistor) | nc | load.
DIGITAL = {"io", "in", "in-vcc", "out", "pullup-in", "anode"}
NEED_KIND = {"power": "power", "power-adc": "power", "ground": "ground", "sda": "i2c", "scl": "i2c", "pwm": "pwm"}


def _frozen(spec: dict) -> dict:
    return {k: frozenset(v) for k, v in spec.items()}


def _arduino(analog: int, power: dict) -> dict:
    pins: dict = {}
    for n in range(14):
        roles = {"gpio"} | ({"pwm"} if n in (3, 5, 6, 9, 10, 11) else set())
        roles |= {"uart-rx"} if n == 0 else {"uart-tx"} if n == 1 else set()
        pins[f"D{n}"] = roles
    for n in range(analog):
        roles = {"analog"} | ({"gpio"} if n < 6 else set())
        roles |= {"i2c-sda"} if n == 4 else {"i2c-scl"} if n == 5 else set()
        pins[f"A{n}"] = roles
    pins.update({name: {role} for name, role in power.items()})
    return _frozen(pins)


def _arduino_alias(supply: dict) -> dict:
    alias = {str(n): f"D{n}" for n in range(14)}
    alias.update({"TX": "D1", "TXO": "D1", "TX0": "D1", "RX": "D0", "RXI": "D0", "RX0": "D0", "SDA": "A4", "SCL": "A5",
                  "GROUND": "GND", "G": "GND"})
    if 5.0 in supply:
        alias.update({"5V": supply[5.0], "+5V": supply[5.0]})
    if 3.3 in supply:
        alias.update({"3V3": supply[3.3], "3.3V": supply[3.3], "3,3V": supply[3.3], "+3V3": supply[3.3]})
    return alias


ARDUINO_GPIO = ("D2", "D4", "D7", "D8", "D12", "D13", "D3", "D5", "D6", "D10", "D11", "D9", "A0", "A1", "A2", "A3")
ARDUINO_PWM = ("D9", "D10", "D11", "D3", "D5", "D6")
UNO_SUPPLY = {5.0: "5V", 3.3: "3V3"}
UNO_POWER = {"5V": "power", "3V3": "power", "VIN": "vin", "GND": "ground", "IOREF": "power", "AREF": "ref", "RESET": "control"}
PROMINI_POWER = {"VCC": "power", "RAW": "vin", "GND": "ground", "RST": "control"}

ESP32_IO = (1, 2, 3, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33, 34, 35, 36, 39)


def _esp32_pins() -> dict:
    pins: dict = {}
    for n in ESP32_IO:
        roles = {"gpio-in", "analog"} if n in (34, 35, 36, 39) else {"gpio", "pwm"}
        if n in (2, 4, 12, 13, 14, 15, 25, 26, 27, 32, 33):
            roles.add("analog")
        roles |= {"i2c-sda"} if n == 21 else {"i2c-scl"} if n == 22 else set()
        roles |= {"uart-tx"} if n == 1 else {"uart-rx"} if n == 3 else set()
        pins[f"GPIO{n}"] = roles
    pins.update({"3V3": {"power"}, "VIN": {"power"}, "GND": {"ground"}, "EN": {"control"}})
    return _frozen(pins)


def _esp32_alias() -> dict:
    alias = {}
    for n in ESP32_IO:
        for form in (str(n), f"D{n}", f"IO{n}", f"G{n}"):
            alias[form] = f"GPIO{n}"
    alias.update({"VP": "GPIO36", "VN": "GPIO39", "TX0": "GPIO1", "TX": "GPIO1", "RX0": "GPIO3", "RX": "GPIO3",
                  "TX2": "GPIO17", "RX2": "GPIO16", "5V": "VIN", "+5V": "VIN", "VBUS": "VIN", "3.3V": "3V3",
                  "+3V3": "3V3", "SDA": "GPIO21", "SCL": "GPIO22", "GROUND": "GND"})
    return alias


NODEMCU = {"D0": 16, "D1": 5, "D2": 4, "D3": 0, "D4": 2, "D5": 14, "D6": 12, "D7": 13, "D8": 15, "RX": 3, "TX": 1}


def _nodemcu_pins() -> dict:
    pins: dict = {"D0": {"gpio"}}
    for d in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"):
        pins[d] = {"gpio", "pwm"}
    pins["D1"] |= {"i2c-scl"}
    pins["D2"] |= {"i2c-sda"}
    pins.update({"RX": {"gpio", "uart-rx"}, "TX": {"gpio", "uart-tx"}, "A0": {"analog"}, "3V3": {"power"},
                 "VIN": {"power"}, "GND": {"ground"}, "RST": {"control"}, "EN": {"control"}})
    return _frozen(pins)


def _nodemcu_alias() -> dict:
    alias = {}
    for d, g in NODEMCU.items():
        for form in (str(g), f"GPIO{g}", f"IO{g}"):
            alias[form] = d
    alias.update({"SDA": "D2", "SCL": "D1", "5V": "VIN", "+5V": "VIN", "VU": "VIN", "VUSB": "VIN", "3.3V": "3V3",
                  "ADC": "A0", "ADC0": "A0", "GROUND": "GND"})
    return alias


PICO_IO = tuple(range(23)) + (26, 27, 28)


def _pico_pins() -> dict:
    pins: dict = {}
    for n in PICO_IO:
        roles = {"gpio", "pwm"} | ({"analog"} if n >= 26 else set())
        roles |= {"i2c-sda"} if n == 4 else {"i2c-scl"} if n == 5 else set()
        roles |= {"uart-tx"} if n == 0 else {"uart-rx"} if n == 1 else set()
        pins[f"GP{n}"] = roles
    pins.update({"VBUS": {"power"}, "VSYS": {"vin"}, "3V3": {"power"}, "3V3_EN": {"control"}, "GND": {"ground"},
                 "AGND": {"ground"}, "RUN": {"control"}, "ADC_VREF": {"ref"}})
    return _frozen(pins)


def _pico_alias() -> dict:
    alias = {}
    for n in PICO_IO:
        for form in (str(n), f"GPIO{n}", f"IO{n}"):
            alias[form] = f"GP{n}"
    alias.update({"ADC0": "GP26", "ADC1": "GP27", "ADC2": "GP28", "5V": "VBUS", "+5V": "VBUS", "3.3V": "3V3",
                  "3V3(OUT)": "3V3", "3V3OUT": "3V3", "+3V3": "3V3", "SDA": "GP4", "SCL": "GP5", "GROUND": "GND"})
    return alias


BOARDS: dict[str, Board] = {
    "uno": Board("uno", "Arduino Uno", 5.0, "arduino", "arduino", _arduino(6, UNO_POWER), UNO_SUPPLY,
                 ARDUINO_GPIO, ARDUINO_PWM, ("A0", "A1", "A2", "A3", "A4", "A5"), "A4", "A5",
                 ("arduino uno", "uno", "ардуино уно", "уно"), ("arduino", "ардуино", "ардуинк"),
                 _arduino_alias(UNO_SUPPLY)),
    "nano": Board("nano", "Arduino Nano", 5.0, "arduino", "arduino", _arduino(8, UNO_POWER), UNO_SUPPLY,
                  ARDUINO_GPIO, ARDUINO_PWM, ("A0", "A1", "A2", "A3", "A6", "A7", "A4", "A5"), "A4", "A5",
                  ("arduino nano", "nano", "ардуино нано", "нано"), (), _arduino_alias(UNO_SUPPLY)),
    "promini5": Board("promini5", "Arduino Pro Mini 5V", 5.0, "arduino", "arduino", _arduino(8, PROMINI_POWER),
                      {5.0: "VCC"}, ARDUINO_GPIO, ARDUINO_PWM, ("A0", "A1", "A2", "A3", "A6", "A7", "A4", "A5"),
                      "A4", "A5", ("arduino pro mini", "pro mini", "promini", "ардуино про мини", "про мини"), (),
                      _arduino_alias({5.0: "VCC"})),
    "promini3": Board("promini3", "Arduino Pro Mini 3.3V", 3.3, "arduino", "arduino", _arduino(8, PROMINI_POWER),
                      {3.3: "VCC"}, ARDUINO_GPIO, ARDUINO_PWM, ("A0", "A1", "A2", "A3", "A6", "A7", "A4", "A5"),
                      "A4", "A5", (), (), _arduino_alias({3.3: "VCC"})),
    "esp32": Board("esp32", "ESP32 DevKit", 3.3, "arduino", "esp32", _esp32_pins(), {5.0: "VIN", 3.3: "3V3"},
                   ("GPIO4", "GPIO16", "GPIO17", "GPIO18", "GPIO19", "GPIO23", "GPIO25", "GPIO26", "GPIO27",
                    "GPIO32", "GPIO33", "GPIO13", "GPIO14", "GPIO5"),
                   ("GPIO18", "GPIO19", "GPIO23", "GPIO25", "GPIO26", "GPIO27", "GPIO32", "GPIO33", "GPIO13",
                    "GPIO14", "GPIO4", "GPIO16", "GPIO17"),
                   ("GPIO34", "GPIO35", "GPIO32", "GPIO33", "GPIO36", "GPIO39"), "GPIO21", "GPIO22",
                   ("esp32 devkit", "esp32 dev kit", "esp wroom 32", "esp32 wroom", "doit esp32", "esp32", "esp 32",
                    "есп32", "есп 32"), (), _esp32_alias()),
    "nodemcu": Board("nodemcu", "ESP8266 NodeMCU", 3.3, "arduino", "esp8266", _nodemcu_pins(), {5.0: "VIN", 3.3: "3V3"},
                     ("D5", "D6", "D7", "D1", "D2", "D0"), ("D5", "D6", "D7", "D1", "D2"), ("A0",), "D2", "D1",
                     ("esp8266 nodemcu", "nodemcu", "node mcu", "esp8266", "esp 8266", "есп8266", "есп 8266", "нодемсу"),
                     (), _nodemcu_alias()),
    "pico": Board("pico", "Raspberry Pi Pico", 3.3, "micropython", "pico", _pico_pins(), {5.0: "VBUS", 3.3: "3V3"},
                  ("GP15", "GP14", "GP13", "GP12", "GP11", "GP10", "GP16", "GP17", "GP18", "GP19", "GP20", "GP21",
                   "GP22", "GP2", "GP3", "GP6", "GP7", "GP8", "GP9"),
                  ("GP16", "GP15", "GP14", "GP13", "GP12", "GP11", "GP10", "GP17", "GP18", "GP19", "GP20", "GP21"),
                  ("GP26", "GP27", "GP28"), "GP4", "GP5",
                  ("raspberry pi pico", "raspberry pico", "pi pico", "pico", "rp2040", "распберри пико", "пико"), (),
                  _pico_alias()),
}

_VCC = {"+": "VCC", "V+": "VCC", "VDD": "VCC", "VIN": "VCC", "5V": "VCC", "3V3": "VCC", "3.3V": "VCC",
        "-": "GND", "V-": "GND", "G": "GND", "GROUND": "GND"}

PARTS: dict[str, Part] = {
    "dht11": Part("dht11", "DHT11", "temperature + humidity", (("VCC", "power"), ("DATA", "io"), ("GND", "ground")),
                  (3.3, 5.5), "climate", ("dht11", "dht 11"),
                  ("temperature and humidity sensor", "temperature sensor", "humidity sensor",
                   "датчик температуры и влажности", "датчик температур", "датчик влажност", "термодатчик",
                   "sensor de temperatura", "sensor de umidade"),
                  pin_alias={**_VCC, "OUT": "DATA", "S": "DATA", "SIG": "DATA", "SIGNAL": "DATA", "DAT": "DATA"}),
    "dht22": Part("dht22", "DHT22 (AM2302)", "temperature + humidity", (("VCC", "power"), ("DATA", "io"), ("GND", "ground")),
                  (3.3, 6.0), "climate", ("dht22", "dht 22", "am2302"),
                  pin_alias={**_VCC, "OUT": "DATA", "S": "DATA", "SIG": "DATA", "SIGNAL": "DATA", "DAT": "DATA"}),
    "hcsr04": Part("hcsr04", "HC-SR04", "ultrasonic distance",
                   (("VCC", "power"), ("TRIG", "out"), ("ECHO", "in-vcc"), ("GND", "ground")), (4.5, 5.5), "distance",
                   ("hc sr04", "hcsr04", "hc sr 04", "sr04"),
                   ("ultrasonic sensor", "ultrasonic", "distance sensor", "ультразвуков датчик", "ультразвуков",
                    "дальномер", "датчик расстояни", "sensor ultrassonico", "sensor de distancia"),
                   pin_alias={**_VCC, "TRIGGER": "TRIG", "TRG": "TRIG", "ECH": "ECHO"}),
    "sg90": Part("sg90", "SG90 servo", "micro servo", (("GND", "ground"), ("VCC", "power"), ("SIG", "pwm")),
                 (4.8, 6.0), "servo",
                 ("sg90", "sg 90", "micro servo", "microservo", "servo motor", "servomotor", "servo", "сервопривод",
                  "сервомотор", "сервомашинк", "серво"),
                 pin_alias={**_VCC, "SIGNAL": "SIG", "S": "SIG", "PWM": "SIG", "DATA": "SIG", "IN": "SIG",
                            "ORANGE": "SIG", "YELLOW": "SIG", "RED": "VCC", "BROWN": "GND", "BLACK": "GND"}),
    "led": Part("led", "LED", "+ 220 Ω resistor", (("A", "anode"), ("K", "ground")), None, "led",
                ("led", "светодиод", "лампочк", "diodo led"),
                pin_alias={"+": "A", "ANODE": "A", "LONG": "A", "-": "K", "CATHODE": "K", "C": "K", "SHORT": "K",
                           "GND": "K"}),
    "button": Part("button", "Push button", "INPUT_PULLUP", (("1", "pullup-in"), ("2", "ground")), None, "button",
                   ("push button", "pushbutton", "tactile switch", "button", "кнопк", "botao", "botoes", "pulsador"),
                   pin_alias={"A": "1", "IN": "1", "SIG": "1", "OUT": "1", "S": "1", "B": "2", "GND": "2", "-": "2"}),
    "pot": Part("pot", "Potentiometer 10 kΩ", "analog input", (("VCC", "power-adc"), ("WIPER", "analog"), ("GND", "ground")),
                (0.0, 5.5), "pot",
                ("potentiometer", "pot 10k", "10k pot", "potenciometro", "потенциометр", "переменн резистор", "крутилк"),
                pin_alias={**_VCC, "1": "VCC", "2": "WIPER", "W": "WIPER", "SIG": "WIPER", "OUT": "WIPER", "S": "WIPER",
                           "3": "GND"}),
    "lcd1602": Part("lcd1602", "LCD 16x2 (I2C)", "PCF8574 backpack, 0x27",
                    (("GND", "ground"), ("VCC", "power"), ("SDA", "sda"), ("SCL", "scl")), (4.5, 5.5), "lcd",
                    ("lcd 1602", "lcd1602", "lcd 16x2", "16x2", "1602", "lcd display", "lcd дисплей", "жк дисплей",
                     "жк экран", "дисплей 1602", "экран 1602", "lcd"),
                    i2c=0x27, pin_alias={**_VCC, "SCK": "SCL", "SDI": "SDA"}),
    "ssd1306": Part("ssd1306", "SSD1306 OLED", "128x64 I2C, 0x3C",
                    (("GND", "ground"), ("VCC", "power"), ("SCL", "scl"), ("SDA", "sda")), (3.3, 5.0), "oled",
                    ("ssd1306", "ssd 1306", "oled display", "oled дисплей", "oled экран", "oled", "олед", "128x64"),
                    i2c=0x3C, pin_alias={**_VCC, "SCK": "SCL", "SDI": "SDA", "D0": "SCL", "D1": "SDA"}),
    "relay": Part("relay", "Relay module 5 V", "1 channel",
                  (("VCC", "power"), ("GND", "ground"), ("IN", "out"), ("COM", "load"), ("NO", "load"), ("NC", "load")),
                  (4.5, 5.5), "relay", ("relay module", "relay", "реле", "rele", "modulo rele"),
                  pin_alias={**_VCC, "IN1": "IN", "S": "IN", "SIG": "IN", "SIGNAL": "IN", "DC+": "VCC", "DC-": "GND"}),
    "bmp280": Part("bmp280", "BMP280", "pressure + temperature, I2C 0x76",
                   (("VCC", "power"), ("GND", "ground"), ("SCL", "scl"), ("SDA", "sda"), ("CSB", "nc"), ("SDO", "nc")),
                   (1.8, 3.6), "baro", ("bmp280", "bmp 280", "gy bmp280"),
                   ("barometer", "барометр", "pressure sensor", "датчик давлени", "barometro", "sensor de pressao"),
                   i2c=0x76, pin_alias={**_VCC, "SCK": "SCL", "SDI": "SDA"}),
    "mpu6050": Part("mpu6050", "MPU6050 (GY-521)", "accel + gyro, I2C 0x68",
                    (("VCC", "power"), ("GND", "ground"), ("SCL", "scl"), ("SDA", "sda"), ("XDA", "nc"), ("XCL", "nc"),
                     ("AD0", "nc"), ("INT", "nc")), (3.3, 5.0), "imu", ("mpu6050", "mpu 6050", "gy 521", "gy521"),
                    ("accelerometer", "gyroscope", "gyro", "акселерометр", "гироскоп", "acelerometro", "giroscopio"),
                    i2c=0x68, pin_alias={**_VCC, "SCK": "SCL"}),
    "pir": Part("pir", "HC-SR501 PIR", "motion sensor", (("VCC", "power"), ("OUT", "in"), ("GND", "ground")),
                (4.5, 20.0), "motion", ("hc sr501", "hcsr501", "sr501", "pir sensor", "pir"),
                ("motion sensor", "датчик движени", "датчик присутстви", "sensor de movimento", "sensor de presenca"),
                pin_alias={**_VCC, "SIG": "OUT", "S": "OUT", "DATA": "OUT"}),
}

RAILS = {"board": ("breadboard rails", "breadboard", "макетн плат", "макетк", "protoboard"),
         "mb102": ("breadboard power supply", "breadboard power module", "power supply module", "mb102",
                   "модуль питания", "блок питания макетк", "fonte protoboard", "fonte de protoboard")}
RAIL = {5.0: "+5V", 3.3: "+3V3"}

# ================================================================ language

W = {
    "dht_pullup": {"en": "{name}: a bare 4-pin sensor needs a 10 kΩ pull-up resistor between DATA and VCC (3-pin modules already have it).",
                   "ru": "{name}: голому 4-выводному датчику нужен подтягивающий резистор 10 кОм между DATA и VCC (в 3-выводных модулях он уже есть).",
                   "pt": "{name}: o sensor de 4 pinos sem placa precisa de um resistor pull-up de 10 kΩ entre DATA e VCC (módulos de 3 pinos já têm)."},
    "divider": {"en": "{name} ECHO outputs 5 V but {board} pins take 3.3 V: keep the voltage divider (1 kΩ from ECHO, 2 kΩ to GND) or use a level shifter.",
                "ru": "{name}: ECHO выдаёт 5 В, а выводы {board} рассчитаны на 3,3 В — нужен делитель напряжения (1 кОм от ECHO, 2 кОм на GND) или преобразователь уровней.",
                "pt": "{name}: o ECHO envia 5 V, mas os pinos da {board} aceitam 3,3 V — use o divisor de tensão (1 kΩ do ECHO, 2 kΩ para GND) ou um conversor de nível."},
    "servo_usb": {"en": "{name} can draw ~700 mA when it starts or stalls, more than USB safely gives: for real loads power it from a separate 5 V supply and join that supply's GND to the board's GND.",
                  "ru": "{name} при старте или упоре берёт до ~700 мА — больше, чем безопасно даёт USB: под нагрузкой питайте его от отдельного источника 5 В и соедините его GND с GND платы.",
                  "pt": "{name} pode puxar ~700 mA ao arrancar ou travar, mais do que o USB fornece com segurança: com carga, use uma fonte de 5 V separada e ligue o GND dela ao GND da placa."},
    "led_resistor": {"en": "Never connect the LED without its 220 Ω resistor; the long leg (anode) goes to the resistor side, the short leg to GND.",
                     "ru": "Не подключайте светодиод без резистора 220 Ом; длинная ножка (анод) — к резистору, короткая — к GND.",
                     "pt": "Nunca ligue o LED sem o resistor de 220 Ω; a perna longa (ânodo) vai para o resistor, a curta para o GND."},
    "relay_mains": {"en": "Relay contacts switching mains (110–230 V) are dangerous: wire them only unplugged, insulated and enclosed, or ask someone qualified. Many relay modules switch on a LOW input.",
                    "ru": "Контакты реле с сетевым напряжением (220 В) опасны: подключайте их только обесточенными, изолированными и в корпусе — или поручите специалисту. Многие модули реле включаются НИЗКИМ уровнем.",
                    "pt": "Contatos do relé com tensão da rede (127–220 V) são perigosos: ligue só desenergizado, isolado e em caixa, ou peça a alguém qualificado. Muitos módulos de relé acionam com nível BAIXO."},
    "relay_33": {"en": "A 5 V relay module may not switch reliably from {board}'s 3.3 V pins: use a 3.3 V-compatible module or a transistor driver.",
                 "ru": "Модуль реле на 5 В может ненадёжно срабатывать от 3,3 В выводов {board}: возьмите модуль, совместимый с 3,3 В, или транзисторный ключ.",
                 "pt": "Um módulo de relé de 5 V pode não acionar bem com os pinos de 3,3 V da {board}: use um módulo compatível com 3,3 V ou um transistor."},
    "lcd_33": {"en": "The LCD backpack pulls SDA/SCL up to 5 V but {board} pins take 3.3 V: add an I2C level shifter.",
               "ru": "Плата I2C у LCD подтягивает SDA/SCL к 5 В, а выводы {board} рассчитаны на 3,3 В — поставьте преобразователь уровней I2C.",
               "pt": "A placa I2C do LCD puxa SDA/SCL para 5 V, mas os pinos da {board} aceitam 3,3 V — use um conversor de nível I2C."},
    "bmp_5v": {"en": "BMP280 is a 3.3 V chip: power it from 3.3 V, and with a 5 V board use a module with level shifting (or an I2C level shifter).",
               "ru": "BMP280 — микросхема на 3,3 В: питайте её от 3,3 В, а с 5-вольтовой платой берите модуль со сдвигом уровней (или преобразователь уровней I2C).",
               "pt": "O BMP280 é um chip de 3,3 V: alimente com 3,3 V e, numa placa de 5 V, use um módulo com conversor de nível (ou um conversor I2C)."},
    "regulator": {"en": "{board} has no 3.3 V output: {name} gets 3.3 V from an AMS1117-3.3 regulator fed from 5 V.",
                  "ru": "У {board} нет выхода 3,3 В: {name} питается через стабилизатор AMS1117-3.3 от 5 В.",
                  "pt": "A {board} não tem saída de 3,3 V: {name} recebe 3,3 V de um regulador AMS1117-3.3 ligado aos 5 V."},
    "external5": {"en": "{board} has no 5 V output: power {name} from an external 5 V supply and join its GND to the board's GND.",
                  "ru": "У {board} нет выхода 5 В: питайте {name} от внешнего источника 5 В и соедините его GND с GND платы.",
                  "pt": "A {board} não tem saída de 5 V: alimente {name} com uma fonte externa de 5 V e ligue o GND dela ao GND da placa."},
    "usb5v": {"en": "{pin} on the {board} carries 5 V only while the board is powered from USB.",
              "ru": "Вывод {pin} на {board} даёт 5 В, только когда плата питается от USB.",
              "pt": "O pino {pin} da {board} só tem 5 V quando a placa está ligada no USB."},
    "no_pin": {"en": "Ran out of free {kind} pins on the {board} for {name}.",
               "ru": "На {board} не хватило свободных выводов ({kind}) для {name}.",
               "pt": "Faltaram pinos {kind} livres na {board} para {name}."},
    "no_power": {"en": "No suitable supply for {name} on the {board}: check its datasheet.",
                 "ru": "Для {name} на {board} нет подходящего питания: сверьтесь с даташитом.",
                 "pt": "Não há alimentação adequada para {name} na {board}: confira o datasheet."},
    "i2c_conflict": {"en": "Two devices share I2C address {addr}: change one's address (ADDR jumper) or use an I2C multiplexer.",
                     "ru": "Два устройства с одним I2C-адресом {addr}: смените адрес одного (перемычка ADDR) или поставьте мультиплексор I2C.",
                     "pt": "Dois dispositivos usam o endereço I2C {addr}: mude o endereço de um (jumper ADDR) ou use um multiplexador I2C."},
    "assumed_board": {"en": "No board named, so I assumed an Arduino Uno.", "ru": "Плата не указана — взял Arduino Uno.",
                      "pt": "Nenhuma placa indicada, então assumi um Arduino Uno."},
    "two_boards": {"en": "Several boards mentioned; this wiring uses the {board}.", "ru": "Упомянуто несколько плат; схема для {board}.",
                   "pt": "Várias placas mencionadas; esta ligação usa a {board}."},
    "unknown_part": {"en": "Not in my parts library: {name}. Check its datasheet before wiring.",
                     "ru": "Нет в моей библиотеке деталей: {name}. Сверьтесь с даташитом перед подключением.",
                     "pt": "Fora da minha biblioteca de peças: {name}. Confira o datasheet antes de ligar."},
    "dropped_wire": {"en": "Dropped a suggested wire to a pin that doesn't exist: {wire}.",
                     "ru": "Убрал предложенный провод к несуществующему выводу: {wire}.",
                     "pt": "Removi um fio sugerido para um pino que não existe: {wire}."},
    "volt_mismatch": {"en": "{name} {pin} is wired to {volts} V but takes {lo}–{hi} V.",
                      "ru": "{name} {pin} подключён к {volts} В, а рассчитан на {lo}–{hi} В.",
                      "pt": "{name} {pin} está ligado a {volts} V, mas aceita {lo}–{hi} V."},
    "rails_mb102": {"en": "With the MB102 module feeding the rails, don't also connect the board's 5V to them; join only the GNDs.",
                    "ru": "Когда шины питает модуль MB102, не подключайте к ним ещё и 5V платы — соедините только GND.",
                    "pt": "Com o módulo MB102 alimentando os trilhos, não ligue também os 5V da placa a eles; una só os GNDs."},
    "model_unverified": {"en": "Wiring for parts outside my library was suggested by the model: double-check it.",
                         "ru": "Подключение деталей вне моей библиотеки предложила модель — перепроверьте его.",
                         "pt": "A ligação de peças fora da minha biblioteca foi sugerida pelo modelo: confira."},
}

S = {
    "connect": {"en": "Connect {name}: {links}.", "ru": "Подключите {name}: {links}.", "pt": "Conecte {name}: {links}."},
    "power_first": {"en": "Power first: {links}.", "ru": "Сначала питание: {links}.", "pt": "Primeiro a alimentação: {links}."},
    "upload_arduino": {"en": "Upload the sketch with the Arduino IDE (board: {board}) and open the Serial Monitor at 115200 baud.",
                       "ru": "Загрузите скетч через Arduino IDE (плата: {board}) и откройте монитор порта на 115200 бод.",
                       "pt": "Envie o sketch pela Arduino IDE (placa: {board}) e abra o Monitor Serial em 115200 baud."},
    "upload_esp": {"en": "Install the {core} board package in the Arduino IDE, upload the sketch and open the Serial Monitor at 115200 baud.",
                   "ru": "Установите в Arduino IDE пакет плат {core}, загрузите скетч и откройте монитор порта на 115200 бод.",
                   "pt": "Instale o pacote de placas {core} na Arduino IDE, envie o sketch e abra o Monitor Serial em 115200 baud."},
    "upload_promini": {"en": "The Pro Mini has no USB: program it through a USB-serial (FTDI) adapter set to {volts} V on its GND/VCC/RXI/TXO/DTR header.",
                       "ru": "У Pro Mini нет USB: прошивайте через USB-UART (FTDI) адаптер на {volts} В, подключённый к выводам GND/VCC/RXI/TXO/DTR.",
                       "pt": "A Pro Mini não tem USB: grave por um adaptador USB-serial (FTDI) em {volts} V nos pinos GND/VCC/RXI/TXO/DTR."},
    "upload_pico": {"en": "Flash MicroPython onto the Pico, save the script as main.py (Thonny or mpremote) and watch the output in the REPL.",
                    "ru": "Прошейте Pico MicroPython, сохраните скрипт как main.py (Thonny или mpremote) и смотрите вывод в REPL.",
                    "pt": "Grave o MicroPython na Pico, salve o script como main.py (Thonny ou mpremote) e veja a saída no REPL."},
    "pir_warmup": {"en": "Give the HC-SR501 30–60 s to settle after power-up; its trimmers set sensitivity and hold time.",
                   "ru": "После включения HC-SR501 нужно 30–60 с на прогрев; подстроечники задают чувствительность и время удержания.",
                   "pt": "Espere 30–60 s após ligar o HC-SR501; os trimpots ajustam sensibilidade e tempo."},
    "libs": {"en": "Libraries: {libs}.", "ru": "Библиотеки: {libs}.", "pt": "Bibliotecas: {libs}."},
    "logic": {"en": "{v} V logic", "ru": "логика {v} В", "pt": "lógica {v} V"},
    "rail": {"en": "{rail} rail", "ru": "шина {rail}", "pt": "trilho {rail}"},
    "divider": {"en": "divider", "ru": "делитель", "pt": "divisor"},
    "external": {"en": "external +5V", "ru": "внешние +5V", "pt": "+5V externo"},
    "summary": {"en": "{title}: {wires} wires in {steps} steps. {warn}", "ru": "{title}: проводов — {wires}, шагов — {steps}. {warn}",
                "pt": "{title}: {wires} fios em {steps} passos. {warn}"},
    "warn_n": {"en": "{n} warning(s), see the card.", "ru": "Предупреждений: {n}, смотрите карточку.", "pt": "{n} aviso(s), veja o cartão."},
    "warn_0": {"en": "No warnings.", "ru": "Без предупреждений.", "pt": "Sem avisos."},
    "which_parts": {"en": "Which parts? Name a board and what to connect, e.g. 'DHT11 to Arduino Nano'.",
                    "ru": "Какие детали? Назовите плату и что подключить, например «DHT11 к Arduino Nano».",
                    "pt": "Quais peças? Diga a placa e o que ligar, por exemplo 'DHT11 na Arduino Nano'."},
    "unknown_only": {"en": "{names}: not in my parts library, and the model couldn't plan it. Check the datasheet.",
                     "ru": "{names}: этого нет в моей библиотеке деталей, а модель не смогла составить схему. Сверьтесь с даташитом.",
                     "pt": "{names}: fora da minha biblioteca de peças, e o modelo não conseguiu planejar. Confira o datasheet."},
    "unverified": {"en": "not in library", "ru": "нет в библиотеке", "pt": "fora da biblioteca"},
}

AUX = {
    "r": {"en": "Resistor", "ru": "Резистор", "pt": "Resistor"},
    "div": {"en": "Voltage divider", "ru": "Делитель напряжения", "pt": "Divisor de tensão"},
    "reg": {"en": "3.3 V regulator", "ru": "Стабилизатор 3,3 В", "pt": "Regulador 3,3 V"},
    "psu": {"en": "External 5 V supply", "ru": "Внешний БП 5 В", "pt": "Fonte 5 V externa"},
    "bb": {"en": "Breadboard rails", "ru": "Шины макетки", "pt": "Trilhos da protoboard"},
    "mb102": {"en": "MB102 power module", "ru": "Модуль питания MB102", "pt": "Módulo MB102"},
}


def _t(table: dict, key: str, lang: str, **fmt) -> str:
    entry = table[key]
    return entry.get(lang, entry["en"]).format(**fmt)


def _v(volts: float) -> str:
    return f"{volts:g}"


def _clean(value, limit: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value if value is not None else "")).strip()[:limit]


# ================================================================ matching

_PLAIN = str.maketrans("ãõçáéíóúâêôàü", "aocaeiouaeoau")
_QTY = {"two": 2, "three": 3, "four": 4, "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3, "четыре": 4,
        "dois": 2, "duas": 2, "tres": 3, "quatro": 4}
_UNIT = re.compile(r"^(?:\d+(?:[.,]\d+)?(?:v|в|k|к|ом|ohms?|kohm|ком|ma|ма|mhz|мгц|ms|мс|mm|мм|cm|см|шт|x\d+)?|"
                   r"3v3|gpio\d+|gp\d+|io\d+|d\d{1,2}|a\d|i2c|pin\d+|x\d+|\d+x\d+)$")
_HINTS = ("motor", "мотор", "двигател", "stepper", "шагов", "driver", "драйвер", "display", "дисплей", "экран", "screen",
          "tela", "strip", "лент", "neopixel", "camera", "камер", "gps", "rfid", "bluetooth", "блютуз", "keypad",
          "клавиатур", "buzzer", "зуммер", "пищалк", "speaker", "динамик", "microphone", "микрофон", "joystick",
          "джойстик", "rtc", "lora", "transistor", "транзистор", "mosfet", "мосфет", "battery", "аккумулятор",
          "батаре", "solar", "солнечн", "газ", "gas", "soil", "почв", "encoder", "энкодер", "laser", "лазер", "fan",
          "вентилятор", "pump", "насос", "matrix", "матриц", "thermocouple", "термопар", "load cell", "тензо")
_MERGE_STOP = {"to", "on", "at", "in", "of", "pin", "pins", "gpio", "gp", "io", "and", "the", "with", "x", "d", "a",
               "на", "к", "в", "и", "с", "em", "no", "na", "de", "e", "um", "uma"}


def _norm(text: str) -> str:
    t = str(text).lower().replace("ё", "е").translate(_PLAIN)
    t = re.sub(r"[-_/]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _alias_re(alias: str) -> re.Pattern:
    out = []
    for word in _norm(alias).split(" "):
        if re.search(r"[а-я]$", word):
            out.append(re.escape(word) + r"[а-я]{0,4}")
        elif re.search(r"[a-z]$", word) and len(word) >= 3 and not re.search(r"\d", word):
            out.append(re.escape(word) + r"(?:e?s)?")
        else:
            out.append(re.escape(word))
    return re.compile(r"(?<![\w.,])" + r"\s".join(out) + r"(?![\w])")


@dataclass(frozen=True)
class _Entry:
    kind: str          # board | part | rails
    key: str
    strong: bool
    category: str
    length: int
    pattern: re.Pattern


def _catalog() -> list[_Entry]:
    entries = []
    for b in BOARDS.values():
        for alias in b.aliases:
            entries.append(_Entry("board", b.key, True, "board", len(alias), _alias_re(alias)))
        for alias in b.weak:
            entries.append(_Entry("board", b.key, False, "board", len(alias), _alias_re(alias)))
    for p in PARTS.values():
        for alias in p.aliases:
            entries.append(_Entry("part", p.key, True, p.category, len(alias), _alias_re(alias)))
        for alias in p.weak:
            entries.append(_Entry("part", p.key, False, p.category, len(alias), _alias_re(alias)))
    for mode, aliases in RAILS.items():
        for alias in aliases:
            entries.append(_Entry("rails", mode, True, "rails", len(alias), _alias_re(alias)))
    return sorted(entries, key=lambda e: -e.length)


CATALOG = _catalog()


def _qty(prefix: str) -> int:
    m = re.search(r"(?:^|\s)(\d{1,2}|" + "|".join(_QTY) + r")\s*(?:x\s*)?$", prefix)
    if not m:
        return 1
    raw = m.group(1)
    return max(1, min(4, int(raw) if raw.isdigit() else _QTY[raw]))


def _scan(text: str) -> tuple[list[tuple[_Entry, int]], str, str]:
    """Known boards/parts/rails in the text (longest alias first, non-overlapping), with quantities;
    returns (matches in text order, leftover text with the matches blanked, normalized text)."""
    t = _norm(text)
    taken = [False] * len(t)
    found: list[tuple[int, _Entry, int]] = []
    for entry in CATALOG:
        for m in entry.pattern.finditer(t):
            s, e = m.span()
            if any(taken[s:e]):
                continue
            for i in range(s, e):
                taken[i] = True
            found.append((s, entry, _qty(t[:s])))
    leftover = "".join(" " if taken[i] else ch for i, ch in enumerate(t))
    return [(entry, qty) for _, entry, qty in sorted(found, key=lambda f: f[0])], leftover, t


def _unknown(leftover: str) -> list[str]:
    """Part-like words the library does not know (model numbers, 'motor', 'дисплей'…), grouped into phrases."""
    tokens = re.findall(r"[a-zа-я0-9.,+]+", leftover)
    merged: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if re.fullmatch(r"[a-z]{2,5}", tok) and tok not in _MERGE_STOP and re.fullmatch(r"\d{1,5}[a-z]{0,3}", nxt):
            merged.append(tok + nxt)       # "mq 2" -> "mq2", "hc 05" -> "hc05"
            i += 2
            continue
        merged.append(tok)
        i += 1
    phrases: list[str] = []
    current: list[str] = []
    for tok in merged:
        is_model = bool(re.search(r"[a-zа-я]", tok) and re.search(r"\d", tok)) and not _UNIT.match(tok)
        is_hint = any(tok.startswith(h) for h in _HINTS)
        if is_model or is_hint:
            current.append(tok)
        elif current:
            phrases.append(" ".join(current))
            current = []
    if current:
        phrases.append(" ".join(current))
    return list(dict.fromkeys(phrases))[:6]


@dataclass
class Parsed:
    board: str | None = None
    boards: list = field(default_factory=list)
    parts: list = field(default_factory=list)          # [(key, qty)] in order
    unknown: list = field(default_factory=list)
    rails: str = ""

    @property
    def count(self) -> int:
        return sum(q for _, q in self.parts)


def parse(request: str, components: list | None = None) -> Parsed:
    out = Parsed()
    texts = [request] + [str(c) for c in (components or []) if str(c).strip()]
    strong_boards: list[str] = []
    weak_boards: list[str] = []
    picked: dict[str, int] = {}
    weak_parts: list[tuple[str, int, str]] = []
    strong_categories: set[str] = set()
    for idx, text in enumerate(texts):
        matches, leftover, normalized = _scan(text)
        for entry, qty in matches:
            if entry.kind == "board":
                key = entry.key
                if key == "promini5" and re.search(r"3[.,]3\s*(?:v|в|volt|вольт)|3v3|8\s*(?:mhz|мгц)", normalized):
                    key = "promini3"
                (strong_boards if entry.strong else weak_boards).append(key)
            elif entry.kind == "rails":
                if entry.key == "mb102" or not out.rails:
                    out.rails = entry.key
            elif entry.strong:
                strong_categories.add(entry.category)
                picked[entry.key] = max(picked.get(entry.key, 0), qty)
            else:
                weak_parts.append((entry.key, qty, entry.category))
        unknown = _unknown(leftover)
        if idx > 0 and not matches and not unknown:
            unknown = [_clean(text, 40)]          # a listed component the library does not know at all
        out.unknown.extend(u for u in unknown if u not in out.unknown)
    for key, qty, category in weak_parts:
        if category not in strong_categories and key not in picked:
            picked[key] = qty
    out.parts = list(picked.items())
    out.boards = list(dict.fromkeys(strong_boards or weak_boards))
    out.board = out.boards[0] if out.boards else None
    return out


# ================================================================ canonical pins

def board_pin(board: Board, raw) -> str | None:
    s = re.sub(r"\s+", "", str(raw or "")).upper()
    s = re.sub(r"^PIN", "", s) or s
    if s in board.pins:
        return s
    return board.pin_alias.get(s)


def part_pin(part: Part, raw) -> str | None:
    s = re.sub(r"\s+", "", str(raw or "")).upper()
    names = [p for p, _ in part.pins]
    if s in names:
        return s
    alias = part.pin_alias.get(s)
    return alias if alias in names else None


def code_pin(board: Board, pin: str) -> str:
    if board.family == "arduino":
        return pin[1:] if pin.startswith("D") else pin
    if board.family == "esp32":
        return pin[4:] if pin.startswith("GPIO") else pin
    if board.family == "pico":
        return pin[2:] if pin.startswith("GP") else pin
    return {"RX": "3", "TX": "1"}.get(pin, pin)          # esp8266: D5 / A0 are defined by the NodeMCU core


# ================================================================ deterministic wiring

@dataclass
class _Inst:
    key: str
    id: str
    label: str

    @property
    def part(self) -> Part:
        return PARTS[self.key]


def _instances(parts: list) -> list[_Inst]:
    out = []
    for key, qty in parts:
        part = PARTS[key]
        for i in range(1, qty + 1):
            out.append(_Inst(key, f"{key}_{i}" if qty > 1 else key, f"{part.name} #{i}" if qty > 1 else part.name))
    return out[:MAX_INSTANCES]


def kb_warning_items(board: Board, key: str) -> list[tuple[str, dict]]:
    name = PARTS[key].name
    items: list[tuple[str, dict]] = []
    if key in ("dht11", "dht22"):
        items.append(("dht_pullup", {"name": name}))
    elif key == "sg90":
        items.append(("servo_usb", {"name": name}))
    elif key == "led":
        items.append(("led_resistor", {}))
    elif key == "relay":
        items.append(("relay_mains", {}))
        if board.logic < 4.5:
            items.append(("relay_33", {"board": board.name}))
    elif key == "lcd1602" and board.logic < 4.5:
        items.append(("lcd_33", {"board": board.name}))
    elif key == "bmp280" and board.logic >= 4.5:
        items.append(("bmp_5v", {}))
    return items


class _Build:
    def __init__(self, board: Board, lang: str, rails: str = "") -> None:
        self.board, self.lang, self.rails = board, lang, rails
        self.comps: dict[str, dict] = {board.key: {"id": board.key, "name": board.name,
                                                   "subtitle": _t(S, "logic", lang, v=_v(board.logic)),
                                                   "pins": [], "side": "right"}}
        self.wires: list[dict] = []
        self.steps: list[str] = []
        self.warnings: list[str] = []
        self.used: set[str] = set()
        self.reserved: set[str] = set()
        self.once: set[str] = set()
        self.infra: list[str] = []                     # power-first links (rails, regulator, external supply)
        self.signals: dict[str, dict[str, str]] = {}
        self.pin_color: dict[tuple[str, str], str] = {}
        self.step = 0
        self.counter: Counter = Counter()

    # ------------------------------------------------------------ primitives
    def warn(self, key: str, **fmt) -> None:
        text = _t(W, key, self.lang, **fmt)
        if text not in self.warnings:
            self.warnings.append(text)

    def comp(self, cid: str, name: str, subtitle: str, pins: list[str], side: str = "left") -> str:
        if cid not in self.comps:
            self.comps[cid] = {"id": cid, "name": name, "subtitle": subtitle, "pins": list(pins), "side": side}
        return cid

    def aux(self, prefix: str, name: str, subtitle: str, pins: list[str]) -> str:
        self.counter[prefix] += 1
        return self.comp(f"{prefix}{self.counter[prefix]}", name, subtitle, pins, "both")

    def wire(self, a: tuple[str, str], b: tuple[str, str], kind: str, label: str, infra: bool = False) -> None:
        for end in (a, b):
            comp = self.comps[end[0]]
            if end[1] not in comp["pins"]:
                comp["pins"].append(end[1])
            self.pin_color.setdefault(end, COLORS[kind])
        self.wires.append({"from": f"{a[0]}:{a[1]}", "to": f"{b[0]}:{b[1]}", "color": COLORS[kind],
                           "label": label, "step": 0 if infra else self.step})

    def alloc(self, kind: str) -> str | None:
        order = {"pwm": self.board.pwm, "analog": self.board.analog}.get(kind, self.board.gpio)
        for pin in order:
            if pin not in self.used and pin not in self.reserved:
                self.used.add(pin)
                return pin
        return None

    # ------------------------------------------------------------ power
    def _bb(self) -> str:
        name = AUX["mb102" if self.rails == "mb102" else "bb"]
        return self.comp("bb", name.get(self.lang, name["en"]), "", ["+5V", "+3V3", "GND"], "left")

    def ground(self) -> tuple[str, str]:
        b = self.board
        if not self.rails:
            return (b.key, "GND")
        self._bb()
        if "rails-gnd" not in self.once:
            self.once.add("rails-gnd")
            self.wire((b.key, "GND"), ("bb", "GND"), "ground", "GND", infra=True)
            self.infra.append(f"{b.name} GND → {_t(S, 'rail', self.lang, rail='GND')}")
            if self.rails == "mb102":
                self.warn("rails_mb102")
        return ("bb", "GND")

    def supply(self, part: Part, lo: float, hi: float, prefer: float) -> tuple[tuple[str, str] | None, float, str]:
        b = self.board
        if self.rails == "mb102":
            volts = prefer if lo <= prefer <= hi else next((v for v in (5.0, 3.3) if lo <= v <= hi), None)
            if volts is not None:
                self.ground()
                return ("bb", RAIL[volts]), volts, _t(S, "rail", self.lang, rail=RAIL[volts])
        candidates = [v for v in b.supply if lo <= v <= hi]
        if candidates:
            volts = prefer if prefer in candidates else max(candidates)
            pin = b.supply[volts]
            if pin in ("VIN", "VBUS") and b.logic < 4.5:
                self.warn("usb5v", pin=pin, board=b.name)
            if self.rails == "board":
                self._bb()
                if f"rail{volts}" not in self.once:
                    self.once.add(f"rail{volts}")
                    self.wire((b.key, pin), ("bb", RAIL[volts]), "power", f"{_v(volts)} V", infra=True)
                    self.infra.append(f"{b.name} {pin} → {_t(S, 'rail', self.lang, rail=RAIL[volts])}")
                self.ground()
                return ("bb", RAIL[volts]), volts, _t(S, "rail", self.lang, rail=RAIL[volts])
            return (b.key, pin), volts, pin
        if hi <= 3.6 and 5.0 in b.supply:
            if "reg" not in self.once:
                self.once.add("reg")
                name = AUX["reg"]
                self.comp("reg", name.get(self.lang, name["en"]), "AMS1117-3.3", ["IN", "GND", "OUT"], "both")
                self.wire((b.key, b.supply[5.0]), ("reg", "IN"), "power", "5 V", infra=True)
                self.wire(self.ground(), ("reg", "GND"), "ground", "GND", infra=True)
                self.infra.append(f"{b.supply[5.0]} → AMS1117 IN, GND → AMS1117 GND")
            self.warn("regulator", board=b.name, name=part.name)
            return ("reg", "OUT"), 3.3, "AMS1117 OUT"
        if lo <= 5.0 <= hi:
            if "psu" not in self.once:
                self.once.add("psu")
                name = AUX["psu"]
                self.comp("psu", name.get(self.lang, name["en"]), "≥ 1 A", ["+5V", "GND"], "right")
                self.wire(("psu", "GND"), self.ground(), "ground", "GND", infra=True)
                self.infra.append(f"{name.get(self.lang, name['en'])} GND → {b.name} GND")
            self.warn("external5", board=b.name, name=part.name)
            return ("psu", "+5V"), 5.0, _t(S, "external", self.lang)
        return None, 0.0, ""

    # ------------------------------------------------------------ parts
    def plan(self, insts: list[_Inst]) -> dict[str, dict[str, str | None]]:
        if any(need in ("sda", "scl") for inst in insts for _, need in inst.part.pins):
            self.reserved |= {self.board.sda, self.board.scl}
        plan: dict[str, dict[str, str | None]] = {inst.id: {} for inst in insts}
        for phase in ("analog", "pwm", "digital"):       # scarce pin kinds first
            for inst in insts:
                for pin, need in inst.part.pins:
                    kind = "analog" if need == "analog" else "pwm" if need == "pwm" else "digital" if need in DIGITAL else ""
                    if kind == phase:
                        plan[inst.id][pin] = self.alloc("gpio" if kind == "digital" else kind)
        return plan

    def add(self, inst: _Inst, plan: dict[str, str | None]) -> None:
        b, part, lang = self.board, inst.part, self.lang
        self.step += 1
        self.comp(inst.id, inst.label, part.subtitle, [p for p, _ in part.pins], "left")
        self.signals[inst.id] = {}
        links: list[str] = []
        volts_in = 0.0
        for pin, need in part.pins:
            me = (inst.id, pin)
            if need in ("nc", "load"):
                continue
            if need in ("power", "power-adc"):
                lo, hi = part.vcc or (0.0, 5.5)
                if need == "power-adc":
                    hi = min(hi, b.logic)
                prefer = b.logic if lo <= b.logic <= hi else 5.0
                src, volts_in, label = self.supply(part, lo, hi, prefer)
                if src is None:
                    self.warn("no_power", name=inst.label, board=b.name)
                    continue
                self.wire(src, me, "power", f"{_v(volts_in)} V")
                links.append(f"{pin} → {label}")
            elif need == "ground":
                self.wire(self.ground(), me, "ground", "GND")
                links.append(f"{pin} → GND")
            elif need in ("sda", "scl"):
                bp = b.sda if need == "sda" else b.scl
                self.wire((b.key, bp), me, "i2c", need.upper())
                links.append(f"{pin} → {bp}")
            else:
                bp = plan.get(pin)
                if bp is None:
                    kind = "PWM" if need == "pwm" else "ADC" if need == "analog" else "GPIO"
                    self.warn("no_pin", kind=kind, board=b.name, name=inst.label)
                    continue
                self.signals[inst.id][pin] = bp
                if need == "anode":
                    r = self.aux("r", AUX["r"].get(lang, AUX["r"]["en"]), "220 Ω", ["1", "2"])
                    self.wire((b.key, bp), (r, "1"), "signal", bp)
                    self.wire((r, "2"), me, "signal", "220 Ω")
                    links.append(f"{pin} → 220 Ω → {bp}")
                elif need == "in-vcc" and b.logic < 4.5 and volts_in >= 4.5:
                    d = self.aux("div", AUX["div"].get(lang, AUX["div"]["en"]), "1 kΩ + 2 kΩ", ["IN", "OUT", "GND"])
                    self.wire(me, (d, "IN"), "signal", pin)
                    self.wire((d, "OUT"), (b.key, bp), "signal", f"{pin} 3.3 V")
                    self.wire((d, "GND"), self.ground(), "ground", "GND")
                    self.warn("divider", name=part.name, board=b.name)
                    links.append(f"{pin} → {_t(S, 'divider', lang)} → {bp}")
                else:
                    self.wire((b.key, bp), me, "pwm" if need == "pwm" else "signal", pin)
                    links.append(f"{pin} → {bp}")
        self.steps.append(_t(S, "connect", lang, name=inst.label, links=", ".join(links)))
        for key, fmt in kb_warning_items(b, inst.key):
            self.warn(key, **fmt)

    def finish(self, insts: list[_Inst]) -> dict:
        b, lang = self.board, self.lang
        if self.infra:      # power wiring becomes step 1, everything else shifts by one
            self.steps.insert(0, _t(S, "power_first", lang, links=", ".join(self.infra)))
            for w in self.wires:
                w["step"] = 1 if w["step"] == 0 else w["step"] + 1
        addrs = Counter(inst.part.i2c for inst in insts if inst.part.i2c is not None)
        for addr, n in addrs.items():
            if n > 1:
                self.warn("i2c_conflict", addr=f"0x{addr:02X}")
        if any(inst.key == "pir" for inst in insts):
            self.steps.append(_t(S, "pir_warmup", lang))
        code, language, libs = generate_code(b, insts, self.signals, title_for(b, insts))
        if libs:
            self.steps.append(_t(S, "libs", lang, libs=", ".join(libs)))
        if b.key.startswith("promini"):
            self.steps.append(_t(S, "upload_promini", lang, volts=_v(b.logic)))
        if b.family == "pico":
            self.steps.append(_t(S, "upload_pico", lang))
        elif b.family in ("esp32", "esp8266"):
            self.steps.append(_t(S, "upload_esp", lang, core="esp32" if b.family == "esp32" else "esp8266"))
        else:
            self.steps.append(_t(S, "upload_arduino", lang, board=b.name))
        return card_data(title_for(b, insts), self.comps, self.pin_color, self.wires, self.steps, self.warnings,
                         code, language)


def title_for(board: Board, insts: list[_Inst]) -> str:
    names = list(dict.fromkeys(inst.part.name for inst in insts))
    return f"{', '.join(names)} → {board.name}" if names else board.name


def card_data(title: str, comps: dict, pin_color: dict, wires: list, steps: list, warnings: list, code: str,
              language: str) -> dict:
    out = []
    for cid, c in comps.items():
        pins = [{"name": p, "color": pin_color.get((cid, p), MUTED)} for p in c["pins"]]
        if c["side"] == "right":
            left, right = [], pins
        elif c["side"] == "both":
            half = max(1, len(pins) // 2) if len(pins) > 1 else len(pins)
            left, right = pins[:half], pins[half:]
        else:
            left, right = pins, []
        out.append({"id": cid, "name": c["name"], "subtitle": c["subtitle"], "left_pins": left, "right_pins": right})
    return {"title": title, "components": out, "wires": wires, "steps": steps, "warnings": warnings,
            "code": code, "language": language}


def wire_kb(board_key: str, parts: list, lang: str, rails: str = "") -> dict:
    board = BOARDS[board_key]
    insts = _instances(parts)
    build = _Build(board, lang, rails)
    plan = build.plan(insts)
    for inst in insts:
        build.add(inst, plan[inst.id])
    return build.finish(insts)


# ================================================================ code

def _var(inst: _Inst) -> str:
    """Identifier for a part in code; never the name of a module it imports (MicroPython `ssd1306`)."""
    return inst.id.replace("ssd1306", "oled")


def _ascii(text: str) -> str:
    return text.replace("Ω", "ohm").replace("→", "->")


def _snippets_arduino(board: Board, inst: _Inst, p: dict) -> tuple[list, list, list, list, list]:
    """(includes, globals, setup, loop, libraries) for one part instance; `p` maps part pin -> code pin."""
    v, name, key = _var(inst), _ascii(inst.part.name), inst.key
    if key in ("dht11", "dht22"):
        return (["#include <DHT.h>"], [f"DHT {v}({p['DATA']}, {key.upper()});"], [f"{v}.begin();"],
                [f"float {v}T = {v}.readTemperature(), {v}H = {v}.readHumidity();",
                 f'if (!isnan({v}T)) {{ Serial.print("{name} C: "); Serial.print({v}T); '
                 f'Serial.print("  RH %: "); Serial.println({v}H); }}'],
                ["DHT sensor library (Adafruit)"])
    if key == "hcsr04":
        return ([], [f"const int {v}Trig = {p['TRIG']}, {v}Echo = {p['ECHO']};"],
                [f"pinMode({v}Trig, OUTPUT);", f"pinMode({v}Echo, INPUT);"],
                [f"digitalWrite({v}Trig, LOW); delayMicroseconds(2);",
                 f"digitalWrite({v}Trig, HIGH); delayMicroseconds(10); digitalWrite({v}Trig, LOW);",
                 f"long {v}Us = pulseIn({v}Echo, HIGH, 30000);",
                 f'Serial.print("{name} cm: "); Serial.println({v}Us / 58.0);'], [])
    if key == "sg90":
        esp32 = board.family == "esp32"
        return (["#include <ESP32Servo.h>" if esp32 else "#include <Servo.h>"],
                [f"Servo {v};", f"int {v}Angle = 0, {v}Step = 30;"], [f"{v}.attach({p['SIG']});"],
                [f"{v}.write({v}Angle);", f"{v}Angle += {v}Step;",
                 f"if ({v}Angle <= 0 || {v}Angle >= 180) {v}Step = -{v}Step;"], ["ESP32Servo"] if esp32 else [])
    if key == "led":
        return ([], [f"const int {v}Pin = {p['A']};"], [f"pinMode({v}Pin, OUTPUT);"],
                [f"digitalWrite({v}Pin, !digitalRead({v}Pin));  // blink"], [])
    if key == "button":
        return ([], [f"const int {v}Pin = {p['1']};"], [f"pinMode({v}Pin, INPUT_PULLUP);"],
                [f'if (digitalRead({v}Pin) == LOW) Serial.println("{name} pressed");'], [])
    if key == "pot":
        return ([], [], [], [f'Serial.print("{name}: "); Serial.println(analogRead({p["WIPER"]}));'], [])
    if key == "lcd1602":
        return (["#include <Wire.h>", "#include <LiquidCrystal_I2C.h>"], [f"LiquidCrystal_I2C {v}(0x27, 16, 2);"],
                [f"{v}.init();", f"{v}.backlight();", f'{v}.print("Hello, Jarvis");'], [], ["LiquidCrystal I2C"])
    if key == "ssd1306":
        return (["#include <Wire.h>", "#include <Adafruit_GFX.h>", "#include <Adafruit_SSD1306.h>"],
                [f"Adafruit_SSD1306 {v}(128, 64, &Wire, -1);"],
                [f"{v}.begin(SSD1306_SWITCHCAPVCC, 0x3C);", f"{v}.clearDisplay();", f"{v}.setTextSize(1);",
                 f"{v}.setTextColor(SSD1306_WHITE);", f"{v}.setCursor(0, 0);", f'{v}.println("Hello, Jarvis");',
                 f"{v}.display();"], [], ["Adafruit SSD1306", "Adafruit GFX"])
    if key == "relay":
        return ([], [f"const int {v}Pin = {p['IN']};"], [f"pinMode({v}Pin, OUTPUT);"],
                [f"digitalWrite({v}Pin, !digitalRead({v}Pin));  // many modules switch on LOW"], [])
    if key == "bmp280":
        return (["#include <Wire.h>", "#include <Adafruit_BMP280.h>"], [f"Adafruit_BMP280 {v};"],
                [f'if (!{v}.begin(0x76)) Serial.println("{name} not found (try 0x77)");'],
                [f'Serial.print("{name} hPa: "); Serial.print({v}.readPressure() / 100.0);',
                 f'Serial.print("  C: "); Serial.println({v}.readTemperature());'], ["Adafruit BMP280"])
    if key == "mpu6050":
        return (["#include <Wire.h>", "#include <Adafruit_MPU6050.h>", "#include <Adafruit_Sensor.h>"],
                [f"Adafruit_MPU6050 {v};"], [f'if (!{v}.begin()) Serial.println("{name} not found");'],
                [f"sensors_event_t {v}A, {v}G, {v}T;", f"{v}.getEvent(&{v}A, &{v}G, &{v}T);",
                 f'Serial.print("accel x/y/z: "); Serial.print({v}A.acceleration.x); Serial.print(" ");',
                 f'Serial.print({v}A.acceleration.y); Serial.print(" "); Serial.println({v}A.acceleration.z);'],
                ["Adafruit MPU6050"])
    if key == "pir":
        return ([], [f"const int {v}Pin = {p['OUT']};"], [f"pinMode({v}Pin, INPUT);"],
                [f'if (digitalRead({v}Pin) == HIGH) Serial.println("Motion!");'], [])
    return [], [], [], [], []


def _snippets_micropython(board: Board, inst: _Inst, p: dict) -> tuple[list, list, list, list, list]:
    """(imports, globals, machine names, loop, libraries) for one part instance on MicroPython."""
    v, name, key = _var(inst), _ascii(inst.part.name), inst.key
    if key in ("dht11", "dht22"):
        return (["import dht"], [f"{v} = dht.{key.upper()}(Pin({p['DATA']}))"], ["Pin"],
                ["try:", f"    {v}.measure()", f'    print("{name}", {v}.temperature(), "C", {v}.humidity(), "%")',
                 "except OSError:", f'    print("{name} read failed")'], [])
    if key == "hcsr04":
        return ([], [f"{v}_trig = Pin({p['TRIG']}, Pin.OUT)", f"{v}_echo = Pin({p['ECHO']}, Pin.IN)"],
                ["Pin", "time_pulse_us"],
                [f"{v}_trig.value(0); time.sleep_us(2); {v}_trig.value(1); time.sleep_us(10); {v}_trig.value(0)",
                 f"{v}_us = time_pulse_us({v}_echo, 1, 30000)",
                 f'print("{name} cm:", {v}_us / 58 if {v}_us > 0 else None)'], [])
    if key == "sg90":
        return ([], [f"{v} = PWM(Pin({p['SIG']}))", f"{v}.freq(50)", f"{v}_angle, {v}_step = 0, 30"], ["Pin", "PWM"],
                [f"{v}.duty_u16(int((0.5 + {v}_angle / 180 * 2.0) / 20 * 65535))  # 0.5-2.5 ms pulse",
                 f"{v}_angle += {v}_step", f"if {v}_angle <= 0 or {v}_angle >= 180:", f"    {v}_step = -{v}_step"], [])
    if key == "led":
        return ([], [f"{v} = Pin({p['A']}, Pin.OUT)"], ["Pin"], [f"{v}.toggle()"], [])
    if key == "button":
        return ([], [f"{v} = Pin({p['1']}, Pin.IN, Pin.PULL_UP)"], ["Pin"],
                [f"if {v}.value() == 0:", f'    print("{name} pressed")'], [])
    if key == "pot":
        return ([], [f"{v} = ADC(Pin({p['WIPER']}))"], ["ADC", "Pin"], [f'print("{name}:", {v}.read_u16())'], [])
    if key == "lcd1602":
        return ([], ['print("I2C devices:", [hex(a) for a in i2c.scan()])  # the LCD backpack is usually 0x27',
                     "# To print text, add a PCF8574 LCD driver (e.g. lcd_api.py + i2c_lcd.py)."], [], [],
                ["a PCF8574 LCD driver"])
    if key == "ssd1306":
        return (["import ssd1306  # mpremote mip install ssd1306"],
                [f"{v} = ssd1306.SSD1306_I2C(128, 64, i2c)", f'{v}.text("Hello, Jarvis", 0, 0)', f"{v}.show()"],
                [], [], ["ssd1306 (mip)"])
    if key == "relay":
        return ([], [f"{v} = Pin({p['IN']}, Pin.OUT)"], ["Pin"], [f"{v}.toggle()  # many modules switch on LOW"], [])
    if key == "bmp280":
        return ([], [f'print("{name} chip id:", hex(i2c.readfrom_mem(0x76, 0xD0, 1)[0]))  # 0x58 = BMP280'], [], [],
                ["a BMP280 driver for readings"])
    if key == "mpu6050":
        return (["import struct"], [f"i2c.writeto_mem(0x68, 0x6B, b'\\x00')  # wake the {name}"], [],
                [f"{v}_ax, {v}_ay, {v}_az = struct.unpack('>hhh', i2c.readfrom_mem(0x68, 0x3B, 6))",
                 f'print("{name} accel g:", {v}_ax / 16384, {v}_ay / 16384, {v}_az / 16384)'], [])
    if key == "pir":
        return ([], [f"{v} = Pin({p['OUT']}, Pin.IN)"], ["Pin"], [f"if {v}.value():", '    print("Motion!")'], [])
    return [], [], [], [], []


def _add(target: list, items: list) -> None:
    for item in items:
        if item not in target:
            target.append(item)


def generate_code(board: Board, insts: list[_Inst], signals: dict, title: str) -> tuple[str, str, list]:
    """A minimal starter program for the wired parts: an Arduino sketch, or MicroPython on the Pico."""
    libs: list[str] = []
    slow = any(inst.key in ("dht11", "dht22") for inst in insts)
    if board.language == "micropython":
        imports, globals_, machine, loop = [], [], {"Pin"}, []
        if any(inst.part.i2c for inst in insts):
            machine.add("I2C")
            globals_.append(f"i2c = I2C(0, sda=Pin({code_pin(board, board.sda)}), scl=Pin({code_pin(board, board.scl)}), "
                            "freq=400000)")
        for inst in insts:
            p = {pin: code_pin(board, bp) for pin, bp in signals.get(inst.id, {}).items()}
            needed = [pin for pin, need in inst.part.pins if need in DIGITAL or need in ("pwm", "analog")]
            if any(pin not in p for pin in needed):
                globals_.append(f"# {_ascii(inst.label)}: no free pin, not included")
                continue
            imp, glob, names, lp, lb = _snippets_micropython(board, inst, p)
            _add(imports, imp)
            globals_.extend(glob)
            machine.update(names)
            loop.extend(lp)
            _add(libs, lb)
        lines = [f"# {_ascii(title)}", "# Generated by Jarvis: check the wiring card before powering up.",
                 f"from machine import {', '.join(sorted(machine))}", "import time"] + imports
        lines += [""] + globals_ + ["", "while True:"] + [f"    {line}" for line in loop]
        lines.append(f"    time.sleep({2 if slow else 0.5})")
        return "\n".join(lines) + "\n", "micropython", libs
    includes, globals_, setup, loop = [], [], [], []
    for inst in insts:
        p = {pin: code_pin(board, bp) for pin, bp in signals.get(inst.id, {}).items()}
        needed = [pin for pin, need in inst.part.pins if need in DIGITAL or need in ("pwm", "analog")]
        if any(pin not in p for pin in needed):
            globals_.append(f"// {_ascii(inst.label)}: no free pin, not included")
            continue
        inc, glob, st, lp, lb = _snippets_arduino(board, inst, p)
        _add(includes, inc)
        globals_.extend(glob)
        setup.extend(st)
        loop.extend(lp)
        _add(libs, lb)
    lines = [f"// {_ascii(title)}", "// Generated by Jarvis: check the wiring card before powering up."] + includes
    lines += [""] + globals_ + ["", "void setup() {", "  Serial.begin(115200);"] + [f"  {s}" for s in setup] + ["}", ""]
    lines += ["void loop() {"] + [f"  {s}" for s in loop] + [f"  delay({2000 if slow else 500});", "}"]
    return "\n".join(lines) + "\n", "arduino", libs


# ================================================================ model path

MODEL_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "components": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "name": {"type": "string"}, "subtitle": {"type": "string"},
            "pins": {"type": "array", "items": {"type": "string"}}}, "required": ["id", "name", "pins"]}},
        "wires": {"type": "array", "items": {"type": "object", "properties": {
            "from": {"type": "string"}, "to": {"type": "string"},
            "kind": {"type": "string", "enum": ["power", "ground", "signal", "i2c", "pwm"]},
            "label": {"type": "string"}, "step": {"type": "integer"}}, "required": ["from", "to", "kind"]}},
        "steps": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "code": {"type": "string"},
        "language": {"type": "string", "enum": ["arduino", "micropython"]},
    },
    "required": ["components", "wires", "steps", "warnings"],
}
SYSTEM = ("You plan safe breadboard wiring for hobby electronics (Arduino, ESP32, ESP8266, Raspberry Pi Pico). "
          "Use only real pin names. Never route mains voltage through a breadboard. Reply with one JSON object only.")


def _describe_board(b: Board) -> str:
    supply = ", ".join(f"{pin}={_v(v)} V" for v, pin in b.supply.items())
    return f"- {b.key}: {b.name}, {_v(b.logic)} V logic; supply pins {supply}; I2C SDA={b.sda} SCL={b.scl}; pins: " \
           + ", ".join(b.pins)


def _describe_part(inst_id: str, part: Part) -> str:
    vcc = f"; supply {_v(part.vcc[0])}–{_v(part.vcc[1])} V" if part.vcc else ""
    return f"- {inst_id}: {part.name} ({part.subtitle}){vcc}; pins: " + ", ".join(p for p, _ in part.pins)


def model_prompt(request: str, parsed: Parsed, lang: str) -> str:
    lines = []
    boards = [BOARDS[k] for k in parsed.boards] or list(BOARDS.values())
    for b in boards:
        lines.append(_describe_board(b))
    for inst in _instances(parsed.parts):
        lines.append(_describe_part(inst.id, inst.part))
    unknown = f"\nParts not in the library (pick a short lowercase id, list their real pins): {', '.join(parsed.unknown)}" \
        if parsed.unknown else ""
    board_note = "" if parsed.boards else "\nNo board was named: choose the most suitable one from the list above."
    language = "MicroPython" if parsed.board == "pico" else "Arduino (or MicroPython for a Pico)"
    return (f"The user's wiring request: {request}\n"
            "Library parts: use EXACTLY these component ids and pin names:\n" + "\n".join(lines) + unknown + board_note +
            "\nEvery wire joins 'id:PIN' to 'id:PIN'; kind is power, ground, signal, i2c or pwm. Wire every ground, "
            "power each part at a voltage it accepts, and add resistors, level shifters or drivers as components where "
            f"needed. Write title, steps and warnings in {LANG_NAMES.get(lang, 'English')}. Put a minimal {language} "
            "example in code.")


def _lookup(cid: str, name: str) -> tuple[str, object | None]:
    """A model component -> ("board", Board) | ("part", Part) | ("unknown", None)."""
    if cid in BOARDS:
        return "board", BOARDS[cid]
    base = re.sub(r"_?\d+$", "", cid)
    if cid in PARTS:
        return "part", PARTS[cid]
    if base in PARTS:
        return "part", PARTS[base]
    matches, _, _ = _scan(name)
    for entry, _ in matches:
        if entry.kind == "board":
            return "board", BOARDS[entry.key]
        if entry.kind == "part":
            return "part", PARTS[entry.key]
    return "unknown", None


def validate_model(raw, lang: str) -> dict:
    """Check the model's wiring against the library. Raises ValueError when nothing usable is left."""
    if not isinstance(raw, dict) or not isinstance(raw.get("components"), list) or not isinstance(raw.get("wires"), list):
        raise ValueError("not a wiring object")
    comps: dict[str, dict] = {}
    warnings: list[str] = []
    for c in raw["components"][:16]:
        if not isinstance(c, dict):
            continue
        cid = re.sub(r"[^a-z0-9_]", "", str(c.get("id", "")).lower())[:24]
        if not cid or cid in comps:
            continue
        name = _clean(c.get("name") or cid, 48)
        kind, ref = _lookup(cid, name)
        declared = [_clean(p, 16) for p in (c.get("pins") or [])[:40] if _clean(p, 16)] \
            if isinstance(c.get("pins"), list) else []
        if kind == "board":
            pins = [p for p in (board_pin(ref, d) for d in declared) if p]
        elif kind == "part":
            pins = [p for p, _ in ref.pins]
        else:
            pins = list(dict.fromkeys(declared))
            warnings.append(_t(W, "unknown_part", lang, name=name))
        comps[cid] = {"kind": kind, "ref": ref, "name": name if kind == "unknown" else getattr(ref, "name", name),
                      "subtitle": _t(S, "unverified", lang) if kind == "unknown" else
                      (ref.subtitle if kind == "part" else _t(S, "logic", lang, v=_v(ref.logic))),
                      "pins": pins if kind != "board" else [], "side": "right" if kind == "board" else "left"}
    if not comps:
        raise ValueError("no components")

    def endpoint(raw_end) -> tuple[str, str] | None:
        text = _clean(raw_end, 48)
        if ":" not in text:
            return None
        cid, pin = text.split(":", 1)
        cid = re.sub(r"[^a-z0-9_]", "", cid.lower())
        comp = comps.get(cid)
        if comp is None:
            return None
        if comp["kind"] == "board":
            canon = board_pin(comp["ref"], pin)
        elif comp["kind"] == "part":
            canon = part_pin(comp["ref"], pin)
        else:
            canon = pin.strip()[:16] if re.fullmatch(r"[A-Za-z0-9_+\-.() ]{1,16}", pin.strip()) else None
        return (cid, canon) if canon else None

    steps = [_clean(s, 300) for s in (raw.get("steps") or [])[:20] if _clean(s, 300)] \
        if isinstance(raw.get("steps"), list) else []
    wires: list[dict] = []
    pin_color: dict[tuple[str, str], str] = {}
    boards_used: list[Board] = []
    for i, w in enumerate(raw["wires"][:60]):
        if not isinstance(w, dict):
            continue
        a, b = endpoint(w.get("from")), endpoint(w.get("to"))
        if a is None or b is None:
            warnings.append(_t(W, "dropped_wire", lang, wire=f"{_clean(w.get('from'), 30)} → {_clean(w.get('to'), 30)}"))
            continue
        kind = w.get("kind") if w.get("kind") in COLORS else "signal"
        step = w.get("step") if isinstance(w.get("step"), int) and 1 <= w.get("step") <= max(1, len(steps)) else \
            min(i + 1, max(1, len(steps)))
        for end in (a, b):
            comp = comps[end[0]]
            if end[1] not in comp["pins"]:
                comp["pins"].append(end[1])
            pin_color.setdefault(end, COLORS[kind])
            if comp["kind"] == "board" and comp["ref"] not in boards_used:
                boards_used.append(comp["ref"])
        wires.append({"from": f"{a[0]}:{a[1]}", "to": f"{b[0]}:{b[1]}", "color": COLORS[kind],
                      "label": _clean(w.get("label") or kind, 24), "step": step})
        warnings.extend(_check_levels(comps, a, b, lang))
    if not wires:
        raise ValueError("no valid wires")
    board = boards_used[0] if boards_used else None
    for comp in comps.values():
        if comp["kind"] == "part" and board is not None:
            for key, fmt in kb_warning_items(board, comp["ref"].key):
                warnings.append(_t(W, key, lang, **fmt))
    if any(c["kind"] == "unknown" for c in comps.values()):
        warnings.append(_t(W, "model_unverified", lang))
    warnings += [_clean(x, 300) for x in (raw.get("warnings") or [])[:12] if _clean(x, 300)] \
        if isinstance(raw.get("warnings"), list) else []
    warnings = list(dict.fromkeys(warnings))[:24]
    language = raw.get("language") if raw.get("language") in ("arduino", "micropython") else \
        (board.language if board else "arduino")
    names = [c["name"] for c in comps.values() if c["kind"] != "board"]
    title = _clean(raw.get("title"), 80) or (f"{', '.join(names)} → {board.name}" if board else ", ".join(names))
    return card_data(title, comps, pin_color, wires, steps, warnings, _clean_code(raw.get("code")), language)


def _clean_code(code) -> str:
    text = str(code or "")[:8000]
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def _check_levels(comps: dict, a: tuple[str, str], b: tuple[str, str], lang: str) -> list[str]:
    """Library knowledge applied to a model's wire: supply voltage in range, 5 V outputs into 3.3 V pins."""
    out = []
    for board_end, part_end in ((a, b), (b, a)):
        bc, pc = comps[board_end[0]], comps[part_end[0]]
        if bc["kind"] != "board" or pc["kind"] != "part":
            continue
        board, part = bc["ref"], pc["ref"]
        need = dict(part.pins).get(part_end[1])
        volts = next((v for v, pin in board.supply.items() if pin == board_end[1]), None)
        if need == "power" and volts is not None and part.vcc and not part.vcc[0] <= volts <= part.vcc[1]:
            out.append(_t(W, "volt_mismatch", lang, name=part.name, pin=part_end[1], volts=_v(volts),
                          lo=_v(part.vcc[0]), hi=_v(part.vcc[1])))
        if need == "in-vcc" and board.logic < 4.5:
            out.append(_t(W, "divider", lang, name=part.name, board=board.name))
    return out


# ================================================================ tool

def _summary(data: dict, lang: str, explain: bool) -> str:
    warn = _t(S, "warn_n", lang, n=len(data["warnings"])) if data["warnings"] else _t(S, "warn_0", lang)
    head = _t(S, "summary", lang, title=data["title"], wires=len(data["wires"]), steps=len(data["steps"]), warn=warn)
    if not explain:
        return head
    return (head + " " + " ".join(data["steps"]) + (" " + " ".join(data["warnings"]) if data["warnings"] else ""))[:2000]


@tool("circuit", "Electronics wiring helper: how to connect sensors, displays, servos, relays, LEDs and buttons to "
      "Arduino (Uno, Nano, Pro Mini), ESP32, ESP8266 NodeMCU or Raspberry Pi Pico. Returns a wiring diagram, steps, "
      "safety warnings (voltage levels, current, pull-ups) and a starter sketch. wire: the diagram; explain: also "
      "speak the steps.",
      {"request": {"type": "string", "description": "What to connect, as the user said it, e.g. 'DHT11 to Arduino Pro Mini'"},
       "components": {"type": "array", "items": {"type": "string"}, "description": "Optional list of parts and the board"},
       "action": {"type": "string", "enum": ["wire", "explain"]}},
      ["request"])
async def circuit(ctx: Context, args: dict) -> dict:
    request = str(args["request"]).strip()[:1000]
    components = [str(c)[:80] for c in (args.get("components") or [])[:12] if isinstance(c, (str, int, float))]
    lang = i18n.detect(request + " " + " ".join(components), ctx.hub.j.lang() if ctx.hub else "en")
    parsed = parse(request, components)
    complex_ = bool(parsed.unknown) or parsed.count > MAX_INSTANCES or len(parsed.boards) > 1 or not parsed.parts
    data, source = None, "library"
    if complex_ and ctx.brain is not None:
        try:
            raw = await ctx.brain.complete(model_prompt(request, parsed, lang), SYSTEM, schema=MODEL_SCHEMA)
            data, source = validate_model(raw, lang), "model"
        except Exception as exc:    # model down, bad JSON, nothing valid: the library result stands
            log.info("circuit model path failed: %s: %s", type(exc).__name__, str(exc)[:200])
    if data is None:
        if not parsed.parts:
            if parsed.unknown:
                return {"ok": False, "message": _t(S, "unknown_only", lang, names=", ".join(parsed.unknown))}
            return {"ok": False, "message": _t(S, "which_parts", lang)}
        board_key = parsed.board or "uno"
        data = wire_kb(board_key, parsed.parts, lang, parsed.rails)
        extra = []
        if parsed.board is None:
            extra.append(_t(W, "assumed_board", lang))
        if len(parsed.boards) > 1:
            extra.append(_t(W, "two_boards", lang, board=BOARDS[board_key].name))
        extra += [_t(W, "unknown_part", lang, name=u) for u in parsed.unknown]
        data["warnings"] = extra + data["warnings"]
    explain = args.get("action") == "explain"
    return {"ok": True, "message": _summary(data, lang, explain), "source": source, "title": data["title"],
            "steps": data["steps"], "warnings": data["warnings"],
            "wiring": [f"{w['from']} → {w['to']}" for w in data["wires"]],
            "code": data["code"], "language": data["language"],
            "card": {"kind": "circuit", "title": data["title"], "data": data, "ttl": 60}}
