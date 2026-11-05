"""
Auto-Organize
-------------
Sort emails into labels (bank statements, offers, alerts, investments, jobs,
"To Delete"). Two steps so nothing changes without review:

1. ``organize_scan_background`` - classify emails, store a plan (preview only).
2. ``organize_apply_background`` - create labels and apply the chosen parts of the plan.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from typing import Optional

from app.core import state
from app.services.auth import get_gmail_service
from app.services.gmail import llm
from app.services.gmail.helpers import build_gmail_query, get_sender_info
from app.services.gmail.organize_rules import KEEP, EmailMeta, classify_by_rules, is_managed_label

logger = logging.getLogger(__name__)

# Gmail allows 15,000 quota units/user/minute; messages.get costs 5.
# 100 gets per batch = 500 units -> pause ~2.1s between batches to stay under 250 units/s.
FETCH_BATCH = 100
FETCH_PAUSE_SECONDS = 2.1
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
            time.sleep(min(60, 5 * 2**attempt))


def _list_message_ids(service, query: str, limit: int) -> list[str]:
    ids: list[str] = []
    page_token = None
    while limit <= 0 or len(ids) < limit:
        params = {"userId": "me", "q": query, "maxResults": 500}
        if limit > 0:
            params["maxResults"] = min(500, limit - len(ids))
        if page_token:
            params["pageToken"] = page_token
        result = _execute_with_retry(service.users().messages().list(**params))
        ids.extend(m["id"] for m in result.get("messages", []))
        state.organize_status["message"] = f"Listing emails... {len(ids):,} found"
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return ids


def _fetch_metadata(service, ids: list[str], on_progress) -> list[dict]:
    """Fetch From/Subject/List-Unsubscribe, labels and snippet, throttled with retries."""
    messages: list[dict] = []
    pending = list(ids)
    rounds = 0
    done = 0
    while pending and rounds < 4:
        retry: list[str] = []
        for start in range(0, len(pending), FETCH_BATCH):
            chunk = pending[start : start + FETCH_BATCH]

            def callback(request_id, response, exception):
                nonlocal done
                if exception is not None:
                    if _is_rate_limited(exception):
                        retry.append(request_id)
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
            on_progress(done, len(ids))
            time.sleep(FETCH_PAUSE_SECONDS)
        pending = retry
        rounds += 1
        if pending:
            time.sleep(10 * rounds)
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


def organize_scan_background(limit: int = 1000, filters: Optional[dict] = None, use_llm: bool = True) -> None:
    """Classify emails and store a preview plan in state (no changes to Gmail)."""
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

        query = " ".join(p for p in (build_gmail_query(filters), BASE_EXCLUDE) if p)
        ids = _list_message_ids(service, query, limit)
        if not ids:
            status.update(message="No emails found", progress=100, done=True, running=False)
            return

        def on_fetch(done: int, total: int) -> None:
            status["progress"] = int(done / total * 50)
            status["message"] = f"Reading emails... {done:,}/{total:,}"

        messages = _fetch_metadata(service, ids, on_fetch)

        plan: dict[str, list[str]] = {}
        details: dict[str, dict] = {}
        unresolved: list[tuple[str, EmailMeta]] = []
        skipped = 0
        kept = 0

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
            elif label:
                assign(label, message["id"], email, "rule")
            else:
                unresolved.append((message["id"], email))

        llm_used = use_llm and llm.is_enabled()
        llm_error = None
        unlabeled = 0
        if llm_used and unresolved:
            cache: dict[tuple[str, str], Optional[str]] = {}
            consecutive_errors = 0
            for i, (mid, email) in enumerate(unresolved, 1):
                status["progress"] = 50 + int(i / len(unresolved) * 48)
                status["message"] = f"AI sorting the unclear ones... {i:,}/{len(unresolved):,}"
                key = _llm_cache_key(email)
                if key in cache:
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
                if label:
                    assign(label, mid, email, "llm")
                else:
                    unlabeled += 1
        else:
            unlabeled = len(unresolved)

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
            unlabeled=unlabeled + kept,
            kept=kept,
            llm_used=llm_used,
            llm_error=llm_error,
            message=(
                f"Preview ready: {total_planned:,} of {len(messages):,} emails sorted into "
                f"{len(plan)} labels. {unlabeled + kept:,} left alone ({kept:,} protected: orders, security, bookings), "
                f"{skipped:,} already organized."
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
