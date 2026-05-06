"""Tests for the markdown auto-render trigger and renderer."""

from mailroom.markdown_render import needs_html, render_html


def test_table_with_separator_triggers():
    body = "intro\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    assert needs_html(body) is True


def test_table_with_alignment_separator_triggers():
    body = "| name | score |\n|:-----|------:|\n| ann  |    99 |\n"
    assert needs_html(body) is True


def test_h1_heading_triggers():
    assert needs_html("# Title\n\nbody\n") is True


def test_h2_heading_triggers():
    assert needs_html("## Subsection\n\nbody\n") is True


def test_h6_heading_triggers():
    assert needs_html("###### Tiny\n\nbody\n") is True


def test_plain_prose_does_not_trigger():
    assert needs_html("Hello,\n\nThanks for the update.\n") is False


def test_bullet_list_does_not_trigger():
    body = "- alpha\n- beta\n- gamma\n"
    assert needs_html(body) is False


def test_link_only_does_not_trigger():
    assert needs_html("See https://example.com for details.\n") is False


def test_pipes_in_prose_without_separator_do_not_trigger():
    body = "shell pipe usage: cat foo | grep bar | wc -l\n"
    assert needs_html(body) is False


def test_hashtag_does_not_trigger():
    # No whitespace after the #, so this is a tag rather than a heading.
    assert needs_html("Check out #news today.\n") is False


def test_seven_hashes_is_not_a_heading():
    # ATX headings only go up to six levels.
    assert needs_html("####### too deep\n") is False


def test_table_renders_as_html_table():
    body = "| col | val |\n|-----|-----|\n| a   | 1   |\n"
    out = render_html(body)
    assert '<table border="1">' in out
    assert "<th>col</th>" in out
    assert "<td>a</td>" in out


def test_h1_renders_as_html_heading():
    out = render_html("# Status\n")
    assert "<h1>Status</h1>" in out


def test_h2_renders_as_html_heading():
    out = render_html("## Section\n")
    assert "<h2>Section</h2>" in out


def test_paragraph_text_is_preserved():
    out = render_html("Hello world.\n")
    assert "Hello world." in out
