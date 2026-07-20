#!/usr/bin/env python3
import argparse
import sys
import os
import json
import re
import time
import subprocess
import random
from datetime import datetime
from collections import Counter
from curl_cffi import requests
from bs4 import BeautifulSoup

def load_env_file():
    """Loads environment variables from a local .env file in the script's directory."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    # Strip outer quotes
                    if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                        value = value[1:-1]
                    os.environ[key] = value

load_env_file()

# Try importing rich for premium CLI UI, fallback to standard print if not available
try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.panel import Panel
    from rich import box
    from rich.align import Align
    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# Fallback print functions if rich is not present
class MockConsole:
    def print(self, *args, **kwargs):
        # Strip rich-style markup if printing normally
        text = " ".join(str(a) for a in args)
        text = re.sub(r'\[/?\w+.*?\]', '', text)
        print(text, **kwargs)
    def log(self, *args, **kwargs):
        self.print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]", *args, **kwargs)

if not HAS_RICH:
    console = MockConsole()

DEFAULT_MOVIE_ID = "ET00480917"
DEFAULT_MOVIE_NAME = "the-odyssey"
DEFAULT_CITY = "chennai"
DEFAULT_DATES = ["20260725", "20260726"]
DEFAULT_INTERVAL = 300  # 5 minutes

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0"
]

def format_date_human(date_str):
    """Converts YYYYMMDD to a human-readable date format."""
    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
        return dt.strftime("%A, %B %d, %Y")
    except ValueError:
        return date_str

def trigger_notifications(found_dates, movie_name, city, args):
    """Triggers all configured alert notifications."""
    date_strs = ", ".join(format_date_human(d) for d in found_dates)
    title = "🎟️ Tickets Open Alert!"
    message = f"Bookings for '{movie_name.replace('-', ' ').title()}' in {city.title()} are now OPEN for: {date_strs}!"
    
    # 1. Desktop Notification (notify-send)
    try:
        subprocess.run([
            "notify-send", 
            title, 
            message, 
            "--urgency=critical", 
            "-i", "appointment-new"
        ], check=False)
    except Exception as e:
        console.log(f"[yellow]Warning: Failed to send desktop notification: {e}[/yellow]")
    
    # 2. Email Alert (if configured)
    if hasattr(args, 'email_sender') and args.email_sender and args.email_password:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            recipient = args.email_recipient or args.email_sender
            msg = MIMEMultipart()
            msg['From'] = args.email_sender
            msg['To'] = recipient
            msg['Subject'] = title
            
            body = f"Hello,\n\n{message}\n\nCheck showtimes here:\nhttps://in.bookmyshow.com/movies/{city}/{movie_name}/buytickets/{args.movie_id}/{found_dates[0]}\n\nRegards,\nBMS Monitor Script"
            msg.attach(MIMEText(body, 'plain'))
            
            # Connect to SMTP server
            if args.email_smtp_port == 465:
                server = smtplib.SMTP_SSL(args.email_smtp_server, args.email_smtp_port, timeout=15)
            else:
                server = smtplib.SMTP(args.email_smtp_server, args.email_smtp_port, timeout=15)
                server.starttls()
                
            server.login(args.email_sender, args.email_password)
            server.sendmail(args.email_sender, recipient, msg.as_string())
            server.close()
            console.log(f"[green]Successfully sent alert email to {recipient}[/green]")
        except Exception as e:
            console.log(f"[yellow]Warning: Failed to send email alert: {e}[/yellow]")

    # 3. Play Audio Sound
    sound_files = [
        "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
        "/usr/share/sounds/freedesktop/stereo/complete.oga",
        "/usr/share/sounds/freedesktop/stereo/audio-test-signal.oga"
    ]
    played = False
    for sound in sound_files:
        if os.path.exists(sound):
            for player in ["pw-play", "paplay", "mpg123"]:
                try:
                    subprocess.run([player, sound], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                    played = True
                    break
                except Exception:
                    continue
            if played:
                break
    
    # 4. Mobile / Web Push Notification via ntfy.sh (if configured)
    if hasattr(args, 'ntfy_topic') and args.ntfy_topic:
        try:
            ntfy_url = f"https://ntfy.sh/{args.ntfy_topic}"
            # Strip emojis or non-ascii from Title for header compatibility
            safe_title = title.encode('ascii', 'ignore').decode('ascii').strip()
            requests.post(
                ntfy_url,
                data=message.encode('utf-8'),
                headers={
                    "Title": safe_title,
                    "Priority": "high",
                    "Tags": "ticket,movie,loudspeaker"
                },
                timeout=10
            )
            console.log(f"[green]Successfully sent push notification to ntfy.sh/{args.ntfy_topic}[/green]")
        except Exception as e:
            console.log(f"[yellow]Warning: Failed to send push notification to ntfy.sh: {e}[/yellow]")

def fetch_page(session, url, retries=3, backoff_factor=3):
    """
    Fetches the page content with retries and exponential backoff.
    Re-creates the session object on 403 errors or network drops.
    """
    current_session = session
    for attempt in range(retries):
        try:
            # We omit custom headers to let curl_cffi use default matching browser headers
            r = current_session.get(url, impersonate="chrome", timeout=15)
            
            if r.status_code == 200:
                if "Just a moment..." in r.text or "Turnstile" in r.text:
                    console.log(f"[yellow]Cloudflare challenge page detected (attempt {attempt + 1}/{retries}). Re-creating session...[/yellow]")
                    current_session.close()
                    current_session = requests.Session()
                    time.sleep(backoff_factor * (2 ** attempt))
                    continue
                return r.text, current_session, None
                
            elif r.status_code == 403:
                console.log(f"[yellow]HTTP 403 Forbidden (attempt {attempt + 1}/{retries}). Re-creating session...[/yellow]")
                current_session.close()
                current_session = requests.Session()
                time.sleep(backoff_factor * (2 ** attempt))
                
            else:
                return None, current_session, f"HTTP Error {r.status_code}"
                
        except Exception as e:
            console.log(f"[yellow]Request exception (attempt {attempt + 1}/{retries}): {e}. Re-creating session...[/yellow]")
            current_session.close()
            current_session = requests.Session()
            time.sleep(backoff_factor * (2 ** attempt))
            
    return None, current_session, "Max retries exceeded (Cloudflare or network block)"

def check_bookings(session, movie_id, movie_name, city, target_dates, venue_codes=None, min_references=10):
    """
    Scrapes the showtimes page and checks if any target dates are active/open and bookable.
    """
    found_dates = []
    carousel_dates = []
    updated_session = session
    
    for target_date in target_dates:
        url = f"https://in.bookmyshow.com/movies/{city}/{movie_name}/buytickets/{movie_id}/{target_date}"
        
        html, updated_session, error_msg = fetch_page(updated_session, url)
        if error_msg:
            return False, [], [], error_msg, updated_session
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Parse all dates in the carousel for logging/reporting
        date_divs = soup.find_all(lambda tag: tag.name == 'div' and tag.get('id') and re.match(r'^\d{8}$', tag.get('id')))
        for div in date_divs:
            d_id = div.get('id')
            if d_id not in carousel_dates:
                carousel_dates.append(d_id)
                
        # 2. Find which date is selected in the carousel (DayName class == MonthName class)
        selected_date = None
        for div in date_divs:
            children = div.find_all('div')
            if len(children) >= 3:
                day_name_class = children[0].get('class', [])
                month_name_class = children[2].get('class', [])
                if day_name_class == month_name_class:
                    selected_date = div.get('id')
                    break
                    
        # 3. Find if there are active showtimes on the page
        showtime_pattern = re.compile(r'\b\d{2}:\d{2}\s*(?:AM|PM)\b')
        showtime_nodes = soup.find_all(string=showtime_pattern)
        has_showtimes = len(showtime_nodes) > 0
        
        # Primary check: Target date is selected in the carousel and has active showtimes
        primary_open = (selected_date == target_date) and has_showtimes
        
        # Fallback checks (inspired by Movie-Alert)
        fallback_open = False
        fallback_reasons = []
        
        if not primary_open:
            # A. Venue-Date Fallback: check if target venue codes are listed for target_date
            if venue_codes:
                for code in venue_codes:
                    pattern = f"/{code}/{target_date}"
                    if pattern in html:
                        fallback_open = True
                        fallback_reasons.append(f"Venue link found for {code}")
                        break
            
            # B. BMS-Date Fallback: count date tokens on the page
            tokens = re.findall(r"\b20\d{6}\b", html)
            if tokens:
                counts = Counter(tokens)
                top_date = counts.most_common(1)[0][0]
                requested_count = counts.get(target_date, 0)
                if top_date == target_date and requested_count >= min_references:
                    fallback_open = True
                    fallback_reasons.append(f"Dominant date token ({requested_count} refs)")
        
        if primary_open or fallback_open:
            reason_str = "primary check" if primary_open else f"fallback check ({', '.join(fallback_reasons)})"
            console.log(f"[green]Date {target_date} detected open via {reason_str}[/green]")
            found_dates.append(target_date)
            
    carousel_dates.sort()
    return True, found_dates, carousel_dates, None, updated_session

def install_systemd_service(args):
    """Helper to install the monitor as a systemd user service using EnvironmentFile for security."""
    service_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(service_dir, exist_ok=True)
    
    script_path = os.path.abspath(sys.argv[0])
    python_path = sys.executable
    project_dir = os.path.dirname(script_path)
    env_file = os.path.join(project_dir, ".env")
    
    # Check if .env exists, if not write a template
    if not os.path.exists(env_file):
        console.print(f"[yellow]Warning: No .env file found at {env_file}. Creating a template for you.[/yellow]")
        with open(env_file, "w") as f:
            f.write(f"""# BookMyShow Monitor Configuration
 
# Target Movie & City settings
BMS_MOVIE_ID={args.movie_id}
BMS_MOVIE_NAME={args.movie_name}
BMS_CITY={args.city}
BMS_DATES={",".join(args.dates)}
BMS_INTERVAL={args.interval}

# Fallback Detectors settings (optional)
BMS_VENUE_CODES={",".join(args.venue_codes) if args.venue_codes else ""}
BMS_MIN_REFERENCES={args.min_references}
 
# Mobile Push Alerts (ntfy.sh)
BMS_NTFY_TOPIC={args.ntfy_topic or "your-custom-ntfy-topic"}
 
# Email Notifications (SMTP)
BMS_EMAIL_SENDER={args.email_sender or "your-email@gmail.com"}
BMS_EMAIL_PASSWORD={args.email_password or "your-app-password"}
BMS_EMAIL_RECIPIENT={args.email_recipient or "destination-email@gmail.com"}
BMS_EMAIL_SMTP_SERVER={args.email_smtp_server or "smtp.gmail.com"}
BMS_EMAIL_SMTP_PORT={args.email_smtp_port or 587}
""")
        console.print(f"[green]✔ Created configuration file: {env_file}[/green]")
        console.print("[yellow]Please edit this file to add your actual secrets (e.g. SMTP passwords) before starting the service.[/yellow]")
        
    service_content = f"""[Unit]
Description=BookMyShow Movie Ticket Monitor Service
After=network.target

[Service]
Type=simple
WorkingDirectory={project_dir}
EnvironmentFile=-{env_file}
ExecStart={python_path} {script_path}
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""
    
    service_file = os.path.join(service_dir, "bms-ticket-monitor.service")
    with open(service_file, "w") as f:
        f.write(service_content)
        
    console.print(f"[green]✔ Systemd service file written to: {service_file}[/green]")
    console.print("\n[bold]To enable and start the service in the background, run:[/bold]")
    console.print("  systemctl --user daemon-reload")
    console.print("  systemctl --user enable bms-ticket-monitor")
    console.print("  systemctl --user start bms-ticket-monitor")
    console.print("\n[bold]To view logs of the background service, run:[/bold]")
    console.print("  journalctl --user -u bms-ticket-monitor -f -n 50")

def run_monitor(args):
    """Main monitoring loop."""
    # Test notifications if requested
    if args.test_notify:
        console.print("[cyan]Testing notifications...[/cyan]")
        trigger_notifications(args.dates[:1], args.movie_name, args.city, args)
        console.print("[green]Test notifications completed successfully.[/green]")
        return

    # Log setup
    console.print(Panel(
        Align.center(
            f"[bold green]🎫 BookMyShow Ticket Monitor Running[/bold green]\n"
            f"[bold white]Movie:[/] {args.movie_name.replace('-', ' ').title()} ({args.movie_id})\n"
            f"[bold white]City:[/] {args.city.title()} | [bold white]Interval:[/] {args.interval}s\n"
            f"[bold white]Watching Dates:[/] {', '.join(format_date_human(d) for d in args.dates)}\n"
            + (f"[bold white]Mobile Alerts:[/] ntfy.sh/{args.ntfy_topic}\n" if args.ntfy_topic else "")
            + (f"[bold white]Email Alerts:[/] {args.email_recipient or args.email_sender}\n" if args.email_sender else "")
        ),
        box=box.ROUNDED,
        border_style="green"
    ))
    
    session = requests.Session()
    consecutive_errors = 0
    check_count = 0
    
    while True:
        check_count += 1
        console.log(f"Check #{check_count}: Contacting BookMyShow...")
        
        success, found_dates, carousel_dates, error_msg, session = check_bookings(
            session, args.movie_id, args.movie_name, args.city, args.dates,
            venue_codes=args.venue_codes, min_references=args.min_references
        )
        
        if success:
            consecutive_errors = 0
            human_carousel = [datetime.strptime(d, "%Y%m%d").strftime("%d %b") for d in sorted(carousel_dates) if len(d) == 8]
            console.log(f"[green]Success.[/green] Currently open dates: {', '.join(human_carousel)}")
            
            if found_dates:
                console.log(f"[bold red]💥 TARGET BOOKINGS OPEN FOR: {', '.join(found_dates)}!!![/bold red]")
                # Alert!
                trigger_notifications(found_dates, args.movie_name, args.city, args)
                
                # Stop monitoring or let it run to avoid missing others?
                # Usually we want to keep running to notify or stop once we notify successfully.
                console.log("[green]Alert sent successfully. Keeping the monitor active for any updates.[/green]")
            else:
                console.log("[cyan]Target dates not yet open.[/cyan]")
        else:
            consecutive_errors += 1
            console.log(f"[red]Error checking BookMyShow: {error_msg}[/red]")
            
            if consecutive_errors >= 5:
                # Notify the user that the script is failing repeatedly (e.g. rate limit, internet down)
                subprocess.run([
                    "notify-send", 
                    "⚠️ Ticket Monitor Error", 
                    f"BMS Monitor has failed {consecutive_errors} times consecutively. Error: {error_msg}", 
                    "--urgency=normal"
                ], check=False)
                consecutive_errors = 0 # Reset count to avoid spamming alerts about errors
                
        # Sleep with countdown panel
        sleep_start = time.time()
        next_check_time = sleep_start + args.interval
        
        # Countdown loop
        while time.time() < next_check_time:
            remaining = int(next_check_time - time.time())
            if remaining <= 0:
                break
            
            # Update terminal progress/countdown if rich is installed
            if HAS_RICH and sys.stdout.isatty():
                # Print a small countdown text that updates in-place
                sys.stdout.write(f"\rNext check in {remaining:02d}s... ")
                sys.stdout.flush()
                time.sleep(1)
            else:
                # If running as systemd service, just sleep the full interval
                time.sleep(min(remaining, 10))
                
        if HAS_RICH and sys.stdout.isatty():
            sys.stdout.write("\r" + " " * 30 + "\r")
            sys.stdout.flush()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor BookMyShow showtimes for specific dates.")
    parser.add_argument("--movie-id", default=os.environ.get("BMS_MOVIE_ID", DEFAULT_MOVIE_ID), help="BMS movie ID (from URL)")
    parser.add_argument("--movie-name", default=os.environ.get("BMS_MOVIE_NAME", DEFAULT_MOVIE_NAME), help="BMS movie URL slug")
    parser.add_argument("--city", default=os.environ.get("BMS_CITY", DEFAULT_CITY), help="City name in BMS URL")
    parser.add_argument("--dates", default=os.environ.get("BMS_DATES", ",".join(DEFAULT_DATES)), help="Comma-separated target dates (YYYYMMDD)")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("BMS_INTERVAL", DEFAULT_INTERVAL)), help="Monitoring interval in seconds")
    parser.add_argument("--ntfy-topic", default=os.environ.get("BMS_NTFY_TOPIC"), help="ntfy.sh custom topic code for mobile push notifications")
    parser.add_argument("--email-sender", default=os.environ.get("BMS_EMAIL_SENDER"), help="Sender email address for SMTP email notifications")
    parser.add_argument("--email-password", default=os.environ.get("BMS_EMAIL_PASSWORD"), help="Password or App Password for SMTP email login")
    parser.add_argument("--email-recipient", default=os.environ.get("BMS_EMAIL_RECIPIENT"), help="Recipient email address (defaults to sender email)")
    parser.add_argument("--email-smtp-server", default=os.environ.get("BMS_EMAIL_SMTP_SERVER", "smtp.gmail.com"), help="SMTP server (default: smtp.gmail.com)")
    parser.add_argument("--email-smtp-port", type=int, default=int(os.environ.get("BMS_EMAIL_SMTP_PORT", 587)), help="SMTP port (default: 587)")
    parser.add_argument("--venue-codes", default=os.environ.get("BMS_VENUE_CODES"), help="Comma-separated venue codes for fallback check (e.g. INPR,PVPZ)")
    parser.add_argument("--min-references", type=int, default=int(os.environ.get("BMS_MIN_REFERENCES", 10)), help="Min page references count for fallback bms_date check (default: 10)")
    parser.add_argument("--test-notify", action="store_true", help="Send test notifications and exit")
    parser.add_argument("--install-service", action="store_true", help="Generate and install Systemd user service")
    
    args = parser.parse_args()
    
    # Process target dates
    args.dates = [d.strip() for d in args.dates.split(",") if re.match(r'^\d{8}$', d.strip())]
    if not args.dates:
        print("Error: No valid target dates provided. Dates must be in YYYYMMDD format.")
        sys.exit(1)
        
    # Process venue codes
    if args.venue_codes:
        args.venue_codes = [c.strip() for c in args.venue_codes.split(",") if c.strip()]
        
    if args.install_service:
        install_systemd_service(args)
    else:
        try:
            run_monitor(args)
        except KeyboardInterrupt:
            console.print("\n[yellow]Monitoring stopped by user.[/yellow]")
            sys.exit(0)
