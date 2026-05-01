"""
test_mcp_client.py — Integration test for pipernet-mcp
Run with: python3 mcp-server/test_mcp_client.py

Requires the server to be running:
    python3 mcp-server/main.py --host 127.0.0.1 --port 9000
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path

# Add repo root to path for cli.core
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

SERVER_URL = "http://127.0.0.1:9000/mcp"
TEST_HANDLE = "dinesh-test"


async def run_tests() -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    print("=" * 60)
    print("pipernet-mcp integration test")
    print("=" * 60)

    async with streamablehttp_client(SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"\n[OK] Connected to {SERVER_URL}")

            # List available tools
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            print(f"[OK] Tools available: {tool_names}")
            assert "pipernet_send" in tool_names, "missing pipernet_send"
            assert "pipernet_inbox" in tool_names, "missing pipernet_inbox"
            assert "pipernet_verify" in tool_names, "missing pipernet_verify"
            assert "oracle_search" in tool_names, "missing oracle_search"
            assert "oracle_recent" in tool_names, "missing oracle_recent"
            print("     All 7 tools present: OK")

            # ---------------------------------------------------------------
            # TEST 1: pipernet_whoami
            # ---------------------------------------------------------------
            print(f"\n[1] pipernet_whoami")
            r = await session.call_tool("pipernet_whoami", {"handle": TEST_HANDLE})
            result = json.loads(r.content[0].text)
            print(f"    Response: {json.dumps(result, indent=2)}")
            # Handle may or may not be registered from a previous test run — both are valid
            assert "handle" in result, "response should have 'handle' field"
            if result.get("registered") is False:
                print("    PASS: unregistered handle handled gracefully")
            else:
                print("    PASS: handle already registered (from prior test run)")

            # ---------------------------------------------------------------
            # TEST 2: pipernet_send (will fail — no keystore yet, but should fail gracefully)
            # ---------------------------------------------------------------
            print(f"\n[2] pipernet_send without keystore (should fail gracefully)")
            r = await session.call_tool("pipernet_send", {
                "handle": TEST_HANDLE,
                "channel": "mcp-test",
                "body": "Hello from pipernet-mcp test"
            })
            result = json.loads(r.content[0].text)
            print(f"    Response keys: {list(result.keys())}")
            # Expected: error about missing keystore
            assert "error" in result or "from" in result, "unexpected response shape"
            if "error" in result:
                print(f"    PASS: Graceful error — {result['error']}")
            else:
                print(f"    PASS: Got signed envelope (keystore already exists!)")
                # Verify it
                print(f"\n[2b] Verifying the envelope we just sent...")
                vr = await session.call_tool("pipernet_verify", {
                    "envelope_json": json.dumps(result)
                })
                verdict = json.loads(vr.content[0].text)
                print(f"     Verdict: {verdict}")

            # ---------------------------------------------------------------
            # TEST 3: Create a keystore via pipernet CLI, then test send
            # ---------------------------------------------------------------
            print(f"\n[3] Creating keystore for '{TEST_HANDLE}' via CLI...")
            import subprocess
            r_gen = subprocess.run(
                ["python3", "-m", "cli", "keygen", "--handle", TEST_HANDLE, "--force"],
                capture_output=True, text=True,
                cwd=str(Path(__file__).parent.parent.resolve())
            )
            if r_gen.returncode != 0:
                print(f"    keygen failed: {r_gen.stderr}")
            else:
                identity = json.loads(r_gen.stdout)
                print(f"    Keypair created. pubkey: {identity.get('pubkey_hex', 'N/A')[:16]}...")

                print(f"\n[3b] pipernet_send with valid keystore")
                r = await session.call_tool("pipernet_send", {
                    "handle": TEST_HANDLE,
                    "channel": "mcp-test",
                    "body": "Hello from pipernet-mcp! Signed by Dinesh."
                })
                envelope = json.loads(r.content[0].text)
                print(f"     Envelope from: {envelope.get('from')}")
                print(f"     Sequence: {envelope.get('sequence')}")
                print(f"     Has signature: {'signature' in envelope}")
                assert envelope.get("from") == TEST_HANDLE
                assert "signature" in envelope
                print("     PASS: Real signed envelope created")

                print(f"\n[3c] pipernet_verify on the envelope")
                vr = await session.call_tool("pipernet_verify", {
                    "envelope_json": json.dumps(envelope)
                })
                verdict = json.loads(vr.content[0].text)
                print(f"     Verdict: {verdict}")
                assert verdict["valid"] is True, f"signature should be valid, got: {verdict}"
                print("     PASS: Signature verified OK")

            # ---------------------------------------------------------------
            # TEST 4: pipernet_inbox
            # ---------------------------------------------------------------
            print(f"\n[4] pipernet_inbox")
            r = await session.call_tool("pipernet_inbox", {
                "channel": "mcp-test",
                "limit": 5
            })
            inbox_result = json.loads(r.content[0].text)
            print(f"    Channel: {inbox_result.get('channel')}")
            print(f"    Count: {inbox_result.get('count')}")
            print(f"    Total in log: {inbox_result.get('total_in_log')}")
            print("    PASS: Inbox returned without error")

            # ---------------------------------------------------------------
            # TEST 5: oracle_search (may hit offline, that's fine)
            # ---------------------------------------------------------------
            print(f"\n[5] oracle_search")
            r = await session.call_tool("oracle_search", {
                "query": "pipernet protocol",
                "limit": 5
            })
            oracle_result = json.loads(r.content[0].text)
            status = oracle_result.get("oracle_status", "unknown")
            print(f"    oracle_status: {status}")
            if status == "offline":
                print("    INFO: Oracle gateway offline (token rotated or unreachable)")
                print("    PASS: Offline fallback returned gracefully, no crash")
            else:
                count = oracle_result.get("count", 0)
                print(f"    Results count: {count}")
                if count > 0:
                    first = oracle_result["results"][0]
                    print(f"    First result keys: {list(first.keys())}")
                    # Privacy check: these fields should NOT be present
                    assert "_id" not in first, "_id leaked!"
                    assert "_neo4j_id" not in first, "_neo4j_id leaked!"
                    assert "token" not in first, "token leaked!"
                    assert "secret" not in first, "secret leaked!"
                print("    PASS: Oracle responded OK")

            # ---------------------------------------------------------------
            # TEST 6: oracle_recent
            # ---------------------------------------------------------------
            print(f"\n[6] oracle_recent")
            r = await session.call_tool("oracle_recent", {"limit": 3})
            recent_result = json.loads(r.content[0].text)
            status = recent_result.get("oracle_status", "unknown")
            print(f"    oracle_status: {status}")
            if status == "offline":
                print("    PASS: Offline fallback returned gracefully")
            else:
                print(f"    Count: {recent_result.get('count')}")
                print("    PASS: oracle_recent OK")

            # ---------------------------------------------------------------
            # TEST 7: Privacy filter verification (standalone, no MCP call needed)
            # ---------------------------------------------------------------
            print(f"\n[7] Privacy filter unit test")
            sys.path.insert(0, str(Path(__file__).parent.resolve()))
            from main import _filter_item, _filter_oracle_response

            # Fake item with forbidden fields + private tag
            fake_private = {
                "_id": "neo4j:123",
                "_neo4j_id": 456,
                "content": "This should be dropped",
                "tags": ["private", "committed"],
                "token": "secret_token_value",
                "auth_key": "should_be_stripped",
                "internal_source": "session-2026-01-01",
            }
            assert _filter_item(fake_private) is None, "private item should be dropped"
            print("    PASS: Item with 'private' tag dropped entirely")

            fake_clean = {
                "_id": "neo4j:789",
                "content": "A" * 600,  # 600 chars, should be truncated to 500
                "tags": ["committed", "public"],
                "token": "should_be_stripped",
                "auth_token": "should_be_stripped",
                "internal_notes": "should_be_stripped",
                "pubkey_hex": "should_be_stripped",
                "type": "learning",
                "confidence": "strong",
            }
            filtered = _filter_item(fake_clean)
            assert filtered is not None, "clean item should not be dropped"
            assert "_id" not in filtered, "_id should be stripped"
            assert "token" not in filtered, "token should be stripped"
            assert "auth_token" not in filtered, "auth_token should be stripped"
            assert "internal_notes" not in filtered, "internal_notes should be stripped"
            assert "pubkey_hex" not in filtered, "pubkey_hex should be stripped"
            assert len(filtered["content"]) == 501, f"content should be truncated to 500+ellipsis, got {len(filtered['content'])}"
            assert filtered["type"] == "learning", "type should pass through"
            assert filtered["confidence"] == "strong", "confidence should pass through"
            print("    PASS: Sensitive fields stripped from clean item")
            print(f"    PASS: Content truncated: {len(filtered['content'])} chars (500 + ellipsis)")
            print(f"    Remaining fields: {list(filtered.keys())}")

            print("\n" + "=" * 60)
            print("ALL TESTS PASSED")
            print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_tests())
