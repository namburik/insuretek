"""Tests for paytek Graph DB via MCP tools (StreamableHTTP)."""

import sys
import asyncio
import json
sys.path.insert(0, "src")

from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession
from shared.config.config_common import Config

MCP_URL = Config.MCP_CORE_URL  # http://127.0.0.1:8070/mcp_paytek
PASS = "PASS"
FAIL = "FAIL"


async def call_tool(session: ClientSession, tool: str, args: dict = {}) -> dict:
    result = await session.call_tool(tool, args)
    text = (result.content[0].text if result.content else None) or "{}"
    return json.loads(text)


def check(label: str, result, assertions: dict) -> bool:
    errors = []
    for key, expected in assertions.items():
        actual = result
        for part in key.split("."):
            actual = actual.get(part, None) if isinstance(actual, dict) else None
        if callable(expected):
            if not expected(actual):
                errors.append(f"  {key}={actual!r} failed check")
        elif actual != expected:
            errors.append(f"  {key}: expected {expected!r}, got {actual!r}")
    status = PASS if not errors else FAIL
    print(f"[{status}] {label}")
    for e in errors:
        print(e)
    return not errors


async def run_tests():
    results = []

    async with streamable_http_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ── 1. get_full_hierarchy_tree — all roots ────────────────────────
            r = await call_tool(session, "get_full_hierarchy_tree")
            print(f"  result: {r}")
            results.append(check(
                "get_full_hierarchy_tree (all roots)",
                r,
                {"roots": lambda lst: isinstance(lst, list)},
            ))

            # ── 2. get_full_hierarchy_tree — specific root ────────────────────
            r2 = await call_tool(session, "get_full_hierarchy_tree", {"root_group_id": "grp_31d6c7edf2b4"})
            print(f"  result: {r2}")
            ok2 = isinstance(r2, dict)
            print(f"[{PASS if ok2 else FAIL}] get_full_hierarchy_tree (root=grp_31d6c7edf2b4)")
            results.append(ok2)

            # ── 3. get_all_descendant_groups ──────────────────────────────────
            r3 = await call_tool(session, "get_all_descendant_groups", {"group_id": "grp_31d6c7edf2b4"})
            print(f"  result: {r3}")
            ok3 = isinstance(r3, (list, dict))
            print(f"[{PASS if ok3 else FAIL}] get_all_descendant_groups (grp_31d6c7edf2b4)")
            results.append(ok3)

            # ── 4. get_all_ancestor_groups ────────────────────────────────────
            r4 = await call_tool(session, "get_all_ancestor_groups", {"group_id": "grp_31d6c7edf2b4"})
            print(f"  result: {r4}")
            ok4 = isinstance(r4, (list, dict))
            print(f"[{PASS if ok4 else FAIL}] get_all_ancestor_groups (grp_31d6c7edf2b4)")
            results.append(ok4)

            # ── 5. get_descendant_groups_with_members ─────────────────────────
            r5 = await call_tool(session, "get_descendant_groups_with_members", {"group_id": "grp_31d6c7edf2b4"})
            print(f"  result: {r5}")
            ok5 = isinstance(r5, (list, dict))
            print(f"[{PASS if ok5 else FAIL}] get_descendant_groups_with_members (grp_31d6c7edf2b4)")
            results.append(ok5)

            # ── 6. get_ancestor_groups_with_members ───────────────────────────
            r6 = await call_tool(session, "get_ancestor_groups_with_members", {"group_id": "grp_31d6c7edf2b4"})
            print(f"  result: {r6}")
            ok6 = isinstance(r6, (list, dict))
            print(f"[{PASS if ok6 else FAIL}] get_ancestor_groups_with_members (grp_31d6c7edf2b4)")
            results.append(ok6)

            # ── 7. find_similar_merchants ─────────────────────────────────────
            r7 = await call_tool(session, "find_similar_merchants", {"merchant_id": "5521b518-e019-43f2-97ff-a58b4e91a234", "top_k": 3})
            print(f"  result: {r7}")
            results.append(check(
                "find_similar_merchants (5521b518-e019-43f2-97ff-a58b4e91a234, top_k=3)",
                r7,
                {
                    "merchant_id":   "5521b518-e019-43f2-97ff-a58b4e91a234",
                    "similar_count": lambda n: isinstance(n, int) and n <= 3,
                    "similar_merchants": lambda lst: isinstance(lst, list),
                },
            ))

            # ── 8. query_merchant_graph ───────────────────────────────────────
            r8 = await call_tool(session, "query_merchant_graph", {"query": "Which merchants are in retail"})
            print(f"  result: {r8}")
            results.append(check(
                "query_merchant_graph",
                r8,
                {
                    "query":  "Which merchants are in retail",
                    "answer": lambda s: isinstance(s, str) and len(s) > 0,
                },
            ))

            # ── 9. get_merchant_network ───────────────────────────────────────
            r9 = await call_tool(session, "get_merchant_network", {"merchant_id": "5521b518-e019-43f2-97ff-a58b4e91a234", "depth": 1})
            print(f"  result: {r9}")
            results.append(check(
                "get_merchant_network (5521b518-e019-43f2-97ff-a58b4e91a234, depth=1)",
                r9,
                {
                    "merchant_id": "5521b518-e019-43f2-97ff-a58b4e91a234",
                    "depth":       1,
                    "node_count":  lambda n: isinstance(n, int),
                    "nodes":       lambda lst: isinstance(lst, list),
                    "edges":       lambda lst: isinstance(lst, list),
                },
            ))

    return results


results = asyncio.run(run_tests())

passed = sum(1 for r in results if r)
total  = len(results)
print(f"\n{'─'*45}")
print(f"  {passed}/{total} tests passed")
if passed < total:
    sys.exit(1)
