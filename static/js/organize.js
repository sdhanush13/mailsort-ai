/**
 * Gmail Cleaner - Auto-Organize Module
 * Preview how emails would be sorted into labels, then apply the chosen ones.
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.Organize = {
    results: [],
    phase: null, // 'scan' | 'apply'

    async checkLlm() {
        const el = document.getElementById('organizeLlmStatus');
        const toggle = document.getElementById('organizeUseLlm');
        if (!el) return;
        el.textContent = 'Checking AI...';
        el.className = 'organize-llm-status';
        try {
            const res = await fetch('/api/llm-status');
            const data = await res.json();
            if (data.ok) {
                el.textContent = `AI ready (${data.model})`;
                el.classList.add('ok');
                toggle.disabled = false;
            } else {
                el.textContent = data.enabled ? `AI unavailable: ${data.error}` : 'AI off (rules only) - set LLM_BASE_URL to enable';
                el.classList.add('warn');
                toggle.checked = false;
                toggle.disabled = !data.enabled;
            }
        } catch (e) {
            el.textContent = 'AI status unknown';
            el.classList.add('warn');
        }
    },

    setupScope() {
        const range = document.getElementById('organizeRange');
        document.querySelectorAll('input[name="organizeScope"]').forEach(radio => {
            radio.addEventListener('change', () => {
                range.classList.toggle('hidden', this.scope() !== 'range');
            });
        });
    },

    scope() {
        const checked = document.querySelector('input[name="organizeScope"]:checked');
        return checked ? checked.value : 'all';
    },

    // Build Gmail date filters from the From/To pickers (To is inclusive).
    rangeFilters() {
        const from = document.getElementById('organizeFrom').value;
        const to = document.getElementById('organizeTo').value;
        if (!from && !to) return { error: 'Pick a From date, a To date, or both.' };
        if (from && to && from > to) return { error: 'From date must be on or before the To date.' };

        const filters = {};
        if (from) filters.after_date = from.replace(/-/g, '/');
        if (to) {
            // Gmail's before: is exclusive, so add one day to include the To date
            const d = new Date(to + 'T00:00:00');
            d.setDate(d.getDate() + 1);
            const pad = n => String(n).padStart(2, '0');
            filters.before_date = `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
        }
        return { filters };
    },

    async startScan() {
        const useLlm = document.getElementById('organizeUseLlm').checked;
        const recheck = document.getElementById('organizeRecheck').checked;
        let filters = null;
        if (this.scope() === 'range') {
            const range = this.rangeFilters();
            if (range.error) {
                GmailCleaner.UI.showInfoToast(range.error);
                return;
            }
            filters = range.filters;
        }

        this.setBusy(true, 'Previewing...');
        document.getElementById('organizeResultsCard').classList.add('hidden');
        try {
            const res = await fetch('/api/organize-scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ limit: 0, use_llm: useLlm, recheck_left_alone: recheck, filters })
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || res.statusText);
            }
            this.phase = 'scan';
            this.poll();
        } catch (e) {
            GmailCleaner.UI.showErrorToast('Error: ' + e.message);
            this.setBusy(false);
        }
    },

    async apply() {
        const labels = [...document.querySelectorAll('.organize-label-check:checked')].map(c => c.value);
        if (labels.length === 0) {
            GmailCleaner.UI.showInfoToast('Select at least one label to apply.');
            return;
        }
        const archive = document.getElementById('organizeArchive').checked;
        const total = this.results.filter(r => labels.includes(r.label)).reduce((n, r) => n + r.count, 0);
        const msg = `Apply ${labels.length} label(s) to ${total.toLocaleString()} emails` +
            (archive ? ' and move them out of the Inbox?' : '?');
        if (!confirm(msg)) return;

        this.setBusy(true, 'Applying...');
        try {
            const res = await fetch('/api/organize-apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ labels, archive })
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || res.statusText);
            }
            this.phase = 'apply';
            this.poll();
        } catch (e) {
            GmailCleaner.UI.showErrorToast('Error: ' + e.message);
            this.setBusy(false);
        }
    },

    async poll() {
        try {
            const res = await fetch('/api/organize-status');
            const s = await res.json();
            document.getElementById('organizeProgressCard').classList.remove('hidden');
            document.getElementById('organizeProgressBar').style.width = s.progress + '%';
            document.getElementById('organizeProgressText').textContent = s.message;

            if (!s.done) {
                setTimeout(() => this.poll(), 700);
                return;
            }
            this.setBusy(false);
            if (s.error) {
                GmailCleaner.UI.showErrorToast('Error: ' + s.error);
                return;
            }
            if (s.llm_error) {
                GmailCleaner.UI.showInfoToast('AI had trouble (' + s.llm_error + '). Unclear emails were left alone.');
            }
            if (this.phase === 'apply') {
                GmailCleaner.UI.showSuccessToast(s.message);
            }
            await this.loadResults();
        } catch (e) {
            setTimeout(() => this.poll(), 1000);
        }
    },

    async loadResults() {
        const res = await fetch('/api/organize-results');
        this.results = await res.json();
        this.render();
    },

    render() {
        const card = document.getElementById('organizeResultsCard');
        const list = document.getElementById('organizeResultsList');
        const esc = GmailCleaner.UI.escapeHtml;

        if (!this.results.length) {
            card.classList.add('hidden');
            return;
        }
        card.classList.remove('hidden');
        list.innerHTML = this.results.map((r, i) => {
            const senders = r.top_senders.map(s => `${esc(s.email)} (${s.count})`).join(', ');
            const samples = r.samples.map(s =>
                `<li><span class="organize-by organize-by-${s.by}">${s.by === 'llm' ? 'AI' : 'rule'}</span>
                 <span class="organize-sample-from">${esc(s.from)}</span> — ${esc(s.subject)}</li>`
            ).join('');
            const isDelete = r.label === 'To Delete';
            return `
            <div class="organize-row">
                <label class="checkbox-wrapper">
                    <input type="checkbox" class="organize-label-check" value="${esc(r.label)}" checked>
                    <span class="checkmark"></span>
                </label>
                <div class="organize-row-main">
                    <div class="organize-row-title">
                        <span class="organize-label-chip ${isDelete ? 'danger' : ''}">${esc(r.label)}</span>
                        <strong>${r.count.toLocaleString()}</strong> emails
                        <span class="organize-split">${r.by_rule.toLocaleString()} by rules · ${r.by_llm.toLocaleString()} by AI</span>
                    </div>
                    <div class="organize-senders">${senders}</div>
                    <details>
                        <summary>Sample emails</summary>
                        <ul class="organize-samples">${samples}</ul>
                    </details>
                </div>
            </div>`;
        }).join('');
    },

    toggleAll(checked) {
        document.querySelectorAll('.organize-label-check').forEach(c => { c.checked = checked; });
    },

    setBusy(busy, text) {
        ['organizeScanBtn', 'organizeApplyBtn'].forEach(id => {
            const btn = document.getElementById(id);
            if (!btn) return;
            btn.disabled = busy;
            if (!btn.dataset.label) btn.dataset.label = btn.textContent.trim();
            btn.textContent = busy && text ? text : btn.dataset.label;
        });
    }
};

function startOrganizeScan() { GmailCleaner.Organize.startScan(); }
function applyOrganize() { GmailCleaner.Organize.apply(); }
