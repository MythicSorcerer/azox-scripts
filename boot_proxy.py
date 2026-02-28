#!/usr/bin/env python3
import argparse
import json
import socket
import threading
import time


def read_varint(sock):
    result = 0
    shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("eof")
        byte = b[0]
        result |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return result
        shift += 7
        if shift > 35:
            raise ValueError("varint too large")


def write_varint(value):
    out = bytearray()
    while True:
        part = value & 0x7F
        value >>= 7
        if value:
            out.append(part | 0x80)
        else:
            out.append(part)
            break
    return bytes(out)


def read_exact(sock, n):
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("eof")
        data.extend(chunk)
    return bytes(data)


def write_packet(sock, packet_id, payload):
    body = write_varint(packet_id) + payload
    sock.sendall(write_varint(len(body)) + body)


def read_string(payload, offset):
    length, consumed = read_varint_from_bytes(payload, offset)
    offset = consumed
    raw = payload[offset : offset + length]
    return raw.decode("utf-8", errors="replace"), offset + length


def read_varint_from_bytes(data, offset):
    result = 0
    shift = 0
    pos = offset
    while True:
        if pos >= len(data):
            raise ValueError("short varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return result, pos
        shift += 7
        if shift > 35:
            raise ValueError("varint too large")


def read_ushort(payload, offset):
    if offset + 2 > len(payload):
        raise ValueError("short ushort")
    return int.from_bytes(payload[offset : offset + 2], "big"), offset + 2


class BootProxy:
    def __init__(self, host, port, motd, timeout):
        self.host = host
        self.port = port
        self.motd = motd
        self.timeout = timeout
        self.stop_event = threading.Event()
        self.server = None

    def run(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.host, self.port))
        self.server.listen(64)
        self.server.settimeout(1.0)
        end_time = time.time() + self.timeout
        while not self.stop_event.is_set() and time.time() < end_time:
            try:
                client, _ = self.server.accept()
            except socket.timeout:
                continue
            threading.Thread(target=self.handle_client, args=(client,), daemon=True).start()
        self.server.close()

    def stop(self):
        self.stop_event.set()

    def handle_client(self, client):
        with client:
            try:
                packet_len = read_varint(client)
                packet = read_exact(client, packet_len)
                packet_id, off = read_varint_from_bytes(packet, 0)
                if packet_id != 0:
                    return
                _protocol, off = read_varint_from_bytes(packet, off)
                _server_addr, off = read_string(packet, off)
                _server_port, off = read_ushort(packet, off)
                next_state, off = read_varint_from_bytes(packet, off)

                if next_state == 1:
                    self.handle_status(client)
                else:
                    self.handle_login_disconnect(client)
            except Exception:
                return

    def handle_status(self, client):
        packet_len = read_varint(client)
        packet = read_exact(client, packet_len)
        packet_id, _ = read_varint_from_bytes(packet, 0)
        if packet_id != 0:
            return

        status = {
            "version": {"name": "Booting", "protocol": 767},
            "players": {"max": 0, "online": 0, "sample": []},
            "description": {"text": self.motd},
        }
        status_json = json.dumps(status, ensure_ascii=False).encode("utf-8")
        write_packet(client, 0, write_varint(len(status_json)) + status_json)

        try:
            packet_len = read_varint(client)
            packet = read_exact(client, packet_len)
            packet_id, off = read_varint_from_bytes(packet, 0)
            if packet_id == 1:
                payload = packet[off:]
                write_packet(client, 1, payload)
        except Exception:
            return

    def handle_login_disconnect(self, client):
        reason = {"text": self.motd}
        payload = json.dumps(reason, ensure_ascii=False).encode("utf-8")
        write_packet(client, 0, write_varint(len(payload)) + payload)


def main():
    parser = argparse.ArgumentParser(description="Temporary Minecraft status proxy")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--motd", type=str, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    proxy = BootProxy(args.host, args.port, args.motd, args.timeout)
    proxy.run()


if __name__ == "__main__":
    main()
