#!/usr/bin/env python3
"""
Download Iridium SBD attachments via IMAP and store them either in daily YYYYMMDD folders or in one flat batch folder.

The email password is read from the IMAP_APP_PASSWORD environment variable (falls back to GMAIL_APP_PASSWORD for backward compatibility).
"""

from __future__ import annotations

import argparse
import email
import imaplib
import os
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_FROM = "sbdservice@sbd.iridium.com"


def yyyymmdd_to_imap(d: str) -> str:
    dt = datetime.strptime(d, "%Y%m%d")
    return dt.strftime("%d-%b-%Y")



def run_search(imap, criteria_parts: list[str]) -> tuple[list[bytes], str]:
    search_criteria = "(" + " ".join(criteria_parts) + ")"
    typ, data = imap.search(None, search_criteria)
    if typ != "OK":
        raise RuntimeError(f"IMAP search failed: {typ} query={search_criteria}")
    msg_ids = data[0].split() if data and data[0] else []
    return msg_ids, search_criteria


def select_mailbox(imap, label: str) -> str:
    cleaned = str(label).strip().strip('"')
    candidates = [cleaned]

    if cleaned.lower() in {"all mail", "[gmail]/all mail", "todos", "[gmail]/todos"}:
        candidates = ["[Gmail]/All Mail", "[Gmail]/Todos", cleaned]

    last_error = None
    for mailbox in dict.fromkeys(candidates):
        quoted = f'"{mailbox}"' if " " in mailbox else mailbox
        try:
            typ, _ = imap.select(quoted)
            if typ == "OK":
                print(f"SELECTED_MAILBOX={mailbox}")
                return mailbox
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"cannot select IMAP label {label!r}: {last_error}")


def diagnostic_searches(imap, since_imap: str, before_imap: str, imei: str, fromaddr: str):
    date_parts = [f'SINCE "{since_imap}"', f'BEFORE "{before_imap}"']

    searches = [
        ("dates_only", date_parts),
        ("dates_plus_from", date_parts + ([f'FROM "{fromaddr}"'] if fromaddr else [])),
        ("dates_plus_subject", date_parts + [f'SUBJECT "{imei}"']),
        (
            "dates_plus_from_plus_subject",
            date_parts
            + ([f'FROM "{fromaddr}"'] if fromaddr else [])
            + [f'SUBJECT "{imei}"'],
        ),
    ]

    results = {}
    for name, parts in searches:
        try:
            ids, query = run_search(imap, parts)
            results[name] = ids
            print(f"SEARCH_DIAGNOSTIC name={name} count={len(ids)} query={query}")
        except Exception as exc:
            results[name] = []
            print(f"SEARCH_DIAGNOSTIC_ERROR name={name} error={exc}")

    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="Email account")
    ap.add_argument("--imei", required=True, help="IMEI / text to search in the subject")
    ap.add_argument("--since", required=True, help="YYYYMMDD inclusive")
    ap.add_argument("--before", required=True, help="YYYYMMDD exclusive, IMAP BEFORE semantics")
    ap.add_argument("--outdir", required=True, help="Float root folder where attachments will be stored")
    ap.add_argument("--fromaddr", default=DEFAULT_FROM, help="Sender filter")
    ap.add_argument("--label", default="INBOX", help="IMAP label/folder")
    ap.add_argument("--imap_server", default="imap.gmail.com", help="IMAP server hostname (e.g. imap.gmail.com, outlook.office365.com)")
    ap.add_argument("--imap_port", type=int, default=993, help="IMAP SSL port (default: 993)")
    ap.add_argument("--layout", choices=["daily", "flat"], default="flat", help="Storage layout: flat stores all SBDs in outdir/sbd_raw; daily stores them in outdir/YYYYMMDD")
    args = ap.parse_args()

    pwd = os.environ.get("IMAP_APP_PASSWORD", "") or os.environ.get("GMAIL_APP_PASSWORD", "")
    if not pwd:
        raise SystemExit("ERROR: missing IMAP_APP_PASSWORD environment variable.")

    since_imap = yyyymmdd_to_imap(args.since)
    # IMAP BEFORE is exclusive — add 1 day so the user's "until" date is included
    before_dt = datetime.strptime(args.before, "%Y%m%d") + timedelta(days=1)
    before_imap = before_dt.strftime("%d-%b-%Y")

    criteria = [f'SINCE "{since_imap}"', f'BEFORE "{before_imap}"', f'SUBJECT "{args.imei}"']
    if args.fromaddr:
        criteria.append(f'FROM "{args.fromaddr}"')
    search_criteria = "(" + " ".join(criteria) + ")"

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    imap = imaplib.IMAP4_SSL(args.imap_server, args.imap_port)
    try:
        imap.login(args.email, pwd)
        select_mailbox(imap, args.label)

        diagnostic = diagnostic_searches(
            imap,
            since_imap=since_imap,
            before_imap=before_imap,
            imei=args.imei,
            fromaddr=args.fromaddr,
        )

        msg_ids = diagnostic.get("dates_plus_from_plus_subject", [])
        search_mode = "dates_plus_from_plus_subject"

        if not msg_ids:
            for candidate_mode in [
                "dates_plus_subject",
                "dates_plus_from",
                "dates_only",
            ]:
                candidate_ids = diagnostic.get(candidate_mode, [])
                if candidate_ids:
                    msg_ids = candidate_ids
                    search_mode = candidate_mode
                    break

        print(f"SEARCH_MODE_USED={search_mode}")
        print(f"FOUND_MESSAGES={len(msg_ids)}")
        downloaded = 0

        def reconnect_imap():
            """Reconnect to IMAP server after a dropped connection."""
            nonlocal imap
            print("RECONNECTING to IMAP server...")
            try:
                imap.logout()
            except Exception:
                pass
            import time
            time.sleep(2)
            imap = imaplib.IMAP4_SSL(args.imap_server, args.imap_port)
            imap.login(args.email, pwd)
            select_mailbox(imap, args.label)
            print("RECONNECTED successfully")

        for mid in msg_ids:
            # Retry fetch up to 3 times with reconnection on failure
            raw = None
            for attempt in range(3):
                try:
                    typ, parts = imap.fetch(mid, "(RFC822)")
                    if typ == "OK" and parts and parts[0] and len(parts[0]) > 1:
                        raw = parts[0][1]
                        break
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, ConnectionError) as e:
                    print(f"FETCH_RETRY attempt={attempt+1}/3 error={e}")
                    try:
                        reconnect_imap()
                    except Exception as re_err:
                        print(f"RECONNECT_FAILED: {re_err}")
                        import time
                        time.sleep(5)

            if raw is None:
                print(f"FETCH_SKIPPED mid={mid} (failed after 3 attempts)")
                continue

            mail = email.message_from_bytes(raw)

            subject = str(mail.get("Subject", "") or "")
            sender = str(mail.get("From", "") or "")

            attachment_names = []
            for probe_part in mail.walk():
                probe_name = probe_part.get_filename()
                if probe_name:
                    attachment_names.append(str(probe_name))

            imei_match = (
                args.imei in subject
                or any(args.imei in name for name in attachment_names)
            )
            sender_match = (
                not args.fromaddr
                or args.fromaddr.lower() in sender.lower()
            )
            has_sbd = any(name.lower().endswith(".sbd") for name in attachment_names)

            printable_mid = mid.decode("ascii", errors="ignore") if isinstance(mid, bytes) else str(mid)
            print(
                f"MESSAGE_CHECK id={printable_mid} "
                f"subject_match={int(imei_match)} sender_match={int(sender_match)} "
                f"has_sbd={int(has_sbd)} subject={subject!r}"
            )

            if not has_sbd:
                continue

            if search_mode != "dates_plus_from_plus_subject":
                if not imei_match and not sender_match:
                    continue

            raw_date = mail.get("Date")
            if raw_date:
                try:
                    dt = parsedate_to_datetime(raw_date)
                    date_folder = dt.strftime("%Y%m%d")
                except Exception:
                    date_folder = "unknown_date"
            else:
                date_folder = "unknown_date"

            if args.layout == "daily":
                target_dir = outdir / date_folder
            else:
                # Flat batch mode: keep all SBDs together so NKE decodes the
                # whole transmission sequence in one pass. This avoids splitting
                # a profile across two calendar-day folders when a transmission
                # starts before midnight and finishes after midnight.
                target_dir = outdir / "sbd_raw"

            target_dir.mkdir(parents=True, exist_ok=True)

            for part in mail.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get("Content-Disposition") is None:
                    continue

                fname = part.get_filename()
                if not fname or not fname.lower().endswith(".sbd"):
                    continue

                if args.layout == "flat":
                    safe_mid = mid.decode("ascii", errors="ignore") if isinstance(mid, bytes) else str(mid)
                    fpath = target_dir / f"{date_folder}_{safe_mid}_{fname}"
                else:
                    fpath = target_dir / fname
                if fpath.exists():
                    continue

                payload = part.get_payload(decode=True)
                if payload is None:
                    continue

                fpath.write_bytes(payload)
                downloaded += 1

        print(f"DOWNLOADED_FILES={downloaded}")
        if downloaded == 0:
            raise SystemExit("ERROR: no matching SBD attachments were downloaded. Review SEARCH_DIAGNOSTIC and MESSAGE_CHECK lines above.")

    finally:
        try:
            imap.close()
        except Exception:
            pass
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
