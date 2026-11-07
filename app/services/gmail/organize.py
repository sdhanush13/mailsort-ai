"""
Auto-Organize
-------------
Sort emails into labels (bank statements, offers, alerts, investments, jobs,
"To Delete"). Two steps so nothing changes without review:

1. ``organize_scan_background`` - classify emails, store a plan (preview only).
2. ``organize_apply_background`` - create labels and apply the chosen parts of the plan.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from typing import Optional

from app.core import state
from app.core import settings
from app.services.auth import get_gmail_service
from app.services.gmail import llm
from app.services.gmail.helpers import build_gmail_query, get_sender_info
from app.services.gmail.organize_rules import (
    KEEP,
    RULES_VERSION,
    EmailMeta,
    classify_by_rules,
    is_managed_label,
)

logger = logging.getLogger(__name__)

# Gmail allows 250 quota units/user/second; messages.get costs 5. Google advises
# batches of at most 50 (larger ones trip "too many concurrent requests").
# 50 gets = 250 units -> one batch per ~1s. The interval adapts: it backs off when
# Gmail rate-limits and creeps back down when batches go through cleanly.
FETCH_BATCH = 50
FETCH_PAUSE_SECONDS = 1.4  # ~70% of the limit leaves headroom for other calls
FETCH_PAUSE_MAX_SECONDS = 8.0
MODIFY_BATCH = 1000  # batchModify limit
LLM_MAX_CONSECUTIVE_ERRORS = 3
BASE_EXCLUDE = "-in:sent -in:chats -in:spam -in:trash -in:drafts"


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc)
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status == 429 or "rateLimit" in text or "RATE_LIMIT" in text or "quota" in text.lower()


def _execute_with_retry(request, attempts: int = 5):
    for attempt in range(attempts):
        try:
            return request.execute()
        except Exception as e:
            if not _is_rate_limited(e) or attempt == attempts - 1:
                raise
            wait = min(60, 5 * 2**attempt)
            logger.warning("Gmail rate limit on request (attempt %d), waiting %ds: %s", attempt + 1, wait, str(e)[:200])
            time.sleep(wait)


def _list_message_ids(
    service,
    query: str,
    limit: int,
    exclude: Optional[set[str]] = None,
    left_alone: Optional[set[str]] = None,
) -> tuple[list[str], int, int]:
    """List ids matching `query`, leaving out already organized and remembered left-alone ids.

    Returns (ids, organized_count, left_alone_count). `limit` counts only ids that
    are returned (<= 0 means no limit).
    """
    exclude = exclude or set()
    left_alone = left_alone or set()
    ids: list[str] = []
    organized = remembered = 0
    page_token = None
    while limit <= 0 or len(ids) < limit:
        params = {"userId": "me", "q": query, "maxResults": 500}
        if page_token:
            params["pageToken"] = page_token
        result = _execute_with_retry(service.users().messages().list(**params))
        for m in result.get("messages", []):
            if m["id"] in exclude:
                organized += 1
            elif m["id"] in left_alone:
                remembered += 1
            elif limit <= 0 or len(ids) < limit:
                ids.append(m["id"])
        state.organize_status["message"] = (
            f"Listing emails... {len(ids):,} to check, {organized:,} already organized"
            + (f", {remembered:,} left alone before" if remembered else "")
        )
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return ids, organized, remembered


def _list_labelled_ids(service, label_ids: list[str]) -> set[str]:
    """Ids of all messages carrying any of `label_ids` (ids only, no message details)."""
    found: set[str] = set()
    for label_id in label_ids:
        page_token = None
        while True:
            params = {"userId": "me", "labelIds": [label_id], "maxResults": 500, "includeSpamTrash": False}
            if page_token:
                params["pageToken"] = page_token
            result = _execute_with_retry(service.users().messages().list(**params))
            found.update(m["id"] for m in result.get("messages", []))
            state.organize_status["message"] = f"Finding already organized emails... {len(found):,}"
            page_token = result.get("nextPageToken")
            if not page_token:
                break
    return found


# AI answers are remembered per message id across runs, so emails the AI already
# looked at (including ones it chose to leave alone) are not sent to it again.
AI_MEMORY_FILE = "organize_ai_memory.json"
AI_MEMORY_SAVE_EVERY = 25


def _ai_memory_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(settings.token_file)), AI_MEMORY_FILE)


def _read_versioned(path: str, key: str):
    """Return (version, payload); files from before versioning count as version 1."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return RULES_VERSION, None
    if isinstance(data, dict) and "version" in data:
        return data.get("version"), data.get(key)
    return 1, data


def _load_ai_memory() -> dict[str, str]:
    version, data = _read_versioned(_ai_memory_path(), "answers")
    if not isinstance(data, dict):
        return {}
    memory = {str(k): str(v) for k, v in data.items()}
    if version != RULES_VERSION:
        # The categories changed: keep real labels, re-ask the AI about "other" answers
        memory = {k: v for k, v in memory.items() if v}
    return memory


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        logger.warning("Could not save %s", path, exc_info=True)


def _save_ai_memory(memory: dict[str, str]) -> None:
    _write_json(_ai_memory_path(), {"version": RULES_VERSION, "answers": memory})


# Emails a scan decided to leave alone (protected mail, or the AI said "other").
# They get no label, so without this list every scan would read them again.
# Re-checking them (e.g. after a rule update) is opt-in per scan.
LEFT_ALONE_FILE = "organize_left_alone.json"


def _left_alone_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(settings.token_file)), LEFT_ALONE_FILE)


def _load_left_alone() -> set[str]:
    version, data = _read_versioned(_left_alone_path(), "ids")
    if version != RULES_VERSION or not isinstance(data, list):
        return set()  # new categories may fit them now, so check them all again
    return {str(x) for x in data}


def _save_left_alone(ids: set[str]) -> None:
    _write_json(_left_alone_path(), {"version": RULES_VERSION, "ids": sorted(ids)})


def _fetch_metadata(service, ids: list[str], on_progress) -> list[dict]:
    """Fetch From/Subject/List-Unsubscribe, labels and snippet, throttled with retries."""
    messages: list[dict] = []
    pending = list(ids)
    rounds = 0
    done = 0
    pause = FETCH_PAUSE_SECONDS
    while pending and rounds < 4:
        retry: list[str] = []
        for start in range(0, len(pending), FETCH_BATCH):
            chunk = pending[start : start + FETCH_BATCH]
            started = time.monotonic()
            limited_before = len(retry)

            def callback(request_id, response, exception):
                nonlocal done
                if exception is not None:
                    if _is_rate_limited(exception):
                        retry.append(request_id)
                    else:
                        logger.warning("Could not read message %s: %s", request_id, exception)
                    return
                messages.append(response)
                done += 1

            batch = service.new_batch_http_request(callback=callback)
            for mid in chunk:
                batch.add(
                    service.users().messages().get(
                        userId="me",
                        id=mid,
                        format="metadata",
                        metadataHeaders=["From", "Subject", "List-Unsubscribe"],
                    ),
                    request_id=mid,
                )
            _execute_with_retry(batch)
            if len(retry) > limited_before:
                pause = min(FETCH_PAUSE_MAX_SECONDS, pause * 1.5)
                logger.warning(
                    "Gmail rate-limited %d of %d reads; next batch in %.1fs",
                    len(retry) - limited_before, len(chunk), pause,
                )
            else:
                pause = max(FETCH_PAUSE_SECONDS, pause * 0.5)
            on_progress(done, len(ids))
            # Pace from the start of the batch, so the request time counts toward the interval
            time.sleep(max(0.0, pause - (time.monotonic() - started)))
        pending = retry
        rounds += 1
        if pending:
            logger.warning("Retrying %d rate-limited reads (round %d)", len(pending), rounds + 1)
            time.sleep(5 * rounds)
    return messages


def _to_email_meta(message: dict) -> EmailMeta:
    headers = message.get("payload", {}).get("headers", [])
    name, address = get_sender_info(headers)
    subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "")
    has_unsub = any(h["name"].lower() == "list-unsubscribe" for h in headers)
    categories = {
        lid[len("CATEGORY_") :].lower()
        for lid in message.get("labelIds", [])
        if lid.startswith("CATEGORY_")
    }
    return EmailMeta(
        sender_email=address.lower(),
        sender_name=name,
        subject=subject,
        snippet=message.get("snippet", ""),
        categories=categories,
        has_unsubscribe=has_unsub,
    )


def _llm_cache_key(email: EmailMeta) -> tuple[str, str]:
    # Same sender + same subject shape (digits removed) -> same answer
    return email.sender_email, re.sub(r"\d+", "#", email.subject.lower()).strip()


def _label_map(service) -> dict[str, str]:
    labels = _execute_with_retry(service.users().labels().list(userId="me")).get("labels", [])
    return {lbl["name"]: lbl["id"] for lbl in labels}


def organize_scan_background(
    limit: int = 1000,
    filters: Optional[dict] = None,
    use_llm: bool = True,
    recheck_left_alone: bool = False,
) -> None:
    """Classify emails and store a preview plan in state (no changes to Gmail).

    Emails left alone by earlier scans are skipped unless `recheck_left_alone` is set.
    """
    state.reset_organize()
    status = state.organize_status
    status["running"] = True
    status["message"] = "Connecting to Gmail..."

    service, error = get_gmail_service()
    if error:
        status.update(error=error, done=True, running=False)
        return

    try:
        label_ids = _label_map(service)
        managed_ids = {lid for name, lid in label_ids.items() if is_managed_label(name)}

        # Leave out already organized mail before reading anything (listing ids is cheap,
        # reading each email's details is the slow, quota-heavy part).
        already_organized = _list_labelled_ids(service, sorted(managed_ids))
        left_alone = _load_left_alone()
        query = " ".join(p for p in (build_gmail_query(filters), BASE_EXCLUDE) if p)
        ids, skipped_at_list, remembered = _list_message_ids(
            service, query, limit, already_organized, None if recheck_left_alone else left_alone
        )
        if not ids:
            status.update(
                skipped=skipped_at_list,
                remembered=remembered,
                message=(
                    f"Nothing new to sort: {skipped_at_list:,} emails already organized, "
                    f"{remembered:,} left alone in earlier runs."
                    if skipped_at_list or remembered
                    else "No emails found"
                ),
                progress=100,
                done=True,
                running=False,
            )
            return

        def on_fetch(done: int, total: int) -> None:
            status["progress"] = int(done / total * 50)
            status["message"] = f"Reading emails... {done:,}/{total:,}"

        messages = _fetch_metadata(service, ids, on_fetch)

        plan: dict[str, list[str]] = {}
        details: dict[str, dict] = {}
        unresolved: list[tuple[str, EmailMeta]] = []
        skipped = skipped_at_list
        kept = 0
        newly_left_alone: set[str] = set()

        def assign(label: str, mid: str, email: EmailMeta, source: str) -> None:
            plan.setdefault(label, []).append(mid)
            d = details.setdefault(
                label, {"senders": Counter(), "samples": [], "by_rule": 0, "by_llm": 0}
            )
            d["senders"][email.sender_email] += 1
            d["by_rule" if source == "rule" else "by_llm"] += 1
            shown = sum(1 for s in d["samples"] if s["by"] == source)
            if shown < (5 if source == "rule" else 25):
                d["samples"].append({"from": email.sender_email, "subject": email.subject[:120], "by": source})

        for message in messages:
            if managed_ids.intersection(message.get("labelIds", [])):
                skipped += 1
                continue
            email = _to_email_meta(message)
            label = classify_by_rules(email)
            if label == KEEP:
                kept += 1
                newly_left_alone.add(message["id"])
            elif label:
                assign(label, message["id"], email, "rule")
            else:
                unresolved.append((message["id"], email))

        llm_used = use_llm and llm.is_enabled()
        llm_error = None
        unlabeled = 0
        if llm_used and unresolved:
            memory = _load_ai_memory()
            new_answers = 0
            cache: dict[tuple[str, str], Optional[str]] = {}
            consecutive_errors = 0
            for i, (mid, email) in enumerate(unresolved, 1):
                status["progress"] = 50 + int(i / len(unresolved) * 48)
                status["message"] = f"AI sorting the unclear ones... {i:,}/{len(unresolved):,}"
                key = _llm_cache_key(email)
                if mid in memory:
                    # "" means the AI already chose to leave this email alone
                    label = memory[mid] if memory[mid] in llm.CATEGORIES.values() else None
                elif key in cache:
                    label = cache[key]
                else:
                    label, err = llm.classify_with_llm(email)
                    if err:
                        consecutive_errors += 1
                        llm_error = err
                        if consecutive_errors >= LLM_MAX_CONSECUTIVE_ERRORS:
                            unlabeled += len(unresolved) - i + 1
                            break
                        unlabeled += 1
                        continue
                    consecutive_errors = 0
                    cache[key] = label
                if mid not in memory:
                    memory[mid] = label or ""
                    new_answers += 1
                    if new_answers % AI_MEMORY_SAVE_EVERY == 0:
                        _save_ai_memory(memory)
                if label:
                    assign(label, mid, email, "llm")
                else:
                    # Only a real AI "leave alone" answer is remembered; errors and
                    # rules-only runs are not, so a later AI run still looks at them.
                    newly_left_alone.add(mid)
                    unlabeled += 1
            if new_answers:
                _save_ai_memory(memory)
        else:
            unlabeled = len(unresolved)

        # Remember what this scan left alone; drop anything it now wants to label
        # (a re-check after a rule update may have found a label for it).
        planned_ids = {mid for mids in plan.values() for mid in mids}
        updated_left_alone = (left_alone - planned_ids) | newly_left_alone
        if updated_left_alone != left_alone:
            _save_left_alone(updated_left_alone)

        state.organize_plan = plan
        state.organize_results = sorted(
            (
                {
                    "label": label,
                    "count": len(plan[label]),
                    "by_rule": d["by_rule"],
                    "by_llm": d["by_llm"],
                    "top_senders": [{"email": s, "count": c} for s, c in d["senders"].most_common(5)],
                    "samples": d["samples"],
                }
                for label, d in details.items()
            ),
            key=lambda r: r["label"],
        )
        total_planned = sum(len(v) for v in plan.values())
        status.update(
            progress=100,
            done=True,
            running=False,
            scanned=len(messages),
            planned=total_planned,
            skipped=skipped,
            remembered=remembered,
            unlabeled=unlabeled + kept,
            kept=kept,
            llm_used=llm_used,
            llm_error=llm_error,
            message=(
                f"Preview ready: {total_planned:,} of {len(messages):,} emails sorted into "
                f"{len(plan)} labels. {unlabeled + kept:,} left alone ({kept:,} protected: orders, security, bookings), "
                f"{skipped:,} already organized"
                + (f", {remembered:,} skipped as left alone in earlier runs." if remembered else ".")
            ),
        )
    except Exception as e:
        logger.exception("Organize scan failed")
        status.update(error=str(e), done=True, running=False)


def _ensure_label(service, name: str, label_ids: dict[str, str]) -> str:
    """Create `name` and its parents (Gmail nests on '/') if missing; return its id."""
    parts = name.split("/")
    for depth in range(1, len(parts) + 1):
        path = "/".join(parts[:depth])
        if path in label_ids:
            continue
        body = {"name": path, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        try:
            created = _execute_with_retry(service.users().labels().create(userId="me", body=body))
            label_ids[path] = created["id"]
        except Exception as e:
            if "exists" not in str(e).lower():
                raise
            label_ids.update(_label_map(service))
    return label_ids[name]


def organize_apply_background(labels: Optional[list[str]] = None, archive: bool = False) -> None:
    """Apply the stored plan. `labels` limits which labels to apply; `archive` removes from Inbox."""
    status = state.organize_status
    plan = state.organize_plan
    chosen = [lbl for lbl in (labels if labels is not None else list(plan)) if lbl in plan]

    status.update(running=True, done=False, error=None, progress=0, applied=0)
    if not chosen:
        status.update(message="Nothing to apply - run a preview first", done=True, running=False)
        return

    service, error = get_gmail_service()
    if error:
        status.update(error=error, done=True, running=False)
        return

    try:
        label_ids = _label_map(service)
        total = sum(len(plan[lbl]) for lbl in chosen)
        applied = 0
        for label in chosen:
            status["message"] = f"Labelling '{label}'..."
            label_id = _ensure_label(service, label, label_ids)
            ids = plan[label]
            for start in range(0, len(ids), MODIFY_BATCH):
                chunk = ids[start : start + MODIFY_BATCH]
                body = {"ids": chunk, "addLabelIds": [label_id]}
                if archive:
                    body["removeLabelIds"] = ["INBOX"]
                _execute_with_retry(service.users().messages().batchModify(userId="me", body=body))
                applied += len(chunk)
                status["applied"] = applied
                status["progress"] = int(applied / total * 100)
                time.sleep(0.3)

        for label in chosen:
            plan.pop(label, None)
        state.organize_results = [r for r in state.organize_results if r["label"] not in chosen]
        status.update(
            progress=100,
            done=True,
            running=False,
            message=f"Done: labelled {applied:,} emails across {len(chosen)} labels"
            + (" and moved them out of the Inbox." if archive else "."),
        )
    except Exception as e:
        logger.exception("Organize apply failed")
        status.update(error=str(e), done=True, running=False)


def get_organize_status() -> dict:
    return state.organize_status.copy()


def get_organize_results() -> list:
    return state.organize_results.copy()
