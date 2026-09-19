"""
tests/scripts/test_dev_pcapng_tls_inspector.py

Tests the PCAPNG/TLS parser against hand-crafted, synthetic pcapng
byte sequences built directly from the documented wire formats
(IETF pcapng, Ethernet II, IPv4, TCP, TLS record/handshake) -- no
real capture file, no dpkt/scapy, no network activity of any kind.
"""

from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "dev_pcapng_tls_inspector.py"
_spec = importlib.util.spec_from_file_location("dev_pcapng_tls_inspector", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["dev_pcapng_tls_inspector"] = _module
_spec.loader.exec_module(_module)


# --- synthetic packet/pcapng builders (test-only, mirrors real wire formats) ---

def _build_client_hello(sni: str, alpn_protocols: list[str], cipher_suites: list[int]) -> bytes:
    version = b"\x03\x03"
    random_bytes = b"\x00" * 32
    session_id = b""
    cipher_bytes = b"".join(struct.pack(">H", c) for c in cipher_suites)
    compression = b"\x00"

    sni_name = sni.encode("ascii")
    sni_entry = b"\x00" + struct.pack(">H", len(sni_name)) + sni_name
    sni_list = struct.pack(">H", len(sni_entry)) + sni_entry
    sni_ext = struct.pack(">HH", 0x0000, len(sni_list)) + sni_list

    extensions = sni_ext
    if alpn_protocols:  # a real client omits the extension entirely when offering no protocols
        alpn_entries = b"".join(bytes([len(p)]) + p.encode("ascii") for p in alpn_protocols)
        alpn_list = struct.pack(">H", len(alpn_entries)) + alpn_entries
        extensions += struct.pack(">HH", 0x0010, len(alpn_list)) + alpn_list

    body = (
        version + random_bytes + bytes([len(session_id)]) + session_id
        + struct.pack(">H", len(cipher_bytes)) + cipher_bytes
        + bytes([len(compression)]) + compression
        + struct.pack(">H", len(extensions)) + extensions
    )
    handshake_msg = bytes([0x01]) + len(body).to_bytes(3, "big") + body
    return bytes([0x16]) + b"\x03\x01" + struct.pack(">H", len(handshake_msg)) + handshake_msg


def _build_server_hello(negotiated_cipher: int, alpn_proto: str | None, supported_version: int | None) -> bytes:
    version = b"\x03\x03"
    random_bytes = b"\x00" * 32
    session_id = b""
    cipher = struct.pack(">H", negotiated_cipher)
    compression = b"\x00"

    extensions = b""
    if alpn_proto is not None:
        alpn_body = bytes([len(alpn_proto)]) + alpn_proto.encode("ascii")
        alpn_list = struct.pack(">H", len(alpn_body)) + alpn_body
        extensions += struct.pack(">HH", 0x0010, len(alpn_list)) + alpn_list
    if supported_version is not None:
        sv_body = struct.pack(">H", supported_version)
        extensions += struct.pack(">HH", 0x002B, len(sv_body)) + sv_body

    body = (
        version + random_bytes + bytes([len(session_id)]) + session_id
        + cipher + compression + struct.pack(">H", len(extensions)) + extensions
    )
    handshake_msg = bytes([0x02]) + len(body).to_bytes(3, "big") + body
    return bytes([0x16]) + b"\x03\x03" + struct.pack(">H", len(handshake_msg)) + handshake_msg


def _build_eth_ipv4_tcp(src_ip: str, src_port: int, dst_ip: str, dst_port: int, flags: int, payload: bytes) -> bytes:
    eth_header = b"\xAA" * 6 + b"\xBB" * 6 + b"\x08\x00"

    def ip_to_bytes(ip: str) -> bytes:
        return bytes(int(o) for o in ip.split("."))

    ip_total_len = 20 + 20 + len(payload)
    ip_header = struct.pack(
        ">BBHHHBBH4s4s", 0x45, 0, ip_total_len, 0, 0, 64, 6, 0,
        ip_to_bytes(src_ip), ip_to_bytes(dst_ip),
    )
    tcp_header = struct.pack(">HHIIBBHHH", src_port, dst_port, 1000, 2000, (5 << 4), flags, 65535, 0, 0)
    return eth_header + ip_header + tcp_header + payload


def _build_pcapng(frames: list[bytes]) -> bytes:
    def make_block(block_type: int, body: bytes) -> bytes:
        block_len = 12 + len(body)
        return struct.pack("<II", block_type, block_len) + body + struct.pack("<I", block_len)

    shb_body = struct.pack("<I", 0x1A2B3C4D) + struct.pack("<HH", 1, 0) + struct.pack("<q", -1)
    out = make_block(0x0A0D0D0A, shb_body) + make_block(0x00000001, struct.pack("<HHI", 1, 0, 0))
    for frame in frames:
        pad = (-len(frame)) % 4
        padded = frame + b"\x00" * pad
        epb_body = struct.pack("<IIIII", 0, 0, 0, len(frame), len(frame)) + padded
        out += make_block(0x00000006, epb_body)
    return out


class TestPcapngBlockParsing:
    def test_extracts_one_frame_per_enhanced_packet_block(self) -> None:
        frame1 = _build_eth_ipv4_tcp("10.0.0.5", 1000, "1.2.3.4", 443, 0x02, b"")
        frame2 = _build_eth_ipv4_tcp("10.0.0.5", 1001, "1.2.3.4", 443, 0x02, b"")
        pcapng = _build_pcapng([frame1, frame2])
        frames = list(_module.iter_pcapng_packets(pcapng))
        assert len(frames) == 2

    def test_empty_capture_yields_no_frames(self) -> None:
        pcapng = _build_pcapng([])
        assert list(_module.iter_pcapng_packets(pcapng)) == []

    def test_rejects_bad_byte_order_magic(self) -> None:
        bad = struct.pack("<II", 0x0A0D0D0A, 28) + b"\x00\x00\x00\x00" + struct.pack("<HH", 1, 0) + struct.pack("<q", -1) + struct.pack("<I", 28)
        with pytest.raises(ValueError):
            list(_module.iter_pcapng_packets(bad))


class TestEthernetIpv4TcpParsing:
    def test_extracts_src_dst_ip_and_port(self) -> None:
        frame = _build_eth_ipv4_tcp("10.0.0.5", 54321, "104.21.19.82", 443, 0x02, b"hello")
        parsed = _module.parse_ethernet_ipv4_tcp(frame)
        assert parsed is not None
        src_ip, src_port, dst_ip, dst_port, flags, payload = parsed
        assert src_ip == "10.0.0.5"
        assert src_port == 54321
        assert dst_ip == "104.21.19.82"
        assert dst_port == 443
        assert payload == b"hello"

    def test_non_ipv4_ethertype_returns_none(self) -> None:
        frame = b"\xAA" * 6 + b"\xBB" * 6 + b"\x86\xdd" + b"\x00" * 20  # IPv6 ethertype
        assert _module.parse_ethernet_ipv4_tcp(frame) is None

    def test_non_tcp_protocol_returns_none(self) -> None:
        eth = b"\xAA" * 6 + b"\xBB" * 6 + b"\x08\x00"
        ip_header = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20, 0, 0, 64, 17, 0, b"\x0a\x00\x00\x05", b"\x01\x02\x03\x04")  # proto=17 UDP
        assert _module.parse_ethernet_ipv4_tcp(eth + ip_header) is None

    def test_truncated_frame_returns_none_not_an_exception(self) -> None:
        assert _module.parse_ethernet_ipv4_tcp(b"\x00\x01\x02") is None


class TestTcpFlags:
    def test_syn_flag_labeled(self) -> None:
        assert _module._tcp_flags_str(0x02) == "SYN"

    def test_syn_ack_labeled(self) -> None:
        assert _module._tcp_flags_str(0x12) == "SYN|ACK"

    def test_psh_ack_labeled(self) -> None:
        assert _module._tcp_flags_str(0x18) == "PSH|ACK"

    def test_no_flags_shows_dash(self) -> None:
        assert _module._tcp_flags_str(0x00) == "-"


class TestClientHelloParsing:
    def test_extracts_sni(self) -> None:
        record = _build_client_hello("api.chaster.app", ["http/1.1"], [0x1301])
        lines = _module.describe_tls_records(record)
        assert any("SNI: api.chaster.app" in line for line in lines)

    def test_extracts_alpn_protocols(self) -> None:
        record = _build_client_hello("example.com", ["http/1.1", "h2"], [0x1301])
        lines = _module.describe_tls_records(record)
        assert any("http/1.1" in line and "h2" in line for line in lines)

    def test_missing_alpn_extension_is_reported_explicitly(self) -> None:
        record = _build_client_hello("example.com", [], [0x1301])  # builder now truly omits the extension
        lines = _module.describe_tls_records(record)
        joined = "\n".join(lines)
        assert "offered ALPN protocols: (extension not present)" in joined

    def test_extracts_known_cipher_suite_names(self) -> None:
        record = _build_client_hello("example.com", ["http/1.1"], [0x1301, 0x1302, 0x1303])
        lines = _module.describe_tls_records(record)
        joined = "\n".join(lines)
        assert "TLS_AES_128_GCM_SHA256" in joined
        assert "TLS_AES_256_GCM_SHA384" in joined
        assert "TLS_CHACHA20_POLY1305_SHA256" in joined

    def test_unknown_cipher_suite_shown_as_hex_not_guessed(self) -> None:
        record = _build_client_hello("example.com", ["http/1.1"], [0xABCD])
        lines = _module.describe_tls_records(record)
        joined = "\n".join(lines)
        assert "0xABCD" in joined

    def test_offered_tls_version_reported(self) -> None:
        record = _build_client_hello("example.com", ["http/1.1"], [0x1301])
        lines = _module.describe_tls_records(record)
        assert any("TLSv1.2" in line for line in lines)  # legacy_version field is always TLSv1.2-coded


class TestExtensionList:
    """The extension-type list added this turn -- names/numbers only,
    never raw extension contents beyond what SNI/ALPN already parse
    structurally."""

    def test_client_hello_lists_known_extension_names_in_wire_order(self) -> None:
        record = _build_client_hello("api.chaster.app", ["http/1.1"], [0x1301])
        lines = _module.describe_tls_records(record)
        joined = "\n".join(lines)
        assert "extension types present (2), in order: server_name, application_layer_protocol_negotiation" in joined

    def test_client_hello_with_no_alpn_lists_only_server_name(self) -> None:
        """Directly mirrors the real captured evidence: a ClientHello
        with SNI but no ALPN extension at all."""
        record = _build_client_hello("api.chaster.app", [], [0x1302, 0x1301])
        lines = _module.describe_tls_records(record)
        joined = "\n".join(lines)
        assert "extension types present (1), in order: server_name" in joined

    def test_unknown_extension_type_shown_as_hex_not_guessed(self) -> None:
        record = _build_client_hello("example.com", [], [0x1301])
        # Manually append an unrecognized extension (type 0x9999) directly
        # onto the already-built record's extension area is nontrivial
        # given length prefixes -- instead verify the lookup function
        # itself, which is what the ClientHello/ServerHello descriptions
        # both delegate to.
        assert _module._describe_extension_list({0x9999: b""}) == "0x9999"

    def test_known_extension_types_are_named_correctly(self) -> None:
        extensions = {0x000A: b"", 0x000D: b"", 0x002B: b"", 0x0033: b""}
        result = _module._describe_extension_list(extensions)
        assert result == "supported_groups, signature_algorithms, supported_versions, key_share"

    def test_empty_extension_dict_shown_as_none(self) -> None:
        assert _module._describe_extension_list({}) == "(none)"

    def test_extension_list_never_includes_raw_extension_bytes(self) -> None:
        """Confirms the list is built from TYPE numbers only -- never
        touches the extension value bytes at all."""
        secret_looking_bytes = b"\xDE\xAD\xBE\xEF\xCA\xFE"
        result = _module._describe_extension_list({0x000A: secret_looking_bytes})
        assert "deadbeefcafe" not in result.lower()

    def test_server_hello_also_lists_extension_types(self) -> None:
        record = _build_server_hello(0x1302, "http/1.1", 0x0304)
        lines = _module.describe_tls_records(record)
        joined = "\n".join(lines)
        assert "extension types present" in joined
        assert "application_layer_protocol_negotiation" in joined
        assert "supported_versions" in joined


class TestServerHelloParsing:
    def test_extracts_negotiated_cipher(self) -> None:
        record = _build_server_hello(0x1302, "http/1.1", 0x0304)
        lines = _module.describe_tls_records(record)
        assert any("TLS_AES_256_GCM_SHA384" in line for line in lines)

    def test_extracts_negotiated_alpn(self) -> None:
        record = _build_server_hello(0x1301, "http/1.1", None)
        lines = _module.describe_tls_records(record)
        assert any("negotiated ALPN protocol: http/1.1" in line for line in lines)

    def test_extracts_real_tls13_version_from_supported_versions_extension(self) -> None:
        record = _build_server_hello(0x1301, "http/1.1", 0x0304)
        lines = _module.describe_tls_records(record)
        joined = "\n".join(lines)
        assert "actual negotiated version" in joined
        assert "TLSv1.3" in joined


class TestApplicationDataIsNeverShown:
    def test_application_data_content_is_never_printed_only_length(self) -> None:
        secret_bytes = b"\xDE\xAD\xBE\xEF" * 10
        record = bytes([0x17]) + b"\x03\x03" + struct.pack(">H", len(secret_bytes)) + secret_bytes
        lines = _module.describe_tls_records(record)
        joined = "\n".join(lines)
        assert "deadbeef" not in joined.lower()
        assert "Application Data" in joined
        assert str(len(secret_bytes)) in joined


class TestFullFileEndToEnd:
    def test_client_hello_capture_reported_correctly(self, capsys) -> None:
        record = _build_client_hello("api.chaster.app", ["http/1.1"], [0x1301, 0x1302])
        frame = _build_eth_ipv4_tcp("10.0.0.5", 54321, "104.21.19.82", 443, 0x18, record)
        pcapng = _build_pcapng([frame])

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pcapng", delete=False) as f:
            f.write(pcapng)
            temp_path = f.name
        try:
            sys.argv = ["dev_pcapng_tls_inspector.py", temp_path]
            _module.main()
        finally:
            Path(temp_path).unlink()

        output = capsys.readouterr().out
        assert "api.chaster.app" in output
        assert "104.21.19.82:443" in output
        assert "1 IPv4/TCP packets found" in output

    def test_missing_file_reported_safely(self, capsys) -> None:
        sys.argv = ["dev_pcapng_tls_inspector.py", "/nonexistent/path.pcapng"]
        _module.main()  # must not raise
        output = capsys.readouterr().out
        assert "not found" in output.lower()

    def test_no_arguments_prints_usage(self, capsys) -> None:
        sys.argv = ["dev_pcapng_tls_inspector.py"]
        _module.main()  # must not raise
        output = capsys.readouterr().out
        assert "Usage" in output


class TestDevOnlyIsolation:
    def test_module_is_not_imported_by_any_production_code_path(self) -> None:
        import importlib
        import inspect

        for module_name in ("bot.discord_bot", "application.service", "chaster.callback_service", "chaster.emergency_unlock", "chaster.emergency_unlock_server", "chaster.lock_client", "chaster.oauth_client"):
            module = importlib.import_module(module_name)
            source = inspect.getsource(module)
            assert "dev_pcapng_tls_inspector" not in source

    def test_script_never_makes_a_network_call(self) -> None:
        import inspect
        source = inspect.getsource(_module)
        for forbidden in ("socket.create_connection", "socket.socket", "requests.", "urllib", "http.client"):
            assert forbidden not in source

    def test_script_never_accepts_or_references_a_token(self) -> None:
        """Checks actual function signatures, not the module's own
        explanatory docstring (which legitimately mentions 'token'
        while explaining that none is needed)."""
        import inspect
        for func in (_module.main, _module.iter_pcapng_packets, _module.parse_ethernet_ipv4_tcp, _module.describe_tls_records):
            sig = inspect.signature(func)
            assert "token" not in " ".join(sig.parameters).lower()
