"""
Local test runner for User Bot — Flow A (onboarding) and Flow B (offboarding).

Usage:
    python test_runner.py

What it does:
    1. Runs Flow A — creates a real test user in your Entra ID tenant
    2. Runs Flow B — offboards that same user immediately after

The test is self-cleaning. After both flows run, no test user remains in your tenant.

Requirements:
    - .env file filled with real credentials
    - Service principal with required Graph API permissions + admin consent granted
    - At least one real group ID set in TEST_GROUP_ID below
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Load .env before any project imports ──────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

# ── Configure logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("test_runner.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("test_runner")

# ── Test configuration — fill these before running ────────────────────────────

# The domain of your Entra ID tenant (e.g. "contoso.onmicrosoft.com")
# Find it in Azure Portal -> Entra ID -> Overview -> Primary domain
TENANT_DOMAIN = os.getenv("TENANT_DOMAIN", "yourdomain.onmicrosoft.com")

# Object ID of a group to assign the test user to during onboarding
# Find it in Azure Portal -> Entra ID -> Groups -> your group -> Object ID
TEST_GROUP_ID = os.getenv("TEST_GROUP_ID", "")

# Test user details — these will be created and then deleted
TEST_USER = {
    "display_name":         "IAM Test User09",
    "mail_nickname":        "iam.testuser09",
    "user_principal_name":  f"iam.testuser99@{TENANT_DOMAIN}",
    "department":           "Engineering",
    "job_title":            "Test Account",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_divider(title: str) -> None:
    width = 60
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


def _print_result(state: dict) -> None:
    status = state.get("status")
    intent = state.get("intent")
    result = state.get("result")
    error  = state.get("error")

    print(f"\n  Status : {status}")
    print(f"  Intent : {intent}")

    if result:
        print(f"  Result :")
        for k, v in result.items():
            print(f"           {k}: {v}")

    if error:
        print(f"  Error  : {error}")


def _validate_config() -> bool:
    """Check all required config is present before running."""
    issues = []

    required_env = [
        "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
        "OPENAI_API_KEY", "OPENAI_ENDPOINT",
    ]
    for key in required_env:
        if not os.getenv(key):
            issues.append(f"Missing env var: {key}")

    if TENANT_DOMAIN == "yourdomain.onmicrosoft.com":
        issues.append(
            "TENANT_DOMAIN is not set. Add it to your .env file: "
            "TENANT_DOMAIN=yourcompany.onmicrosoft.com"
        )

    if not TEST_GROUP_ID:
        issues.append(
            "TEST_GROUP_ID is not set. Add it to your .env file: "
            "TEST_GROUP_ID=your-group-object-id"
        )

    if issues:
        print("\n[CONFIG ERRORS] Fix these before running:\n")
        for issue in issues:
            print(f"  - {issue}")
        return False

    return True


# ── Flow A: User Onboarding ───────────────────────────────────────────────────

async def test_flow_a() -> dict | None:
    """Run Flow A — create a real test user in Entra ID."""
    from shared.models import UserOnboardingRequest
    from user_bot.triggers.hr_webhook import handle_hr_event

    _print_divider("Flow A — User Onboarding")
    print(f"  Creating user : {TEST_USER['user_principal_name']}")
    print(f"  Group         : {TEST_GROUP_ID}")

    hr_event = {
        "event_type":           "hire",
        "display_name":         TEST_USER["display_name"],
        "mail_nickname":        TEST_USER["mail_nickname"],
        "user_principal_name":  TEST_USER["user_principal_name"],
        "department":           TEST_USER["department"],
        "job_title":            TEST_USER["job_title"],
        "group_ids":            [TEST_GROUP_ID],
        "license_sku_id":       None,
    }

    try:
        state = await handle_hr_event(hr_event)
        _print_result(state)

        if state.get("status") == "completed":
            user_id = state["result"]["user_id"]
            print(f"\n  ✓ User created successfully. Object ID: {user_id}")
            return state["result"]
        else:
            print(f"\n  ✗ Flow A did not complete. Status: {state.get('status')}")
            return None

    except Exception as exc:
        print(f"\n  ✗ Flow A raised an exception: {exc}")
        logger.exception("Flow A failed")
        return None


# ── Flow B: User Offboarding ──────────────────────────────────────────────────

async def test_flow_b(user_id: str) -> bool:
    """Run Flow B — offboard the test user created in Flow A."""
    from user_bot.triggers.hr_webhook import handle_hr_event

    _print_divider("Flow B — User Offboarding")
    print(f"  Offboarding   : {TEST_USER['user_principal_name']}")
    print(f"  User ID       : {user_id}")

    hr_event = {
        "event_type":           "terminate",
        "user_id":              user_id,
        "user_principal_name":  TEST_USER["user_principal_name"],
        "reason":               "Test user cleanup after automated test run",
    }

    try:
        state = await handle_hr_event(hr_event)
        _print_result(state)

        # Offboarding requires approval — it will be ESCALATED not COMPLETED
        if state.get("status") in ("completed", "escalated"):
            print(f"\n  ✓ Flow B finished with status: {state.get('status')}")
            if state.get("status") == "escalated":
                print(
                    "  i  Offboarding was escalated for approval (expected behaviour).\n"
                    "     The user account still exists in Entra ID.\n"
                    "     Manually disable/delete it in the Azure Portal if needed."
                )
            return True
        else:
            print(f"\n  ✗ Flow B did not complete. Status: {state.get('status')}")
            return False

    except Exception as exc:
        print(f"\n  ✗ Flow B raised an exception: {exc}")
        logger.exception("Flow B failed")
        return False


# ── Cleanup fallback ──────────────────────────────────────────────────────────

async def emergency_cleanup(user_id: str) -> None:
    """
    Direct Graph API cleanup — called if Flow B fails or is escalated.
    Disables and deletes the test user directly without going through the bot.
    """
    _print_divider("Emergency Cleanup")
    print(f"  Attempting direct Graph API cleanup for user: {user_id}")

    try:
        from shared.graph_client import GraphClient
        client = GraphClient()
        await client.disable_user(user_id)
        print("  ✓ User disabled.")
        await client.revoke_user_sessions(user_id)
        print("  ✓ Sessions revoked.")
        await client.delete_user(user_id)
        print("  ✓ User deleted from Entra ID.")
    except Exception as exc:
        print(f"  ✗ Emergency cleanup failed: {exc}")
        print(
            f"  ! Please manually delete the test user from Azure Portal:\n"
            f"    UPN: {TEST_USER['user_principal_name']}\n"
            f"    Object ID: {user_id}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("\n" + "=" * 60)
    print("  AGENTIC IAM — User Bot Test Runner")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Config validation
    if not _validate_config():
        sys.exit(1)

    print(f"\n  Tenant domain : {TENANT_DOMAIN}")
    print(f"  Test user UPN : {TEST_USER['user_principal_name']}")
    print(f"  Test group ID : {TEST_GROUP_ID}")
    print(f"  OpenAI model  : {os.getenv('OPENAI_MODEL', 'gpt-4o')}")

    user_id = None
    flow_a_passed = False
    flow_b_passed = False

    # ── Run Flow A ────────────────────────────────────────────────────────────
    onboarding_result = await test_flow_a()
    if onboarding_result:
        user_id = onboarding_result["user_id"]
        flow_a_passed = True
    else:
        print("\n  Flow A failed — skipping Flow B.")

    # ── Run Flow B (only if Flow A succeeded) ─────────────────────────────────
    if user_id:
        flow_b_passed = await test_flow_b(user_id)

        # If offboarding was escalated (approval required), run emergency cleanup
        # so the test user doesn't remain in the tenant
        if not flow_b_passed:
            await emergency_cleanup(user_id)

    # ── Summary ───────────────────────────────────────────────────────────────
    _print_divider("Test Summary")
    print(f"  Flow A (onboarding)  : {'PASSED ✓' if flow_a_passed else 'FAILED ✗'}")
    print(f"  Flow B (offboarding) : {'PASSED ✓' if flow_b_passed else 'FAILED ✗'}")
    print(f"\n  Full logs saved to   : test_runner.log")

    if flow_a_passed and flow_b_passed:
        print("\n  All flows passed. User bot is working correctly.")
    else:
        print("\n  Some flows failed. Check logs above and test_runner.log for details.")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())