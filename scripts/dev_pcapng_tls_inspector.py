#!/usr/bin/env python3
"""
scripts/dev_pcapng_tls_inspector.py

DEV-ONLY, MANUAL, LOCAL DIAGNOSTIC. Never imported by any production
code path, never run automatically, never exposed through Discord or
any listener. Pure Python stdlib -- no dpkt/scapy/pyshark, since none
are available and none should be installed just for this one-off
inspection.

Purpose: parse an EXISTING, already-captured PCAPNG file (produced by
Windows `pktmon etl2pcap`, or any standard pcapng writer) entirely
locally, and print ONLY safe TLS handshake metadata -- never payload
bytes beyond what is structurally part of a ClientHello/ServerHello
handshake message, and NEVER anything from an encrypted Application
Data record (TLS content type 0x17), which is skipped and only its
byte length is reported.

What this extracts, from the cleartext portion of the TLS handshake
(ClientHello/ServerHello are NEVER encrypted -- this is true for both
TLS 1.2 and TLS 1.3, since encryption begins only after the
handshake's own key exchange completes):
- TCP connection summary: src/dst IP:port, SYN/ACK/FIN/RST flags, in
  capture order -- to confirm which Chaster IP was actually used and
  that the TCP handshake completed.
- ClientHello: offered TLS version, SNI (server_name extension),
  offered ALPN protocols, offered cipher suites (as hex codes -- a
  fixed, small, built-in lookup table maps the handful of modern,
  common suites to names; anything unrecognized is shown as a hex
  code, never guessed).
- ServerHello: negotiated TLS version, negotiated cipher suite,
  negotiated ALPN protocol (if the extension is present).

What this NEVER does: does not decrypt anything, does not print any
byte from an Application Data record, does not print any byte beyond
a parsed handshake message's own structural fields, does not touch
the network, does not require or accept a token of any kind (it reads
an already-existing local file only).

USAGE (from the repository root):

    python3 scripts/dev_pcapng_tls_inspector.py <path-to-file.pcapng>

Treat both the input pcapng file and this script's own output as a
sensitive artifact -- delete the pcapng file once you are done with
it, and do not paste this script's raw multi-packet output anywhere
without a quick read-through first (this script does not print
Authorization headers or any TLS-encrypted content, but a capture
file is still worth treating cautiously as a matter of general
hygiene).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

# A small, fixed lookup of common modern TLS 1.2/1.3 cipher suite IDs
# (IANA TLS Cipher Suite registry) -- NOT an exhaustive list. Anything
# not in this table is printed as its raw hex code, never guessed.
KNOWN_CIPHER_SUITES = {
    0x1301: "TLS_AES_128_GCM_SHA256",
    0x1302: "TLS_AES_256_GCM_SHA384",
    0x1303: "TLS_CHACHA20_POLY1305_SHA256",
    0xC02B: "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256",
    0xC02C: "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384",
    0xC02F: "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256",
    0xC030: "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384",
    0xCCA8: "TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256",
    0xCCA9: "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256",
    0x009C: "TLS_RSA_WITH_AES_128_GCM_SHA256",
    0x009D: "TLS_RSA_WITH_AES_256_GCM_SHA384",
    0x002F: "TLS_RSA_WITH_AES_128_CBC_SHA",
    0x0035: "TLS_RSA_WITH_AES_256_CBC_SHA",
}

TLS_VERSION_NAMES = {
    0x0301: "TLSv1.0", 0x0302: "TLSv1.1", 0x0303: "TLSv1.2", 0x0304: "TLSv1.3",
}

_TCP_FLAG_BITS = [("FIN", 0x01), ("SYN", 0x02), ("RST", 0x04), ("PSH", 0x08), ("ACK", 0x10), ("URG", 0x20)]


def _cipher_name(code: int) -> str:
    return KNOWN_CIPHER_SUITES.get(code, f"0x{code:04X}")


def _tls_version_name(code: int) -> str:
    return TLS_VERSION_NAMES.get(code, f"0x{code:04X}")


def _tcp_flags_str(flags: int) -> str:
    return "|".join(name for name, bit in _TCP_FLAG_BITS if flags & bit) or "-"


# ---------------------------------------------------------------------------
# PCAPNG block parsing (IETF pcapng format) -- Section Header Block,
# Interface Description Block, Enhanced Packet Block. Any other block
# type is skipped by its own declared length, never interpreted.
# ---------------------------------------------------------------------------

BLOCK_TYPE_SHB = 0x0A0D0D0A
BLOCK_TYPE_IDB = 0x00000001
BLOCK_TYPE_EPB = 0x00000006
LINKTYPE_ETHERNET = 1


def iter_pcapng_packets(data: bytes):
    """Yields the raw captured-packet bytes (link-layer frame, usually
    Ethernet) for every Enhanced Packet Block in the file, in capture
    order. Detects byte order from the Section Header Block's own
    magic number, as the format requires. Skips any block type other
    than SHB/IDB/EPB by its own declared length -- never guesses at
    an unknown block's internal structure."""
    offset = 0
    endian = "<"  # default; corrected immediately once the first SHB is read
    while offset + 8 <= len(data):
        block_type = int.from_bytes(data[offset:offset + 4], "little")
        if block_type == BLOCK_TYPE_SHB:
            byte_order_magic = data[offset + 8:offset + 12]
            if byte_order_magic == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            elif byte_order_magic == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            else:
                raise ValueError("Not a valid pcapng file (bad byte-order magic in Section Header Block).")
            block_len = struct.unpack_from(endian + "I", data, offset + 4)[0]
        else:
            block_len = struct.unpack_from(endian + "I", data, offset + 4)[0]

        if block_len < 12 or offset + block_len > len(data):
            break  # truncated/corrupt trailing block -- stop rather than misread

        if block_type == BLOCK_TYPE_EPB:
            # EPB layout: type(4) totalLen(4) interfaceID(4) tsHigh(4)
            # tsLow(4) capturedLen(4) origLen(4) packetData(capturedLen) ...
            captured_len = struct.unpack_from(endian + "I", data, offset + 20)[0]
            packet_start = offset + 28
            yield data[packet_start:packet_start + captured_len]

        offset += block_len
        if block_len % 4:
            offset += 4 - (block_len % 4)  # blocks are padded to a 4-byte boundary


# ---------------------------------------------------------------------------
# Ethernet / IPv4 / TCP parsing -- just enough to reach the TCP payload
# and print a safe connection summary.
# ---------------------------------------------------------------------------

def parse_ethernet_ipv4_tcp(frame: bytes):
    """Returns (src_ip, src_port, dst_ip, dst_port, tcp_flags, tcp_payload)
    or None if this frame is not an Ethernet/IPv4/TCP packet. Never
    raises on a malformed/short frame -- returns None instead."""
    if len(frame) < 14:
        return None
    ethertype = struct.unpack_from(">H", frame, 12)[0]
    if ethertype != 0x0800:  # not IPv4
        return None
    ip_start = 14
    if len(frame) < ip_start + 20:
        return None
    version_ihl = frame[ip_start]
    if version_ihl >> 4 != 4:
        return None
    ihl = (version_ihl & 0x0F) * 4
    protocol = frame[ip_start + 9]
    if protocol != 6:  # not TCP
        return None
    src_ip = ".".join(str(b) for b in frame[ip_start + 12:ip_start + 16])
    dst_ip = ".".join(str(b) for b in frame[ip_start + 16:ip_start + 20])
    tcp_start = ip_start + ihl
    if len(frame) < tcp_start + 20:
        return None
    src_port, dst_port = struct.unpack_from(">HH", frame, tcp_start)
    data_offset = (frame[tcp_start + 12] >> 4) * 4
    tcp_flags = frame[tcp_start + 13]
    payload_start = tcp_start + data_offset
    payload = frame[payload_start:]
    return src_ip, src_port, dst_ip, dst_port, tcp_flags, payload


# ---------------------------------------------------------------------------
# TLS record / handshake parsing -- ClientHello and ServerHello only.
# Any other TLS content type (in particular 0x17 Application Data,
# which is always encrypted) is recognized and explicitly skipped --
# its bytes are never inspected or printed.
# ---------------------------------------------------------------------------

TLS_CONTENT_HANDSHAKE = 0x16
TLS_CONTENT_APPLICATION_DATA = 0x17
HANDSHAKE_CLIENT_HELLO = 0x01
HANDSHAKE_SERVER_HELLO = 0x02
EXT_SERVER_NAME = 0x0000
EXT_ALPN = 0x0010

# A small, fixed lookup of common TLS extension type numbers -> names
# (IANA TLS ExtensionType registry) -- for LABELING which extensions
# are present only. Never used to interpret/decode extension
# contents beyond the two (SNI, ALPN) this inspector already parses
# structurally. Anything not in this table is shown as its raw
# numeric type, never guessed.
KNOWN_EXTENSION_TYPES = {
    0x0000: "server_name",
    0x0005: "status_request",
    0x000A: "supported_groups",
    0x000D: "signature_algorithms",
    0x0010: "application_layer_protocol_negotiation",
    0x0012: "signed_certificate_timestamp",
    0x0016: "encrypt_then_mac",
    0x0017: "extended_master_secret",
    0x0023: "session_ticket",
    0x002B: "supported_versions",
    0x002D: "psk_key_exchange_modes",
    0x0033: "key_share",
    0x0039: "quic_transport_parameters",
    0xFF01: "renegotiation_info",
}


def _describe_extension_list(extensions: dict[int, bytes]) -> str:
    """Returns a safe, comma-separated list of the extension TYPES
    present -- names where known, numeric type otherwise -- and
    NEVER any extension's own raw byte content. The order shown is
    the order the extensions actually appeared in the handshake
    message, which is itself part of what a ClientHello comparison
    would want to see."""
    names = [KNOWN_EXTENSION_TYPES.get(t, f"0x{t:04X}") for t in extensions]
    return ", ".join(names) if names else "(none)"


def describe_tls_records(payload: bytes) -> list[str]:
    """Walks the TLS records in one TCP segment's payload. Returns a
    list of safe, human-readable description lines. Never returns any
    byte content from an Application Data record -- only its length."""
    lines: list[str] = []
    offset = 0
    while offset + 5 <= len(payload):
        content_type = payload[offset]
        record_version = struct.unpack_from(">H", payload, offset + 1)[0]
        record_len = struct.unpack_from(">H", payload, offset + 3)[0]
        record_body = payload[offset + 5:offset + 5 + record_len]
        if content_type == TLS_CONTENT_APPLICATION_DATA:
            lines.append(f"    TLS record: Application Data (encrypted), {len(record_body)} bytes -- contents not shown.")
        elif content_type == TLS_CONTENT_HANDSHAKE:
            lines.extend(_describe_handshake_messages(record_body))
        elif content_type in (0x14, 0x15):
            lines.append(f"    TLS record: {'ChangeCipherSpec' if content_type == 0x14 else 'Alert'} ({len(record_body)} bytes).")
        else:
            lines.append(f"    TLS record: unknown content type 0x{content_type:02X}, {len(record_body)} bytes.")
        offset += 5 + record_len
        if record_len == 0:
            break
    return lines


def _describe_handshake_messages(body: bytes) -> list[str]:
    lines: list[str] = []
    offset = 0
    while offset + 4 <= len(body):
        msg_type = body[offset]
        msg_len = int.from_bytes(body[offset + 1:offset + 4], "big")
        msg_body = body[offset + 4:offset + 4 + msg_len]
        if msg_type == HANDSHAKE_CLIENT_HELLO:
            lines.extend(_describe_client_hello(msg_body))
        elif msg_type == HANDSHAKE_SERVER_HELLO:
            lines.extend(_describe_server_hello(msg_body))
        else:
            lines.append(f"    Handshake message: type 0x{msg_type:02X}, {len(msg_body)} bytes (not decoded -- only ClientHello/ServerHello are).")
        offset += 4 + msg_len
        if msg_len == 0:
            break
    return lines


def _parse_extensions(data: bytes) -> dict[int, bytes]:
    extensions: dict[int, bytes] = {}
    offset = 0
    while offset + 4 <= len(data):
        ext_type = struct.unpack_from(">H", data, offset)[0]
        ext_len = struct.unpack_from(">H", data, offset + 2)[0]
        extensions[ext_type] = data[offset + 4:offset + 4 + ext_len]
        offset += 4 + ext_len
    return extensions


def _describe_client_hello(body: bytes) -> list[str]:
    if len(body) < 34:
        return ["    ClientHello: too short to parse safely -- skipped."]
    lines = ["    ClientHello:"]
    client_version = struct.unpack_from(">H", body, 0)[0]
    lines.append(f"      offered version: {_tls_version_name(client_version)}")
    offset = 34  # 2 (version) + 32 (random)
    session_id_len = body[offset]
    offset += 1 + session_id_len
    if offset + 2 > len(body):
        return lines
    cipher_suites_len = struct.unpack_from(">H", body, offset)[0]
    offset += 2
    cipher_codes = [struct.unpack_from(">H", body, offset + i)[0] for i in range(0, cipher_suites_len, 2)]
    lines.append(f"      offered cipher suites ({len(cipher_codes)}): {', '.join(_cipher_name(c) for c in cipher_codes)}")
    offset += cipher_suites_len
    if offset >= len(body):
        return lines
    compression_len = body[offset]
    offset += 1 + compression_len
    if offset + 2 > len(body):
        return lines
    extensions_len = struct.unpack_from(">H", body, offset)[0]
    offset += 2
    extensions = _parse_extensions(body[offset:offset + extensions_len])
    lines.append(f"      extension types present ({len(extensions)}), in order: {_describe_extension_list(extensions)}")
    if EXT_SERVER_NAME in extensions:
        sni_block = extensions[EXT_SERVER_NAME]
        # server_name_list: 2-byte list len, then [1-byte type][2-byte len][name]
        if len(sni_block) >= 5:
            name_len = struct.unpack_from(">H", sni_block, 3)[0]
            name = sni_block[5:5 + name_len].decode("ascii", errors="replace")
            lines.append(f"      SNI: {name}")
    if EXT_ALPN in extensions:
        alpn_block = extensions[EXT_ALPN]
        protos = []
        p = 2  # skip the 2-byte ALPN protocol list length
        while p < len(alpn_block):
            plen = alpn_block[p]
            protos.append(alpn_block[p + 1:p + 1 + plen].decode("ascii", errors="replace"))
            p += 1 + plen
        lines.append(f"      offered ALPN protocols: {protos}")
    else:
        lines.append("      offered ALPN protocols: (extension not present)")
    return lines


def _describe_server_hello(body: bytes) -> list[str]:
    if len(body) < 34:
        return ["    ServerHello: too short to parse safely -- skipped."]
    lines = ["    ServerHello:"]
    server_version = struct.unpack_from(">H", body, 0)[0]
    lines.append(f"      negotiated version (legacy field): {_tls_version_name(server_version)}")
    offset = 34
    session_id_len = body[offset]
    offset += 1 + session_id_len
    if offset + 2 > len(body):
        return lines
    cipher_code = struct.unpack_from(">H", body, offset)[0]
    lines.append(f"      negotiated cipher suite: {_cipher_name(cipher_code)}")
    offset += 2 + 1  # cipher(2) + compression method(1)
    if offset + 2 > len(body):
        return lines
    extensions_len = struct.unpack_from(">H", body, offset)[0]
    offset += 2
    extensions = _parse_extensions(body[offset:offset + extensions_len])
    lines.append(f"      extension types present ({len(extensions)}), in order: {_describe_extension_list(extensions)}")
    if EXT_ALPN in extensions:
        alpn_block = extensions[EXT_ALPN]
        if len(alpn_block) >= 3:
            plen = alpn_block[2]
            proto = alpn_block[3:3 + plen].decode("ascii", errors="replace")
            lines.append(f"      negotiated ALPN protocol: {proto}")
    # TLS 1.3's real negotiated version often appears in a
    # "supported_versions" extension (type 0x002b) rather than the
    # legacy top-level field above -- report it if present, since the
    # legacy field is pinned to TLSv1.2 for TLS 1.3 ServerHellos by
    # the protocol's own design.
    if 0x002B in extensions and len(extensions[0x002B]) >= 2:
        real_version = struct.unpack_from(">H", extensions[0x002B], 0)[0]
        lines.append(f"      actual negotiated version (supported_versions extension): {_tls_version_name(real_version)}")
    return lines


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/dev_pcapng_tls_inspector.py <path-to-file.pcapng>")
        return
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        return
    data = path.read_bytes()
    print(f"Reading {path} ({len(data)} bytes) -- local, read-only, no network activity.\n")

    packet_index = 0
    for frame in iter_pcapng_packets(data):
        parsed = parse_ethernet_ipv4_tcp(frame)
        if parsed is None:
            continue
        packet_index += 1
        src_ip, src_port, dst_ip, dst_port, tcp_flags, payload = parsed
        print(f"[{packet_index}] {src_ip}:{src_port} -> {dst_ip}:{dst_port}  TCP flags: {_tcp_flags_str(tcp_flags)}  payload: {len(payload)} bytes")
        if payload and payload[0] in (TLS_CONTENT_HANDSHAKE, TLS_CONTENT_APPLICATION_DATA, 0x14, 0x15):
            for line in describe_tls_records(payload):
                print(line)

    print(f"\nDone. {packet_index} IPv4/TCP packets found. No network activity performed; no data written anywhere.")


if __name__ == "__main__":
    main()
