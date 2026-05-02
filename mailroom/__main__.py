"""Mailroom — email toolkit for AI assistants and command-line scripting.

All CLI commands are subcommands of `mailroom`. The `mcp` subcommand starts
the MCP server; every other subcommand operates directly via IMAP without
importing the mcp package.
"""

import json
import logging
import os
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import typer

from mailroom import __version__
from mailroom.config import (
    ImapBlock,
    MailroomConfig,
    SmtpConfig,
    load_config,
    load_config_with_warnings,
)
from mailroom.imap_client import ImapClient
from mailroom.models import extract_links_batch

if TYPE_CHECKING:
    from mailroom.local_cache import MuBackend


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mailroom {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="mailroom",
    help=(
        "Email toolkit for AI assistants and command-line scripting.\n\n"
        "Exit codes for data-returning commands (search, attachments, links): "
        "0 on success with results, 1 on success with zero results. "
        "This makes shell idioms work:\n\n"
        "  mailroom search 'from:alice@x' || mailroom search 'alice'\n"
        "  ( mailroom -i A search Q ; mailroom -i B search Q ) | jq -s add\n\n"
        "Multi-block search (-i A -i B or --all-imap) makes the second "
        "idiom unnecessary in most cases."
    ),
    no_args_is_help=True,
)

# Module-level state set by the --config callback.
_config_path: Optional[str] = None
_imap_names: List[str] = []
_all_imap: bool = False

# Process-wide MuBackend instance, lazily built when the configured
# [imap.*] blocks opt into the local cache.  Shared across ImapClient
# instances so the muhome discovery (mu info store) runs at most once.
_mu_backend_singleton: Optional["MuBackend"] = None

logger = logging.getLogger(__name__)


def _get_mu_backend(cfg: MailroomConfig) -> Optional["MuBackend"]:
    """Return the shared MuBackend, building it on first use.

    Args:
        cfg: The loaded mailroom configuration.

    Returns:
        A ``MuBackend`` instance when ``cfg.local_cache`` is present;
        ``None`` when the local cache is not configured.
    """
    global _mu_backend_singleton
    if cfg.local_cache is None:
        return None
    if _mu_backend_singleton is None:
        from mailroom.local_cache import MuBackend

        _mu_backend_singleton = MuBackend(cfg.local_cache)
    return _mu_backend_singleton


@app.callback()
def _global_options(
    config: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to TOML configuration file.",
        envvar="MAILROOM_CONFIG",
    ),
    imap_names: List[str] = typer.Option(
        [],
        "--imap",
        "-i",
        help=(
            "[imap.NAME] block to use. Uses default_imap if omitted. "
            "Pass multiple times with 'search' to query several blocks."
        ),
    ),
    all_imap: bool = typer.Option(
        False,
        "--all-imap",
        "-A",
        help="Search every configured [imap.NAME] block. Only meaningful for 'search'.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging."
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    global _config_path, _imap_names, _all_imap
    _config_path = config
    _imap_names = list(imap_names)
    _all_imap = all_imap
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def _resolve_imap_names() -> List[str]:
    """Resolve module-level [imap.*] flags to a deduplicated list of names.

    Returns the [imap.NAME] block names to search, validated against the
    config. Errors hard if a name is unknown or if ``--all-imap`` is set
    with no [imap.*] blocks configured. Emits a stderr note when
    ``--all-imap`` overrides explicit ``-i`` flags.
    """
    try:
        cfg = load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    if _all_imap:
        if not cfg.imap_blocks:
            typer.echo("Error: no [imap.*] blocks configured", err=True)
            raise typer.Exit(1)
        if _imap_names:
            typer.echo("note: --all-imap overrides --imap values", err=True)
        return list(cfg.imap_blocks.keys())
    if not _imap_names:
        return [cfg.default_imap]
    seen: set = set()
    resolved: List[str] = []
    for name in _imap_names:
        if name not in cfg.imap_blocks:
            available = list(cfg.imap_blocks.keys())
            typer.echo(
                f"Error: unknown [imap.{name}] block. Available: {available}",
                err=True,
            )
            raise typer.Exit(1)
        if name not in seen:
            resolved.append(name)
            seen.add(name)
    return resolved


def _make_client(imap_override: Optional[str] = None) -> ImapClient:
    """Create and connect an ImapClient.

    Args:
        imap_override: If given, use this [imap.NAME] block instead of
            the global ``--imap`` flag.

    Raises:
        typer.Exit: On config error, unknown [imap.NAME] block,
            multi-block misuse, or IMAP connect failure.
    """
    try:
        cfg = load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    if imap_override is None:
        if _all_imap or len(_imap_names) > 1:
            typer.echo(
                "Error: this command does not support multi-block flags "
                "(--all-imap or repeated --imap)",
                err=True,
            )
            raise typer.Exit(1)
        single = _imap_names[0] if _imap_names else None
        name = single or cfg.default_imap
    else:
        name = imap_override
    if name not in cfg.imap_blocks:
        available = list(cfg.imap_blocks.keys())
        typer.echo(
            f"Error: unknown [imap.{name}] block. Available: {available}",
            err=True,
        )
        raise typer.Exit(1)
    if not _imap_names and imap_override is None:
        typer.echo(f"Using [imap.{name}]", err=True)
    block = cfg.imap_blocks[name]
    client = ImapClient(block, local_cache=_get_mu_backend(cfg))
    try:
        client.connect()
    except Exception as exc:
        typer.echo(f"Error: failed to connect to IMAP server: {exc}", err=True)
        raise typer.Exit(1)
    return client


def _make_client_soft(name: str) -> Optional[ImapClient]:
    """Connect for one [imap.NAME] block, returning None on failure.

    Used by the multi-block search loop so one unreachable block does
    not abort the whole command. Emits a stderr warning on failure.
    """
    try:
        cfg = load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    if name not in cfg.imap_blocks:
        available = list(cfg.imap_blocks.keys())
        typer.echo(
            f"Error: unknown [imap.{name}] block. Available: {available}",
            err=True,
        )
        raise typer.Exit(1)
    block = cfg.imap_blocks[name]
    client = ImapClient(block, local_cache=_get_mu_backend(cfg))
    try:
        client.connect()
    except Exception as exc:
        typer.echo(f"warning: connect failed for [imap.{name}]: {exc}", err=True)
        return None
    return client


def _resolve_single_imap_name() -> str:
    """Return the single [imap.NAME] block for non-multi commands.

    Raises:
        typer.Exit: On config error, unknown block name, or multi-block
            flags.
    """
    try:
        cfg = load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    if _all_imap or len(_imap_names) > 1:
        typer.echo(
            "Error: this command does not support multi-block flags "
            "(--all-imap or repeated --imap)",
            err=True,
        )
        raise typer.Exit(1)
    name = _imap_names[0] if _imap_names else cfg.default_imap
    if name not in cfg.imap_blocks:
        typer.echo(f"Error: unknown [imap.{name}] block.", err=True)
        raise typer.Exit(1)
    return name


def _empty_result_for_subcmd(subcmd: str) -> Dict[str, Any]:
    """Return a placeholder result for a failed [imap.NAME] block connection."""
    if subcmd == "search":
        return _empty_search_result()
    return {"error": "connection failed"}


def _build_op_key(subcmd: str, **kwargs: Any) -> str:
    """Build a canonical operation key string from a subcommand and its arguments.

    Non-default arguments are included; defaults are omitted so the key
    remains compact. The key for a single-command run is used as the outer
    JSON key, matching the format expected by the batch subcommand.
    """
    parts = [subcmd]
    if subcmd == "search":
        folder = kwargs.get("folder")
        limit = kwargs.get("limit", 10)
        query = kwargs.get("query", "")
        if folder:
            parts += ["-f", folder]
        if limit != 10:
            parts += ["--limit", str(limit)]
        if query:
            parts.append(query)
    elif subcmd == "read":
        folder = kwargs.get("folder", "")
        uid = kwargs.get("uid", 0)
        parts += ["-f", folder, "--uid", str(uid)]
    return " ".join(parts)


def _fetch_email_result(client: ImapClient, folder: str, uid: int) -> Dict[str, Any]:
    """Fetch one email and return its JSON representation, or ``{"error": ...}``.

    Extracted so both the standalone ``read`` command and the batch executor
    share identical output structure.
    """
    email_obj = client.fetch_email(uid, folder)
    if not email_obj:
        return {"error": f"Email UID {uid} not found in {folder}"}
    result: Dict[str, Any] = {
        "uid": uid,
        "folder": folder,
        "from": str(email_obj.from_),
        "to": [str(to) for to in email_obj.to],
        "subject": email_obj.subject,
        "date": (email_obj.date.isoformat() if email_obj.date else None),
        "flags": email_obj.flags,
        "message_id": email_obj.message_id,
        "content_type": ("text/html" if email_obj.content.html else "text/plain"),
        "body": (
            str(email_obj.content.html)
            if email_obj.content.html
            else str(email_obj.content.text) if email_obj.content.text else None
        ),
    }
    if email_obj.in_reply_to:
        result["in_reply_to"] = email_obj.in_reply_to
    if email_obj.references:
        result["references"] = list(email_obj.references)
    if email_obj.cc:
        result["cc"] = [str(cc) for cc in email_obj.cc]
    if email_obj.attachments:
        result["attachments"] = [
            {
                "index": i,
                "filename": att.filename,
                "size": att.size,
                "content_type": att.content_type,
            }
            for i, att in enumerate(email_obj.attachments)
        ]
    return result


def _run_op(client: ImapClient, subcmd: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch one operation against an already-connected IMAP client.

    Args:
        client: Connected ImapClient for the current [imap.NAME] block.
        subcmd: Subcommand name (``"search"`` or ``"read"``).
        kwargs: Parsed arguments for the subcommand.

    Returns:
        Per-block result dict suitable for inclusion in the batch output.
    """
    if subcmd == "search":
        return client.search_emails(
            kwargs["query"],
            folder=kwargs.get("folder"),
            limit=kwargs.get("limit", 10),
        )
    if subcmd == "read":
        return _fetch_email_result(client, kwargs["folder"], kwargs["uid"])
    return {"error": f"unknown subcommand '{subcmd}'"}


def _execute_batch(
    operations: List[Tuple[str, str, Dict[str, Any]]],
    names: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Execute multiple ops block-first over one IMAP connection per block.

    Opens one connection per [imap.NAME] block, runs every operation for
    that block, then closes before moving to the next. Blocks are
    processed sequentially to avoid server-side throttling.

    Args:
        operations: List of ``(op_key, subcmd, kwargs)`` tuples. ``op_key`` is
            echoed back as the outer JSON key.
        names: [imap.NAME] block names to query, processed in order.

    Returns:
        ``{op_key: {imap_name: result_dict}}`` -- the batch JSON shape.

    Raises:
        ValueError: Re-raised from ``_run_op`` so the caller can map it to
            exit code 2 (invalid query syntax).
    """
    batch: Dict[str, Dict[str, Any]] = {key: {} for key, _, _ in operations}
    for name in names:
        client = _make_client_soft(name)
        if client is None:
            for key, subcmd, _ in operations:
                batch[key][name] = _empty_result_for_subcmd(subcmd)
            continue
        try:
            for key, subcmd, kwargs in operations:
                try:
                    batch[key][name] = _run_op(client, subcmd, kwargs)
                except ValueError:
                    raise
                except Exception as exc:
                    batch[key][name] = {"error": str(exc)}
        finally:
            client.disconnect()
    return batch


def _parse_search_args(tokens: List[str]) -> Dict[str, Any]:
    """Parse tokenised search arguments into a kwargs dict."""
    folder: Optional[str] = None
    limit = 10
    query_parts: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-f", "--folder") and i + 1 < len(tokens):
            folder = tokens[i + 1]
            i += 2
        elif tok.startswith("--folder="):
            folder = tok.split("=", 1)[1]
            i += 1
        elif tok in ("-n", "--limit") and i + 1 < len(tokens):
            limit = int(tokens[i + 1])
            i += 2
        elif tok.startswith("--limit="):
            limit = int(tok.split("=", 1)[1])
            i += 1
        elif not tok.startswith("-"):
            query_parts.append(tok)
            i += 1
        else:
            i += 1
    return {"query": " ".join(query_parts), "folder": folder, "limit": limit}


def _parse_read_args(tokens: List[str]) -> Dict[str, Any]:
    """Parse tokenised read arguments into a kwargs dict."""
    folder: Optional[str] = None
    uid: Optional[int] = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-f", "--folder") and i + 1 < len(tokens):
            folder = tokens[i + 1]
            i += 2
        elif tok.startswith("--folder="):
            folder = tok.split("=", 1)[1]
            i += 1
        elif tok in ("-u", "--uid") and i + 1 < len(tokens):
            uid = int(tokens[i + 1])
            i += 2
        elif tok.startswith("--uid="):
            uid = int(tok.split("=", 1)[1])
            i += 1
        else:
            i += 1
    if folder is None or uid is None:
        raise ValueError("read requires --folder (-f) and --uid (-u)")
    return {"folder": folder, "uid": uid}


def _parse_op_string(op_string: str) -> Tuple[str, str, Dict[str, Any]]:
    """Parse an operation string into ``(op_key, subcmd, kwargs)``.

    The ``op_key`` is the original string echoed back unchanged so the caller
    can correlate input operations with output results.

    Args:
        op_string: A subcommand string such as ``"search from:x"`` or
            ``"read -f INBOX --uid 42"``.

    Returns:
        ``(op_string, subcmd, kwargs)`` ready for ``_execute_batch``.

    Raises:
        ValueError: If the string is empty or names an unsupported subcommand.
    """
    tokens = shlex.split(op_string)
    if not tokens:
        raise ValueError("empty operation string")
    subcmd = tokens[0]
    args = tokens[1:]
    if subcmd == "search":
        kwargs: Dict[str, Any] = _parse_search_args(args)
    elif subcmd == "read":
        kwargs = _parse_read_args(args)
    else:
        raise ValueError(f"unsupported batch subcommand '{subcmd}'")
    return op_string, subcmd, kwargs


def _out(data: object) -> None:
    """Print data as JSON to stdout."""
    if isinstance(data, str):
        # Many tools return a JSON string; pass it through as-is.
        print(data)
    else:
        print(json.dumps(data, indent=2, default=str))


def _email_only(s: str) -> str:
    """Strip display name from ``Display Name <addr@host>``."""
    if "<" in s and ">" in s:
        return s.split("<", 1)[1].rsplit(">", 1)[0]
    return s


def _load_cfg_or_exit() -> MailroomConfig:
    """Load config or exit 1 with the usual error formatting."""
    try:
        return load_config(_config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)


def _perform_send(
    client: ImapClient,
    mime_message: Any,
    imap_block: ImapBlock,
    imap_name: str,
    smtp_blocks: Dict[str, SmtpConfig],
    identity: Any,
    save_sent_override: Optional[bool],
    sent_folder_override: Optional[str],
) -> Dict[str, Any]:
    """Resolve SMTP, transmit, and FCC. Used by compose/reply --send and send-draft.

    Args:
        client: Connected ImapClient (used for FCC and folder discovery).
        mime_message: Built MIME message ready to serialise.
        imap_block: Selected [imap.NAME] block.
        imap_name: Name of the selected block (for error messages).
        smtp_blocks: Top-level ``[smtp.*]`` map from the loaded config.
        identity: Resolved ``Identity`` driving From, SMTP route, sent_folder.
        save_sent_override: When set, overrides the SMTP block's
            ``save_sent`` resolution. None means "use the configured default".
        sent_folder_override: When set, overrides the identity's sent_folder
            (and the auto-discovered Sent folder). None means default.

    Returns:
        The standard send-result JSON shape (status, identity, message_ids,
        smtp_response, accepted_recipients, fcc_folder, fcc_uid).
    """
    from mailroom.identity import SmtpUnresolved, resolve_smtp_for_identity
    from mailroom.smtp_transport import send as smtp_send

    try:
        smtp = resolve_smtp_for_identity(identity, imap_block, imap_name, smtp_blocks)
    except SmtpUnresolved as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    try:
        fcc_bytes, send_result = smtp_send(mime_message, smtp)
    except Exception as exc:
        typer.echo(f"Error: SMTP send failed: {exc}", err=True)
        raise typer.Exit(1)

    do_fcc = (
        save_sent_override
        if save_sent_override is not None
        else smtp.resolve_save_sent()
    )
    fcc_folder: Optional[str] = None
    fcc_uid: Optional[int] = None
    if do_fcc:
        target = (
            sent_folder_override or identity.sent_folder or client._get_sent_folder()
        )
        fcc_folder = target
        try:
            fcc_uid = client.append_raw(target, fcc_bytes, flags=(r"\Seen",))
        except Exception as exc:
            typer.echo(f"warning: FCC to {target} failed: {exc}", err=True)

    return {
        "status": "success",
        "identity": identity.address,
        **send_result,
        "fcc_folder": fcc_folder,
        "fcc_uid": fcc_uid,
    }


def _print_eager_warnings_if_relevant() -> None:
    """Print config warnings to stderr when user is 'checking in' on mailroom.

    Detects no-args, ``--help``, or ``-h`` in argv (after stripping known
    global options like ``--config``/``--imap``). Surfaces warnings before
    typer takes over so the user sees them above the help text.

    Safe-fails: any error in load (missing config, bad TOML) is swallowed
    here; the user will see the actual error from typer's normal flow if
    they then run a real command.
    """
    args = sys.argv[1:]
    globals_with_value = {"--config", "-c", "--imap", "-i"}

    config_path: Optional[str] = None
    first_real: Optional[str] = None
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            break
        if tok in globals_with_value and i + 1 < len(args):
            if tok in ("--config", "-c"):
                config_path = args[i + 1]
            i += 2
            continue
        if "=" in tok and tok.split("=", 1)[0] in globals_with_value:
            if tok.startswith("--config="):
                config_path = tok.split("=", 1)[1]
            i += 1
            continue
        first_real = tok
        break

    is_help_or_noargs = first_real is None or first_real in ("--help", "-h")
    if not is_help_or_noargs:
        return

    try:
        _, warnings = load_config_with_warnings(config_path)
    except Exception:
        return
    for w in warnings:
        print(f"warn: {w}", file=sys.stderr)


def _empty_search_result() -> Dict[str, Any]:
    """Return the wrapped result shape for a block that returned nothing.

    Used when client construction or connection fails, so the per-block
    output stays uniform with successful calls.
    """
    return {
        "results": [],
        "provenance": {
            "source": "remote",
            "indexed_at": None,
            "fell_back_reason": None,
        },
    }


def _format_provenance_line(provenance: Dict[str, Any]) -> str:
    """Format a one-line provenance summary for the text output mode."""
    source = provenance.get("source", "remote")
    indexed_at = provenance.get("indexed_at") or "-"
    reason = provenance.get("fell_back_reason")
    parts = [f"source={source}", f"indexed_at={indexed_at}"]
    if reason:
        parts.append(f"fell_back={reason}")
    return "# " + " ".join(parts)


def _format_batch_text(batch: Dict[str, Dict[str, Any]]) -> str:
    """Render a batch result as multi-line, prompt-friendly text."""
    sections: List[str] = []
    for op_key, blocks in batch.items():
        lines: List[str] = [f"=== {op_key} ==="]
        for imap_name, value in blocks.items():
            hits: List[Dict[str, Any]] = value.get("results", []) or []
            provenance = value.get("provenance") or {}
            lines.append(f"== {imap_name} ==")
            if "error" in value and not hits:
                lines.append(f"(error: {value['error']})")
            else:
                lines.append(_format_provenance_line(provenance))
                if not hits:
                    lines.append("(no results)")
                else:
                    for r in hits:
                        date = str(r.get("date", ""))[:10]
                        subject = r.get("subject", "")
                        from_ = r.get("from", "")
                        to_list = r.get("to") or [""]
                        to = to_list[0]
                        folder = r.get("folder", "")
                        message_id = r.get("message_id", "")
                        lines.append(f"{date}  {subject}")
                        lines.append(f"            from: {from_}")
                        lines.append(f"            to:   {to}")
                        lines.append(f"            folder: {folder}")
                        if message_id:
                            lines.append(f"            id:     {message_id}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _format_batch_oneline(batch: Dict[str, Dict[str, Any]]) -> str:
    """Render a batch result as one tab-separated line per result.

    Columns: op_key, imap_name, date, subject, from -> to, message_id.
    """
    lines: List[str] = []
    for op_key, blocks in batch.items():
        for imap_name, value in blocks.items():
            hits: List[Dict[str, Any]] = value.get("results", []) or []
            if not hits:
                lines.append(f"{op_key}\t{imap_name}\t(no results)")
                continue
            for r in hits:
                date = str(r.get("date", ""))[:10]
                subject = r.get("subject", "")
                from_addr = _email_only(r.get("from", ""))
                to_list = r.get("to") or [""]
                to_addr = _email_only(to_list[0]) if to_list[0] else ""
                message_id = r.get("message_id", "")
                lines.append(
                    f"{op_key}\t{imap_name}\t{date}\t{subject}"
                    f"\t{from_addr} -> {to_addr}\t{message_id}"
                )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# config-check, list, status
# ---------------------------------------------------------------------------


@app.command("config-check")
def config_check() -> None:
    """Validate the configuration file without invoking IMAP or SMTP.

    Reports hard errors on invalid TOML or bad cross-references (typo'd
    default_smtp, identity smtp referencing an undefined block, duplicate
    addresses within one [imap.NAME] block, etc.) and lists non-fatal
    warnings (send-disabled blocks, shared credential-less SMTP on
    non-Gmail hosts, no smtp blocks).

    Exit codes:
        0  config is valid (warnings may still be present on stderr)
        1  config is invalid (errors on stderr)
    """
    try:
        _, warnings = load_config_with_warnings(_config_path)
    except (ValueError, FileNotFoundError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    for w in warnings:
        print(f"warn: {w}", file=sys.stderr)
    path = _config_path or "~/.config/mailroom/config.toml"
    print(f"config-check: OK ({path})")
    if warnings:
        print(f"  ({len(warnings)} warning(s) above)")


def _build_inventory(cfg: MailroomConfig) -> Dict[str, Any]:
    """Build the unified JSON inventory of [imap.*]/[smtp.*]/[identity.*]."""
    by_imap: Dict[str, List[str]] = {}
    for ident_name, ident in cfg.identities.items():
        by_imap.setdefault(ident.imap, []).append(ident_name)
    imap_out: Dict[str, Any] = {}
    for name, block in cfg.imap_blocks.items():
        imap_out[name] = {
            "host": block.host,
            "port": block.port,
            "username": block.username,
            "ssl": block.use_ssl,
            "default_smtp": block.default_smtp,
            "maildir": block.maildir,
            "allowed_folders": (
                list(block.allowed_folders) if block.allowed_folders else None
            ),
            "identities": sorted(by_imap.get(name, [])),
        }
    smtp_out: Dict[str, Any] = {}
    for name, smtp in cfg.smtp_blocks.items():
        smtp_out[name] = {
            "host": smtp.host,
            "port": smtp.port,
            "has_creds": bool(smtp.username and smtp.password),
            "save_sent": smtp.save_sent,
            "rewrite_msgid_from_response": smtp.rewrite_msgid_from_response,
        }
    identity_out: Dict[str, Any] = {}
    for name, ident in cfg.identities.items():
        identity_out[name] = {
            "imap": ident.imap,
            "address": ident.address,
            "name": ident.name or None,
            "smtp": ident.smtp,
            "sent_folder": ident.sent_folder,
        }
    return {
        "default_imap": cfg.default_imap,
        "imap": imap_out,
        "smtp": smtp_out,
        "identity": identity_out,
    }


@app.command("list")
def list_cmd() -> None:
    """List configured [imap.*], [smtp.*], and [identity.*] blocks as JSON."""
    cfg = _load_cfg_or_exit()
    _out(_build_inventory(cfg))
    for w in cfg.warnings:
        print(f"warn: {w}", file=sys.stderr)


@app.command("status")
def status() -> None:
    """Show server status: inventory plus version metadata.

    Same shape as `list` with ``server`` and ``version`` keys prepended.
    Use ``list`` for a tighter inventory; use ``status`` when scripts
    want the version stamp alongside the configuration.
    """
    cfg = _load_cfg_or_exit()
    out: Dict[str, Any] = {"server": "Mailroom", "version": __version__}
    out.update(_build_inventory(cfg))
    _out(out)
    for w in cfg.warnings:
        print(f"warn: {w}", file=sys.stderr)


# ---------------------------------------------------------------------------
# folders (NEW)
# ---------------------------------------------------------------------------


@app.command("folders")
def folders() -> None:
    """List available email folders."""
    client = _make_client()
    try:
        folder_list = client.list_folders()
        _out(folder_list)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@app.command("search")
def search(
    query: str = typer.Argument(
        "",
        help=(
            "Gmail-style search query. Examples: "
            "'from:alice subject:invoice', 'is:unread after:2025-03-01', "
            "'meeting notes' (bare words search text), "
            "'imap:OR TEXT foo SUBJECT bar' (raw IMAP)."
        ),
    ),
    query_opt: Optional[str] = typer.Option(
        None, "--query", "-q", help="Alias for the positional query (overrides if set)."
    ),
    folder: Optional[str] = typer.Option(
        None, "--folder", "-f", help="Folder to search (default: all)."
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum number of results."),
    output_format: str = typer.Option(
        "json",
        "--format",
        "-F",
        help="Output format: json (default), text, or oneline.",
    ),
) -> None:
    """Search for emails.

    Output is a JSON object keyed first by operation string, then by
    [imap.NAME] block. Each per-block value is ``{"results": [...],
    "provenance": {...}}`` where ``provenance`` reports whether the
    result came from IMAP (``source: "remote"``) or from a local mu
    cache (``source: "local"``). ``--limit`` is applied per block.

    ``--format text`` renders multi-line, prompt-friendly output grouped
    by operation and block; ``--format oneline`` renders one
    tab-separated line per result (op_key, imap_name, date, subject,
    from -> to, message_id).

    Exit code: 0 on hits, 1 when every block returned zero results, so
    shell fallback chains work: ``mailroom search 'from:x' || mailroom
    search 'x'``.
    """
    effective = query_opt if query_opt is not None else query
    op_key = _build_op_key("search", query=effective, folder=folder, limit=limit)
    names = _resolve_imap_names()
    try:
        batch = _execute_batch(
            [
                (
                    op_key,
                    "search",
                    {"query": effective, "folder": folder, "limit": limit},
                )
            ],
            names,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    if output_format == "json":
        _out(batch)
    elif output_format == "text":
        typer.echo(_format_batch_text(batch))
    elif output_format == "oneline":
        typer.echo(_format_batch_oneline(batch))
    else:
        typer.echo(
            f"Error: unknown --format '{output_format}'. Use json, text, or oneline.",
            err=True,
        )
        raise typer.Exit(2)
    has_results = any(
        v.get("results")
        for per_block in batch.values()
        for v in per_block.values()
        if isinstance(v, dict)
    )
    if not has_results:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------


@app.command("batch")
def batch_cmd(
    operations: Optional[List[str]] = typer.Argument(
        None,
        help=(
            "Operation strings to execute, e.g. "
            "'search from:x' 'read -f INBOX --uid 1'."
        ),
    ),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        help="JSON file containing an array of operation strings.",
    ),
    output_format: str = typer.Option(
        "json",
        "--format",
        "-F",
        help="Output format: json (default), text, or oneline.",
    ),
) -> None:
    """Execute multiple operations in one IMAP connection per block.

    Supply operations as positional arguments, via ``--file``, or as a
    JSON array on stdin. Each operation is a subcommand string identical
    to what you would pass on the command line (e.g. ``"search from:x"``).

    All operations for a given [imap.NAME] block are executed over a
    single connection before moving to the next block, which avoids
    per-query reconnections and server-side throttling.

    Output JSON is keyed by operation string, then by block name -- the
    same shape as a single-command run but with multiple outer keys.

    Exit code: 0 if any search operation returned results, 1 otherwise.
    """
    op_strings: List[str] = []
    if file is not None:
        try:
            op_strings = json.loads(file.read_text())
        except Exception as exc:
            typer.echo(f"Error reading --file: {exc}", err=True)
            raise typer.Exit(1)
    elif operations:
        op_strings = list(operations)
    elif not sys.stdin.isatty():
        try:
            op_strings = json.loads(sys.stdin.read())
        except Exception as exc:
            typer.echo(f"Error reading stdin: {exc}", err=True)
            raise typer.Exit(1)
    else:
        typer.echo(
            "Error: provide operations as arguments, via --file, or on stdin.",
            err=True,
        )
        raise typer.Exit(1)

    parsed: List[Tuple[str, str, Dict[str, Any]]] = []
    for s in op_strings:
        try:
            parsed.append(_parse_op_string(s))
        except ValueError as exc:
            typer.echo(f"Error parsing operation {s!r}: {exc}", err=True)
            raise typer.Exit(1)

    names = _resolve_imap_names()
    try:
        batch = _execute_batch(parsed, names)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)

    if output_format == "json":
        _out(batch)
    elif output_format == "text":
        typer.echo(_format_batch_text(batch))
    elif output_format == "oneline":
        typer.echo(_format_batch_oneline(batch))
    else:
        typer.echo(
            f"Error: unknown --format '{output_format}'. Use json, text, or oneline.",
            err=True,
        )
        raise typer.Exit(2)
    has_results = any(
        v.get("results")
        for per_block in batch.values()
        for v in per_block.values()
        if isinstance(v, dict)
    )
    if not has_results:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# read (NEW)
# ---------------------------------------------------------------------------


@app.command("read")
def read(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """Read an email's content.

    Output is a JSON object keyed by operation string, then by [imap.NAME]
    block, consistent with the batch output format.
    """
    name = _resolve_single_imap_name()
    client = _make_client()
    try:
        result = _fetch_email_result(client, folder, uid)
        if "error" in result:
            typer.echo(result["error"], err=True)
            raise typer.Exit(1)
        op_key = _build_op_key("read", folder=folder, uid=uid)
        _out({op_key: {name: result}})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------


@app.command("move")
def move(
    folder: str = typer.Option(..., "--folder", "-f", help="Source folder."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    target: str = typer.Option(..., "--target", "-t", help="Destination folder."),
) -> None:
    """Move an email to another folder."""
    client = _make_client()
    try:
        success = client.move_email(uid, folder, target)
        _out(
            {
                "success": success,
                "message": (
                    f"Moved from {folder} to {target}"
                    if success
                    else "Failed to move email"
                ),
            }
        )
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------


@app.command("copy")
def copy_cmd(
    from_imap: str = typer.Option(..., "--from-imap", help="Source [imap.NAME] block."),
    from_folder: str = typer.Option(..., "--from-folder", help="Source folder."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID in the source folder."),
    to_folder: str = typer.Option(
        "INBOX", "--to-folder", "-t", help="Destination folder."
    ),
    move_flag: bool = typer.Option(
        False, "--move", help="Delete from source after copy."
    ),
    preserve_flags: bool = typer.Option(
        False, "--preserve-flags", help="Copy original flags to destination."
    ),
) -> None:
    """Copy an email from one [imap.NAME] block into another.

    The global --imap/-i selects the destination block.
    Fetches the raw RFC 822 message from the source and APPENDs it to the
    destination, preserving the message byte-for-byte and its original date.
    """
    from mailroom.imap_client import copy_email_between_imap_blocks

    source = _make_client(imap_override=from_imap)
    dest = _make_client()
    try:
        result = copy_email_between_imap_blocks(
            source,
            dest,
            uid,
            from_folder,
            to_folder=to_folder,
            move=move_flag,
            preserve_flags=preserve_flags,
        )
        if not result["success"]:
            typer.echo(f"Error: {result['error']}", err=True)
            raise typer.Exit(1)
        _out(
            {
                "success": True,
                "subject": result["subject"],
                "source": f"{from_imap}/{from_folder}/{uid}",
                "destination": to_folder,
                "new_uid": result["new_uid"],
                "moved": result["moved"],
            }
        )
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    finally:
        source.disconnect()
        dest.disconnect()


# ---------------------------------------------------------------------------
# mark-read / mark-unread
# ---------------------------------------------------------------------------


@app.command("mark-read")
def mark_read(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """Mark an email as read."""
    client = _make_client()
    try:
        success = client.mark_email(uid, folder, r"\Seen", True)
        _out({"success": success})
    finally:
        client.disconnect()


@app.command("mark-unread")
def mark_unread(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """Mark an email as unread."""
    client = _make_client()
    try:
        success = client.mark_email(uid, folder, r"\Seen", False)
        _out({"success": success})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# flag
# ---------------------------------------------------------------------------


@app.command("flag")
def flag(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    unflag: bool = typer.Option(
        False, "--unflag", help="Remove the flag instead of setting it."
    ),
) -> None:
    """Flag (star) an email, or unflag it with --unflag."""
    client = _make_client()
    try:
        success = client.mark_email(uid, folder, r"\Flagged", not unflag)
        _out({"success": success, "flagged": not unflag})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@app.command("delete")
def delete(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """Delete an email."""
    client = _make_client()
    try:
        success = client.delete_email(uid, folder)
        _out({"success": success})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# triage
# ---------------------------------------------------------------------------


@app.command("triage")
def triage(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    action: str = typer.Argument(
        ..., help="Action: move, read, unread, flag, unflag, delete."
    ),
    target_folder: Optional[str] = typer.Option(
        None, "--target-folder", "-t", help="Target folder (for move)."
    ),
    notes: Optional[str] = typer.Option(None, "--notes", help="Optional notes."),
) -> None:
    """Triage an email with a given action."""
    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)
        try:
            message = client.process_email_action(
                uid, folder, action, target_folder=target_folder
            )
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1)
        _out({"message": message})
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# attachments
# ---------------------------------------------------------------------------


@app.command("attachments")
def attachments(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
) -> None:
    """List attachments for an email.

    Exit code: 0 if at least one attachment is found, 1 if the email has
    none. Shell idiom: ``mailroom attachments -f INBOX -u 1 || echo none``.
    """
    client = _make_client()
    empty = False
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            _out({"error": f"Email UID {uid} not found in {folder}"})
            raise typer.Exit(1)
        result = []
        for i, att in enumerate(email_obj.attachments):
            entry = {
                "index": i,
                "filename": att.filename,
                "size": att.size,
                "content_type": att.content_type,
            }
            if att.content_id:
                entry["content_id"] = att.content_id
            result.append(entry)
        _out(result)
        empty = not result
    finally:
        client.disconnect()
    if empty:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


@app.command("save")
def save(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    identifier: str = typer.Option(
        ..., "--identifier", "-i", help="Attachment filename or numeric index."
    ),
    save_path: str = typer.Option(
        ..., "--save-path", "-o", help="Path to save the attachment."
    ),
) -> None:
    """Download an attachment from an email."""
    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)
        result = email_obj.save_attachment(identifier, save_path)
        _out(result)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def _export_raw(client: ImapClient, folder: str, uid: int, save_path: str) -> None:
    """Export raw RFC 822 bytes to *save_path* (or stdout if ``-``)."""
    fetched = client.fetch_raw(uid, folder)
    if not fetched:
        typer.echo(f"Email UID {uid} not found in {folder}", err=True)
        raise typer.Exit(1)

    raw_bytes = fetched["raw"]
    if save_path == "-":
        sys.stdout.buffer.write(raw_bytes)
        return

    dir_part = os.path.dirname(save_path)
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)
    with open(save_path, "wb") as fh:
        fh.write(raw_bytes)
    _out(
        {
            "success": True,
            "save_path": save_path,
            "size": len(raw_bytes),
            "subject": fetched.get("subject"),
        }
    )


def _export_html(client: ImapClient, folder: str, uid: int, save_path: str) -> None:
    """Export HTML with embedded images to *save_path*."""
    email_obj = client.fetch_email(uid, folder)
    if not email_obj:
        typer.echo(f"Email UID {uid} not found in {folder}", err=True)
        raise typer.Exit(1)
    _out(email_obj.export_html_to_file(save_path))


@app.command("export")
def export(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    save_path: str = typer.Option(
        ...,
        "--save-path",
        "-o",
        help="Path to save to. Use '-' with --raw to stream raw RFC 822 to stdout.",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Export the raw RFC 822 message bytes instead of HTML.",
    ),
) -> None:
    """Export email content to a standalone file.

    Default: HTML with embedded images.
    With --raw: the raw RFC 822 message as stored on the IMAP server.
    """
    client = _make_client()
    try:
        if raw:
            _export_raw(client, folder, uid, save_path)
        else:
            _export_html(client, folder, uid, save_path)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------


@app.command("links")
def links(
    folder: str = typer.Option(..., "--folder", "-f", help="Folder name."),
    uids: List[int] = typer.Option(..., "--uid", "-u", help="One or more email UIDs."),
) -> None:
    """Extract all links from email HTML content."""
    client = _make_client()
    try:
        results = extract_links_batch(client.fetch_email, folder, uids)
        _out(results)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# compose
# ---------------------------------------------------------------------------


def _write_raw_output(mime_message: Any, output: str) -> None:
    """Serialise *mime_message* and write to *output* path (``-`` is stdout)."""
    if hasattr(mime_message, "as_bytes"):
        raw = mime_message.as_bytes()
    else:
        raw = mime_message.as_string().encode("utf-8")
    if output == "-":
        sys.stdout.buffer.write(raw)
        return
    dir_part = os.path.dirname(output)
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)
    with open(output, "wb") as fh:
        fh.write(raw)
    typer.echo(f"Wrote {len(raw)} bytes to {output}", err=True)


@app.command("compose")
def compose(
    to: List[str] = typer.Option(
        ..., "--to", help="Recipient email address. Repeatable."
    ),
    body: str = typer.Option(..., "--body", "-b", help="Plain-text body."),
    subject: str = typer.Option("", "--subject", "-s", help="Subject line."),
    cc: Optional[List[str]] = typer.Option(None, "--cc", help="CC recipients."),
    bcc: Optional[List[str]] = typer.Option(
        None,
        "--bcc",
        help="BCC recipients (added to raw message; stripped by sending agents).",
    ),
    body_html: Optional[str] = typer.Option(
        None, "--body-html", help="HTML version of body."
    ),
    attach: Optional[List[str]] = typer.Option(
        None, "--attach", help="Path to a file to attach. Repeatable."
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path for raw RFC 822 message. Use '-' for stdout (suitable for piping).",
    ),
    send_flag: bool = typer.Option(
        False,
        "--send",
        help="Transmit via SMTP instead of saving as a draft. Mutually exclusive with --output.",
    ),
    from_email: Optional[str] = typer.Option(
        None,
        "--from",
        help=(
            "Identity address to send as. Defaults to the first identity "
            "pointing at the selected [imap.NAME] block."
        ),
    ),
    save_sent: Optional[bool] = typer.Option(
        None,
        "--save-sent/--no-save-sent",
        help="Override the SMTP block's save_sent default for this send.",
    ),
    sent_folder: Optional[str] = typer.Option(
        None,
        "--sent-folder",
        help="Override the identity's sent_folder for the FCC step.",
    ),
) -> None:
    """Compose a new email.

    Default: saves to the IMAP drafts folder.
    --output: writes raw RFC 822 to a file or stdout.
    --send: transmits via SMTP (resolved per identity), then optionally FCCs
    to Sent.
    """
    import email.utils

    from mailroom.identity import (
        IdentityNotFound,
        SendDisabled,
        resolve_identity_for_send,
    )
    from mailroom.models import EmailAddress
    from mailroom.smtp_client import create_mime

    if send_flag and output is not None:
        typer.echo("Error: --send and --output are mutually exclusive", err=True)
        raise typer.Exit(2)

    cfg = _load_cfg_or_exit()
    name = _resolve_single_imap_name()
    block = cfg.imap_blocks[name]

    try:
        identity = resolve_identity_for_send(cfg, name, from_addr=from_email)
    except (IdentityNotFound, SendDisabled) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    client = _make_client()
    try:
        from_addr = EmailAddress(name=identity.name, address=identity.address)
        to_addrs = [EmailAddress.parse(a) for a in to]
        cc_addrs = [EmailAddress.parse(a) for a in cc] if cc else None
        bcc_addrs = [EmailAddress.parse(a) for a in bcc] if bcc else None

        mime_message = create_mime(
            from_addr=from_addr,
            body=body,
            to=to_addrs,
            subject=subject,
            cc=cc_addrs,
            bcc=bcc_addrs,
            html_body=body_html,
            attachments=attach,
        )
        if not mime_message.get("Message-ID"):
            mime_message["Message-ID"] = email.utils.make_msgid()

        if send_flag:
            _out(
                _perform_send(
                    client,
                    mime_message,
                    block,
                    name,
                    cfg.smtp_blocks,
                    identity,
                    save_sent,
                    sent_folder,
                )
            )
        elif output is not None:
            _write_raw_output(mime_message, output)
        else:
            draft_uid = client.save_draft_mime(mime_message)
            if draft_uid is None:
                typer.echo("Failed to save draft", err=True)
                raise typer.Exit(1)
            _out(
                {
                    "status": "success",
                    "message": "Draft saved",
                    "identity": identity.address,
                    "draft_uid": draft_uid,
                    "draft_folder": client._get_drafts_folder(),
                }
            )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# reply
# ---------------------------------------------------------------------------


@app.command("reply")
def reply(
    folder: str = typer.Option(
        ..., "--folder", "-f", help="Folder containing the email."
    ),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID to reply to."),
    body: str = typer.Option(..., "--body", "-b", help="Reply body text."),
    reply_all: bool = typer.Option(
        False, "--reply-all", help="Reply to all recipients."
    ),
    cc: Optional[List[str]] = typer.Option(None, "--cc", help="CC recipients."),
    bcc: Optional[List[str]] = typer.Option(
        None,
        "--bcc",
        help="BCC recipients (added to raw message; stripped by sending agents).",
    ),
    body_html: Optional[str] = typer.Option(
        None, "--body-html", help="HTML version of reply body."
    ),
    attach: Optional[List[str]] = typer.Option(
        None,
        "--attach",
        help="Path to a file to attach. Repeatable.",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path for raw RFC 822 message. Use '-' for stdout (suitable for piping).",
    ),
    send_flag: bool = typer.Option(
        False,
        "--send",
        help="Transmit via SMTP instead of saving as a draft. Mutually exclusive with --output.",
    ),
    from_email: Optional[str] = typer.Option(
        None,
        "--from",
        help="Identity address to reply as. Defaults to whichever identity matches the parent's recipients.",
    ),
    save_sent: Optional[bool] = typer.Option(
        None,
        "--save-sent/--no-save-sent",
        help="Override the SMTP block's save_sent default for this send.",
    ),
    sent_folder: Optional[str] = typer.Option(
        None,
        "--sent-folder",
        help="Override the identity's sent_folder for the FCC step.",
    ),
) -> None:
    """Draft or send a reply to an email.

    Default: saves to drafts.
    --output: writes raw RFC 822.
    --send: transmits via SMTP and FCCs to Sent.
    Reply identity defaults to whichever block identity matches the
    parent's recipients (so a reply to alias X is sent as alias X).
    """
    import email.utils

    from mailroom.identity import (
        IdentityNotFound,
        SendDisabled,
        resolve_identity_for_reply,
        resolve_identity_for_send,
    )
    from mailroom.models import EmailAddress
    from mailroom.smtp_client import create_mime

    if send_flag and output is not None:
        typer.echo("Error: --send and --output are mutually exclusive", err=True)
        raise typer.Exit(2)

    cfg = _load_cfg_or_exit()
    name = _resolve_single_imap_name()
    block = cfg.imap_blocks[name]

    client = _make_client()
    try:
        email_obj = client.fetch_email(uid, folder)
        if not email_obj:
            typer.echo(f"Email UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)

        try:
            if from_email is not None:
                identity = resolve_identity_for_send(cfg, name, from_addr=from_email)
            else:
                identity = resolve_identity_for_reply(cfg, name, email_obj)
        except (IdentityNotFound, SendDisabled) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

        from_addr = EmailAddress(name=identity.name, address=identity.address)
        cc_addresses = [EmailAddress.parse(addr) for addr in cc] if cc else None
        bcc_addresses = [EmailAddress.parse(addr) for addr in bcc] if bcc else None

        mime_message = create_mime(
            original_email=email_obj,
            from_addr=from_addr,
            body=body,
            reply_all=reply_all,
            cc=cc_addresses,
            bcc=bcc_addresses,
            html_body=body_html,
            attachments=attach,
        )
        if not mime_message.get("Message-ID"):
            mime_message["Message-ID"] = email.utils.make_msgid()

        if send_flag:
            _out(
                _perform_send(
                    client,
                    mime_message,
                    block,
                    name,
                    cfg.smtp_blocks,
                    identity,
                    save_sent,
                    sent_folder,
                )
            )
        elif output is not None:
            _write_raw_output(mime_message, output)
        else:
            draft_uid = client.save_draft_mime(mime_message)
            if draft_uid is None:
                typer.echo("Failed to save reply draft", err=True)
                raise typer.Exit(1)
            _out(
                {
                    "status": "success",
                    "message": "Draft reply saved",
                    "identity": identity.address,
                    "draft_uid": draft_uid,
                    "draft_folder": client._get_drafts_folder(),
                }
            )
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# send-draft
# ---------------------------------------------------------------------------


@app.command("send-draft")
def send_draft(
    folder: str = typer.Option(
        ..., "--folder", "-f", help="Folder containing the draft."
    ),
    uid: int = typer.Option(..., "--uid", "-u", help="Draft UID to send."),
    keep_draft: bool = typer.Option(
        False,
        "--keep-draft",
        help="Leave the draft in place after sending. Default deletes it.",
    ),
    save_sent: Optional[bool] = typer.Option(
        None,
        "--save-sent/--no-save-sent",
        help="Override the SMTP block's save_sent default for this send.",
    ),
    sent_folder: Optional[str] = typer.Option(
        None,
        "--sent-folder",
        help="Override the identity's sent_folder for the FCC step.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Connect to SMTP and authenticate, but stop before MAIL FROM. Useful for validating creds.",
    ),
    bcc: Optional[List[str]] = typer.Option(
        None,
        "--bcc",
        help="Add envelope-time BCC recipients without rewriting the draft body.",
    ),
) -> None:
    """Send an existing draft as-is.

    The draft's From header is parsed and matched against the selected
    [imap.NAME] block's identities. An unknown From is a hard error (no
    silent fallback) so that drafting from the wrong identity cannot
    send through the wrong SMTP route.

    On success the draft is deleted from the source folder unless
    --keep-draft is set, and the message is FCC'd to Sent unless
    --no-save-sent or the SMTP block's save_sent resolution says otherwise.
    """
    from email.parser import BytesParser

    from mailroom.identity import (
        IdentityNotFound,
        SendDisabled,
        SmtpUnresolved,
        resolve_identity_for_send,
        resolve_smtp_for_identity,
    )
    from mailroom.smtp_transport import _pick_default_transport

    cfg = _load_cfg_or_exit()
    name = _resolve_single_imap_name()
    block = cfg.imap_blocks[name]

    client = _make_client()
    try:
        fetched = client.fetch_raw(uid, folder)
        if not fetched:
            typer.echo(f"Error: draft UID {uid} not found in {folder}", err=True)
            raise typer.Exit(1)

        msg = BytesParser().parsebytes(fetched["raw"])

        from_raw = str(msg.get("From", "") or "").strip()
        from_addr_only = from_raw
        if "<" in from_raw and ">" in from_raw:
            from_addr_only = from_raw.split("<", 1)[1].rsplit(">", 1)[0].strip()
        if not from_addr_only:
            typer.echo("Error: draft has no From header", err=True)
            raise typer.Exit(1)

        try:
            identity = resolve_identity_for_send(cfg, name, from_addr=from_addr_only)
        except (IdentityNotFound, SendDisabled) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

        try:
            smtp = resolve_smtp_for_identity(identity, block, name, cfg.smtp_blocks)
        except SmtpUnresolved as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

        if bcc:
            existing = str(msg.get("Bcc", "") or "").strip()
            merged = ", ".join([existing] + list(bcc)) if existing else ", ".join(bcc)
            if "Bcc" in msg:
                del msg["Bcc"]
            msg["Bcc"] = merged

        if dry_run:
            factory = _pick_default_transport(smtp.port)
            try:
                conn = factory(smtp.host, smtp.port)
                conn.ehlo()
                if smtp.port in (587, 2587):
                    conn.starttls()
                    conn.ehlo()
                if smtp.username and smtp.password:
                    conn.login(smtp.username, smtp.password)
                conn.quit()
            except Exception as exc:
                typer.echo(f"Error: dry-run failed: {exc}", err=True)
                raise typer.Exit(1)
            _out(
                {
                    "dry_run": True,
                    "identity": identity.address,
                    "smtp": {"host": smtp.host, "port": smtp.port},
                }
            )
            return

        result = _perform_send(
            client,
            msg,
            block,
            name,
            cfg.smtp_blocks,
            identity,
            save_sent,
            sent_folder,
        )

        draft_removed = False
        if not keep_draft:
            try:
                client.delete_email(uid, folder)
                draft_removed = True
            except Exception as exc:
                typer.echo(f"warning: draft delete failed: {exc}", err=True)

        result["draft_removed"] = draft_removed
        _out(result)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# accept-invite
# ---------------------------------------------------------------------------


@app.command("accept-invite")
def accept_invite(
    folder: str = typer.Option(
        ..., "--folder", "-f", help="Folder containing the invite email."
    ),
    uid: int = typer.Option(..., "--uid", "-u", help="Email UID."),
    availability_mode: str = typer.Option(
        "random",
        "--availability-mode",
        help="Availability mode: random, always_available, always_busy, business_hours, weekdays.",
    ),
) -> None:
    """Process a meeting invite and create a draft reply."""
    from mailroom.workflows.meeting_reply import process_meeting_invite_workflow

    client = _make_client()
    try:
        result = process_meeting_invite_workflow(client, folder, uid, availability_mode)
        _out(result)
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# mcp (start MCP server)
# ---------------------------------------------------------------------------


@app.command("mcp")
def mcp_serve(
    config: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to TOML configuration file.",
        envvar="MAILROOM_CONFIG",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging."),
    dev: bool = typer.Option(False, "--dev", help="Enable development mode."),
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    """Start the MCP server (Model Context Protocol)."""
    if version:
        print(f"Mailroom MCP server version {__version__}")
        raise typer.Exit()
    from mailroom.mcp_server import create_server

    server = create_server(config, debug)
    server.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_SEARCH_ALIASES = {"search-email", "search_email", "email-search", "email_search"}


def _rewrite_argv(argv: List[str]) -> List[str]:
    """Rewrite argv for AI-friendly invocation.

    Two transformations:
    1. If the subcommand is one of the known search aliases
       (``email_search``, ``email-search``, ``search_email``, ``search-email``),
       rewrite it to ``search`` and emit a note to stderr.
    2. If ``-i``/``--imap`` appears after the subcommand, hoist it to
       before the subcommand so Typer's global callback sees it.

    Skips when ``_MAILROOM_COMPLETE`` is set so shell-completion is undisturbed.
    """
    if os.environ.get("_MAILROOM_COMPLETE"):
        return argv
    out = list(argv)
    globals_with_value = {"--config", "-c", "--imap", "-i"}
    sub_idx: Optional[int] = None
    i = 0
    while i < len(out):
        tok = out[i]
        if tok == "--":
            break
        if tok in globals_with_value:
            i += 2
            continue
        if (
            tok.startswith("--")
            and "=" in tok
            and tok.split("=", 1)[0] in globals_with_value
        ):
            i += 1
            continue
        if tok.startswith("-"):
            i += 1
            continue
        sub_idx = i
        break
    if sub_idx is None:
        return out
    sub = out[sub_idx]
    if sub in _SEARCH_ALIASES:
        sys.stderr.write(f"note: no such subcommand {sub!r}; running 'search'\n")
        out[sub_idx] = "search"
    imap_values: List[str] = []
    tail: List[str] = []
    j = sub_idx + 1
    while j < len(out):
        tok = out[j]
        if tok in ("--imap", "-i") and j + 1 < len(out):
            imap_values.append(out[j + 1])
            j += 2
            continue
        if tok.startswith("--imap=") or tok.startswith("-i="):
            imap_values.append(tok.split("=", 1)[1])
            j += 1
            continue
        tail.append(tok)
        j += 1
    if imap_values:
        flat: List[str] = []
        for v in imap_values:
            flat.extend(["--imap", v])
        return out[:sub_idx] + flat + [out[sub_idx]] + tail
    return out


def main() -> None:
    sys.argv[1:] = _rewrite_argv(sys.argv[1:])
    _print_eager_warnings_if_relevant()
    app()


if __name__ == "__main__":
    main()
