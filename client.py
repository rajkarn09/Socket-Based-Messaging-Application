"""
client.py  –  Tkinter GUI Chat Client
=======================================
A graphical chat client that connects to the ChatServer over TCP using
our custom length-prefixed framing protocol.

Features
--------
    • Connection dialog: enter server host, port, and desired username.
    • Main window with:
        – Left sidebar: room list (click to switch), create-room button.
        – Right sidebar: online users in the current room.
        – Centre: scrollable message log.
        – Bottom: message input field with Send button.
    • Invite dialog to invite another user to the current room.
    • Background receiver thread for real-time message display.
    • Graceful disconnect on window close.

Usage
-----
    python client.py
    python client.py --host 192.168.1.10 --port 6000
"""

import argparse
import json
import os
import socket
import sys
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, scrolledtext, font as tkfont
from datetime import datetime

# ── Import our custom framing protocol ──────────────────────────────
from protocol import send_message, recv_message

# ── Default configuration ──────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config() -> dict:
    """Load client defaults from config.json."""
    defaults = {"host": "127.0.0.1", "port": 5000}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        defaults["host"] = cfg.get("host", defaults["host"])
        defaults["port"] = cfg.get("port", defaults["port"])
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return defaults


# ────────────────────────────────────────────────────────────────────
#  Colour Palette & Styling Constants
# ────────────────────────────────────────────────────────────────────

# A modern dark colour scheme inspired by Discord / Slack
COLORS = {
    "bg_dark":       "#1e1e2e",   # main background
    "bg_sidebar":    "#181825",   # sidebar background
    "bg_input":      "#313244",   # input field background
    "bg_msg":        "#28283a",   # message bubble bg
    "bg_msg_self":   "#3b3d8e",   # own message bubble bg
    "bg_button":     "#7c3aed",   # primary button
    "bg_button_hov": "#6d28d9",   # primary button hover
    "bg_room_sel":   "#45475a",   # selected room highlight
    "fg_main":       "#cdd6f4",   # main text
    "fg_dim":        "#a6adc8",   # dimmed text
    "fg_accent":     "#b4befe",   # accent text (usernames, room names)
    "fg_system":     "#f9e2af",   # system messages
    "fg_error":      "#f38ba8",   # error messages
    "fg_timestamp":  "#6c7086",   # timestamps
    "border":        "#45475a",   # subtle borders
    "fg_invite":     "#a6e3a1",   # invite messages
}

FONT_FAMILY = "Segoe UI"   # Falls back to system default if not found
FONT_SIZE   = 11


# ────────────────────────────────────────────────────────────────────
#  Connection Dialog
# ────────────────────────────────────────────────────────────────────

class ConnectionDialog:
    """
    A modal dialog that asks for host, port, and username before
    connecting to the server.  Returns a connected socket and the
    confirmed username on success, or None on cancel.
    """

    def __init__(self, default_host: str, default_port: int):
        self.result = None  # will be (sock, username, room, rooms, users_in_room)

        # ── Root window ────────────────────────────────────────────
        self.root = tk.Tk()
        self.root.title("Connect to Chat Server")
        self.root.configure(bg=COLORS["bg_dark"])
        self.root.resizable(False, False)

        # Centre the window on screen
        w, h = 420, 380
        sx = (self.root.winfo_screenwidth() - w) // 2
        sy = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"{w}x{h}+{sx}+{sy}")

        # ── Title ──────────────────────────────────────────────────
        title_font = (FONT_FAMILY, 18, "bold")
        tk.Label(
            self.root, text="💬  Chat Connect",
            font=title_font, fg=COLORS["fg_accent"], bg=COLORS["bg_dark"],
        ).pack(pady=(30, 5))

        subtitle_font = (FONT_FAMILY, 10)
        tk.Label(
            self.root, text="Enter server details and choose a username",
            font=subtitle_font, fg=COLORS["fg_dim"], bg=COLORS["bg_dark"],
        ).pack(pady=(0, 20))

        # ── Form fields ───────────────────────────────────────────
        form = tk.Frame(self.root, bg=COLORS["bg_dark"])
        form.pack(padx=40, fill="x")

        label_cfg = {"font": (FONT_FAMILY, FONT_SIZE), "fg": COLORS["fg_main"],
                     "bg": COLORS["bg_dark"], "anchor": "w"}
        entry_cfg = {"font": (FONT_FAMILY, FONT_SIZE), "fg": COLORS["fg_main"],
                     "bg": COLORS["bg_input"], "insertbackground": COLORS["fg_main"],
                     "relief": "flat", "highlightthickness": 1,
                     "highlightcolor": COLORS["fg_accent"],
                     "highlightbackground": COLORS["border"]}

        tk.Label(form, text="Server Host", **label_cfg).pack(fill="x")
        self.host_entry = tk.Entry(form, **entry_cfg)
        self.host_entry.insert(0, default_host)
        self.host_entry.pack(fill="x", ipady=4, pady=(2, 10))

        tk.Label(form, text="Port", **label_cfg).pack(fill="x")
        self.port_entry = tk.Entry(form, **entry_cfg)
        self.port_entry.insert(0, str(default_port))
        self.port_entry.pack(fill="x", ipady=4, pady=(2, 10))

        tk.Label(form, text="Username", **label_cfg).pack(fill="x")
        self.user_entry = tk.Entry(form, **entry_cfg)
        self.user_entry.pack(fill="x", ipady=4, pady=(2, 20))
        self.user_entry.focus_set()

        # ── Connect button ─────────────────────────────────────────
        self.connect_btn = tk.Button(
            form, text="Connect", font=(FONT_FAMILY, 12, "bold"),
            fg="#ffffff", bg=COLORS["bg_button"], activebackground=COLORS["bg_button_hov"],
            relief="flat", cursor="hand2", command=self._on_connect,
        )
        self.connect_btn.pack(fill="x", ipady=6)

        # ── Status label ───────────────────────────────────────────
        self.status_label = tk.Label(
            self.root, text="", font=(FONT_FAMILY, 9),
            fg=COLORS["fg_error"], bg=COLORS["bg_dark"],
        )
        self.status_label.pack(pady=(8, 0))

        # ── Keyboard binding ──────────────────────────────────────
        self.root.bind("<Return>", lambda _: self._on_connect())

    def run(self):
        """Show the dialog and block until it closes."""
        self.root.mainloop()
        return self.result

    def _on_connect(self) -> None:
        """Validate inputs, connect to the server, send CONNECT, wait for reply."""
        host = self.host_entry.get().strip()
        port_str = self.port_entry.get().strip()
        username = self.user_entry.get().strip()

        if not host or not port_str or not username:
            self.status_label.config(text="All fields are required.")
            return
        try:
            port = int(port_str)
        except ValueError:
            self.status_label.config(text="Port must be a number.")
            return

        self.status_label.config(text="Connecting...", fg=COLORS["fg_dim"])
        self.root.update()

        # ── TCP connection ─────────────────────────────────────────
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((host, port))
            sock.settimeout(None)
        except (socket.error, OSError) as exc:
            self.status_label.config(
                text=f"Connection failed: {exc}", fg=COLORS["fg_error"],
            )
            return

        # ── Send CONNECT ───────────────────────────────────────────
        try:
            send_message(sock, {"type": "CONNECT", "username": username})
            reply = recv_message(sock)
        except Exception as exc:
            self.status_label.config(
                text=f"Protocol error: {exc}", fg=COLORS["fg_error"],
            )
            sock.close()
            return

        if reply is None:
            self.status_label.config(
                text="Server closed the connection.", fg=COLORS["fg_error"],
            )
            sock.close()
            return

        if reply.get("type") == "ERROR":
            self.status_label.config(
                text=reply.get("message", "Unknown error"), fg=COLORS["fg_error"],
            )
            sock.close()
            return

        if reply.get("type") == "CONNECT_OK":
            self.result = (
                sock,
                reply["username"],
                reply["room"],
                reply.get("rooms", []),
                reply.get("users_in_room", []),
            )
            self.root.destroy()
        else:
            self.status_label.config(
                text=f"Unexpected reply: {reply.get('type')}",
                fg=COLORS["fg_error"],
            )
            sock.close()


# ────────────────────────────────────────────────────────────────────
#  Main Chat Window
# ────────────────────────────────────────────────────────────────────

class ChatWindow:
    """
    The main chat GUI.  Receives a connected socket and initial state
    from the ConnectionDialog.
    """

    def __init__(self, sock: socket.socket, username: str,
                 current_room: str, rooms: list, users_in_room: list):
        self.sock = sock
        self.username = username
        self.current_room = current_room
        self.rooms = list(rooms)
        self.users_in_room = list(users_in_room)
        self.running = True

        # ── Root window ────────────────────────────────────────────
        self.root = tk.Tk()
        self.root.title(f"ChatApp  –  {username}")
        self.root.configure(bg=COLORS["bg_dark"])
        self.root.minsize(900, 600)

        # Centre on screen
        w, h = 1000, 650
        sx = (self.root.winfo_screenwidth() - w) // 2
        sy = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"{w}x{h}+{sx}+{sy}")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._refresh_room_sidebar()
        self._refresh_user_sidebar()
        self._append_system(f"Connected as '{username}' in room #{current_room}")

        # ── Start background receiver ─────────────────────────────
        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.recv_thread.start()

    # ────────────────────────────────────────────────────────────────
    #  UI Construction
    # ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Construct the main window layout."""
        main_font = (FONT_FAMILY, FONT_SIZE)
        bold_font = (FONT_FAMILY, FONT_SIZE, "bold")
        small_font = (FONT_FAMILY, 9)

        # ── Top bar ────────────────────────────────────────────────
        topbar = tk.Frame(self.root, bg=COLORS["bg_sidebar"], height=48)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)

        self.room_title_label = tk.Label(
            topbar, text=f"# {self.current_room}",
            font=(FONT_FAMILY, 14, "bold"),
            fg=COLORS["fg_accent"], bg=COLORS["bg_sidebar"],
        )
        self.room_title_label.pack(side="left", padx=16)

        self.status_label = tk.Label(
            topbar, text=f"Logged in as {self.username}",
            font=small_font, fg=COLORS["fg_dim"], bg=COLORS["bg_sidebar"],
        )
        self.status_label.pack(side="right", padx=16)

        # ── Body (three columns) ──────────────────────────────────
        body = tk.Frame(self.root, bg=COLORS["bg_dark"])
        body.pack(fill="both", expand=True)

        # ── LEFT SIDEBAR: Rooms ────────────────────────────────────
        left = tk.Frame(body, bg=COLORS["bg_sidebar"], width=200)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(
            left, text="ROOMS", font=(FONT_FAMILY, 10, "bold"),
            fg=COLORS["fg_dim"], bg=COLORS["bg_sidebar"],
        ).pack(anchor="w", padx=12, pady=(14, 6))

        self.room_listbox = tk.Listbox(
            left, font=main_font, fg=COLORS["fg_main"],
            bg=COLORS["bg_sidebar"], selectbackground=COLORS["bg_room_sel"],
            selectforeground=COLORS["fg_accent"],
            relief="flat", highlightthickness=0, borderwidth=0,
            activestyle="none", cursor="hand2",
        )
        self.room_listbox.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.room_listbox.bind("<<ListboxSelect>>", self._on_room_select)

        # Buttons under the room list
        btn_frame = tk.Frame(left, bg=COLORS["bg_sidebar"])
        btn_frame.pack(fill="x", padx=8, pady=(0, 10))

        btn_style = {
            "font": (FONT_FAMILY, 9, "bold"),
            "fg": "#ffffff", "relief": "flat", "cursor": "hand2",
            "activebackground": COLORS["bg_button_hov"],
        }
        tk.Button(
            btn_frame, text="+ New Room", bg=COLORS["bg_button"],
            command=self._on_create_room, **btn_style,
        ).pack(fill="x", ipady=3, pady=(0, 4))

        invite_style = {**btn_style, "activebackground": "#1b4332"}
        tk.Button(
            btn_frame, text="📨 Invite User", bg="#2d6a4f",
            command=self._on_invite, **invite_style,
        ).pack(fill="x", ipady=3)

        # ── CENTRE: Message area ──────────────────────────────────
        centre = tk.Frame(body, bg=COLORS["bg_dark"])
        centre.pack(side="left", fill="both", expand=True)

        # Message display (read-only scrolled text)
        self.msg_display = scrolledtext.ScrolledText(
            centre, font=main_font, fg=COLORS["fg_main"],
            bg=COLORS["bg_dark"], relief="flat", state="disabled",
            wrap="word", highlightthickness=0, borderwidth=0,
            padx=12, pady=8, cursor="arrow",
        )
        self.msg_display.pack(fill="both", expand=True)

        # Configure text tags for coloured messages
        self.msg_display.tag_configure("system",    foreground=COLORS["fg_system"])
        self.msg_display.tag_configure("error",     foreground=COLORS["fg_error"])
        self.msg_display.tag_configure("timestamp",  foreground=COLORS["fg_timestamp"])
        self.msg_display.tag_configure("username",   foreground=COLORS["fg_accent"],
                                       font=(FONT_FAMILY, FONT_SIZE, "bold"))
        self.msg_display.tag_configure("self_name",  foreground="#a6e3a1",
                                       font=(FONT_FAMILY, FONT_SIZE, "bold"))
        self.msg_display.tag_configure("invite",     foreground=COLORS["fg_invite"])

        # ── Input bar ──────────────────────────────────────────────
        input_frame = tk.Frame(centre, bg=COLORS["bg_input"])
        input_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.msg_entry = tk.Entry(
            input_frame, font=main_font, fg=COLORS["fg_main"],
            bg=COLORS["bg_input"], insertbackground=COLORS["fg_main"],
            relief="flat", highlightthickness=0, borderwidth=0,
        )
        self.msg_entry.pack(side="left", fill="both", expand=True, padx=(10, 4), ipady=8)
        self.msg_entry.bind("<Return>", lambda _: self._on_send())
        self.msg_entry.focus_set()

        self.send_btn = tk.Button(
            input_frame, text="Send ➤", font=(FONT_FAMILY, 10, "bold"),
            fg="#ffffff", bg=COLORS["bg_button"],
            activebackground=COLORS["bg_button_hov"],
            relief="flat", cursor="hand2", command=self._on_send,
        )
        self.send_btn.pack(side="right", padx=(0, 6), pady=4, ipady=2, ipadx=8)

        # ── RIGHT SIDEBAR: Users ──────────────────────────────────
        right = tk.Frame(body, bg=COLORS["bg_sidebar"], width=180)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        tk.Label(
            right, text="ONLINE", font=(FONT_FAMILY, 10, "bold"),
            fg=COLORS["fg_dim"], bg=COLORS["bg_sidebar"],
        ).pack(anchor="w", padx=12, pady=(14, 6))

        self.user_listbox = tk.Listbox(
            right, font=main_font, fg=COLORS["fg_accent"],
            bg=COLORS["bg_sidebar"], highlightthickness=0,
            relief="flat", borderwidth=0, activestyle="none",
        )
        self.user_listbox.pack(fill="both", expand=True, padx=8)

    # ────────────────────────────────────────────────────────────────
    #  Sidebar Refresh
    # ────────────────────────────────────────────────────────────────

    def _refresh_room_sidebar(self) -> None:
        """Re-populate the room listbox."""
        self.room_listbox.delete(0, tk.END)
        for room in self.rooms:
            prefix = "▸ " if room == self.current_room else "  "
            self.room_listbox.insert(tk.END, f"{prefix}# {room}")
        # Highlight current room
        try:
            idx = self.rooms.index(self.current_room)
            self.room_listbox.selection_set(idx)
        except ValueError:
            pass

    def _refresh_user_sidebar(self) -> None:
        """Re-populate the online-users listbox."""
        self.user_listbox.delete(0, tk.END)
        for user in sorted(self.users_in_room):
            marker = " (you)" if user == self.username else ""
            self.user_listbox.insert(tk.END, f"  ● {user}{marker}")

    # ────────────────────────────────────────────────────────────────
    #  Message Display Helpers
    # ────────────────────────────────────────────────────────────────

    def _append_text(self, text: str, *tags) -> None:
        """Append styled text to the message display."""
        self.msg_display.config(state="normal")
        self.msg_display.insert(tk.END, text, tags)
        self.msg_display.config(state="disabled")
        self.msg_display.see(tk.END)

    def _append_system(self, text: str) -> None:
        """Append a system notification line."""
        ts = datetime.now().strftime("%H:%M")
        self._append_text(f"  [{ts}] ", "timestamp")
        self._append_text(f"⚙  {text}\n", "system")

    def _append_error(self, text: str) -> None:
        """Append an error line."""
        ts = datetime.now().strftime("%H:%M")
        self._append_text(f"  [{ts}] ", "timestamp")
        self._append_text(f"✖  {text}\n", "error")

    def _append_chat(self, username: str, message: str, timestamp: str = None) -> None:
        """Append a chat message with formatted username and text."""
        ts = timestamp if timestamp else datetime.now().strftime("%H:%M:%S")
        # Extract just time portion if full datetime provided
        if len(ts) > 8:
            ts = ts.split(" ")[-1] if " " in ts else ts
        self._append_text(f"  [{ts}] ", "timestamp")
        tag = "self_name" if username == self.username else "username"
        self._append_text(f"{username}: ", tag)
        self._append_text(f"{message}\n")

    def _append_invite(self, text: str) -> None:
        """Append an invite notification line."""
        ts = datetime.now().strftime("%H:%M")
        self._append_text(f"  [{ts}] ", "timestamp")
        self._append_text(f"📨  {text}\n", "invite")

    # ────────────────────────────────────────────────────────────────
    #  User Actions
    # ────────────────────────────────────────────────────────────────

    def _on_send(self) -> None:
        """Send the text in the input field as a SEND_MSG."""
        text = self.msg_entry.get().strip()
        if not text:
            return
        self.msg_entry.delete(0, tk.END)
        try:
            send_message(self.sock, {"type": "SEND_MSG", "message": text})
        except (ConnectionError, OSError):
            self._append_error("Failed to send – connection lost.")

    def _on_room_select(self, _event) -> None:
        """User clicked a room in the sidebar → send JOIN_ROOM."""
        sel = self.room_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.rooms):
            room_name = self.rooms[idx]
            if room_name == self.current_room:
                return  # already in this room
            try:
                send_message(self.sock, {"type": "JOIN_ROOM", "room": room_name})
            except (ConnectionError, OSError):
                self._append_error("Failed to switch room – connection lost.")

    def _on_create_room(self) -> None:
        """Prompt for a room name and send CREATE_ROOM."""
        room_name = simpledialog.askstring(
            "Create Room", "Enter new room name:",
            parent=self.root,
        )
        if room_name and room_name.strip():
            try:
                send_message(self.sock, {
                    "type": "CREATE_ROOM",
                    "room": room_name.strip(),
                })
            except (ConnectionError, OSError):
                self._append_error("Failed to create room – connection lost.")

    def _on_invite(self) -> None:
        """Prompt for a username and send INVITE to the current room."""
        target = simpledialog.askstring(
            "Invite User",
            f"Invite user to #{self.current_room}.\nEnter username:",
            parent=self.root,
        )
        if target and target.strip():
            try:
                send_message(self.sock, {
                    "type": "INVITE",
                    "target_user": target.strip(),
                    "room": self.current_room,
                })
            except (ConnectionError, OSError):
                self._append_error("Failed to invite – connection lost.")

    def _on_close(self) -> None:
        """Handle window close: send DISCONNECT and shut down."""
        self.running = False
        try:
            send_message(self.sock, {"type": "DISCONNECT"})
        except (ConnectionError, OSError):
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.root.destroy()

    # ────────────────────────────────────────────────────────────────
    #  Background Receiver
    # ────────────────────────────────────────────────────────────────

    def _recv_loop(self) -> None:
        """
        Continuously receive messages from the server in a background
        thread.  Updates to the GUI are scheduled via root.after() so
        they execute on the main (Tk) thread.
        """
        while self.running:
            try:
                msg = recv_message(self.sock)
            except (ConnectionError, OSError, ValueError):
                if self.running:
                    self.root.after(0, lambda: self._append_error(
                        "Connection to server lost."
                    ))
                break

            if msg is None:
                if self.running:
                    self.root.after(0, lambda: self._append_error(
                        "Server closed the connection."
                    ))
                break

            # Schedule GUI update on the main thread
            self.root.after(0, self._handle_server_msg, msg)

    def _handle_server_msg(self, msg: dict) -> None:
        """Process an incoming server message (called on the Tk thread)."""
        msg_type = msg.get("type", "")

        if msg_type == "CHAT_MSG":
            self._append_chat(
                msg.get("username", "?"),
                msg.get("message", ""),
                msg.get("timestamp"),
            )

        elif msg_type == "USER_JOINED":
            user = msg.get("username", "?")
            room = msg.get("room", "?")
            self._append_system(f"{user} joined #{room}")
            # Refresh user list if it's our current room
            if room == self.current_room:
                if user not in self.users_in_room:
                    self.users_in_room.append(user)
                self._refresh_user_sidebar()

        elif msg_type == "USER_LEFT":
            user = msg.get("username", "?")
            room = msg.get("room", "?")
            self._append_system(f"{user} left #{room}")
            if room == self.current_room and user in self.users_in_room:
                self.users_in_room.remove(user)
                self._refresh_user_sidebar()

        elif msg_type == "ROOM_JOINED":
            # We successfully switched to a new room
            room = msg.get("room", self.current_room)
            self.current_room = room
            self.users_in_room = msg.get("users_in_room", [])
            self.room_title_label.config(text=f"# {room}")
            self._refresh_room_sidebar()
            self._refresh_user_sidebar()
            self._append_system(f"You are now in #{room}")

        elif msg_type == "ROOM_CREATED":
            room = msg.get("room", "?")
            rooms = msg.get("rooms", [])
            if rooms:
                self.rooms = rooms
            elif room not in self.rooms:
                self.rooms.append(room)
            self._refresh_room_sidebar()
            self._append_system(
                f"New room #{room} created by {msg.get('created_by', '?')}"
            )

        elif msg_type == "ROOM_LIST":
            room_dict = msg.get("rooms", {})
            self.rooms = list(room_dict.keys())
            self._refresh_room_sidebar()

        elif msg_type == "USER_LIST":
            self.users_in_room = msg.get("users", [])
            self._refresh_user_sidebar()

        elif msg_type == "INVITE":
            from_user = msg.get("from_user", "?")
            room = msg.get("room", "?")
            self._append_invite(
                f"{from_user} invited you to #{room}!  "
                f"Click the room in the sidebar to join."
            )
            # Make sure the room is visible in sidebar
            if room not in self.rooms:
                self.rooms.append(room)
                self._refresh_room_sidebar()

        elif msg_type == "INVITE_SENT":
            target = msg.get("target_user", "?")
            room = msg.get("room", "?")
            self._append_system(f"Invitation sent to {target} for #{room}")

        elif msg_type == "ERROR":
            self._append_error(msg.get("message", "Unknown error"))

        else:
            self._append_system(f"[{msg_type}] {msg}")

    # ────────────────────────────────────────────────────────────────
    #  Run
    # ────────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Enter the Tk main loop."""
        self.root.mainloop()


# ────────────────────────────────────────────────────────────────────
#  Entry Point
# ────────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(description="Chat Client (Tkinter GUI)")
    parser.add_argument("--host", default=cfg["host"], help="Server host")
    parser.add_argument("--port", type=int, default=cfg["port"], help="Server port")
    args = parser.parse_args()

    # ── Show connection dialog ─────────────────────────────────────
    dialog = ConnectionDialog(args.host, args.port)
    result = dialog.run()

    if result is None:
        print("Connection cancelled.")
        sys.exit(0)

    sock, username, room, rooms, users_in_room = result

    # ── Launch the main chat window ────────────────────────────────
    app = ChatWindow(sock, username, room, rooms, users_in_room)
    app.run()


if __name__ == "__main__":
    main()
