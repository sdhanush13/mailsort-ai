"""
Tests for Auto-Organize: rules, LLM reply parsing, scan/apply flow and API.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.core import state
from app.services.gmail import llm, organize
from app.services.gmail.organize_rules import (
    LABEL_BANK_ALERTS,
    LABEL_BANK_OFFERS,
    LABEL_INVEST_STATEMENTS,
    LABEL_JOB_ALERTS,
    LABEL_JOB_APPLICATIONS,
    LABEL_TO_DELETE,
    KEEP,
    EmailMeta,
    classify_by_rules,
    is_managed_label,
)


def meta(sender, subject="", cats=(), unsub=False, name=""):
    return EmailMeta(
        sender_email=sender, sender_name=name, subject=subject,
        categories=set(cats), has_unsubscribe=unsub,
    )


class TestBankStatements:
    @pytest.mark.parametrize(
        "sender,subject,expected",
        [
            ("estatement@icicibank.com", "ICICI Bank Statement from February 01, 2026 to February 28, 2026 for XXXXXXXX0435", "Banks/ICICI/Savings"),
            ("estatement@icici.bank.in", "ICICI Bank Statement from October 01, 2026 to October 31, 2026", "Banks/ICICI/Savings"),
            ("credit_cards@icicibank.com", "Amazon Pay ICICI Bank Credit Card Statement for the period", "Banks/ICICI/Credit Card"),
            ("estatements@aubank.in", "Your XCITE ULTRA Credit Card Statement -October 2026 is here!", "Banks/AU/Credit Card"),
            ("estatements@info.aubank.in", "Your XciteUltra Credit Card Statement -  February 2025 is here!", "Banks/AU/Credit Card"),
            ("bankstatements@kotak.bank.in", "Your September 2026 statement for Kotak A/c X8706", "Banks/Kotak/Savings"),
            ("cardstatement@kotak.com", "Credit Card Statement Mar-24", "Banks/Kotak/Credit Card"),
            ("estatements@indusind.com", "Your account statement for the month of September 2026", "Banks/IndusInd/Savings"),
            ("creditcard.estatements@indusind.com", "Your IndusInd Bank Credit Card statement for period", "Banks/IndusInd/Credit Card"),
            ("emailstatements.cards@hdfcbank.bank.in", "Your HDFC Bank - Swiggy HDFC Bank Credit Card Statement - November-2026", "Banks/HDFC/Credit Card"),
            ("estatement@yes.bank.in", "Your YES_BANK_Klick Credit Card E-Statement", "Banks/YES/Credit Card"),
            ("cc.statements@axis.bank.in", "Your Axis Bank Neo Rupay Credit Card Statement ending XX00", "Banks/Axis/Credit Card"),
            ("statement@getonecard.app", "SIB One Credit Card statement for October 2026", "Banks/OneCard/Credit Card"),
            ("statement@idfcfirst.bank.in", "Your FIRST Wealth Credit Card Statement", "Banks/IDFC FIRST/Credit Card"),
            ("alerts.in@sc.com", "Your Standard Chartered Credit Card statement is now generated.", "Banks/Standard Chartered/Credit Card"),
        ],
    )
    def test_statement_routing(self, sender, subject, expected):
        assert classify_by_rules(meta(sender, subject, ["updates"])) == expected

    def test_non_bank_lookalikes_are_not_banks(self):
        # ICICI Lombard / Prudential are insurers, not ICICI Bank
        assert classify_by_rules(meta("communication@iciciprulife.com", "Save up to 25% today")) is None

    def test_key_fact_statement_is_not_a_statement(self):
        email = meta("services@custcomm.icicibank.com", "Review Key Fact Statement before availing a loan", ["updates"])
        assert classify_by_rules(email) == LABEL_BANK_OFFERS


class TestBankOffersAndAlerts:
    @pytest.mark.parametrize(
        "sender,subject,cats",
        [
            ("credit_cards@connect.aubank.in", "Shubh Dhanteras with ₹20,000* Voucher", ["promotions"]),
            ("info@digital.axisbankmail.bank.in", "Up to 20%* Off on Your Favourite Electronics Brands", ["promotions"]),
            ("alwaysyoufirst@emailers.idfcfirst.bank.in", "Congratulations! You are eligible…", ["promotions"]),
            ("ccoffers@yes.bank.in", "You are eligible to avail the Over Limit Facility!", ["promotions"]),
            ("mail@info.paytm.com", "Lifetime Free Paytm AU Credit Card", ["promotions"]),
            ("information@mailers.hdfcbank.bank.in", "Card xx7103: Higher Loan Eligibility Update", ["updates"]),
        ],
    )
    def test_offers(self, sender, subject, cats):
        assert classify_by_rules(meta(sender, subject, cats)) == LABEL_BANK_OFFERS

    @pytest.mark.parametrize(
        "sender,subject",
        [
            ("credit_cards@icici.bank.in", "Transaction alert for your ICICI Bank Credit Card"),
            ("noreply@idfcfirstbank.com", "Payment received for IDFC FIRST Credit Card ending XX5556"),
            ("alerts@yes.bank.in", "YES BANK - Transaction Alert"),
            ("protect@cred.club", "Important : New smart statement for your HDFC Bank card XXXX-7103 is here"),
            ("billreminder@kiwiclub.co.in", "Your HDFC Bank Credit Card Bill is due on 21-11-2026"),
            ("indusind_bank@indusind.com", "Scheduled Maintenance Activity for UPI Services"),
        ],
    )
    def test_alerts(self, sender, subject):
        assert classify_by_rules(meta(sender, subject, ["updates"])) == LABEL_BANK_ALERTS

    def test_impersonator_goes_to_delete(self):
        email = meta("update@banking2day.com", "Update: Your HDFC Bank credit card options", ["updates"], name="HDFC Bank credit card")
        assert classify_by_rules(email) == LABEL_TO_DELETE


class TestInvestments:
    @pytest.mark.parametrize(
        "sender,subject",
        [
            ("ecas@cdslstatement.com", "CDSL Consolidated Account Statement (CAS) across Mutual Funds"),
            ("noreply@groww.in", "Transaction and Holding Statement"),
            ("enq_sbimf@camsonline.com", "Statement of Account for Folio No. XXXXX362 in SBI Mutual Fund"),
            ("nps-statements@mailer.proteantech.in", "Monthly Transaction Statement of your NPS account"),
            ("donotreply@transactions.upstox.com", "Monthly Demat Account Statement for August 2026"),
        ],
    )
    def test_investment_statements(self, sender, subject):
        assert classify_by_rules(meta(sender, subject, ["updates"])) == LABEL_INVEST_STATEMENTS

    @pytest.mark.parametrize(
        "sender,subject",
        [
            ("noreply@campaigns.upstox.com", "It's here! Jio Platforms IPO is LIVE"),
            ("communication@quant.in", "quant Silver ETF FoF - NFO Opens Today"),
            ("donotreply_sbimf@camsonline.com", "Change in Base Expense Ratio of SBI Large Cap Fund"),
        ],
    )
    def test_investment_marketing_is_clutter(self, sender, subject):
        assert classify_by_rules(meta(sender, subject, ["updates"], unsub=True)) == LABEL_TO_DELETE


class TestKeep:
    """Important mail must be left alone and never reach the LLM."""

    @pytest.mark.parametrize(
        "sender,subject",
        [
            ("no-reply@accounts.google.com", "Security alert for srohan1999@gmail.com"),
            ("noreply@github.com", "[ACTION REQUIRED] Your GitHub account will soon require 2FA"),
            ("support@railway.app", "Urgent: Railway Security Disclosure - Rotate Credentials"),
            ("noreply@supabase.com", "Action required: security vulnerabilities detected in your projects"),
            ("security@mail.instagram.com", "New login to Instagram from Instagram on GM1911"),
            ("order-update@amazon.in", "Delivered: Seethalakshmi's Pickles..."),
            ("return@amazon.in", "Your refund for DEBIRE Steel-Plastic Kitchen Gas"),
            ("contactus@viaterragear.com", "Shipping update for order #456553"),
            ("ticketadmin@irctc.co.in", "Booking Confirmation on IRCTC, Train: 12295"),
            ("noreply.irctc@royalsundaram.in", "Your Travel Policy is issued for PNR No.4659919783"),
            ("dhanush@micglobal.com", "Fw: Payslip for September 2026"),
            ("donotreply@transactions.upstox.com", "OTP to login"),
        ],
    )
    def test_protected(self, sender, subject):
        assert classify_by_rules(meta(sender, subject, ["updates"], unsub=True)) == KEEP

    def test_bank_alerts_are_not_swallowed_by_keep(self):
        email = meta("customernotification@icici.bank.in", "Grid Card Authentication (GCA) attempt has failed", ["personal"])
        assert classify_by_rules(email) == LABEL_BANK_ALERTS

    def test_amazon_pay_is_a_bank_alert(self):
        assert classify_by_rules(meta("no-reply@amazonpay.in", "Payment Reminder", ["updates"])) == LABEL_BANK_ALERTS

    def test_marketing_newsletter_with_unsubscribe(self):
        email = meta("noreply@zen-makemytrip.com", "FLASH SALE ON DOMESTIC FLIGHTS", ["updates"], unsub=True)
        assert classify_by_rules(email) == LABEL_TO_DELETE

    def test_company_hiring_drive_is_job_alert(self):
        email = meta("workwithus@join.capgemini.com", "Mega Azure Integration Hiring Drive at Capgemini", ["updates"], unsub=True)
        assert classify_by_rules(email) == LABEL_JOB_ALERTS


class TestJobs:
    @pytest.mark.parametrize(
        "sender,subject",
        [
            ("info@naukri.com", "You applied for 1 job on 07 Oct"),
            ("job@monsterindia.com", "Kaygen Consulting Application Successful | Ace your interview"),
            ("noreply-zf-recruiting@successfactors.eu", "Your application at ZF"),
            ("equiniti@myworkday.com", "Thank you for your application - *Do Not Reply*"),
            ("r.balakrishnancgvyzmljawvudc5jb20=@naukri.com", "Job | Senior Software Engineer in Bengaluru"),
        ],
    )
    def test_applications(self, sender, subject):
        assert classify_by_rules(meta(sender, subject, ["updates"])) == LABEL_JOB_APPLICATIONS

    @pytest.mark.parametrize(
        "sender,subject",
        [
            ("noreply@glassdoor.com", "Senior .NET Developer at INNOBAYT and 5 more jobs in Dubai for you. Apply Now."),
            ("jobs-noreply@linkedin.com", "Brady Technologies is hiring for Remote"),
            ("info@hirist.tech", "10+ Matching Jobs based on your preferences"),
            ("jobmessenger@monsterindia.com", "10 jobs that you haven't applied yet but others are applying"),
            ("newsletters-noreply@linkedin.com", "New India Jobs Update - 05th Nov 2026"),
            ("reviews@ambitionbox.com", "You applied to Travtech Software - see what employees have to say!"),
        ],
    )
    def test_alerts(self, sender, subject):
        assert classify_by_rules(meta(sender, subject, ["updates"])) == LABEL_JOB_ALERTS

    def test_unknown_recruiter_is_left_for_llm(self):
        email = meta("lalita.rajbhar@capgemini.com", "Capgemini F2F Interview Confirmation – Azure Integration", ["personal"])
        assert classify_by_rules(email) is None


class TestNoiseAndFallthrough:
    def test_promotions_category(self):
        assert classify_by_rules(meta("promotions@99rpm.com", "Ready for the Extra Visits", ["promotions"], True)) == LABEL_TO_DELETE

    def test_linkedin_social(self):
        assert classify_by_rules(meta("messages-noreply@linkedin.com", "Someone you may know", ["social"])) == LABEL_TO_DELETE

    def test_easemytrip_marketing(self):
        assert classify_by_rules(meta("connect@easemytrip.com", "2 Days Left!!!", ["updates"])) == LABEL_TO_DELETE

    def test_easemytrip_booking_is_kept(self):
        assert classify_by_rules(meta("noreply@easemytrip.com", "Booking Confirmed - Trip ID 123", ["updates"])) == KEEP

    def test_personal_mail_is_untouched(self):
        assert classify_by_rules(meta("friend@gmail.com", "Dinner tomorrow?", ["personal"])) is None

    def test_managed_labels(self):
        assert is_managed_label("Banks/ICICI/Savings")
        assert is_managed_label("To Delete")
        assert not is_managed_label("Banks")
        assert not is_managed_label("Receipts")


class TestLlmParsing:
    @pytest.mark.parametrize(
        "reply,expected",
        [
            ('{"category": "job_application"}', "job_application"),
            ('```json\n{"category": "bank_offer"}\n```', "bank_offer"),
            ("The answer is promotion.", "promotion"),
            ('{"category": "nonsense"}', None),
            ("", None),
        ],
    )
    def test_parse_category(self, reply, expected):
        assert llm.parse_category(reply) == expected

    def test_classify_maps_to_label(self, monkeypatch):
        monkeypatch.setattr(llm.settings, "llm_base_url", "http://llm.test/v1")
        reply = {"choices": [{"message": {"content": '{"category": "job_application"}'}}]}
        with patch.object(llm, "_post", return_value=reply) as post:
            label, err = llm.classify_with_llm(meta("hr@acme.com", "Interview invite"))
        assert (label, err) == (LABEL_JOB_APPLICATIONS, None)
        sent = post.call_args.args[1]["messages"][1]["content"]
        assert "hr@acme.com" in sent and "Interview invite" in sent

    def test_job_label_without_job_signal_is_rejected(self):
        reply = {"choices": [{"message": {"content": '{"category": "job_alert"}'}}]}
        with patch.object(llm, "_post", return_value=reply):
            email = meta("notify@mg.petsmatefinder.com", "Dhanush S, Found matches for your Pet")
            assert llm.classify_with_llm(email) == (None, None)

    def test_other_maps_to_none(self, monkeypatch):
        reply = {"choices": [{"message": {"content": '{"category": "other"}'}}]}
        with patch.object(llm, "_post", return_value=reply):
            assert llm.classify_with_llm(meta("a@b.com", "hi")) == (None, None)

    def test_disabled_without_base_url(self, monkeypatch):
        monkeypatch.setattr(llm.settings, "llm_base_url", "")
        assert llm.check_llm()["enabled"] is False


def _message(mid, sender, subject, labels=("CATEGORY_UPDATES",), unsub=False):
    headers = [{"name": "From", "value": f"X <{sender}>"}, {"name": "Subject", "value": subject}]
    if unsub:
        headers.append({"name": "List-Unsubscribe", "value": "<https://x/u>"})
    return {"id": mid, "labelIds": list(labels), "snippet": "", "payload": {"headers": headers}}


class TestScanAndApply:
    @pytest.fixture(autouse=True)
    def _fast(self, monkeypatch):
        monkeypatch.setattr(organize, "FETCH_PAUSE_SECONDS", 0)
        monkeypatch.setattr(organize.time, "sleep", lambda *_: None)
        state.reset_organize()

    def _run_scan(self, messages, labels=(), llm_label=None, use_llm=True, llm_enabled=True,
                  memory=None, skipped_at_list=0):
        service = MagicMock()
        self.saved_memory = None

        def save(mem):
            self.saved_memory = dict(mem)

        with patch.object(organize, "get_gmail_service", return_value=(service, None)), \
             patch.object(organize, "_label_map", return_value=dict(labels)), \
             patch.object(organize, "_list_labelled_ids", return_value=set()), \
             patch.object(organize, "_list_message_ids",
                          return_value=([m["id"] for m in messages], skipped_at_list)), \
             patch.object(organize, "_fetch_metadata", return_value=messages) as fetch, \
             patch.object(organize, "_load_ai_memory", return_value=dict(memory or {})), \
             patch.object(organize, "_save_ai_memory", side_effect=save), \
             patch.object(organize.llm, "is_enabled", return_value=llm_enabled), \
             patch.object(organize.llm, "classify_with_llm", return_value=(llm_label, None)) as clf:
            organize.organize_scan_background(100, None, use_llm)
        self.fetch = fetch
        return clf

    def test_ai_answers_are_remembered_across_runs(self):
        msgs = [
            _message("1", "hr@acme.com", "Lunch plans", ["CATEGORY_PERSONAL"]),
            _message("2", "promo@shop.example", "Hello there", ["CATEGORY_PERSONAL"]),
        ]
        clf = self._run_scan(msgs, llm_label=None)
        assert clf.call_count == 2
        assert self.saved_memory == {"1": "", "2": ""}

        # Next run: both answers come from memory, the AI is not called again
        clf = self._run_scan(msgs, llm_label=LABEL_TO_DELETE, memory={"1": "", "2": LABEL_TO_DELETE})
        assert clf.call_count == 0
        assert state.organize_plan == {LABEL_TO_DELETE: ["2"]}

    def test_nothing_new_to_sort(self):
        self._run_scan([], skipped_at_list=500)
        assert self.fetch.call_count == 0
        assert state.organize_status["skipped"] == 500
        assert "already organized" in state.organize_status["message"]

    def test_already_organized_count_includes_list_time_skips(self):
        msgs = [_message("1", "noreply@glassdoor.com", "5 more jobs for you")]
        self._run_scan(msgs, skipped_at_list=40)
        assert state.organize_status["skipped"] == 40

    def test_scan_builds_plan_without_modifying(self):
        msgs = [
            _message("1", "estatement@icicibank.com", "ICICI Bank Statement from Jan"),
            _message("2", "noreply@glassdoor.com", "5 more jobs for you"),
            _message("3", "friend@gmail.com", "Lunch?", ["CATEGORY_PERSONAL"]),
        ]
        clf = self._run_scan(msgs, llm_label=None)
        assert state.organize_plan == {"Banks/ICICI/Savings": ["1"], LABEL_JOB_ALERTS: ["2"]}
        assert state.organize_status["done"] and state.organize_status["unlabeled"] == 1
        assert clf.call_count == 1  # only the unclear email reached the LLM

    def test_llm_results_are_cached_per_sender_and_subject_shape(self):
        msgs = [
            _message("1", "hr@acme.com", "Interview on 12 Nov", ["CATEGORY_PERSONAL"]),
            _message("2", "hr@acme.com", "Interview on 13 Nov", ["CATEGORY_PERSONAL"]),
        ]
        clf = self._run_scan(msgs, llm_label=LABEL_JOB_APPLICATIONS)
        assert clf.call_count == 1
        assert state.organize_plan[LABEL_JOB_APPLICATIONS] == ["1", "2"]

    def test_already_organized_mail_is_skipped(self):
        msgs = [_message("1", "noreply@glassdoor.com", "jobs", ["Label_9"])]
        self._run_scan(msgs, labels={"Jobs/Alerts": "Label_9"})
        assert state.organize_plan == {}
        assert state.organize_status["skipped"] == 1

    def test_protected_mail_never_reaches_llm(self):
        msgs = [_message("1", "no-reply@accounts.google.com", "Security alert")]
        clf = self._run_scan(msgs, llm_label=LABEL_TO_DELETE)
        assert clf.call_count == 0 and state.organize_plan == {}
        assert state.organize_status["kept"] == 1

    def test_rules_only_when_llm_off(self):
        msgs = [_message("1", "hr@acme.com", "Interview", ["CATEGORY_PERSONAL"])]
        clf = self._run_scan(msgs, use_llm=False)
        assert clf.call_count == 0 and state.organize_status["unlabeled"] == 1

    def test_apply_creates_nested_labels_and_archives(self):
        state.organize_plan = {"Banks/ICICI/Savings": ["1", "2"], LABEL_TO_DELETE: ["3"]}
        service = MagicMock()
        created = iter(["L1", "L2", "L3"])
        service.users().labels().create.return_value.execute.side_effect = lambda: {"id": next(created)}
        with patch.object(organize, "get_gmail_service", return_value=(service, None)), \
             patch.object(organize, "_label_map", return_value={}):
            organize.organize_apply_background(["Banks/ICICI/Savings"], archive=True)

        names = [c.kwargs["body"]["name"] for c in service.users().labels().create.call_args_list]
        assert names == ["Banks", "Banks/ICICI", "Banks/ICICI/Savings"]
        body = service.users().messages().batchModify.call_args.kwargs["body"]
        assert body == {"ids": ["1", "2"], "addLabelIds": ["L3"], "removeLabelIds": ["INBOX"]}
        assert state.organize_plan == {LABEL_TO_DELETE: ["3"]}  # unapplied label kept
        assert state.organize_status["applied"] == 2


def _paged_list_service(pages_by_call):
    """Fake service whose messages().list(**params).execute() returns pages in order."""
    service = MagicMock()
    pages = iter(pages_by_call)
    calls = []

    def list_(**params):
        calls.append(params)
        req = MagicMock()
        req.execute.return_value = next(pages)
        return req

    service.users.return_value.messages.return_value.list.side_effect = list_
    return service, calls


class TestListing:
    def test_excluded_ids_are_skipped_and_counted(self):
        service, _ = _paged_list_service([
            {"messages": [{"id": "1"}, {"id": "2"}], "nextPageToken": "p2"},
            {"messages": [{"id": "3"}, {"id": "4"}]},
        ])
        ids, excluded = organize._list_message_ids(service, "q", 0, exclude={"2", "3"})
        assert ids == ["1", "4"] and excluded == 2

    def test_limit_counts_only_new_ids(self):
        service, _ = _paged_list_service([
            {"messages": [{"id": "1"}, {"id": "2"}, {"id": "3"}], "nextPageToken": "p2"},
            {"messages": [{"id": "4"}, {"id": "5"}]},
        ])
        ids, excluded = organize._list_message_ids(service, "q", 2, exclude={"1", "2"})
        assert ids == ["3", "4"] and excluded == 2

    def test_labelled_ids_union_across_labels_and_pages(self):
        service, calls = _paged_list_service([
            {"messages": [{"id": "1"}], "nextPageToken": "p2"},
            {"messages": [{"id": "2"}]},
            {"messages": [{"id": "2"}, {"id": "3"}]},
        ])
        assert organize._list_labelled_ids(service, ["L1", "L2"]) == {"1", "2", "3"}
        assert [c["labelIds"] for c in calls] == [["L1"], ["L1"], ["L2"]]


class TestAiMemoryFile:
    def test_round_trip(self, tmp_path):
        with patch.object(organize, "_ai_memory_path", return_value=str(tmp_path / "m.json")):
            assert organize._load_ai_memory() == {}
            organize._save_ai_memory({"a": "", "b": LABEL_TO_DELETE})
            assert organize._load_ai_memory() == {"a": "", "b": LABEL_TO_DELETE}

    def test_corrupt_file_is_ignored(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text("{not json", encoding="utf-8")
        with patch.object(organize, "_ai_memory_path", return_value=str(path)):
            assert organize._load_ai_memory() == {}


class TestOrganizeApi:
    def test_scan_starts(self, client):
        state.reset_organize()
        with patch("app.api.actions.organize_scan_background"):
            res = client.post("/api/organize-scan", json={"limit": 500, "use_llm": False})
        assert res.status_code == 200 and res.json()["status"] == "started"

    def test_scan_rejects_negative_limit(self, client):
        assert client.post("/api/organize-scan", json={"limit": -1}).status_code == 422

    def test_apply_without_preview_is_rejected(self, client):
        state.reset_organize()
        assert client.post("/api/organize-apply", json={}).status_code == 400

    def test_conflict_while_running(self, client):
        state.reset_organize()
        state.organize_status["running"] = True
        assert client.post("/api/organize-scan", json={}).status_code == 409
        state.reset_organize()

    def test_status_and_results(self, client):
        state.reset_organize()
        assert client.get("/api/organize-status").json()["message"] == "Ready"
        assert client.get("/api/organize-results").json() == []
