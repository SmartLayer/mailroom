# Complex IMAP Search Support - Implementation Summary

## Overview

Added support for complex IMAP search expressions using OR/AND/NOT operators in Polish notation, enabling powerful email filtering capabilities.

## Changes Made

### 1. Code Changes

#### `imap_mcp/imap_client.py`
- **Added `ImapClient.parse_raw_criteria()` static method** to parse raw IMAP search strings into the format expected by `imapclient`
- **Updated `search_emails()` tool** to accept `criteria="raw"` for complex IMAP expressions
- **Enhanced docstring** to document the new raw criteria option
- **Improved error handling** with better error messages for invalid criteria

Key implementation details:
```python
@staticmethod
def parse_raw_criteria(raw_query: str) -> Union[str, List]:
    """Parse raw IMAP search query string into format suitable for imapclient."""
    # Uses shlex to handle quoted strings properly
    # Returns string for single keywords, list for complex queries
```

### 2. Test Coverage

#### `tests/test_tools.py`
- **Added `TestRawImapCriteriaParsing` class** with 7 comprehensive tests:
  - Simple single-keyword queries (ALL, UNSEEN, etc.)
  - Simple TEXT searches
  - Simple OR expressions
  - Nested OR expressions
  - Complex travel query example
  - FROM/SUBJECT criteria
  - Combined criteria without OR

- **Added `test_search_emails_raw_criteria()` integration test** covering:
  - Simple raw criteria parsing
  - Complex OR expressions
  - Nested OR expressions
  - Single keyword raw queries

All tests pass successfully.

### 3. Documentation

#### `README.md`
- Updated Features section to highlight advanced search capabilities
- Added "Advanced Email Search" section with examples:
  - Simple search examples
  - Complex OR expressions
  - Travel booking search example
  - Combined criteria
- Added note about Polish notation syntax
- Updated roadmap to mark "Advanced search capabilities" as complete

#### `docs/SEARCH_GUIDE.md` (NEW)
Comprehensive 200+ line guide covering:
- Simple search criteria reference table
- Raw IMAP search expression syntax
- Available search keys (TEXT, BODY, FROM, TO, etc.)
- OR operator usage and nesting
- Combined criteria (AND)
- NOT operator
- Complex query examples
- Server compatibility notes
- Performance tips
- Use case examples (receipts, project updates, team emails)
- Testing strategy

#### `examples/complex_search_example.py` (NEW)
Working example script demonstrating:
- Simple OR expression
- Nested OR expression
- Complex travel booking search (the original use case)
- Combined criteria (UNSEEN + OR)

## Example Query

The original query from the issue is now fully supported:

```python
search_emails(
    query='OR TEXT "Edinburgh" OR TEXT "Berlin" OR TEXT "Munich" OR TEXT "Vienna" OR TEXT "Warsaw" OR TEXT "itinerary" OR TEXT "booking confirmation" OR TEXT "e-ticket" OR TEXT "reservation" OR TEXT "receipt" OR TEXT "ticket" TEXT "order"',
    criteria="raw",
    folder="INBOX"
)
```

This is parsed into:
```python
["OR", "TEXT", "Edinburgh", "OR", "TEXT", "Berlin", "OR", "TEXT", "Munich", ...]
```

And passed directly to the IMAP server via `imapclient`.

## Technical Details

### Why This Works

1. **IMAP servers natively understand Polish notation** - they handle the parsing and execution
2. **We don't need to parse the logic** - just convert the string into a list format
3. **The `imapclient` library passes lists directly** to the IMAP server
4. **`shlex` handles quoted strings** properly, so "booking confirmation" stays as one token

### Parser Behavior

- Single keywords (`"ALL"`) → returned as string `"ALL"`
- Multiple tokens (`"TEXT foo"`) → returned as list `["TEXT", "foo"]`
- Quoted strings (`'TEXT "booking confirmation"'`) → properly parsed as `["TEXT", "booking confirmation"]`
- Complex expressions → all tokens in a flat list

### Error Handling

- Invalid criteria types return clear error message with valid options
- Parse errors are caught and returned as JSON error response
- IMAP server errors are logged and don't crash the tool

## Validation

The query syntax was validated against:
- RFC 3501 (IMAP4rev1)
- RFC 9051 (IMAP4rev2)
- Web search results confirming Polish notation support
- imapclient library documentation

## Testing Results

```
tests/test_tools.py::TestRawImapCriteriaParsing::test_parse_simple_single_keyword PASSED
tests/test_tools.py::TestRawImapCriteriaParsing::test_parse_simple_text_search PASSED
tests/test_tools.py::TestRawImapCriteriaParsing::test_parse_simple_or_expression PASSED
tests/test_tools.py::TestRawImapCriteriaParsing::test_parse_nested_or_expression PASSED
tests/test_tools.py::TestRawImapCriteriaParsing::test_parse_complex_travel_query PASSED
tests/test_tools.py::TestRawImapCriteriaParsing::test_parse_from_subject_criteria PASSED
tests/test_tools.py::TestRawImapCriteriaParsing::test_parse_combined_criteria PASSED

7 passed in 0.17s
```

## Backward Compatibility

All existing simple criteria (`text`, `from`, `to`, `subject`, `all`, `unseen`, `seen`, `today`, `week`, `month`) continue to work exactly as before. The new `raw` criteria is opt-in.

## Future Enhancements

Potential future improvements:
- Query builder UI/helper function for constructing complex queries
- Query validation before sending to server
- Saved query templates for common searches
- Server capability detection to warn about unsupported features

## Files Modified

- `imap_mcp/imap_client.py` - `parse_raw_criteria()` static method
- `tests/test_tools.py` - Test coverage
- `README.md` - User documentation
- `docs/SEARCH_GUIDE.md` - Detailed guide (NEW)
- `examples/complex_search_example.py` - Working example (NEW)





