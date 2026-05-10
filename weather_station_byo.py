#!/usr/bin/env python3
import time
import json
import os
import socket
import requests
from gpiozero import MCP3008, Button
import bme280
import smbus2
from collections import deque
from datetime import datetime, timezone

# ------------------ Tijd ------------------
def current_utc_string():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
# ------------------ Configuratie ------------------
API_URL = "http://woltaief.raincatcher.online:8000/"
STATION_ID = "WOLTAIEF1"
STATION_PASSWORD = "641kGAk0ooWovYfvlbZJ5YlzODkL29gl"
BUFFER_FILE = "offline_buffer.json"
LOG_FILE = "upload.log"
REFERENCE_VOLTAGE = 3.3
WARN_THRESHOLD = 0.12
WIND_FACTOR = 0.6667  # m/s per puls/s
MEETINTERVAL = 60  # seconden
rain_count = 0
# ------------------ BME280 ------------------
port = 1
address = 0x76
bus = smbus2.SMBus(port)
calibration_params = bme280.load_calibration_params(bus, address)

def read_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000.0, 1)
    except Exception as e:
        log_event(f"⚠️ CPU-temp fout: {e}")
        return None


def read_bme280():
    try:
        bme280_data = bme280.sample(bus, address, calibration_params)
        return {
            "temperature": bme280_data.temperature,
            "pressure": bme280_data.pressure,
            "humidity": bme280_data.humidity
        }
    except Exception as e:
        log_event(f"Fout BME280: {e}")
        return {}

# ------------------ Windrichting ------------------
measured_voltages = [
    0.459, 1.388, 1.385, 1.217, 2.768, 2.713, 2.874, 2.242,
    2.533, 1.804, 1.975, 0.772, 0.063, 0.076, 0.276, 0.621
]
angles = [i * 22.5 for i in range(16)]
voltage_to_angle_map = {round(v, 3): a for v, a in zip(measured_voltages, angles)}
adc = MCP3008(channel=0)

def voltage_to_angle(voltage):
    closest_v = min(voltage_to_angle_map.keys(), key=lambda k: abs(k - voltage))
    angle = voltage_to_angle_map[closest_v]
    diff = abs(closest_v - voltage)
    return angle, closest_v, diff

def read_wind_direction():
    raw = adc.value
    voltage = raw * REFERENCE_VOLTAGE
    angle, ref_v, diff = voltage_to_angle(round(voltage, 3))
    if diff > WARN_THRESHOLD:
        log_event(f"⚠️ Windrichting afwijking: {voltage:.3f}V vs {ref_v:.3f}V")
    return {"wind_angle": angle, "voltage": voltage}

# ------------------ Windsnelheid ------------------
wind_sensor = Button(5)
wind_count = 0
gust_speeds = deque(maxlen=10)

def spin_detected():
    global wind_count
    wind_count += 1

wind_sensor.when_pressed = spin_detected

def read_wind_speed(interval=5):
    global wind_count
    wind_count = 0
    start_time = time.time()
    gust_speeds.clear()

    while time.time() - start_time < interval:
        count_at_moment = wind_count
        speed = (count_at_moment / (time.time() - start_time)) * WIND_FACTOR
        gust_speeds.append(speed)
        time.sleep(0.5)

    total_count = wind_count
    avg_speed = (total_count / interval) * WIND_FACTOR
    gust_speed = max(gust_speeds) if gust_speeds else 0
    return avg_speed, gust_speed
 # -----------

def bucket_tipped():
    global rain_count
    rain_count += 1

rain_sensor = Button(6)
rain_sensor.when_pressed = bucket_tipped



# ------------------ Internetcheck ------------------
def internet_available(host="8.8.8.8", port=53, timeout=3):
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except socket.error:
        return False

# ------------------ Bufferbeheer ------------------
def load_buffer():
    if os.path.exists(BUFFER_FILE):
        with open(BUFFER_FILE, "r") as f:
            try:
                return json.load(f)
            except:
                return []
    return []

def save_buffer(buffer):
    with open(BUFFER_FILE, "w") as f:
        json.dump(buffer, f)

def append_to_buffer(entry):
    buffer = load_buffer()
    buffer.append(entry)
    save_buffer(buffer)

def flush_buffer():
    buffer = load_buffer()
    if not buffer:
        return
    log_event(f"📤 Probeer {len(buffer)} buffered metingen te uploaden...")
    success_entries = []
    for entry in buffer:
        if upload_data(entry):
            success_entries.append(entry)
    remaining = [e for e in buffer if e not in success_entries]
    save_buffer(remaining)
    log_event(f"✅ {len(success_entries)} geüpload, {len(remaining)} blijven in buffer.")

# ------------------ Upload ------------------
#def upload_data(payload):
#    try:
#        response = requests.post(API_URL, data=payload, timeout=10)
#        if response.status_code == 200:
#            log_event("✅ Upload geslaagd: " + str(payload))
#            return True
#        else:
#            log_event(f"❌ Upload mislukt ({response.status_code}): {response.text}")
#            return False
#    except Exception as e:
#        log_event(f"⚠️ Uploadfout: {e}")
#        return False

def upload_data(payload):
    try:
        response = requests.post(
            API_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,  # GEEN json.dumps(), GEEN encode()
            timeout=10
        )
        if response.status_code == 200:
            log_event("✅ Upload geslaagd: " + str(payload))
            return True
        else:
            log_event(f"❌ Upload mislukt ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        log_event(f"⚠️ Uploadfout: {e}")
        return False


# ------------------ Logging ------------------
def log_event(message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {message}\n")

# ------------------ Hoofdloop ------------------
def main_loop(interval=MEETINTERVAL):
    global rain_count
    print(f"🌦️ Start weerstation — elke {interval} seconden metingen uitvoeren.")
    try:
        while True:
            bme_data = read_bme280()
            wind_data = read_wind_direction()
            avg_speed, gust_speed = read_wind_speed()
            cpu_temp = read_cpu_temp() 

            if datetime.now().hour == 0 and datetime.now().minute == 0:
                rain_count = 0 

            payload = {
                "ID": STATION_ID,
                "PASSWORD": STATION_PASSWORD,
                "action": "updateraw",
                "dateutc": current_utc_string(),
                "tempf": round(bme_data.get("temperature", 0) * 9 / 5 + 32, 2),
                "dailyrainin": round(rain_count * 0.00787, 3),
                "humidity": round(bme_data.get("humidity", 0), 1),
                "baromin": round(bme_data.get("pressure", 0) / 33.8639, 3),
                "winddir": round(wind_data["wind_angle"], 1),
                "windspeedmph": round(avg_speed * 2.23694, 2),
                "windgustmph": round(gust_speed * 2.23694, 2),
                "cputemp": round(cpu_temp * 9 / 5 + 32, 2) # ✅ Toegevoegd hier, correct ingesprongen
            }


            if internet_available():
                flush_buffer()
                if upload_data(payload):
                    print("🧹 Meting geüpload.")
                else:
                    print("⚠️ Upload faalde — meting gebufferd.")
                    append_to_buffer(payload)
            else:
                print("📡 Geen internet — meting gebufferd.")
                append_to_buffer(payload)

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n🛑 Weerstation gestopt door gebruiker.")

# ------------------ Start ------------------
if __name__ == "__main__":
    main_loop()

