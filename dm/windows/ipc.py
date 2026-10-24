"""Bounded descriptor-free HIPC/CMIF and synchronous SM/set sessions.

This is an explicit protocol subset, not a general Horizon service emulator.
"""
from dataclasses import dataclass
import struct

IPC_SIZE = 0x100
SYNTHETIC_PID = 1
MAX_SESSIONS = 32
INVALID_HANDLE = 0xE401
OUT_OF_HANDLES = 0xD201
OUT_OF_RANGE = 0xEE01
NOT_FOUND = 0xF201
INVALID_CLIENT = 0x415
UNKNOWN_COMMAND = (221 << 9) | 10
LANGUAGES = ("ja", "en-US", "fr", "de", "it", "es", "zh-CN", "ko", "nl",
             "pt", "ru", "zh-TW", "en-GB", "fr-CA", "es-419", "zh-Hans",
             "zh-Hant", "pt-BR")


class IpcError(ValueError):
    """Unsupported or malformed IPC; no invented Horizon fidelity."""


@dataclass(frozen=True)
class Request:
    command: int
    send_pid: bool
    payload: bytes
    raw_start: int
    raw_end: int
    cmif_offset: int


def parse_request(buffer):
    if len(buffer) != IPC_SIZE:
        raise IpcError("ipc_size: expected exactly 0x100 bytes")
    word0, word1 = struct.unpack_from("<II", buffer)
    counts = [(word0 >> shift) & 15 for shift in (16, 20, 24, 28)]
    if word0 & 0xFFFF != 4:
        raise IpcError("unsupported_ipc_type: only CMIF request type 4")
    if word1 & 0x7FFFFC00:
        raise IpcError("unsupported_ipc_fields: receive statics/reserved fields")
    cursor = 8
    send_pid = False
    copy_count = move_count = 0
    if word1 >> 31:
        special, = struct.unpack_from("<I", buffer, cursor)
        if special & ~0x1FF:
            raise IpcError("unsupported_ipc_special: reserved bits")
        send_pid = bool(special & 1)
        copy_count, move_count = (special >> 1) & 15, (special >> 5) & 15
        cursor += 4 + (8 if send_pid else 0) + 4*(copy_count+move_count)
    cursor += counts[0]*8 + sum(counts[1:])*12
    end = cursor + 4*(word1 & 0x3FF)
    if cursor > IPC_SIZE or end > IPC_SIZE:
        raise IpcError("ipc_bounds: descriptors/raw data exceed command buffer")
    if any(counts) or copy_count or move_count:
        raise IpcError("unsupported_ipc_descriptors: no incoming buffers/handles")
    cmif = (cursor+15) & ~15
    if cmif+16 > end:
        raise IpcError("ipc_bounds: CMIF header outside declared raw data")
    magic, version, command, token = struct.unpack_from("<4I", buffer, cmif)
    if magic != 0x49434653:
        raise IpcError("ipc_magic: expected SFCI (domains unsupported)")
    if version or token:
        raise IpcError("unsupported_cmif: version/token must be zero")
    return Request(command, send_pid, bytes(buffer[cmif+16:end]), cursor, end, cmif)


def response(result=0, payload=b"", moved_handle=None):
    """Compact SFCO packet, independent of request alignment allowance."""
    packet = bytearray(IPC_SIZE)
    if moved_handle is None:
        struct.pack_into("<II", packet, 0, 0, 6+len(payload)//4)
    else:
        struct.pack_into("<4I", packet, 0, 0, 0x80000004, 0x20, moved_handle)
    struct.pack_into("<4I", packet, 0x10, 0x4F434653, 0, result, 0)
    packet[0x20:0x20+len(payload)] = payload
    return bytes(packet)


@dataclass
class Session:
    service: str
    registered_pid: int | None = None


class Services:
    def __init__(self, language="en-US", capacity=MAX_SESSIONS):
        if language not in LANGUAGES:
            raise ValueError("unsupported_language: choose a documented language code")
        if not isinstance(capacity, int) or not 1 <= capacity <= MAX_SESSIONS:
            raise ValueError("invalid_session_capacity")
        self.language = language
        self.language_code = int.from_bytes(language.encode("ascii").ljust(8, b"\0"), "little")
        self.capacity = capacity
        self.sessions = {}
        self.next_handle = 0x100
        self.lifecycle = []

    def allocate(self, service):
        if len(self.sessions) >= self.capacity or self.next_handle >= 0x80000000:
            return OUT_OF_HANDLES, None
        handle = self.next_handle
        self.next_handle += 1  # never reuse closed handles or reach pseudo-handles
        self.sessions[handle] = Session(service)
        self.lifecycle.append({"event": "open", "handle": handle, "service": service})
        return 0, handle

    def connect(self, name):
        if name != b"sm:":
            return NOT_FOUND, None
        return self.allocate("sm:")

    def close(self, handle):
        handle &= 0xFFFFFFFF
        session = self.sessions.pop(handle, None)
        if session is None:
            return INVALID_HANDLE
        self.lifecycle.append({"event": "close", "handle": handle, "service": session.service})
        return 0

    def dispatch(self, handle, buffer):
        """Return transport Result, optional response and bounded decoded metadata."""
        handle &= 0xFFFFFFFF
        session = self.sessions.get(handle)
        if session is None:
            return INVALID_HANDLE, None, {"handle": handle}
        request = parse_request(buffer)
        info = {"handle": handle, "service": session.service,
                "command": request.command, "send_pid": request.send_pid,
                "raw_start": request.raw_start, "raw_end": request.raw_end,
                "cmif_offset": request.cmif_offset}
        result, payload, moved = 0, b"", None
        if request.send_pid and not (session.service == "sm:" and request.command == 0):
            raise IpcError("unsupported_pid: only SM RegisterClient accepts PID")
        if session.service == "sm:" and request.command == 0:
            if not request.send_pid or len(request.payload) < 8:
                raise IpcError("ipc_register: PID descriptor and raw u64 required")
            session.registered_pid = SYNTHETIC_PID  # neither guest PID nor placeholder trusted
            info["kernel_pid"] = SYNTHETIC_PID
        elif session.service == "sm:" and request.command == 1:
            if len(request.payload) < 8:
                raise IpcError("ipc_get_service: raw 8-byte service name required")
            if session.registered_pid is None:
                result = INVALID_CLIENT
            elif request.payload[:8] != b"set\0\0\0\0\0":
                raise IpcError("unsupported_service: only set is implemented; no deferred requests")
            else:
                transport, moved = self.allocate("set")
                if transport:
                    return transport, None, info
        elif session.service == "set" and request.command == 0:
            payload = struct.pack("<Q", self.language_code)
            info["language"] = self.language
        else:
            result = UNKNOWN_COMMAND
        packet = response(result, payload, moved)
        info.update(service_result=result, moved_handle=moved)
        return 0, packet, info
