#!/usr/bin/env python3
"""
Quick test to verify the raw IMAP search functionality works correctly.
This script tests the parsing without requiring an actual IMAP connection.
"""

from imap_mcp.imap_client import ImapClient


def test_example_queries():
    """Test the example queries from the documentation."""
    
    print("Testing IMAP Search Query Parser")
    print("=" * 80)
    
    test_cases = [
        {
            "name": "Simple single keyword",
            "query": "UNSEEN",
            "expected_type": str,
        },
        {
            "name": "Simple TEXT search",
            "query": "TEXT Edinburgh",
            "expected": ["TEXT", "Edinburgh"],
        },
        {
            "name": "Simple OR expression",
            "query": 'OR TEXT "Edinburgh" TEXT "Berlin"',
            "expected": ["OR", "TEXT", "Edinburgh", "TEXT", "Berlin"],
        },
        {
            "name": "Nested OR expression",
            "query": 'OR TEXT "Edinburgh" OR TEXT "Berlin" TEXT "Munich"',
            "expected": ["OR", "TEXT", "Edinburgh", "OR", "TEXT", "Berlin", "TEXT", "Munich"],
        },
        {
            "name": "Complex travel query (original use case)",
            "query": 'OR TEXT "Edinburgh" OR TEXT "Berlin" OR TEXT "Munich" OR TEXT "Vienna" OR TEXT "Warsaw" OR TEXT "itinerary" OR TEXT "booking confirmation" OR TEXT "e-ticket" OR TEXT "reservation" OR TEXT "receipt" OR TEXT "ticket" TEXT "order"',
            "expected_length": 35,  # Correct token count: 12 cities/keywords * 3 (OR/TEXT/keyword) - 1 (first OR) + 2 (last TEXT + order)
        },
        {
            "name": "Combined criteria",
            "query": 'UNSEEN FROM "john@example.com"',
            "expected": ["UNSEEN", "FROM", "john@example.com"],
        },
    ]
    
    passed = 0
    failed = 0
    
    for test in test_cases:
        print(f"\n{test['name']}")
        print("-" * 80)
        print(f"Query: {test['query'][:80]}...")
        
        try:
            result = ImapClient.parse_raw_criteria(test['query'])
            print(f"Result: {result if len(str(result)) < 100 else str(result)[:100] + '...'}")
            
            # Check result
            if "expected_type" in test:
                if isinstance(result, test["expected_type"]):
                    print("✓ PASS - Correct type")
                    passed += 1
                else:
                    print(f"✗ FAIL - Expected type {test['expected_type']}, got {type(result)}")
                    failed += 1
            elif "expected" in test:
                if result == test["expected"]:
                    print("✓ PASS - Exact match")
                    passed += 1
                else:
                    print(f"✗ FAIL - Expected {test['expected']}")
                    failed += 1
            elif "expected_length" in test:
                if isinstance(result, list) and len(result) == test["expected_length"]:
                    print(f"✓ PASS - Correct length ({len(result)} tokens)")
                    passed += 1
                else:
                    print(f"✗ FAIL - Expected length {test['expected_length']}, got {len(result) if isinstance(result, list) else 'not a list'}")
                    failed += 1
        except Exception as e:
            print(f"✗ ERROR - {e}")
            failed += 1
    
    print("\n" + "=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)
    
    return failed == 0


if __name__ == "__main__":
    success = test_example_queries()
    exit(0 if success else 1)

