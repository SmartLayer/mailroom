#!/usr/bin/env python3
"""
Quick test to verify the Gmail-style search query parser works correctly.
This script tests parsing without requiring an actual IMAP connection.
"""

from mailroom.query_parser import parse_query


def test_example_queries():
    """Test example queries from the documentation."""

    print("Testing Gmail-Style Query Parser")
    print("=" * 80)

    test_cases = [
        {
            "name": "Bare words → TEXT search",
            "query": "meeting notes",
            "expected": ["TEXT", "meeting notes"],
        },
        {
            "name": "from: prefix",
            "query": "from:alice@example.com",
            "expected": ["FROM", "alice@example.com"],
        },
        {
            "name": "Combined prefixes",
            "query": "from:alice subject:invoice is:unread",
            "expected": ["FROM", "alice", "SUBJECT", "invoice", "UNSEEN"],
        },
        {
            "name": "OR operator",
            "query": "from:alice or from:bob",
            "expected": ["OR", "FROM", "alice", "FROM", "bob"],
        },
        {
            "name": "Chained OR",
            "query": "from:a or from:b or from:c",
            "expected": ["OR", "FROM", "a", "OR", "FROM", "b", "FROM", "c"],
        },
        {
            "name": "Negation with dash",
            "query": "-from:alice",
            "expected": ["NOT", "FROM", "alice"],
        },
        {
            "name": "Standalone keyword: all",
            "query": "all",
            "expected_type": str,
        },
        {
            "name": "Empty query → ALL",
            "query": "",
            "expected_type": str,
        },
        {
            "name": "Raw IMAP passthrough",
            "query": 'imap:OR TEXT "Edinburgh" TEXT "Berlin"',
            "expected": ["OR", "TEXT", "Edinburgh", "TEXT", "Berlin"],
        },
        {
            "name": "Complex raw IMAP travel query",
            "query": 'imap:OR TEXT "Edinburgh" OR TEXT "Berlin" OR TEXT "Munich" OR TEXT "Vienna" OR TEXT "Warsaw" OR TEXT "itinerary" OR TEXT "booking confirmation" OR TEXT "e-ticket" OR TEXT "reservation" OR TEXT "receipt" OR TEXT "ticket" TEXT "order"',
            "expected_length": 35,
        },
    ]

    passed = 0
    failed = 0

    for test in test_cases:
        print(f"\n{test['name']}")
        print("-" * 80)
        query_display = test["query"][:80] + "..." if len(test["query"]) > 80 else test["query"]
        print(f"Query: {query_display!r}")

        try:
            result = parse_query(test["query"])
            result_display = str(result)[:100] + "..." if len(str(result)) > 100 else str(result)
            print(f"Result: {result_display}")

            if "expected_type" in test:
                if isinstance(result, test["expected_type"]):
                    print("PASS - Correct type")
                    passed += 1
                else:
                    print(f"FAIL - Expected type {test['expected_type']}, got {type(result)}")
                    failed += 1
            elif "expected" in test:
                if result == test["expected"]:
                    print("PASS - Exact match")
                    passed += 1
                else:
                    print(f"FAIL - Expected {test['expected']}")
                    failed += 1
            elif "expected_length" in test:
                if isinstance(result, list) and len(result) == test["expected_length"]:
                    print(f"PASS - Correct length ({len(result)} tokens)")
                    passed += 1
                else:
                    actual = len(result) if isinstance(result, list) else "not a list"
                    print(f"FAIL - Expected length {test['expected_length']}, got {actual}")
                    failed += 1
        except Exception as e:
            print(f"ERROR - {e}")
            failed += 1

    print("\n" + "=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)

    return failed == 0


if __name__ == "__main__":
    success = test_example_queries()
    exit(0 if success else 1)
