"""
Entity resolver for the IAM AI Assistant.

Resolves pronouns, partial names, and references in extracted params
to real Entra ID entities using conversation history as context.

Key rule: for onboard_user, NEVER validate the UPN — the user does not exist yet.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_USER_PRONOUNS = {"him", "her", "them", "he", "she", "they", "the user", "this user", "that user"}
_APP_PRONOUNS  = {"it", "the app", "this app", "that app", "the application"}

# Actions where the user already exists — validate UPN against Entra ID
_EXISTING_USER_ACTIONS = {
    "offboard_user", "isolate_user",
    "add_to_group", "remove_from_group",
    "assign_license", "remove_license",
}

# Actions where the user is being created — NEVER validate UPN
_NEW_USER_ACTIONS = {"onboard_user"}

# Full country name -> ISO 3166-1 alpha-2 mapping for usage_location normalisation
_COUNTRY_NAME_TO_CODE: dict[str, str] = {
    "india": "IN", "indian": "IN",
    "united states": "US", "usa": "US", "us": "US", "america": "US",
    "united kingdom": "GB", "uk": "GB", "britain": "GB", "england": "GB",
    "australia": "AU", "australian": "AU",
    "canada": "CA", "canadian": "CA",
    "germany": "DE", "german": "DE",
    "france": "FR", "french": "FR",
    "singapore": "SG",
    "united arab emirates": "AE", "uae": "AE", "dubai": "AE",
    "japan": "JP", "japanese": "JP",
    "netherlands": "NL", "dutch": "NL",
    "sweden": "SE", "swedish": "SE",
    "norway": "NO", "norwegian": "NO",
    "denmark": "DK", "danish": "DK",
    "finland": "FI", "finnish": "FI",
    "switzerland": "CH", "swiss": "CH",
    "brazil": "BR", "brazilian": "BR",
    "mexico": "MX", "mexican": "MX",
    "south africa": "ZA",
    "new zealand": "NZ",
    "ireland": "IE", "irish": "IE",
    "italy": "IT", "italian": "IT",
    "spain": "ES", "spanish": "ES",
    "portugal": "PT", "portuguese": "PT",
    "poland": "PL", "polish": "PL",
    "china": "CN", "chinese": "CN",
    "korea": "KR", "south korea": "KR", "korean": "KR",
    "indonesia": "ID", "indonesian": "ID",
    "malaysia": "MY", "malaysian": "MY",
    "philippines": "PH", "filipino": "PH",
    "thailand": "TH", "thai": "TH",
    "vietnam": "VN", "vietnamese": "VN",
    "pakistan": "PK", "pakistani": "PK",
    "bangladesh": "BD", "bangladeshi": "BD",
    "sri lanka": "LK",
    "kenya": "KE", "kenyan": "KE",
    "nigeria": "NG", "nigerian": "NG",
    "egypt": "EG", "egyptian": "EG",
    "saudi arabia": "SA", "saudi": "SA",
    "qatar": "QA",
    "kuwait": "KW",
    "bahrain": "BH",
    "oman": "OM",
    "israel": "IL", "israeli": "IL",
    "turkey": "TR", "turkish": "TR",
    "russia": "RU", "russian": "RU",
    "ukraine": "UA", "ukrainian": "UA",
    "argentina": "AR", "argentinian": "AR",
    "colombia": "CO", "colombian": "CO",
    "chile": "CL", "chilean": "CL",
    "peru": "PE", "peruvian": "PE",
}


def normalise_usage_location(value: str) -> str:
    """
    Convert a country name or code to an ISO 3166-1 alpha-2 code.

    "India" -> "IN"
    "United States" -> "US"
    "IN" -> "IN"  (already a valid code)

    Returns the original value uppercased if no mapping found
    (handles already-correct codes like "US", "GB" etc.)
    """
    if not value:
        return value
    v = value.strip().lower()
    if v in _COUNTRY_NAME_TO_CODE:
        return _COUNTRY_NAME_TO_CODE[v]
    # Check if it's already a valid 2-letter code
    if len(value.strip()) == 2:
        return value.strip().upper()
    # Try partial match for compound names
    for name, code in _COUNTRY_NAME_TO_CODE.items():
        if name in v or v in name:
            return code
    logger.warning("Could not normalise usage_location: '%s' — using as-is uppercased.", value)
    return value.strip().upper()


class EntityResolver:
    """
    Resolves entity references in extracted parameters.

    For onboard_user: never validates UPN (user doesn't exist yet).
    For all other actions: validates UPN/ID against Entra ID.
    Always normalises usage_location to a 2-letter country code.
    """

    def __init__(self, memory):
        self._memory = memory

    async def resolve(
        self,
        action: str,
        extracted: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        resolved = dict(extracted)
        warnings: list[str] = []

        # Always normalise usage_location if present
        if "usage_location" in resolved:
            original = resolved["usage_location"]
            normalised = normalise_usage_location(original)
            if normalised != original:
                logger.info("usage_location normalised: '%s' -> '%s'", original, normalised)
            resolved["usage_location"] = normalised

        # Resolve user entity reference
        identifier = (
            resolved.get("user_principal_name_or_id")
            or resolved.get("user_principal_name")
            or resolved.get("user_id")
            or ""
        )

        if not identifier:
            return resolved, warnings

        # For new user creation — skip ALL entity validation
        # The UPN doesn't exist yet so any lookup will return 404
        if action in _NEW_USER_ACTIONS:
            logger.info(
                "Skipping entity validation for onboard_user — "
                "UPN '%s' will be created.", identifier
            )
            return resolved, warnings

        # For existing user actions — resolve and validate
        if action in _EXISTING_USER_ACTIONS:
            resolved, w = await self._resolve_existing_user(identifier, resolved)
            warnings.extend(w)

        return resolved, warnings

    async def _resolve_existing_user(
        self,
        identifier: str,
        resolved: dict,
    ) -> tuple[dict, list[str]]:
        warnings: list[str] = []
        identifier_lower = identifier.strip().lower()

        # Step 1: Pronoun -> last mentioned user from memory
        if identifier_lower in _USER_PRONOUNS or identifier_lower in _APP_PRONOUNS:
            last = self._memory.last_user()
            if last:
                logger.info("Pronoun '%s' -> '%s' from memory.", identifier, last.get("user_principal_name"))
                return _inject_user(resolved, last), warnings
            else:
                warnings.append(
                    f"The reference '{identifier}' could not be resolved — "
                    "no previous user in conversation history. Please provide a full UPN."
                )
                return resolved, warnings

        # Step 2: Full UPN (has @) -> validate against Entra ID
        if "@" in identifier:
            validated, w = await self._validate_user_upn(identifier)
            warnings.extend(w)
            if validated:
                resolved = _inject_user(resolved, validated)
            return resolved, warnings

        # Step 3: Partial name -> search memory
        mem_match = self._memory.find_user_by_name(identifier)
        if mem_match:
            logger.info("Partial name '%s' -> '%s' from memory.", identifier, mem_match.get("user_principal_name"))
            return _inject_user(resolved, mem_match), warnings

        # Step 4: Partial name -> search Entra ID
        entra_matches, w = await self._search_entra_user(identifier)
        warnings.extend(w)

        if len(entra_matches) == 1:
            resolved = _inject_user(resolved, entra_matches[0])
            logger.info("Partial name '%s' -> '%s' via Entra search.", identifier, entra_matches[0].get("user_principal_name"))
        elif len(entra_matches) > 1:
            names = [f"{u.get('display_name')} ({u.get('user_principal_name')})" for u in entra_matches]
            warnings.append(
                f"'{identifier}' matched multiple users: {', '.join(names)}. "
                "Please be more specific or use the full UPN."
            )
        else:
            warnings.append(
                f"Could not find user '{identifier}' in memory or Entra ID. "
                "Please provide the full UPN."
            )

        return resolved, warnings

    async def _validate_user_upn(self, upn: str) -> tuple[dict | None, list[str]]:
        try:
            from shared.graph_client import GraphClient
            user = await GraphClient().get_user_by_upn(upn)
            return {
                "user_id":             user["id"],
                "user_principal_name": user.get("userPrincipalName", upn),
                "display_name":        user.get("displayName", ""),
            }, []
        except Exception as exc:
            logger.warning("UPN validation failed for '%s': %s", upn, exc)
            return None, [
                f"Could not verify user '{upn}' in Entra ID: {exc}. "
                "Please check the UPN is correct."
            ]

    async def _search_entra_user(self, name: str) -> tuple[list[dict], list[str]]:
        try:
            from shared.graph_client import GraphClient
            results = await GraphClient().search_users(name)
            return [
                {"user_id": u["id"], "user_principal_name": u.get("userPrincipalName", ""),
                 "display_name": u.get("displayName", "")}
                for u in results
            ], []
        except Exception as exc:
            logger.warning("Entra search failed for '%s': %s", name, exc)
            return [], [f"Entra ID search failed: {exc}"]


def _inject_user(resolved: dict, user: dict) -> dict:
    out = dict(resolved)
    if user.get("user_id"):
        out["user_id"] = user["user_id"]
    if user.get("user_principal_name"):
        out["user_principal_name"]       = user["user_principal_name"]
        out["user_principal_name_or_id"] = user["user_principal_name"]
    if user.get("display_name"):
        out["display_name"] = out.get("display_name") or user["display_name"]
    return out

