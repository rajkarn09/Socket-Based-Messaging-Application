# ChatApp

A modern, multi-room chat application built from scratch in Python using low-level socket programming (`socket`) and concurrent connection handling (`threading`). 

This project demonstrates advanced networking concepts—including custom wire-framing to handle TCP stream boundaries—wrapped in a clean, dark-themed Tkinter Graphical User Interface.

---

## Features

- **Real-Time Messaging**: Instant text transmission using raw TCP sockets.
- **Multi-Room Support**:
  - Starts with default rooms (`#General` and `#Random`).
  - Create new rooms dynamically.
  - Switch between rooms via a visual sidebar.
- **Invitation System**: Invite online users to your current chat room.
- **Concurrent Connections**: Thread-per-client server architecture supporting 3+ concurrent users.
- **Unique Usernames**: Real-time username verification on connection.
- **Modern Dark GUI**: Built using Python's native `tkinter` with a clean dark theme.
- **Custom Framing Protocol**: A `[4-byte big-endian length prefix] + [JSON payload]` framing scheme that solves the TCP "sticky packet" (concatenation) and fragmentation issues.
- **Robust Error Handling & Logging**: Fully integrated with Python's standard `logging` library. Writes debug logs to `server.log`.

---

## Quick Start Guide

### 1. Prerequisites
- **Python 3.10+**
- No external packages are required (uses Python standard libraries only).
- *Linux users:* If Tkinter is not installed on your system, install it via:
  ```bash
  sudo apt install python3-tk
  ```

### 2. Start the Server
Navigate to the project directory and run:
```bash
python server.py
```
*The server will bind to `127.0.0.1:5000` by default (defined in `config.json`).*

### 3. Start the Clients
Open separate terminal windows for each user and run:
```bash
python client.py
```
A connection dialog will appear. Enter:
1. **Server Host**: `127.0.0.1` (or the IP of the host machine if running over a network).
2. **Port**: `5000` (or your configured port).
3. **Username**: A unique display name (e.g., Alice, Bob, Charlie).

Click **Connect** to enter the chat.

---

## Project Structure

```
ChatApp/
├── protocol.py     # Custom TCP framing library (shared by server & client)
├── server.py       # Multi-threaded TCP chat server
├── client.py       # Tkinter GUI chat client
├── config.json     # Configuration parameters (default rooms, host, port, logs)
└── README.md       # Full documentation and quick start guide
```

---

## Configuration File (`config.json`)

Edit `config.json` to change default behaviors:
```json
{
    "host": "127.0.0.1",
    "port": 5000,
    "max_connections": 50,
    "log_level": "INFO",
    "log_file": "server.log",
    "default_rooms": ["General", "Random"]
}
```

---

## System Architecture Design

### 1. Architecture Overview
This application follows a **Client-Server Architecture** where a single central server manages all connections, chat rooms, and message routing. Multiple clients connect to the server over TCP.

```
┌──────────────┐     TCP      ┌──────────────────────────┐     TCP      ┌──────────────┐
│   Client 1   │◄────────────►│                          │◄────────────►│   Client 2   │
│  (Tkinter)   │              │     Central Server       │              │  (Tkinter)   │
└──────────────┘              │                          │              └──────────────┘
                              │  • User Management       │
┌──────────────┐     TCP      │  • Room Management       │     TCP      ┌──────────────┐
│   Client 3   │◄────────────►│  • Message Routing       │◄────────────►│   Client N   │
│  (Tkinter)   │              │  • Invite System         │              │  (Tkinter)   │
└──────────────┘              └──────────────────────────┘              └──────────────┘
```

### 2. Server Architecture (Thread-per-Client Model)
```
┌────────────────────────────────────────────────────────────────┐
│                       CHAT SERVER                              │
│                                                                │
│  ┌──────────────┐                                              │
│  │ Main Thread   │  socket.accept() loop                       │
│  │ (Acceptor)    │──────────────────────┐                      │
│  └──────────────┘                       │                      │
│                                         ▼                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Client       │  │ Client       │  │ Client       │         │
│  │ Thread #1    │  │ Thread #2    │  │ Thread #3    │  ...    │
│  │ (recv loop)  │  │ (recv loop)  │  │ (recv loop)  │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         │                 │                 │                  │
│         ▼                 ▼                 ▼                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Shared State (Thread-Safe via Lock)         │   │
│  │                                                         │   │
│  │   users:      { "Alice": ClientInfo, "Bob": ClientInfo }│   │
│  │   rooms:      { "General": {"Alice","Bob"}, ... }       │   │
│  │   user_rooms: { "Alice": "General", "Bob": "Random" }  │   │
│  └─────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

### 3. Client Architecture
```
┌───────────────────────────────────────────────────┐
│                  CHAT CLIENT                       │
│                                                   │
│  ┌─────────────┐       ┌────────────────────────┐ │
│  │  Main Thread │       │  Receiver Thread       │ │
│  │  (Tk GUI)    │◄─────│  (Background)          │ │
│  │              │ after()  recv_message() loop   │ │
│  │  • Sidebar   │       │                        │ │
│  │  • Messages  │       │  Pushes msgs to GUI    │ │
│  │  • Input     │       │  via root.after()      │ │
│  └──────┬──────┘       └────────────────────────┘ │
│         │ send_message()                           │
│         ▼                                          │
│     TCP Socket ◄──────────────────► Server         │
└───────────────────────────────────────────────────┘
```

---

## Detailed Protocol Specifications

### 1. Wire Format
Every message sent over TCP uses a **length-prefixed framing** scheme:

```
┌──────────────────────┬──────────────────────────────────────────┐
│  Header (4 bytes)    │         Payload (N bytes)                │
│  Big-endian uint32   │         UTF-8 encoded JSON               │
│  Value = N           │                                          │
└──────────────────────┴──────────────────────────────────────────┘

Example:  Message = {"type": "SEND_MSG", "message": "Hello"}

Bytes on the wire:
  [0x00][0x00][0x00][0x2C]  ← header: payload is 44 bytes
  {"type": "SEND_MSG", "message": "Hello"}  ← JSON payload (44 bytes)
```

### 2. Message Types (Client → Server)
- `CONNECT` (`username`): Register with the server.
- `SEND_MSG` (`message`): Send text to the current room.
- `CREATE_ROOM` (`room`): Create a new chat room.
- `JOIN_ROOM` (`room`): Switch to a different room.
- `LIST_ROOMS`: Request the list of all rooms.
- `LIST_USERS`: Request users in the current room.
- `INVITE` (`target_user`, `room`): Invite a user to a room.
- `DISCONNECT`: Graceful disconnect request.

### 3. Message Types (Server → Client)
- `CONNECT_OK` (`username`, `room`, `rooms`, `users_in_room`): Successful registration.
- `CHAT_MSG` (`username`, `room`, `message`, `timestamp`): Chat message from a user.
- `USER_JOINED` (`username`, `room`, `timestamp`): Someone joined the room.
- `USER_LEFT` (`username`, `room`, `timestamp`): Someone left the room.
- `ROOM_JOINED` (`room`, `users_in_room`): Confirmation of room switch.
- `ROOM_CREATED` (`room`, `created_by`, `rooms`, `timestamp`): A new room was created.
- `ROOM_LIST` (`rooms` dict): Response containing rooms and member counts.
- `USER_LIST` (`room`, `users` list): Response containing users in the room.
- `INVITE` (`from_user`, `room`, `timestamp`): Received an invitation.
- `INVITE_SENT` (`target_user`, `room`): Confirmation invite was sent.
- `ERROR` (`message`): Error notification.

---

## Network Communication Flow

### 1. Connection & Registration Flow
```
    Client                                Server
      │                                      │
      │──── TCP SYN ─────────────────────►   │
      │◄─── TCP SYN-ACK ────────────────── │
      │──── TCP ACK ─────────────────────►   │
      │          (TCP 3-way handshake)       │
      │                                      │
      │──── CONNECT {username:"Alice"} ──►   │
      │                                      │ (Check username uniqueness)
      │                                      │ (Add to users dict & default room)
      │◄── CONNECT_OK {room, rooms, ...} ── │
      │                                      │
      │                                      │──► Broadcast USER_JOINED
      │                                      │    to other room members
```

### 2. Message Sending Flow
```
    Alice (Client)           Server              Bob (Client)
        │                      │                      │
        │── SEND_MSG ─────►    │                      │
        │  {"message":"Hi"}    │                      │
        │                      │ (Lookup Alice's      │
        │                      │  current room)       │
        │                      │                      │
        │◄── CHAT_MSG ─────── │ ── CHAT_MSG ────────► │
        │  {from:"Alice",      │  {from:"Alice",      │
        │   msg:"Hi"}          │   msg:"Hi"}          │
```

---

## Protocol Analysis

### 1. Protocol Selection Rationale
- **TCP over UDP**: TCP is chosen because chat applications require reliable, ordered delivery of messages. If segment drops or sequence reordering occurs (common in UDP), it results in corrupt, missed, or scrambled messages. TCP's connection management also provides simple notification of client drops.
- **Length-Prefixed Framing**: TCP is a stream protocol and has no concept of packet boundaries. "Sticky packets" (merging multiple writes) and fragmentation (splitting writes) are common. The 4-byte header tells the socket receiver exactly how many bytes to read, preventing stream corruption.
- **JSON Serialization**: Using JSON provides a human-readable, self-describing layout that makes debugging network transactions straightforward while keeping the packet structures clean and easily expandable.
- **Threading Model**: Utilizing Python's native `threading` module to allocate one thread per connection is robust and simple. Thread-safety is strictly maintained on the server via `threading.Lock()` wrappers surrounding all shared structures.
