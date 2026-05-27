import requests
import json
import sys
import codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2YTBiZWQ0NDlmYzdhODBjYTg1MTljNTUiLCJ0eXBlIjoiZGV2Iiwiand0aWQiOiI2YTBjNjMzNDhjZTg1ZjgwZWI4MzVkNmUifQ.Vx6H4A6YO-ZtVeEhPVbWWAGhGToyBEZTcht5tWMlY20"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}
BASE = "https://api.gologin.com"
FOLDER_ID = "6a01e809c4397236ebdaf311"

print("=== Tao profile trong folder Hoang Phong_100 ===")

profile_data = {
    "name": "Hoang Phong_",
    "browserType": "chrome",
    "os": "win",
    "navigator": {
        "language": "vi-VN,vi,en-US,en",
        "resolution": "1920x1080",
        "platform": "Win32",
        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    },
    "proxy": {
        "mode": "none",
        "host": "",
        "port": 80,
        "username": "",
        "password": ""
    },
    "proxyEnabled": False,
    "folder": FOLDER_ID,
}

r = requests.post(f"{BASE}/browser", headers=HEADERS, json=profile_data)
print(f"Status: {r.status_code}")
if r.status_code in (200, 201):
    result = r.json()
    print(f"Profile created!")
    print(f"  ID: {result.get('id')}")
    print(f"  Name: {result.get('name')}")
else:
    print(f"Error: {r.text[:1000]}")
