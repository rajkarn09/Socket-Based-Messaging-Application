"""
server.py  –  Multi-Room Chat Server
======================================
A threaded TCP chat server that manages users, chat rooms, and message
routing.  Each connecting client is handled in its own thread.

Architecture overview
---------------------
    ┌──────────────┐
    │  Main Thread  │   Listens on (host, port) for incoming connections.
    │  (Acceptor)   │   Spawns a handler thread per client.
    └──────┬───────┘
           │ accept()
    ┌──────▼───────┐
    │ ClientThread  │   Runs recv-loop, dispatches commands.
    │  (per user)   │   Accesses shared state through locks.
    └──────────────┘

Shared state (protected by threading.Lock):
    • users        – dict mapping username → ClientInfo
    • rooms        – dict mapping room_name → set of usernames
    • user_rooms   – dict mapping username → current active room name

Usage
-----
    python server.py                    # uses config.json defaults
    python server.py --host 0.0.0.0     # override host
    python server.py --port 6000        # override port
"""

import argparse
import json
import logging
import os
import socket
import sys
import threading
from datetime import datetime

# ── Import our custom framing protocol ──────────────────────────────
from protocol import send_message, recv_message

# ────────────────────────────────────────────────────────────────────
#  Configuration Management
# ────────────────────────────────────────────────────────────────────

# Path to the config file (same directory as this script)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config() -> dict:
    """Load configuration from config.json with sensible defaults."""
    defaults = {
        "host": "127.0.0.1",
        "port": 5000,
        "max_connections": 50,
        "log_level": "INFO",
        "log_file": "server.log",
        "default_rooms": ["General", "Random"],
    }
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            user_cfg = json.load(fh)
        defaults.update(user_cfg)
        print(f"[CONFIG] Loaded configuration from {CONFIG_PATH}")
    except FileNotFoundError:
        print(f"[CONFIG] {CONFIG_PATH} not found – using defaults.")
    except json.JSONDecodeError as exc:
        print(f"[CONFIG] Invalid JSON in {CONFIG_PATH}: {exc} – using defaults.")
    return defaults


# ────────────────────────────────────────────────────────────────────
#  Logging Setup
# ────────────────────────────────────────────────────────────────────

def setup_logging(cfg: dict) -> logging.Logger:
    """Configure root and application loggers."""
    log_level = getattr(logging, cfg.get("log_level", "INFO").upper(), logging.INFO)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)

    # File handler
    log_file = cfg.get("log_file", "server.log")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # always capture DEBUG in the file
    file_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s  %(message)s",
    )
    file_handler.setFormatter(file_fmt)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    return logging.getLogger("server")


# ────────────────────────────────────────────────────────────────────
#  Data Structures
# ────────────────────────────────────────────────────────────────────

class ClientInfo:
    """Holds metadata about a single connected client."""

    def __init__(self, sock: socket.socket, address: tuple, username: str):
        self.sock = sock
        self.address = address
        self.username = username


# ────────────────────────────────────────────────────────────────────
#  Chat Server
# ────────────────────────────────────────────────────────────────────

class ChatServer:
    """
    Central chat server.

    Responsibilities:
        • Accept TCP connections and spawn per-client handler threads.
        • Enforce unique usernames.
        • Manage chat rooms (create, join, list, invite).
        • Route messages to all users in the sender's current room.
    """

    def __init__(self, host: str, port: int, cfg: dict):
        self.host = host
        self.port = port
        self.cfg = cfg
        self.logger = logging.getLogger("server")

        # ── Shared state ───────────────────────────────────────────
        self.lock = threading.Lock()          # protects all dicts below
        self.users: dict[str, ClientInfo] = {}        # username → ClientInfo
        self.rooms: dict[str, set[str]] = {}          # room_name → {usernames}
        self.user_rooms: dict[str, str] = {}           # username → active room

        # Create default rooms
        for room_name in cfg.get("default_rooms", ["General", "Random"]):
            self.rooms[room_name] = set()

        # ── Server socket ──────────────────────────────────────────
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # ────────────────────────────────────────────────────────────────
    #  Start / Accept Loop
    # ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Bind, listen, and enter the accept loop."""
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(self.cfg.get("max_connections", 50))
        self.logger.info(
            "Server listening on %s:%d  (default rooms: %s)",
            self.host, self.port,
            ", ".join(self.rooms.keys()),
        )
        print(f"\n{'='*55}")
        print(f"  CHAT SERVER RUNNING  –  {self.host}:{self.port}")
        print(f"  Default rooms: {', '.join(self.rooms.keys())}")
        print(f"  Press Ctrl+C to shut down.")
        print(f"{'='*55}\n")

        try:
            while True:
                client_sock, client_addr = self.server_sock.accept()
                self.logger.info("New TCP connection from %s:%d", *client_addr)
                # Spawn a daemon thread so it dies when the main thread exits
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, client_addr),
                    daemon=True,
                )
                t.start()
        except KeyboardInterrupt:
            self.logger.info("Server shutting down (KeyboardInterrupt).")
        finally:
            self.server_sock.close()

    # ────────────────────────────────────────────────────────────────
    #  Per-Client Handler
    # ────────────────────────────────────────────────────────────────

    def _handle_client(self, sock: socket.socket, addr: tuple) -> None:
        """
        Receive-loop for a single client.  Runs in its own thread.

        Steps:
            1. Wait for a CONNECT message with the desired username.
            2. Validate username uniqueness.
            3. Auto-join the first default room.
            4. Enter the command dispatch loop.
            5. Clean up on disconnect.
        """
        username = None
        try:
            # ── Step 1 & 2: Registration ───────────────────────────
            msg = recv_message(sock)
            if msg is None or msg.get("type") != "CONNECT":
                send_message(sock, {
                    "type": "ERROR",
                    "message": "Expected CONNECT message as first packet.",
                })
                sock.close()
                return

            desired_name = msg.get("username", "").strip()
            if not desired_name:
                send_message(sock, {
                    "type": "ERROR",
                    "message": "Username cannot be empty.",
                })
                sock.close()
                return

            with self.lock:
                if desired_name in self.users:
                    send_message(sock, {
                        "type": "ERROR",
                        "message": f"Username '{desired_name}' is already taken.",
                    })
                    sock.close()
                    return

                # Register the user
                username = desired_name
                self.users[username] = ClientInfo(sock, addr, username)

                # Auto-join the first default room
                default_room = list(self.rooms.keys())[0]
                self.rooms[default_room].add(username)
                self.user_rooms[username] = default_room

            self.logger.info("User '%s' registered from %s:%d", username, *addr)

            # Send confirmation to the client
            send_message(sock, {
                "type": "CONNECT_OK",
                "username": username,
                "room": default_room,
                "rooms": list(self.rooms.keys()),
                "users_in_room": list(self.rooms[default_room]),
            })

            # Broadcast join notification to others in the room
            self._broadcast_to_room(default_room, {
                "type": "USER_JOINED",
                "username": username,
                "room": default_room,
                "timestamp": self._timestamp(),
            }, exclude=username)

            # ── Step 4: Command dispatch loop ──────────────────────
            while True:
                msg = recv_message(sock)
                if msg is None:
                    # Client disconnected
                    break
                self._dispatch(username, msg)

        except (ConnectionError, OSError) as exc:
            self.logger.warning("Connection error for '%s': %s", username, exc)
        except ValueError as exc:
            self.logger.error("Protocol error for '%s': %s", username, exc)
        finally:
            # ── Step 5: Cleanup ────────────────────────────────────
            if username:
                self._remove_user(username)
            try:
                sock.close()
            except OSError:
                pass

    # ────────────────────────────────────────────────────────────────
    #  Command Dispatcher
    # ────────────────────────────────────────────────────────────────

    def _dispatch(self, username: str, msg: dict) -> None:
        """Route an incoming message to the appropriate handler."""
        msg_type = msg.get("type", "")
        handlers = {
            "SEND_MSG":     self._handle_send_msg,
            "CREATE_ROOM":  self._handle_create_room,
            "JOIN_ROOM":    self._handle_join_room,
            "LIST_ROOMS":   self._handle_list_rooms,
            "LIST_USERS":   self._handle_list_users,
            "INVITE":       self._handle_invite,
            "DISCONNECT":   self._handle_disconnect,
        }
        handler = handlers.get(msg_type)
        if handler:
            handler(username, msg)
        else:
            self.logger.warning("Unknown message type '%s' from '%s'", msg_type, username)
            self._send_to_user(username, {
                "type": "ERROR",
                "message": f"Unknown command: {msg_type}",
            })

    # ────────────────────────────────────────────────────────────────
    #  Command Handlers
    # ────────────────────────────────────────────────────────────────

    def _handle_send_msg(self, username: str, msg: dict) -> None:
        """Broadcast a chat message to all users in the sender's room."""
        text = msg.get("message", "").strip()
        if not text:
            return  # silently ignore empty messages

        with self.lock:
            room = self.user_rooms.get(username)
        if not room:
            return

        broadcast = {
            "type": "CHAT_MSG",
            "username": username,
            "room": room,
            "message": text,
            "timestamp": self._timestamp(),
        }
        self._broadcast_to_room(room, broadcast)
        self.logger.info("[%s] %s: %s", room, username, text)

    def _handle_create_room(self, username: str, msg: dict) -> None:
        """Create a new chat room and auto-join the creator."""
        room_name = msg.get("room", "").strip()
        if not room_name:
            self._send_to_user(username, {
                "type": "ERROR",
                "message": "Room name cannot be empty.",
            })
            return

        with self.lock:
            if room_name in self.rooms:
                self._send_to_user(username, {
                    "type": "ERROR",
                    "message": f"Room '{room_name}' already exists.",
                })
                return

            # Create the room
            self.rooms[room_name] = set()
            self.logger.info("Room '%s' created by '%s'", room_name, username)

        # Notify all connected clients about the new room
        self._broadcast_all({
            "type": "ROOM_CREATED",
            "room": room_name,
            "created_by": username,
            "rooms": list(self.rooms.keys()),
            "timestamp": self._timestamp(),
        })

        # Auto-join the creator into the new room
        self._switch_user_room(username, room_name)

    def _handle_join_room(self, username: str, msg: dict) -> None:
        """Switch a user to a different room."""
        room_name = msg.get("room", "").strip()
        if not room_name:
            self._send_to_user(username, {
                "type": "ERROR",
                "message": "Room name cannot be empty.",
            })
            return

        with self.lock:
            if room_name not in self.rooms:
                self._send_to_user(username, {
                    "type": "ERROR",
                    "message": f"Room '{room_name}' does not exist.",
                })
                return

        self._switch_user_room(username, room_name)

    def _handle_list_rooms(self, username: str, _msg: dict) -> None:
        """Send the current list of all rooms to the requesting user."""
        with self.lock:
            room_list = {name: len(members) for name, members in self.rooms.items()}
        self._send_to_user(username, {
            "type": "ROOM_LIST",
            "rooms": room_list,
        })

    def _handle_list_users(self, username: str, _msg: dict) -> None:
        """Send the list of users in the requester's current room."""
        with self.lock:
            room = self.user_rooms.get(username)
            if room and room in self.rooms:
                user_list = list(self.rooms[room])
            else:
                user_list = []
        self._send_to_user(username, {
            "type": "USER_LIST",
            "room": room,
            "users": user_list,
        })

    def _handle_invite(self, username: str, msg: dict) -> None:
        """Send an invitation to another user to join a room."""
        target_user = msg.get("target_user", "").strip()
        room_name = msg.get("room", "").strip()

        if not target_user or not room_name:
            self._send_to_user(username, {
                "type": "ERROR",
                "message": "Invite requires both target_user and room.",
            })
            return

        with self.lock:
            if target_user not in self.users:
                self._send_to_user(username, {
                    "type": "ERROR",
                    "message": f"User '{target_user}' is not online.",
                })
                return
            if room_name not in self.rooms:
                self._send_to_user(username, {
                    "type": "ERROR",
                    "message": f"Room '{room_name}' does not exist.",
                })
                return

        # Send invitation to the target user
        self._send_to_user(target_user, {
            "type": "INVITE",
            "from_user": username,
            "room": room_name,
            "timestamp": self._timestamp(),
        })
        # Acknowledge to the inviter
        self._send_to_user(username, {
            "type": "INVITE_SENT",
            "target_user": target_user,
            "room": room_name,
        })
        self.logger.info("'%s' invited '%s' to room '%s'", username, target_user, room_name)

    def _handle_disconnect(self, username: str, _msg: dict) -> None:
        """Graceful disconnect requested by the client."""
        self.logger.info("User '%s' requested disconnect.", username)
        # The cleanup will happen in the finally block of _handle_client
        raise ConnectionError("Client requested disconnect")

    # ────────────────────────────────────────────────────────────────
    #  Internal Helpers
    # ────────────────────────────────────────────────────────────────

    def _switch_user_room(self, username: str, new_room: str) -> None:
        """Move a user from their current room to `new_room`."""
        with self.lock:
            old_room = self.user_rooms.get(username)

            # Leave the old room
            if old_room and old_room in self.rooms:
                self.rooms[old_room].discard(username)

            # Enter the new room
            self.rooms[new_room].add(username)
            self.user_rooms[username] = new_room

            users_in_new_room = list(self.rooms[new_room])

        self.logger.info("'%s' switched from '%s' to '%s'", username, old_room, new_room)

        # Notify the old room
        if old_room and old_room != new_room:
            self._broadcast_to_room(old_room, {
                "type": "USER_LEFT",
                "username": username,
                "room": old_room,
                "timestamp": self._timestamp(),
            })

        # Notify the new room
        self._broadcast_to_room(new_room, {
            "type": "USER_JOINED",
            "username": username,
            "room": new_room,
            "timestamp": self._timestamp(),
        }, exclude=username)

        # Confirm to the user
        self._send_to_user(username, {
            "type": "ROOM_JOINED",
            "room": new_room,
            "users_in_room": users_in_new_room,
        })

    def _remove_user(self, username: str) -> None:
        """Remove a user from all data structures and notify others."""
        with self.lock:
            room = self.user_rooms.pop(username, None)
            if room and room in self.rooms:
                self.rooms[room].discard(username)
            self.users.pop(username, None)

        self.logger.info("User '%s' disconnected (was in room '%s').", username, room)

        if room:
            self._broadcast_to_room(room, {
                "type": "USER_LEFT",
                "username": username,
                "room": room,
                "timestamp": self._timestamp(),
            })

    def _send_to_user(self, username: str, msg: dict) -> None:
        """Send a message to a single user by username."""
        with self.lock:
            client = self.users.get(username)
        if client:
            try:
                send_message(client.sock, msg)
            except (ConnectionError, OSError) as exc:
                self.logger.warning("Failed to send to '%s': %s", username, exc)

    def _broadcast_to_room(self, room: str, msg: dict, exclude: str = None) -> None:
        """Send a message to all users in a given room."""
        with self.lock:
            members = list(self.rooms.get(room, set()))
        for member in members:
            if member != exclude:
                self._send_to_user(member, msg)

    def _broadcast_all(self, msg: dict) -> None:
        """Send a message to every connected user."""
        with self.lock:
            all_users = list(self.users.keys())
        for user in all_users:
            self._send_to_user(user, msg)

    @staticmethod
    def _timestamp() -> str:
        """Return the current time as a human-readable string."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ────────────────────────────────────────────────────────────────────
#  Entry Point
# ────────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()

    # Allow command-line overrides
    parser = argparse.ArgumentParser(description="Multi-Room Chat Server")
    parser.add_argument("--host", default=cfg["host"], help="Bind address")
    parser.add_argument("--port", type=int, default=cfg["port"], help="Bind port")
    args = parser.parse_args()

    logger = setup_logging(cfg)

    server = ChatServer(args.host, args.port, cfg)
    server.start()


if __name__ == "__main__":
    main()
