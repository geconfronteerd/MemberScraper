import os
import json
import subprocess
import sys
import websocket
import threading
import time
from typing import Optional

def load_config():
    if os.path.exists('config.json'):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("[CONFIG] Warning: config.json is not valid JSON.")
    return {}

def install_requirements():
    if os.path.exists('requirements.txt'):
        print("[SETUP] Installing dependencies from requirements.txt...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    else:
        print("[SETUP] requirements.txt not found, skipping installation.")

def create_output_folder():
    folder = 'scraped_data'
    if not os.path.exists(folder):
        os.makedirs(folder)
        print(f"[SETUP] Created folder: {folder}")

# Run setup on script start
install_requirements()
create_output_folder()
    

def get_token() -> Optional[str]:
    """Get Discord token from user with better instructions"""
    print("🔐 Discord Token Setup")
    print("=" * 60)
    print("To get your Discord token:")
    print("1. Open Discord in your web browser (NOT the desktop app)")
    print("2. Press F12 to open Developer Tools")
    print("3. Go to the 'Network' tab")
    print("4. Reload Discord (Ctrl+R) or send a message")
    print("5. Look for requests to 'discord.com/api'")
    print("6. Click on any request → Headers → Request Headers")
    print("7. Find 'authorization:' and copy the FULL value")
    print("\n⚠️  SECURITY WARNING:")
    print("   - Never share this token with anyone!")
    print("   - This gives full access to your Discord account!")
    print("   - Use at your own risk!")
    print("=" * 60)

    token = input("\n🎫 Paste your Discord token: ").strip()

    if not token:
        print("❌ No token provided!")
        return None

    # Clean up common token formats
    if token.startswith('Bearer '):
        token = token[7:]
    if token.startswith('"') and token.endswith('"'):
        token = token[1:-1]

    return token


def get_guild_id() -> Optional[str]:
    """Get Discord Guild ID from user with instructions"""
    print("\n🏰 Discord Guild ID Setup")
    print("=" * 60)
    print("To get your Discord Guild (Server) ID:")
    print("1. Open Discord and go to the server you want to scrape")
    print("2. Right-click on the server name (top left)")
    print("3. Select 'Copy Server ID' from the context menu")
    print("\n📝 Alternative method:")
    print("1. Enable Developer Mode: User Settings → Advanced → Developer Mode")
    print("2. Right-click the server name → 'Copy Server ID'")
    print("\n💡 The Guild ID is a long number (like: 798968708802281512)")
    print("\n⚠️  IMPORTANT:")
    print("   - You must be a member of the server to scrape it!")
    print("   - Make sure you have permission to access member lists!")
    print("=" * 60)

    guild_id = input("\n🎯 Paste your Guild ID: ").strip()

    if not guild_id:
        print("❌ No Guild ID provided!")
        return None

    # Validate that it looks like a valid Discord ID (should be all digits, ~18 characters)
    if not guild_id.isdigit():
        print("❌ Invalid Guild ID format! Should be all numbers.")
        return None
    
    if len(guild_id) < 17 or len(guild_id) > 20:
        print("⚠️  Warning: Guild ID length seems unusual, but proceeding...")

    return guild_id


# Configuration
token = None  # Will be obtained from user input
guild_id = None  # Will be obtained from user input
channel_id = "1154080170056097792"  # You might want to make this configurable too

# Global variables
heartbeat_interval = None
member_cache = set()
next_range_start = 0
page_size = 100  # request 100 users at a time
no_new_count = 0  # stop condition if nothing new shows up
ws = None
heartbeat_thread = None
member_data = []  # Store full member data for processing


def create_output_folder():
    """Create scraped_data folder if it doesn't exist"""
    if not os.path.exists('scraped_data'):
        os.makedirs('scraped_data')
        print("[FOLDER] Created 'scraped_data' folder")


def save_data():
    """Save collected data to files"""
    create_output_folder()

    server_nicknames = []
    usernames = []

    for member in member_data:
        # Extract username
        username = f"{member['user']['username']}#{member['user']['discriminator']}"
        usernames.append(username)

        # Extract server nickname (if exists)
        server_nickname = member.get('nick', '')
        if server_nickname:
            server_nicknames.append(f"{username} -> {server_nickname}")

    # Save server nicknames
    with open('scraped_data/server_nicknames.txt', 'w', encoding='utf-8') as f:
        for nickname in server_nicknames:
            f.write(nickname + '\n')

    # Save usernames
    with open('scraped_data/usernames.txt', 'w', encoding='utf-8') as f:
        for username in usernames:
            f.write(username + '\n')

    # Save overall data as JSON
    with open('scraped_data/data.json', 'w', encoding='utf-8') as f:
        json.dump(member_data, f, indent=2, ensure_ascii=False)

    print(f"[SAVED] Data saved to 'scraped_data' folder:")
    print(f"  - {len(server_nicknames)} server nicknames")
    print(f"  - {len(usernames)} total usernames")
    print(f"  - Full data in data.json")


def heartbeat():
    """Send heartbeat to keep connection alive"""
    while heartbeat_interval and ws and ws.sock and ws.sock.connected:
        try:
            ws.send(json.dumps({"op": 1, "d": None}))
            time.sleep(heartbeat_interval / 1000)
        except Exception as e:
            print(f"[HEARTBEAT] Error: {e}")
            break


def request_range(start, end):
    """Request a range of members"""
    print(f"[REQUEST] Asking for members {start}–{end}")
    payload = {
        "op": 14,
        "d": {
            "guild_id": guild_id,
            "channels": {
                channel_id: [
                    [start, end]
                ]
            }
        }
    }
    ws.send(json.dumps(payload))


def on_message(ws, message):
    global heartbeat_interval, next_range_start, no_new_count, heartbeat_thread

    try:
        packet = json.loads(message)
        op = packet.get("op")
        event = packet.get("t")
        data = packet.get("d", {})

        if op == 10:  # Hello
            print("[DISCORD] Op code: 10")
            heartbeat_interval = data["heartbeat_interval"]

            # Start heartbeat thread
            heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
            heartbeat_thread.start()

            # Send identify
            print("[GATEWAY] Sending Identify")
            identify_payload = {
                "op": 2,
                "d": {
                    "token": token,
                    "intents": 1 | 2 | 512 | 1024 | 4096,  # enough to get presence/members
                    "properties": {
                        "os": "linux",
                        "browser": "custom",
                        "device": "custom"
                    }
                }
            }
            ws.send(json.dumps(identify_payload))

        elif event == "READY":
            print(f"[READY] Logged in as {data['user']['username']}#{data['user']['discriminator']}")
            request_range(next_range_start, next_range_start + page_size - 1)

        elif event == "GUILD_MEMBER_LIST_UPDATE":
            print(f"[MEMBERS] Update for guild {data['guild_id']}")

            added = 0
            if "ops" in data:
                for op in data["ops"]:
                    if "items" in op:
                        for item in op["items"]:
                            if "member" in item and "user" in item["member"]:
                                member = item["member"]
                                user = member["user"]
                                tag = f"{user['username']}#{user['discriminator']}"

                                if tag not in member_cache:
                                    member_cache.add(tag)
                                    member_data.append(member)

                                    # Print username and server nickname if exists
                                    server_nick = member.get('nick', '')
                                    if server_nick:
                                        print(f" - {tag} (Server: {server_nick})")
                                    else:
                                        print(f" - {tag}")
                                    added += 1

            if added > 0:
                no_new_count = 0
            else:
                no_new_count += 1

            # Request next page if we still see activity
            if no_new_count < 3:
                next_range_start += page_size
                request_range(next_range_start, next_range_start + page_size - 1)
            else:
                print("[DONE] No more new members, finished paging.")
                print(f"[TOTAL] Collected {len(member_cache)} members")
                save_data()
                ws.close()

    except json.JSONDecodeError:
        print("[ERROR] Failed to decode JSON message")
    except Exception as e:
        print(f"[ERROR] Error processing message: {e}")


def on_error(ws, error):
    print(f"[ERROR] WebSocket error: {error}")


def on_close(ws, close_status_code, close_msg):
    print("[WS] Connection closed")
    if heartbeat_thread and heartbeat_thread.is_alive():
        heartbeat_interval = None


def on_open(ws):
    print("[WS] Connected to Discord Gateway")


def main():
    global ws, token, guild_id

    print("Starting Discord member scraper...")
    print("⚠️  Warning: Make sure you have permission to scrape this server")
    print("⚠️  Using user tokens may violate Discord's Terms of Service")
    print()

    # Get token from user
    token = get_token()
    if not token:
        print("Exiting...")
        return

    # Get guild ID from user
    guild_id = get_guild_id()
    if not guild_id:
        print("Exiting...")
        return

    print(f"\n🎯 Target Server: {guild_id}")
    print(f"📡 Target Channel: {channel_id}")
    print("\nStarting connection to Discord Gateway...")

    # websocket.enableTrace(False)  # Commented out - not needed
    ws = websocket.WebSocketApp(
        "wss://gateway.discord.gg/?v=10&encoding=json",
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    try:
        ws.run_forever()
    except KeyboardInterrupt:
        print("\n[INTERRUPT] Stopping scraper...")
        if member_data:
            save_data()
        ws.close()


if __name__ == "__main__":
    main()
