"""
tests/chaster/test_token_encryptor.py
"""

from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet

from chaster.token_encryptor import TokenDecryptionError, TokenEncryptor, TokenEncryptorConfigurationError


def _encryptor() -> TokenEncryptor:
    return TokenEncryptor(Fernet.generate_key())


class TestConstruction:
    def test_missing_key_raises_configuration_error(self) -> None:
        with pytest.raises(TokenEncryptorConfigurationError):
            TokenEncryptor(None)

    def test_empty_string_key_raises_configuration_error(self) -> None:
        with pytest.raises(TokenEncryptorConfigurationError):
            TokenEncryptor("")

    def test_invalid_key_format_raises_configuration_error(self) -> None:
        with pytest.raises(TokenEncryptorConfigurationError):
            TokenEncryptor("not-a-valid-fernet-key")

    def test_never_generates_a_replacement_key(self) -> None:
        """A missing key must fail -- never silently produce a working
        encryptor with a key nobody chose."""
        with pytest.raises(TokenEncryptorConfigurationError):
            TokenEncryptor(None)
        # No plausible code path here could succeed; this test exists
        # to make the invariant explicit, not just implied by the
        # missing-key test above.

    def test_valid_key_constructs_successfully(self) -> None:
        TokenEncryptor(Fernet.generate_key())  # must not raise


class TestRoundTrip:
    def test_encrypt_then_decrypt_returns_original_plaintext(self) -> None:
        encryptor = _encryptor()
        ciphertext = encryptor.encrypt("a-real-looking-access-token")
        assert encryptor.decrypt(ciphertext) == "a-real-looking-access-token"

    def test_two_encryptions_of_the_same_plaintext_produce_different_ciphertext(self) -> None:
        """Proves nonce uniqueness -- Fernet embeds a fresh random IV
        on every call, so the same plaintext never produces identical
        ciphertext twice."""
        encryptor = _encryptor()
        first = encryptor.encrypt("same-token-value")
        second = encryptor.encrypt("same-token-value")
        assert first != second
        assert encryptor.decrypt(first) == "same-token-value"
        assert encryptor.decrypt(second) == "same-token-value"

    def test_unusual_unicode_content_round_trips_correctly(self) -> None:
        encryptor = _encryptor()
        weird = "token-with-\u00e9\u00e8\u4e2d\u6587-emoji-\U0001F512"
        assert encryptor.decrypt(encryptor.encrypt(weird)) == weird

    def test_ciphertext_is_ascii_text_suitable_for_a_text_column(self) -> None:
        ciphertext = _encryptor().encrypt("token")
        assert isinstance(ciphertext, str)
        ciphertext.encode("ascii")  # must not raise


class TestRejection:
    def test_empty_string_plaintext_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _encryptor().encrypt("")

    def test_whitespace_only_plaintext_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _encryptor().encrypt("   ")

    def test_non_string_plaintext_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            _encryptor().encrypt(12345)  # type: ignore[arg-type]

    def test_tampered_ciphertext_is_rejected(self) -> None:
        encryptor = _encryptor()
        ciphertext = encryptor.encrypt("a-real-token")
        tampered = ciphertext[:-4] + ("A" if ciphertext[-4] != "A" else "B") + ciphertext[-3:]
        with pytest.raises(TokenDecryptionError):
            encryptor.decrypt(tampered)

    def test_wrong_key_is_rejected(self) -> None:
        ciphertext = TokenEncryptor(Fernet.generate_key()).encrypt("a-real-token")
        wrong_key_encryptor = TokenEncryptor(Fernet.generate_key())
        with pytest.raises(TokenDecryptionError):
            wrong_key_encryptor.decrypt(ciphertext)

    def test_malformed_ciphertext_is_rejected(self) -> None:
        with pytest.raises(TokenDecryptionError):
            _encryptor().decrypt("this-is-not-a-fernet-token-at-all")

    def test_non_string_ciphertext_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            _encryptor().decrypt(b"raw-bytes")  # type: ignore[arg-type]


class TestNoLeakage:
    def test_decryption_error_message_never_contains_the_ciphertext(self) -> None:
        encryptor = _encryptor()
        ciphertext = "this-is-not-a-fernet-token-at-all-XYZUNIQUE"
        try:
            encryptor.decrypt(ciphertext)
            pytest.fail("expected TokenDecryptionError")
        except TokenDecryptionError as exc:
            assert "XYZUNIQUE" not in str(exc)

    def test_configuration_error_never_contains_the_invalid_key_value(self) -> None:
        try:
            TokenEncryptor("MY-UNIQUE-INVALID-KEY-VALUE-ABC123")
            pytest.fail("expected TokenEncryptorConfigurationError")
        except TokenEncryptorConfigurationError as exc:
            assert "MY-UNIQUE-INVALID-KEY-VALUE-ABC123" not in str(exc)

    def test_no_plaintext_appears_in_log_output_across_encrypt_decrypt_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        encryptor = _encryptor()
        secret_plaintext = "UNIQUE-SECRET-PLAINTEXT-MARKER-999"
        with caplog.at_level(logging.DEBUG):
            ciphertext = encryptor.encrypt(secret_plaintext)
            encryptor.decrypt(ciphertext)
            try:
                encryptor.decrypt("garbage")
            except TokenDecryptionError:
                pass
        assert secret_plaintext not in caplog.text
