"""maps (geo), circuit and generate_image capabilities. No network: every public source is answered by
an httpx.MockTransport installed through web._client, and the model is a fake."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from jarvis.capabilities import circuits, geo, imagegen, web
from jarvis.tools import REGISTRY, ToolArgError, safe_run, validate


def run(coro):
    return asyncio.run(coro)


def call(ctx, name: str, args: dict) -> dict:
    tool = REGISTRY[name]
    return run(safe_run(tool, ctx, validate(tool, args)))


class FakeBrain:
    def __init__(self):
        self.json_replies: list = []
        self.prompts: list[str] = []
        self.images: list[tuple[str, str]] = []
        self.image_reply: tuple = (b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png", "a caption")
        self.image_error: Exception | None = None

    async def complete(self, prompt, system="", *, schema=None, **kw):
        self.prompts.append(prompt)
        reply = self.json_replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def image(self, prompt, model):
        self.images.append((prompt, model))
        if self.image_error is not None:
            err, self.image_error = self.image_error, None
            raise err
        return self.image_reply


# ================================================================== geo

CITIES = {"Lisbon": (38.7167, -9.1333, "Portugal"), "Porto": (41.1496, -8.611, "Portugal"),
          "New York": (40.7143, -74.006, "United States"), "Лиссабон": (38.7167, -9.1333, "Португалия"),
          "Lisboa": (38.7167, -9.1333, "Portugal")}


def geocoder(request: httpx.Request) -> httpx.Response:
    name = request.url.params["name"]
    if name not in CITIES:
        return httpx.Response(200, json={"generationtime_ms": 0.1})
    lat, lon, country = CITIES[name]
    return httpx.Response(200, json={"results": [{"name": name, "country": country, "latitude": lat,
                                                  "longitude": lon, "timezone": "Europe/Lisbon"}]})


@pytest.fixture
def net(monkeypatch):
    """Route web._client (used by web.geocode and geo) to per-host handlers; record every request."""
    geo._CACHE.clear()
    web._GEO_CACHE.clear()
    state: dict = {"hosts": {"geocoding-api.open-meteo.com": geocoder}, "calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        assert request.headers.get("user-agent") == web.UA
        state["calls"].append(request)
        fn = state["hosts"].get(request.url.host)
        return fn(request) if fn else httpx.Response(404)

    def client(timeout: float = 10) -> httpx.AsyncClient:
        assert timeout <= 15
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout,
                                 headers={"User-Agent": web.UA}, follow_redirects=True)

    monkeypatch.setattr(web, "_client", client)
    yield state
    geo._CACHE.clear()
    web._GEO_CACHE.clear()


def hits(state, host: str) -> list[httpx.Request]:
    return [r for r in state["calls"] if r.url.host == host]


def test_maps_locate_and_open(ctx, fake, net):
    r = call(ctx, "maps", {"action": "locate", "place": "Lisbon"})
    assert r["ok"] and "Lisbon, Portugal" in r["message"] and "38.72°N" in r["message"]
    card = r["card"]
    assert card["kind"] == "globe" and card["ttl"] == 30
    assert card["data"]["center"] == {"lat": 38.7167, "lon": -9.1333}
    assert card["data"]["points"][0]["kind"] == "place" and card["data"]["routes"] == []

    opened = []
    fake.open_url = lambda url: opened.append(url)
    r = call(ctx, "maps", {"action": "open", "place": "Lisbon"})
    assert r["ok"] and opened[-1] == "https://www.openstreetmap.org/?mlat=38.71670&mlon=-9.13330#map=12/38.71670/-9.13330"
    assert r["card"]["kind"] == "globe"
    r = call(ctx, "maps", {"action": "open", "origin": "Lisbon", "destination": "Porto"})
    assert opened[-1].startswith("https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route=38.71670%2C-9.13330%3B41.14960")
    assert r["card"]["data"]["routes"][0]["path"]

    r = call(ctx, "maps", {"action": "locate", "place": "Atlantis"})
    assert not r["ok"] and "Atlantis" in r["message"]


def test_maps_route_by_road_is_downsampled_and_cached(ctx, net):
    coords = [[-9.1333 + i * 0.001, 38.7167 + i * 0.005] for i in range(500)]

    def osrm(request):
        assert request.url.path == "/route/v1/driving/-9.133300,38.716700;-8.611000,41.149600"
        assert request.url.params["overview"] == "simplified" and request.url.params["geometries"] == "geojson"
        return httpx.Response(200, json={"code": "Ok", "routes": [
            {"distance": 313400.0, "duration": 11100.0, "geometry": {"type": "LineString", "coordinates": coords}}]})

    net["hosts"]["router.project-osrm.org"] = osrm
    r = call(ctx, "maps", {"action": "route", "origin": "Lisbon", "destination": "Porto"})
    assert r["ok"] and r["route"]["distance_km"] == 313.4 and r["route"]["duration_min"] == 185
    assert "313 km by road" in r["message"] and "3 h 5 min" in r["message"]
    route = r["card"]["data"]["routes"][0]
    assert route["great_circle"] is False and len(route["path"]) == 200
    assert route["path"][0] == [38.7167, -9.1333] and route["path"][-1] == [coords[-1][1], coords[-1][0]]
    assert [p["kind"] for p in r["card"]["data"]["points"]] == ["origin", "destination"]
    call(ctx, "maps", {"action": "route", "origin": "Lisbon", "destination": "Porto"})
    assert len(hits(net, "router.project-osrm.org")) == 1      # second answer came from the cache


def test_maps_route_intercontinental_and_flight_use_great_circle(ctx, net):
    net["hosts"]["router.project-osrm.org"] = lambda request: pytest.fail("no road route across an ocean")
    r = call(ctx, "maps", {"action": "route", "origin": "Lisbon", "destination": "New York"})
    assert r["ok"] and r["route"]["great_circle"] and r["route"]["source"] == "great-circle"
    assert 5300 < r["route"]["distance_km"] < 5500
    route = r["card"]["data"]["routes"][0]
    assert route["great_circle"] is True and len(route["path"]) == 64
    assert route["path"][0] == [38.7167, -9.1333] and route["path"][-1] == [40.7143, -74.006]
    assert max(p[0] for p in route["path"]) > 41      # bends north of both endpoints, as a great circle does
    assert "great-circle" in r["message"]

    r = call(ctx, "maps", {"action": "route", "origin": "Lisbon", "destination": "Porto", "mode": "flight"})
    assert r["route"]["great_circle"] and r["route"]["flight_time_min_estimate"] > 30
    assert "estimate" in r["message"]


def test_maps_route_falls_back_when_osrm_fails(ctx, net):
    net["hosts"]["router.project-osrm.org"] = lambda request: httpx.Response(503)
    r = call(ctx, "maps", {"action": "route", "origin": "Lisbon", "destination": "Porto"})
    assert r["ok"] and r["route"]["great_circle"]
    assert "Road routing is unavailable" in r["message"]
    r = call(ctx, "maps", {"action": "route", "destination": "Porto"})
    assert not r["ok"]                                     # no origin and no home city


def test_maps_nearby_overpass_query_and_russian(ctx, net):
    seen = []

    def overpass(request):
        assert request.method == "POST"
        seen.append(parse_qs(request.content.decode())["data"][0])
        return httpx.Response(200, json={"elements": [
            {"type": "node", "id": 1, "lat": 38.73, "lon": -9.14, "tags": {"name": "Farmácia Far", "opening_hours": "24/7"}},
            {"type": "way", "id": 2, "center": {"lat": 38.7170, "lon": -9.1335}, "tags": {"name": "Farmácia Perto"}},
            {"type": "node", "id": 3, "lat": 38.72, "lon": -9.13, "tags": {}},
            {"type": "way", "id": 4, "tags": {"name": "no geometry"}}]})

    net["hosts"]["overpass-api.de"] = overpass
    r = call(ctx, "maps", {"action": "nearby", "place": "Лиссабон", "query": "аптека", "radius_km": 1.5})
    assert r["ok"] and r["query"] == "pharmacy" and r["untrusted"]
    q = seen[0]
    assert q.startswith("[out:json][timeout:10];") and q.endswith("out center 15;")
    assert 'node["amenity"="pharmacy"](around:1500,38.716700,-9.133300)' in q
    assert 'way["amenity"="pharmacy"](around:1500,38.716700,-9.133300)' in q
    names = [i["name"] for i in r["results"]]
    assert names == ["Farmácia Perto", "без названия", "Farmácia Far"]     # nearest first; unnamed labelled
    assert r["message"].startswith("Аптеки рядом с Лиссабон")
    assert r["results"][2]["opening_hours"] == "24/7"
    assert [p["kind"] for p in r["card"]["data"]["points"]] == ["place", "pharmacy", "pharmacy", "pharmacy"]

    r = call(ctx, "maps", {"action": "nearby", "place": "Lisbon", "query": "supermarket"})
    assert 'node["shop"="supermarket"](around:2000,' in seen[-1]
    r = call(ctx, "maps", {"action": "nearby", "place": "Lisbon", "query": "casino"})
    assert not r["ok"] and "pharmacy" in r["message"]
    with pytest.raises(ToolArgError):
        validate(REGISTRY["maps"], {"action": "nearby", "radius_km": 50})


def test_maps_iss_and_earthquakes(ctx, net):
    net["hosts"]["api.wheretheiss.at"] = lambda request: httpx.Response(200, json={
        "name": "iss", "id": 25544, "latitude": 12.34, "longitude": -45.6, "altitude": 418.2, "velocity": 27600.5,
        "visibility": "daylight"})
    r = call(ctx, "maps", {"action": "iss"})
    assert r["ok"] and "12.34°N, 45.60°W" in r["message"] and "418 km" in r["message"]
    assert r["card"]["data"]["points"] == [{"lat": 12.34, "lon": -45.6, "label": "ISS", "kind": "iss"}]
    assert r["card"]["data"]["stats"]["velocity_kmh"] == 27600

    now_ms = time.time() * 1000
    mags = [2.6, 5.1, 4.0, 6.3, 3.0] + [4.5] * 12
    features = [{"type": "Feature", "properties": {"mag": m, "place": f"quake {i}", "time": now_ms - 3 * 3600_000},
                 "geometry": {"type": "Point", "coordinates": [100.0 + i, -5.0, 10.0]}} for i, m in enumerate(mags)]
    features.append({"properties": {"mag": None, "place": "no magnitude"}, "geometry": {"coordinates": [0, 0, 0]}})
    net["hosts"]["earthquake.usgs.gov"] = lambda request: httpx.Response(200, json={"features": features})
    r = call(ctx, "maps", {"action": "earthquakes", "min_magnitude": 4})
    assert r["ok"] and len(r["earthquakes"]) == 10
    assert [q["mag"] for q in r["earthquakes"][:3]] == [6.3, 5.1, 4.5]
    assert all(q["mag"] >= 4 for q in r["earthquakes"])
    assert "15 earthquakes of magnitude 4+" in r["message"] and "M6.3 quake 3" in r["message"] and "3 h ago" in r["message"]
    assert r["card"]["data"]["center"] == {"lat": -5.0, "lon": 103.0} and len(r["card"]["data"]["points"]) == 10
    r = call(ctx, "maps", {"action": "earthquakes", "min_magnitude": 7})
    assert r["ok"] and r["earthquakes"] == [] and "No earthquakes" in r["message"]
    assert len(hits(net, "earthquake.usgs.gov")) == 1          # the feed is cached for a few minutes
    with pytest.raises(ToolArgError):
        validate(REGISTRY["maps"], {"action": "earthquakes", "min_magnitude": 1})


def test_maps_flights_and_rate_limits(ctx, net):
    def opensky(request):
        p = request.url.params
        assert (p["lamin"], p["lomin"], p["lamax"], p["lomax"]) == ("37.7167", "-10.1333", "39.7167", "-8.1333")
        return httpx.Response(200, json={"time": 1, "states": [
            ["abc123", "TAP123  ", "Portugal", 1, 1, -9.0, 38.8, 3000.0, False, 200.0, 90.0, 0, None, 3050.0, "1000", False, 0],
            ["def456", "", "Spain", 1, 1, -9.13, 38.71, None, True, 5.0, 0.0, 0, None, None, None, False, 0],
            ["fff000", "RYR9", "Ireland", 1, 1, None, None, 9000.0, False, 230.0, 10.0, 0, None, None, None, False, 0]]})

    net["hosts"]["opensky-network.org"] = opensky
    r = call(ctx, "maps", {"action": "flights", "place": "Lisbon"})
    assert r["ok"] and [a["callsign"] for a in r["aircraft"]] == ["TAP123", "def456"]   # airborne first
    assert r["aircraft"][0]["altitude_m"] == 3050 and r["aircraft"][0]["velocity_kmh"] == 720
    assert "2 aircraft around Lisbon" in r["message"] and r["untrusted"]
    assert [p["kind"] for p in r["card"]["data"]["points"]] == ["place", "aircraft", "aircraft"]

    geo._CACHE.clear()
    net["hosts"]["opensky-network.org"] = lambda request: httpx.Response(429, text="Too many requests")
    r = call(ctx, "maps", {"action": "flights", "place": "Lisbon"})
    assert not r["ok"] and "OpenSky" in r["message"] and "anonymous" in r["message"]

    net["hosts"]["earthquake.usgs.gov"] = lambda request: httpx.Response(502)
    r = call(ctx, "maps", {"action": "earthquakes"})
    assert not r["ok"] and "USGS" in r["message"] and "502" in r["message"]

    web._GEO_CACHE.clear()
    net["hosts"]["geocoding-api.open-meteo.com"] = lambda request: httpx.Response(429)
    r = call(ctx, "maps", {"action": "locate", "place": "Porto"})
    assert not r["ok"] and "limiting requests" in r["message"]

    def boom(request):
        raise httpx.ConnectTimeout("slow", request=request)
    net["hosts"]["api.wheretheiss.at"] = boom
    r = call(ctx, "maps", {"action": "iss"})
    assert not r["ok"] and "ConnectTimeout" in r["message"]


def test_maps_geometry_helpers():
    assert round(geo.distance_km(38.7167, -9.1333, 41.1496, -8.611)) == 274
    path = geo.great_circle(0, 0, 0, 90, n=5)
    assert path == [[0.0, 0.0], [0.0, 22.5], [0.0, 45.0], [0.0, 67.5], [0.0, 90.0]]
    assert len(geo.downsample(list(range(1000)), 200)) == 200
    assert geo.downsample([1, 2, 3]) == [1, 2, 3]


# ================================================================== circuits

def wires_of(r) -> dict[tuple[str, str], dict]:
    return {(w["from"], w["to"]): w for w in r["card"]["data"]["wires"]}


def test_circuit_dht11_pro_mini(ctx):
    r = call(ctx, "circuit", {"request": "connect DHT11 to Arduino Pro Mini"})
    assert r["ok"] and r["source"] == "library"
    card = r["card"]
    assert card["kind"] == "circuit" and card["ttl"] == 60
    data = card["data"]
    assert set(data) == {"title", "components", "wires", "steps", "warnings", "code", "language"}
    w = wires_of(r)
    assert w[("promini5:VCC", "dht11:VCC")]["color"] == "#f97316"
    assert w[("promini5:GND", "dht11:GND")]["color"] == "#ffffff"
    assert w[("promini5:D2", "dht11:DATA")]["color"] == "#38bdf8"
    assert all(x["step"] == 1 for x in data["wires"])
    assert any("pull-up" in x for x in data["warnings"])
    assert any("FTDI" in s for s in data["steps"])
    assert "DHT dht11(2, DHT11);" in data["code"] and data["language"] == "arduino"
    board = data["components"][0]
    assert board["id"] == "promini5" and [p["name"] for p in board["right_pins"]] == ["VCC", "D2", "GND"]
    assert "3 wires" in r["message"] and "1 warning" in r["message"]

    r = call(ctx, "circuit", {"request": "DHT11 on a Pro Mini 3.3V"})
    assert ("promini3:VCC", "dht11:VCC") in wires_of(r)


def test_circuit_hcsr04_esp32_needs_divider(ctx):
    r = call(ctx, "circuit", {"request": "HC-SR04 to ESP32", "action": "explain"})
    w = wires_of(r)
    assert ("esp32:VIN", "hcsr04:VCC") in w                    # 5 V part: 5 V from VIN, not 3V3
    assert ("hcsr04:ECHO", "div1:IN") in w and ("div1:GND", "esp32:GND") in w
    out = [k for k in w if k[0] == "div1:OUT"]
    assert len(out) == 1 and out[0][1].startswith("esp32:GPIO")
    assert not any(k == ("hcsr04:ECHO", out[0][1]) for k in w)   # never straight into the 3.3 V pin
    warnings = r["card"]["data"]["warnings"]
    assert any("divider" in x for x in warnings) and any("USB" in x for x in warnings)
    assert any(c["id"] == "div1" for c in r["card"]["data"]["components"])
    assert "ECHO → divider → GPIO" in r["message"]             # explain speaks the steps


def test_circuit_ssd1306_uno_uses_i2c_pins(ctx):
    r = call(ctx, "circuit", {"request": "wire an SSD1306 OLED to an Arduino Uno"})
    w = wires_of(r)
    assert w[("uno:A4", "ssd1306:SDA")]["color"] == "#a78bfa"
    assert w[("uno:A5", "ssd1306:SCL")]["color"] == "#a78bfa"
    assert ("uno:5V", "ssd1306:VCC") in w
    assert "Adafruit_SSD1306 oled(128, 64, &Wire, -1);" in r["card"]["data"]["code"]


def test_circuit_servo_nano_pwm_and_usb_warning(ctx):
    r = call(ctx, "circuit", {"request": "servo", "components": ["SG90", "Arduino Nano"]})
    w = wires_of(r)
    assert w[("nano:D9", "sg90:SIG")]["color"] == "#22c55e"
    assert ("nano:5V", "sg90:VCC") in w
    assert any("USB" in x and "mA" in x for x in r["card"]["data"]["warnings"])
    code = r["card"]["data"]["code"]
    assert "#include <Servo.h>" in code and "sg90.attach(9);" in code


def test_circuit_russian_request_and_pico(ctx):
    r = call(ctx, "circuit", {"request": "подключи датчик температуры dht11 к ардуино нано"})
    assert r["ok"] and r["title"] == "DHT11 → Arduino Nano"
    assert [c["id"] for c in r["card"]["data"]["components"]] == ["nano", "dht11"]
    assert "проводов" in r["message"] and r["card"]["data"]["steps"][0].startswith("Подключите DHT11")

    r = call(ctx, "circuit", {"request": "2 LEDs and a button on a Raspberry Pi Pico"})
    data = r["card"]["data"]
    assert data["language"] == "micropython"
    w = wires_of(r)
    assert ("pico:GP15", "r1:1") in w and ("r1:2", "led_1:A") in w and ("pico:GND", "led_1:K") in w
    assert ("pico:GP14", "r2:1") in w and ("pico:GP13", "button:1") in w
    compile(data["code"], "main.py", "exec")                   # the MicroPython sketch is valid Python syntax
    assert "led_1 = Pin(15, Pin.OUT)" in data["code"] and "Pin.PULL_UP" in data["code"]


def test_circuit_helpers_regulator_rails_and_no_board(ctx):
    r = call(ctx, "circuit", {"request": "BMP280 to Arduino Pro Mini on a breadboard"})
    w = wires_of(r)
    assert ("promini5:VCC", "reg:IN") in w and ("reg:OUT", "bmp280:VCC") in w   # 3.3 V part, no 3V3 pin
    assert ("promini5:GND", "bb:GND") in w and ("bb:GND", "bmp280:GND") in w
    steps = r["card"]["data"]["steps"]
    assert steps[0].startswith("Power first") and w[("promini5:VCC", "reg:IN")]["step"] == 1
    assert w[("reg:OUT", "bmp280:VCC")]["step"] == 2

    r = call(ctx, "circuit", {"request": "relay module and PIR sensor"})
    assert r["card"]["data"]["components"][0]["id"] == "uno"
    assert any("Arduino Uno" in x for x in r["card"]["data"]["warnings"])
    assert any("mains" in x for x in r["card"]["data"]["warnings"])

    r = call(ctx, "circuit", {"request": "how do I wire this?"})
    assert not r["ok"]


def model_reply():
    return {"title": "L298N → Arduino Uno",
            "components": [{"id": "uno", "name": "Arduino Uno", "pins": ["5V", "GND", "D8", "D99"]},
                           {"id": "l298n", "name": "L298N motor driver", "pins": ["IN1", "IN2", "GND", "+12V"]},
                           {"id": "dht11", "name": "DHT11", "pins": ["VCC", "DATA", "GND"]}],
            "wires": [{"from": "uno:D99", "to": "l298n:IN1", "kind": "signal", "label": "IN1"},
                      {"from": "uno:D8", "to": "l298n:IN2", "kind": "signal", "label": "IN2", "step": 1},
                      {"from": "uno:GND", "to": "l298n:GND", "kind": "ground"},
                      {"from": "uno:2", "to": "dht11:OUT", "kind": "signal"},
                      {"from": "uno:5V", "to": "dht11:FOO", "kind": "power"},
                      {"from": "mystery:1", "to": "uno:GND", "kind": "ground"}],
            "steps": ["Wire the driver inputs.", "Wire the sensor."],
            "warnings": ["Power the motors from their own supply."],
            "code": "void setup() {}\nvoid loop() {}\n", "language": "arduino"}


def test_circuit_model_answer_is_validated(ctx):
    ctx.brain = FakeBrain()
    ctx.brain.json_replies.append(model_reply())
    r = call(ctx, "circuit", {"request": "connect an L298N motor driver and a DHT11 to Arduino Uno"})
    assert r["ok"] and r["source"] == "model"
    prompt = ctx.brain.prompts[0]
    assert "l298n motor driver" in prompt and "- dht11: DHT11" in prompt and "- uno: Arduino Uno" in prompt
    w = wires_of(r)
    assert set(w) == {("uno:D8", "l298n:IN2"), ("uno:GND", "l298n:GND"), ("uno:D2", "dht11:DATA")}
    assert w[("uno:GND", "l298n:GND")]["color"] == "#ffffff"
    warnings = r["card"]["data"]["warnings"]
    dropped = [x for x in warnings if "doesn't exist" in x]
    assert len(dropped) == 3 and any("uno:D99" in x for x in dropped) and any("dht11:FOO" in x for x in dropped)
    assert any("Not in my parts library: L298N motor driver" in x for x in warnings)
    assert any("pull-up" in x for x in warnings)                  # library knowledge still applies
    assert "Power the motors from their own supply." in warnings
    comps = {c["id"]: c for c in r["card"]["data"]["components"]}
    assert comps["l298n"]["subtitle"] == "not in library"
    assert [p["name"] for p in comps["uno"]["right_pins"]] == ["D8", "GND", "D2"]


def test_circuit_validator_unit():
    raw = {"components": [{"id": "uno", "name": "Arduino Uno", "pins": []},
                          {"id": "bmp", "name": "BMP280 breakout", "pins": ["VIN", "GND", "SCK", "SDI"]}],
           "wires": [{"from": "uno:5V", "to": "bmp:VIN", "kind": "power"},
                     {"from": "uno:A4", "to": "bmp:SDI", "kind": "i2c"},
                     {"from": "uno:A9", "to": "bmp:SCK", "kind": "i2c"}],
           "steps": [], "warnings": []}
    data = circuits.validate_model(raw, "en")
    assert [(w["from"], w["to"]) for w in data["wires"]] == [("uno:5V", "bmp:VCC"), ("uno:A4", "bmp:SDA")]
    assert any("BMP280 VCC is wired to 5 V but takes 1.8–3.6 V" in x for x in data["warnings"])
    assert any("uno:A9" in x for x in data["warnings"])
    with pytest.raises(ValueError):
        circuits.validate_model({"components": raw["components"], "wires": [raw["wires"][2]]}, "en")
    with pytest.raises(ValueError):
        circuits.validate_model("not json", "en")


def test_circuit_model_failure_falls_back_to_library(ctx):
    ctx.brain = FakeBrain()
    ctx.brain.json_replies.append(RuntimeError("all model providers failed"))
    r = call(ctx, "circuit", {"request": "DHT22 and an L298N to ESP32"})
    assert r["ok"] and r["source"] == "library"
    assert ("esp32:3V3", "dht22:VCC") in wires_of(r) and ("esp32:GPIO4", "dht22:DATA") in wires_of(r)
    assert any("Not in my parts library: l298n" in x for x in r["card"]["data"]["warnings"])

    ctx.brain.json_replies.append({"components": [], "wires": []})
    r = call(ctx, "circuit", {"request": "stepper motor with a TMC2209"})
    assert not r["ok"] and "tmc2209" in r["message"]


def test_circuit_parse_aliases():
    p = circuits.parse("датчик температуры dht22 и сервопривод к esp32")
    assert p.board == "esp32" and p.parts == [("dht22", 1), ("sg90", 1)] and p.unknown == []
    p = circuits.parse("three LEDs, an MPU-6050 and a 16x2 LCD to a NodeMCU, pin D5")
    assert p.board == "nodemcu" and dict(p.parts) == {"led": 3, "mpu6050": 1, "lcd1602": 1} and p.unknown == []
    p = circuits.parse("ultrasonic sensor HC-SR04 with arduino uno 5v")
    assert p.parts == [("hcsr04", 1)] and p.unknown == []
    p = circuits.parse("MQ-2 gas sensor to arduino")
    assert p.board == "uno" and p.parts == [] and "mq2 gas" in p.unknown
    assert circuits.board_pin(circuits.BOARDS["esp32"], "D21") == "GPIO21"
    assert circuits.board_pin(circuits.BOARDS["nodemcu"], "GPIO14") == "D5"
    assert circuits.board_pin(circuits.BOARDS["pico"], "pin 15") == "GP15"
    assert circuits.board_pin(circuits.BOARDS["uno"], "D14") is None


# ================================================================== images

@pytest.fixture
def images(ctx, tmp_path):
    ctx.brain = FakeBrain()
    ctx.config.values["images"].update({"dir": str(tmp_path / "pics"), "max_per_hour": 3, "model": "img-model"})
    return tmp_path / "pics"


def test_generate_image_saves_with_safe_name(ctx, images):
    r = call(ctx, "generate_image", {"prompt": "A red fox in snow", "style": "watercolor", "aspect": "16:9"})
    assert r["ok"]
    path = Path(r["path"])
    assert path.parent == images and path.exists() and path.suffix == ".png"
    assert path.name.startswith("a-red-fox-in-snow-") and path.read_bytes() == ctx.brain.image_reply[0]
    prompt, model = ctx.brain.images[0]
    assert model == "img-model" and "Watercolour" in prompt and "16:9" in prompt
    assert r["card"] == {"kind": "image", "title": "A red fox in snow",
                         "data": {"path": str(path), "prompt": "A red fox in snow", "model": "img-model"}, "ttl": 30}

    ctx.brain.image_reply = (b"\xff\xd8\xff\xe0jpegdata", "image/jpeg", "")
    r = call(ctx, "generate_image", {"prompt": "../../etc/Кот в космосе"})
    path = Path(r["path"])
    assert path.parent == images and path.suffix == ".jpg" and path.name.startswith("etc-kot-v-kosmose-")
    assert r["message"].startswith("Готово")

    ctx.brain.image_reply = (b"not an image", "application/octet-stream", "")
    r = call(ctx, "generate_image", {"prompt": "x"})
    assert not r["ok"]


def test_generate_image_never_overwrites(ctx, images, monkeypatch):
    monkeypatch.setattr(imagegen.time, "strftime", lambda fmt, *a: "20261021-120000")
    ctx.brain.image_reply = (b"\x89PNG\r\n\x1a\nfirst", "image/png", "")
    first = Path(call(ctx, "generate_image", {"prompt": "same prompt"})["path"])
    ctx.brain.image_reply = (b"\x89PNG\r\n\x1a\nsecond", "image/png", "")
    second = Path(call(ctx, "generate_image", {"prompt": "same prompt"})["path"])
    assert first.name == "same-prompt-20261021-120000.png" and second.name == "same-prompt-20261021-120000-2.png"
    assert first.read_bytes().endswith(b"first") and second.read_bytes().endswith(b"second")


def test_generate_image_hourly_limit(ctx, images):
    ctx.memory.schema(imagegen.SCHEMA)
    ctx.memory.execute("INSERT INTO image_generations(ts, model) VALUES (?, ?)", (time.time() - 4000, "old"))
    ctx.brain.image_error = RuntimeError("quota")
    r = call(ctx, "generate_image", {"prompt": "one"})
    assert not r["ok"] and "quota" in r["message"]            # a failed attempt gives its slot back
    for prompt in ("one", "two", "three"):
        assert call(ctx, "generate_image", {"prompt": prompt})["ok"]
    r = call(ctx, "generate_image", {"prompt": "four"})
    assert not r["ok"] and r["limited"] and "limit" in r["message"]
    assert len(ctx.brain.images) == 4                          # 1 failed + 3 saved; the 4th never reached the model
    rows = ctx.memory.query("SELECT path FROM image_generations WHERE ts > ? ORDER BY id", (time.time() - 3600,))
    assert len(rows) == 3 and all(Path(row["path"]).exists() for row in rows)

    ctx.config.values["images"]["max_per_hour"] = 0
    assert "turned off" in call(ctx, "generate_image", {"prompt": "x"})["message"]


def test_generate_image_rejects_long_prompt_and_missing_model(ctx, images):
    r = call(ctx, "generate_image", {"prompt": "x" * 2001})
    assert not r["ok"] and "2000" in r["message"] and ctx.brain.images == []
    with pytest.raises(ToolArgError):
        validate(REGISTRY["generate_image"], {"prompt": "x", "aspect": "21:9"})
    ctx.brain = None
    assert "GEMINI_API_KEY" in call(ctx, "generate_image", {"prompt": "x"})["message"]
    assert imagegen.slug("Ünïcödé — Привет, мир!") == "unicode-privet-mir"
    assert imagegen.slug("!!!") == "image"
