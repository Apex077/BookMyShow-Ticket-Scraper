# BookMyShow Movie Ticket Monitor

This project monitors BookMyShow showtimes for specific dates (e.g. July 25th or 26th) for **The Odyssey** in **Chennai** and alerts you immediately when bookings open.

It uses browser impersonation (via `curl-cffi`) to bypass Cloudflare anti-bot blocks and provides multiple notification channels:
- **Desktop Notifications** (native Linux `notify-send`)
- **Audio Alerts** (PipeWire/PulseAudio system sound)
- **Email Alerts** (SMTP SSL/TLS)
- **Mobile Push Notifications** (via free `ntfy.sh` channel)

---

## Security First 🔒
To protect your email credentials and application passwords from leaking into shell history, process listings (`ps aux`), or plaintext systemd unit files, this project uses a `.env` file for configuration.

---

## Quick Start

### 1. Set Up Environment & Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install requests beautifulsoup4 curl-cffi rich
```

### 2. Configure Settings
Copy the example configuration file and fill in your details:
```bash
cp .env.example .env
```
Open `.env` in your favorite editor and configure your target movie, dates, and credentials (e.g., SMTP details or ntfy.sh topic).

### 3. Test Notifications
Verify that desktop notifications, audio, and configured remote alerts are working correctly:
```bash
./venv/bin/python3 monitor.py --test-notify
```

### 4. Run the Monitor
Run the monitor locally in your terminal:
```bash
./venv/bin/python3 monitor.py
```
*(Arguments passed directly on the command line will override values specified in the `.env` file if needed).*

---

## Run Persistently in the Background (Systemd Daemon)

To install the monitor as a background service:

1. **Install the service** (this reads configuration from `.env` and sets up a secure user service):
   ```bash
   ./venv/bin/python3 monitor.py --install-service
   ```

2. **Enable and start the service**:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable bms-ticket-monitor
   systemctl --user start bms-ticket-monitor
   ```

3. **Check daemon status and logs**:
   ```bash
   systemctl --user status bms-ticket-monitor
   journalctl --user -u bms-ticket-monitor -f -n 50
   ```

4. **Updating Configuration**:
   Simply edit the `.env` file in the project folder and restart the service:
   ```bash
   systemctl --user restart bms-ticket-monitor
   ```
