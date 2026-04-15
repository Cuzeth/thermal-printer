"""Thin wrappers around py_webauthn 2.x.

The flow is:
  begin  → server generates options + persists challenge to session
  finish → browser returns credential, server verifies against challenge

We deal in raw bytes for credential_id / public_key / user_handle inside
this module; the blueprint serializes to base64url for the wire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

import config


@dataclass
class RegistrationVerified:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: list[str]


@dataclass
class AuthenticationVerified:
    new_sign_count: int


# ---------- registration ----------

def begin_registration(username: str, user_handle: bytes, exclude_ids: list[bytes]) -> tuple[str, bytes]:
    """Return (json_options, challenge_bytes). Persist the challenge until finish."""
    options = generate_registration_options(
        rp_id=config.RP_ID,
        rp_name=config.RP_NAME,
        user_id=user_handle,
        user_name=username,
        user_display_name=username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=cid) for cid in exclude_ids],
    )
    return options_to_json(options), options.challenge


def finish_registration(credential: dict, expected_challenge: bytes) -> RegistrationVerified:
    v = verify_registration_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=config.RP_ID,
        expected_origin=config.ORIGIN,
        require_user_verification=False,
    )
    transports = (credential.get("response") or {}).get("transports") or []
    return RegistrationVerified(
        credential_id=v.credential_id,
        public_key=v.credential_public_key,
        sign_count=v.sign_count,
        transports=list(transports),
    )


# ---------- authentication ----------

def begin_authentication(allow_ids: list[bytes]) -> tuple[str, bytes]:
    options = generate_authentication_options(
        rp_id=config.RP_ID,
        allow_credentials=[PublicKeyCredentialDescriptor(id=cid) for cid in allow_ids],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return options_to_json(options), options.challenge


def finish_authentication(
    credential: dict,
    expected_challenge: bytes,
    public_key: bytes,
    current_sign_count: int,
) -> AuthenticationVerified:
    v = verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=config.RP_ID,
        expected_origin=config.ORIGIN,
        credential_public_key=public_key,
        credential_current_sign_count=current_sign_count,
        require_user_verification=False,
    )
    return AuthenticationVerified(new_sign_count=v.new_sign_count)
