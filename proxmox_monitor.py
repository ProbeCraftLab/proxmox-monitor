from pathlib import Path
import argparse
import time
import json
import ssl
from requests.adapters import HTTPAdapter
import re
import os
import paramiko
import getpass
import requests

# --- LOAD CONFIGURATION ---

PROJECT_DIR = Path(__file__).resolve().parent

try:
    with open(PROJECT_DIR / "config.json", "r", encoding="utf-8") as config_file:
        config = json.load(config_file)

except FileNotFoundError:
    raise SystemExit(
        "ERROR: config.json not found. "
        "Copy config.example.json to config.json and configure it."
    )

except json.JSONDecodeError as error:
    raise SystemExit(
        f"ERROR: Invalid config.json at line {error.lineno}, "
        f"column {error.colno}."
    )

# --- VALIDATE CONFIGURATION ---

required_settings = (
    "proxmox_url",
    "node_name",
    "token_id",
    "ca_cert",
    "ssh_host",
    "ssh_user",
    "ssh_key"
)

missing_settings = [
    key for key in required_settings if key not in config
]

if missing_settings:
    raise SystemExit(
        "ERROR: Missing settings in config.json: "
        + ", ".join(missing_settings)
    )

# --- CHECK CONFIGURATION VALUES ---

invalid_settings = [
    key for key in required_settings
    if not isinstance(config[key], str) or not config[key].strip()
]

if invalid_settings:
    raise SystemExit(
        "ERROR: Invalid or empty settings in config.json: "
        + ", ".join(invalid_settings)
    )

PROXMOX_URL = config["proxmox_url"]
NODE_NAME = config["node_name"]
TOKEN_ID = config["token_id"]
CA_CERT = PROJECT_DIR / config["ca_cert"]

# --- SECURE HTTPS CONNECTION ---

try:
    context = ssl.create_default_context(cafile=CA_CERT)

except OSError as error:
    raise SystemExit(
        f"ERROR: Cannot load HTTPS certificate: {CA_CERT}\n"
        f"Details: {error}\n"
        "Check the 'ca_cert' setting in config.json."
    )

context.verify_flags &= ~ssl.VERIFY_X509_STRICT

class ProxmoxAdapter(HTTPAdapter):
    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        pool_kwargs["ssl_context"] = context
        return host_params, pool_kwargs


session = requests.Session()
session.mount(f"{PROXMOX_URL}/", ProxmoxAdapter())


def display_snapshot(headers):
    try:
        response = session.get(
            f"{PROXMOX_URL}/api2/json/nodes/{NODE_NAME}/status",
            headers=headers,
            timeout=5
        )

    except requests.exceptions.RequestException:
        raise SystemExit(
            "ERROR: Cannot connect to Proxmox. "
            "Check the IP address, network connection, and port 8006."
        )

    if response.status_code == 401:
        raise SystemExit("ERROR: Invalid API token or secret.")

    if response.status_code != 200:
        raise SystemExit(f"ERROR: Proxmox API returned HTTP {response.status_code}")

    try:
        data = response.json()["data"]

        if not isinstance(data, dict):
            raise ValueError("Invalid node data")

    except (ValueError, KeyError, TypeError):
        raise SystemExit(
            "ERROR: Proxmox returned invalid or unexpected node status data."
        )

    try:
        cpu_value = float(data["cpu"])

        if not 0.0 <= cpu_value <= 1.0:
            raise ValueError("CPU usage outside valid range")

        cpu_percent = cpu_value * 100

    except (KeyError, TypeError, ValueError):
        raise SystemExit(
            "ERROR: Proxmox returned invalid CPU usage information."
        )

    try:
        memory_used = float(data["memory"]["used"])
        memory_total = float(data["memory"]["total"])

        if memory_total <= 0:
            raise ValueError("Invalid total memory")

        memory_percent = (memory_used / memory_total) * 100

    except (KeyError, TypeError, ValueError):
        raise SystemExit(
            "ERROR: Proxmox returned invalid RAM usage information."
        )

    try:
        disk_used = float(data["rootfs"]["used"])
        disk_total = float(data["rootfs"]["total"])

        if disk_total <= 0:
            raise ValueError("Invalid total disk capacity")

        disk_percent = (disk_used / disk_total) * 100

    except (KeyError, TypeError, ValueError):
        raise SystemExit(
            "ERROR: Proxmox returned invalid system disk information."
        )

    print(f"\n=== Proxmox Monitor V1 ===")
    print(f"Node: {NODE_NAME}")
    print("-" * 35)

    print(f"{'CPU usage:':<22}{cpu_percent:>6.1f}%")
    print(f"{'RAM usage:':<22}{memory_percent:>6.1f}%")
    print(f"{'System disk usage:':<22}{disk_percent:>6.1f}%")

    try:
        storage_response = session.get(
            f"{PROXMOX_URL}/api2/json/nodes/{NODE_NAME}/storage",
            headers=headers,
            timeout=5
        )

    except requests.exceptions.RequestException:
        raise SystemExit(
            "ERROR: Cannot retrieve storage information. "
            "Check the Proxmox connection."
        )

    if storage_response.status_code == 401:
        raise SystemExit("ERROR: Invalid API token or secret.")

    if storage_response.status_code != 200:
        raise SystemExit(
            f"ERROR: Cannot retrieve storage information. "
            f"HTTP {storage_response.status_code}"
        )

    try:
        storages = storage_response.json()["data"]

        if not isinstance(storages, list):
            raise ValueError("Invalid storage data")

    except (ValueError, KeyError, TypeError):
        raise SystemExit(
            "ERROR: Proxmox returned invalid or unexpected storage data."
        )

    print("\n--- Storage usage ---")
    print("-" * 35)

    if not storages:
        print("No storages visible. Check Proxmox storage permissions.")

    for storage in sorted(storages, key=lambda item: item.get("storage", "")):
        storage_name = storage.get("storage", "unknown")
        storage_type = storage.get("type", "unknown")
        storage_used = storage.get("used")
        storage_total = storage.get("total")

        if (
            isinstance(storage_used, (int, float))
            and isinstance(storage_total, (int, float))
            and storage_total > 0
        ):
            storage_percent = (storage_used / storage_total) * 100
            usage_text = f"{storage_percent:.1f}%"
        else:
            usage_text = "N/A"

        print(
            f"  {storage_name:<16} "
            f"{usage_text:>6}  "
            f"({storage_type})"
        )
    
    # --- TEMPERATURE MONITORING VIA SSH ---

    print("\n--- Temperature sensors ---")
    print("-" * 35)

    SSH_HOST = config["ssh_host"]
    SSH_USER = config["ssh_user"]
    SSH_KEY = os.path.expanduser(config["ssh_key"])

    client = paramiko.SSHClient()
    client.load_system_host_keys()

    try:
        client.connect(
            hostname=SSH_HOST,
            username=SSH_USER,
            key_filename=SSH_KEY,
            timeout=5,
            look_for_keys=False,
            allow_agent=False
        )

        stdin, stdout, stderr = client.exec_command("sensors", timeout=5)
        sensor_output = stdout.read().decode()
    
        exit_code = stdout.channel.recv_exit_status()

        if exit_code != 0:
            raise RuntimeError(
                f"The sensors command failed (exit code {exit_code})."
            )        

        if not sensor_output.strip():
            raise RuntimeError("The sensors command returned no data.")

        # Intel CPU temperature
        cpu_match = re.search(
            r"Package id \d+:\s+\+?([0-9.]+)°C",
            sensor_output
        )

        # AMD CPU temperature - prefer Tdie
        if not cpu_match:
            cpu_match = re.search(
                r"Tdie:\s+\+?([0-9.]+)°C",
                sensor_output
            )

        # AMD fallback - Tctl
        if not cpu_match:
            cpu_match = re.search(
                r"Tctl:\s+\+?([0-9.]+)°C",
                sensor_output
            )

        # NVMe temperature
        nvme_match = re.search(
            r"Composite:\s+\+?([0-9.]+)°C",
            sensor_output
        )

        # PCH temperature
        pch_match = re.search(
            r"(?m)^pch_[^\r\n]*\r?\nAdapter:[^\r\n]*\r?\ntemp1:\s*\+?([0-9.]+)°C",
            sensor_output
        )

        if cpu_match:
            print(f"{'CPU temperature:':<22}{float(cpu_match.group(1)):>6.1f}°C")
        else:
            print("CPU temperature: unavailable")

        if nvme_match:
            print(f"{'NVMe temperature:':<22}{float(nvme_match.group(1)):>6.1f}°C")
        else:
            print("NVMe temperature: unavailable")

        if pch_match:
            print(f"{'PCH temperature:':<22}{float(pch_match.group(1)):>6.1f}°C")
        else:
            print("PCH temperature: unavailable")

    except (paramiko.SSHException, OSError, EOFError, RuntimeError) as error:
        print(f"ERROR: Cannot retrieve temperatures via SSH: {error}")

    finally:
        client.close()

def main():
    parser = argparse.ArgumentParser(
        description="Read-only monitoring of a Proxmox node."
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Refresh measurements every 5 seconds until Ctrl+C."
    )
    args = parser.parse_args()

    token_secret = getpass.getpass("Token secret: ")
    headers = {
        "Authorization": f"PVEAPIToken={TOKEN_ID}={token_secret}"
    }

    try:
        if args.live:
            while True:
                os.system("cls" if os.name == "nt" else "clear")
                display_snapshot(headers)
                print("\nLive monitoring: refreshing every 5 seconds. Press Ctrl+C to stop.")
                time.sleep(5)
        else:
            display_snapshot(headers)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
