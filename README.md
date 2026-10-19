# Proxmox Monitor V1

A lightweight, command-line monitor for a **single Proxmox VE node**, written in Python. It displays CPU, RAM, system-disk and storage usage through the Proxmox API, plus available hardware temperatures through a restricted SSH connection.

The monitoring operations are read-only. You can request a single snapshot or refresh the display every five seconds.

## Features

- CPU, memory, and system (root filesystem) usage
- Usage of storage entries visible to the configured API token
- CPU package, NVMe, and PCH temperatures, when exposed by `sensors`
- **Snapshot mode** (default) and optional **Live Monitoring** mode
- API token secret entered at runtime, not stored in the application
- HTTPS server certificate verification using a locally trusted Proxmox CA
- SSH public-key authentication and known-host verification

## Requirements

- A reachable Proxmox VE node, with HTTPS API access on port `8006`
- A Proxmox API token with **read-only** permission to query the node and storage
- Python 3 and the packages listed in `requirements.txt`
- For temperatures: SSH access to a dedicated, unprivileged account permitted to run `sensors`, an SSH key, and an available `sensors` command on the host

**Tested development environment:** Windows 11, Python 3.14, and one Proxmox VE node. Other operating systems and configurations have not yet been fully tested.

## Secure Proxmox setup (one time)

> **For new installations only.** These setup steps require an administrator on the target Proxmox node and change its user/permission configuration. **The monitoring program itself is read-only and does not perform any of these steps.** If the API token, SSH account, and certificates are already configured, skip to [Quick start](#quick-start-windows-command-prompt). Do not alter existing administrator SSH access or networking for this project.

### 1. Create a dedicated, read-only API token

In the **Proxmox VE web interface**, sign in as an administrator:

1. Go to **Datacenter → Permissions → Users → Add**. Create a dedicated user in the **Proxmox VE authentication server (`pve`)** realm. Choose your own `YOUR_USER`; do **not** give this account an administrator role. Its login password, if configured, is not used by this monitor.
2. Go to **Datacenter → Permissions → API Tokens → Add**, select that user, and create a token named `YOUR_TOKEN_NAME`. **Keep Privilege Separation enabled**. Copy the token's **secret** to a safe place when it appears; Proxmox shows it only once. The token *ID* is `YOUR_USER@pve!YOUR_TOKEN_NAME`.
3. Under **Datacenter → Permissions → Add**, assign **User Permission** and **API Token Permission** entries as follows. Grant the built-in **`PVEAuditor`** role to **both** the user and the separated token:

   | ACL path | Role | Purpose |
   | --- | --- | --- |
   | `/nodes/YOUR_NODE_NAME` | `PVEAuditor` | Read status for the monitored node |
   | `/storage` | `PVEAuditor` | Read storage usage and allow storage entries to be listed |

   The `/storage` path covers storage objects within the cluster, but the role is **read-only**. Where supported by your setup, you can narrow storage visibility using `/storage/STORAGE_ID` permissions instead; confirm that the monitor still lists the intended storages. Do **not** assign `Administrator` or permissions at `/` just to make the monitor work.
4. In `config.json`, set `token_id` to the new token ID. **Do not put the token secret in any project file.** The program asks for the secret when you run it.

With **Privilege Separation** enabled, Proxmox intersects user permissions with token permissions. Both must permit the requested read operation. If storage is missing or the API returns HTTP 403, check the node/storage ACLs for **both identities** before widening permissions.

See [Proxmox VE access-control documentation](https://pve.proxmox.com/pve-docs/chapter-pveum.html) for details.

### 2. Prepare the HTTPS CA certificate

The program verifies the Proxmox HTTPS certificate using a CA certificate file. Ask your Proxmox administrator for a trusted copy of **`/etc/pve/pve-root-ca.pem`** and place that public CA certificate in the project folder on the monitoring computer. Set `ca_cert` to its filename in `config.json` (for example, `proxmox-ca.pem`). If the server uses a certificate issued by another CA, supply the appropriate trusted CA file instead.

**Never copy or share `pve-root-ca.key`**, which is the private CA key. Do not disable certificate or hostname verification to work around an error; check that the trusted CA and the host address in `proxmox_url` match the server. The program includes a compatibility adjustment for older Proxmox CA certificates while retaining CA/hostname verification.

### 3. Set up a restricted SSH account for temperatures

**Optional for API statistics, but required for temperature readings.** The current version attempts SSH at each refresh; if SSH is unavailable, it shows an error in the temperature section while keeping the API measurements already displayed. Use a **different account** from the Proxmox API user, and never use `root` for this monitoring key.

**On Windows (monitoring computer), in Command Prompt**, create a dedicated SSH key:

```cmd
if not exist "%USERPROFILE%\.ssh" mkdir "%USERPROFILE%\.ssh"
ssh-keygen -t ed25519 -f "%USERPROFILE%\.ssh\proxmox_monitor_ed25519" -C "proxmox-monitor"
type "%USERPROFILE%\.ssh\proxmox_monitor_ed25519.pub"
```

Copy the **entire public key** shown by the last command. Never copy or publish the private key (without `.pub`). The current V1 does not support prompting for an encrypted SSH-key passphrase, so this dedicated key must be accessible without a passphrase; protect it with your Windows account permissions and only authorize it for the forced command below. Do not reuse the key for other accounts.

**On the Proxmox host, using an existing administrator terminal** (replace `YOUR_SSH_USERNAME` in every command):

```bash
command -v sensors
sudo adduser --disabled-password --gecos "" YOUR_SSH_USERNAME
sudo install -d -m 700 -o YOUR_SSH_USERNAME -g YOUR_SSH_USERNAME /home/YOUR_SSH_USERNAME/.ssh
sudo nano /home/YOUR_SSH_USERNAME/.ssh/authorized_keys
```

The first command must display the location of the existing `sensors` executable (commonly `/usr/bin/sensors`). If it is missing, ask the host administrator to decide whether installing/configuring `lm-sensors` is appropriate; the monitor does not install anything on Proxmox.

In `authorized_keys`, insert **one line**, using the verified `sensors` path and your **actual public key**:

```text
restrict,command="/usr/bin/sensors" ssh-ed25519 REPLACE_WITH_ACTUAL_PUBLIC_KEY_DATA
```

The `restrict` option disables forwarding and interactive terminal allocation for that key. `command=` forces the server to run `sensors` even if a client requests something else. Make sure this monitoring account has **no sudo permissions, no password login, and no other unrestricted authorized keys**. Then run:

```bash
sudo chown YOUR_SSH_USERNAME:YOUR_SSH_USERNAME /home/YOUR_SSH_USERNAME/.ssh/authorized_keys
sudo chmod 600 /home/YOUR_SSH_USERNAME/.ssh/authorized_keys
```

**Back on Windows**, verify the real SSH host-key fingerprint with the administrator through a trusted channel **before** accepting its first-time prompt. Then run:

```cmd
ssh -T -i "%USERPROFILE%\.ssh\proxmox_monitor_ed25519" YOUR_SSH_USERNAME@YOUR_PROXMOX_IP sensors
```

You should see sensor readings, and the verified host key should be recorded in your Windows user's `~/.ssh/known_hosts` file. The Python monitor uses that host-key file; it does not automatically accept unknown SSH hosts.

Finally, set `ssh_host`, `ssh_user`, and `ssh_key` in `config.json`; the key path would be `~/.ssh/proxmox_monitor_ed25519` for the example above.

**Security boundary:** The restricted command applies to the **specific authorized key**, not automatically to every way of logging into the Linux account. Keep the dedicated account disabled for password login and do not add unrestricted keys.

## Quick start (Windows Command Prompt)

Clone or download the repository, open Command Prompt in its directory, then run:

```cmd
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
copy config.example.json config.json
```

1. Edit `config.json` with your node address, node name, API token ID, CA certificate filename, and restricted SSH account/key details. Do **not** put the API token *secret* in this file.
2. Obtain the Proxmox cluster CA certificate (`/etc/pve/pve-root-ca.pem`) from your trusted Proxmox administrator using a secure method and save it locally as `proxmox-ca.pem` (or update `ca_cert` in the configuration).
3. If needed, follow [Secure Proxmox setup](#secure-proxmox-setup-one-time) to create the limited API token and restricted SSH access. Verify the node's authentic SSH host key before adding it to your local `known_hosts` file.
4. Run one snapshot first and check that the displayed storages and sensors are what you expect.

The application reads `config.json` from the same directory as `proxmox_monitor.py`, regardless of the current working directory.

### Example configuration

See [`config.example.json`](config.example.json) for the full template:

```json
{
    "proxmox_url": "https://YOUR_PROXMOX_IP:8006",
    "node_name": "YOUR_NODE_NAME",
    "token_id": "YOUR_USER@pve!YOUR_TOKEN_NAME",
    "ca_cert": "YOUR_CA_CERT_FILE.pem",

    "ssh_host": "YOUR_PROXMOX_IP",
    "ssh_user": "YOUR_SSH_USERNAME",
    "ssh_key": "~/.ssh/YOUR_SSH_PRIVATE_KEY_FILENAME"
}
```

> **Security:** The account and token names above are examples, not credentials. Store `config.json`, CA certificate copies, and private SSH keys outside version control. The supplied `.gitignore` excludes the usual local files; always review `git status` before publishing.

## Usage

**Single snapshot:**

```cmd
python proxmox_monitor.py
```

**Live Monitoring** (refresh approximately every five seconds):

```cmd
python proxmox_monitor.py --live
```

Enter the API token **secret** when prompted. Live mode asks only once; press **Ctrl+C** to stop.

```cmd
python proxmox_monitor.py --help
```

The displayed temperature fields depend on which sensors are available. A sensor may show as *unavailable* on hardware that does not expose it.

## Security design

- The monitor uses **GET** requests for `/api2/json/nodes/<NODE_NAME>/status` and `/api2/json/nodes/<NODE_NAME>/storage`; it does not perform Proxmox configuration changes.
- Use the `PVEAuditor` role with the **minimum required scope**, rather than an administrator token. The exact ACLs depend on your Proxmox setup.
- Prefer a dedicated Linux SSH user, **without sudo**, with its public key restricted in `authorized_keys` to the sensors command, for example a key option beginning with:

  ```text
  restrict,command="/usr/bin/sensors" ssh-ed25519 YOUR_PUBLIC_KEY
  ```

  This is a *pattern*, not a complete public key. Verify the sensors path on your host first.
- SSH verifies the server against known host keys; HTTPS verifies the server certificate against the specified CA. For compatibility with certain older Proxmox CAs, the code disables OpenSSL's *strict X.509 checking* but **does not disable CA or hostname verification**.
- Setup of a dedicated API token and restricted SSH account requires administrator action on the Proxmox host. Once configured, the monitoring program itself does not install anything or write to the host.

## Notes and limitations

- Designed for **one node** in V1; there is no GUI, history database, alerting, or automatic discovery.
- If the API is unavailable or returns unexpected data, the run may stop with an error. Live mode does not yet retry failed API requests automatically.
- Temperature matching currently recognizes common Intel CPU package, AMD Tdie/Tctl, NVMe Composite, and PCH `sensors` output formats; other hardware may show unavailable values.
- Proxmox storage visibility depends on the token's access rights.

## Project status

V1 is being prepared for an initial public release. Installation instructions and compatibility will be refined as additional environments are tested.
