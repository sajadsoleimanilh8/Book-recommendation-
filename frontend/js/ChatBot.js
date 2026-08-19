
const API_BASE = 'http://localhost:8000';


const state = {
    userId: _getUserId(),
    bookId: null, 
    history: [], 
    isTyping: false,
};


function _getUserId() {
    let uid = localStorage.getItem('digikitab_user_id');
    if (!uid) {
        uid = 'user_' + Math.random().toString(36).slice(2, 10);
        localStorage.setItem('digikitab_user_id', uid);
    }
    return uid;
}


const QUICK_REPLIES = [
    { label: 'Recommend me a book', text: 'Recommend me a book' },
    { label: 'Fantasy suggestions', text: 'Give me fantasy book suggestions' },
    { label: 'Psychology reads', text: 'I want psychology books' },
    { label: 'Motivational books', text: 'Suggest motivational books' },
    { label: 'Romance novels', text: 'Give me romance novels' },
    { label: 'Thriller picks', text: 'I like thriller books' },
    { label: 'Make an audiobook', text: 'I want to listen to a book' },
    { label: 'View book comments', text: 'Show me the comments for this book' },
    { label: 'Set a reading reminder', text: 'Set a reading reminder for me' },
    { label: 'Books like Atomic Habits', text: 'Books similar to Atomic Habits' },
    { label: 'Emotional reads', text: 'I am sad suggest something emotional' },
    { label: 'Relaxing books', text: 'I want something relaxing to read' },
];

// ─── DOM helpers ──────────────────────────────────────────────────────────────
function _wrap() {
    return document.getElementById('chatMessages');
}

function _input() {
    return document.getElementById('chatInput');
}

function _send() {
    return document.getElementById('sendBtn');
}

// ─── Message rendering ────────────────────────────────────────────────────────
function appendMessage(content, role = 'bot', extra = null) {
    const wrap = _wrap();
    if (!wrap) return;
    const outer = document.createElement('div');
    outer.className = `message-row ${role}`;
    const bubble = document.createElement('div');
    bubble.className = `bubble ${role}`;
    if (typeof content === 'string') {
        // Convert newlines to <br> and bold **text**
        bubble.innerHTML = _formatText(content);
    } else {
        bubble.appendChild(content);
    }
    outer.appendChild(bubble);
    // Attach extra card (book list / action panel) below the bubble
    if (extra) {
        outer.appendChild(extra);
    }
    wrap.appendChild(outer);
    wrap.scrollTop = wrap.scrollHeight;
}

function _formatText(text) {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
}

// ─── Typing indicator ─────────────────────────────────────────────────────────
function showTyping() {
    const wrap = _wrap();
    if (!wrap || state.isTyping) return;
    state.isTyping = true;
    const row = document.createElement('div');
    row.className = 'message-row bot';
    row.id = 'typingIndicator';
    row.innerHTML = `<div class="typing-dots">...</div>`;
    wrap.appendChild(row);
    wrap.scrollTop = wrap.scrollHeight;
}

function hideTyping() {
    state.isTyping = false;
    const el = document.getElementById('typingIndicator');
    if (el) el.remove();
}

// ─── Book mini-cards (shown inside chat) ──────────────────────────────────────
function bookListEl(books) {
    if (!books || !books.length) return null;
    const container = document.createElement('div');
    container.className = 'chat-book-list';
    books.slice(0, 6).forEach((book, i) => {
        const card = document.createElement('div');
        card.className = 'chat-book-card';
        const rating = book.rating != null ? `<span class="rating">${Number(book.rating).toFixed(1)}</span>` : '';
        const mlScore = book.ml_score != null ? `<span class="ml-score">${(book.ml_score * 100).toFixed(0)}%</span>` : '';
        const audioBadge = book.audiobook ? `<span class="audio-badge">🎧</span>` : '';
        const srcBadge = book.source === 'gutenberg' ? `<span class="src-badge">Free</span>` : '';
        const price = book.price === 0 || book.price == null ? 'Free' : `$${Number(book.price).toFixed(2)}`;
        const thumbHtml = book.thumbnail ? `<img src="${book.thumbnail}" alt="${_esc(book.title)}">` : `<div class="no-img">📚</div>`;
        
        card.innerHTML = `
            <div class="card-header">#${i + 1}</div>
            ${thumbHtml}
            <div class="card-title">${_esc(book.title)}</div>
            <div class="card-author">${_esc(book.author)} · ${_esc(book.genre)}</div>
            <div class="card-meta">${rating} ${mlScore} ${audioBadge} ${srcBadge}</div>
            <div class="card-price">${price}</div>
            ${book.url ? `<a href="${book.url}" class="card-link">View Book</a>` : ''}
        `;
        container.appendChild(card);
    });
    return container;
}

// ─── Action panels (audiobook result / comments / reminders) ─────────────────
function _actionEl(action) {
    if (!action) return null;
    const panel = document.createElement('div');
    panel.className = 'chat-action-panel';
    if (action.type === 'audiobook') {
        const r = action.result || {};
        panel.innerHTML = r.ok ? `
            <div class="panel-success">
                <strong>Audiobook ready!</strong><br>
                File: <code>${r.output_file}</code><br>
                Chunks: ${r.chunks} · Characters: ${r.characters?.toLocaleString()}
            </div>
        ` : `
            <div class="panel-error">
                ${r.error || 'Audiobook generation failed.'}
            </div>
        `;
    } else if (action.type === 'comments') {
        const s = action.summary || {};
        const cs = action.comments || [];
        panel.innerHTML = `
            <div class="panel-comments">
                <strong>Comments</strong>
                <div class="summary">Total: ${s.total ?? 0} · ${s.positive ?? 0} positive · ${s.negative ?? 0} negative · Avg: ${s.avg_rating ?? 'N/A'}</div>
                <div class="comments-list">
                    ${cs.slice(0, 3).map(c => `
                        <div class="comment-item">
                            <span class="sentiment">${c.sentiment === 'Positive' ? '👍' : '👎'}</span>
                            <span class="text">${_esc(c.text)}</span>
                            <span class="meta">${_esc(c.user_id)} · ${c.timestamp}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    } else if (action.type === 'reminders') {
        const rs = action.reminders || [];
        panel.innerHTML = `
            <div class="panel-reminders">
                <strong>Your Reading Reminders</strong>
                ${rs.length ? rs.map(r => `
                    <div class="reminder-item">
                        <strong>${_esc(r.title)}</strong><br>
                        Progress: ${r.progress_pct}% ${r.eta_minutes ? `· ~${r.eta_minutes} min left` : ''} ${r.next_milestone ? `· Next alert at ${r.next_milestone}%` : '· Complete!'}
                    </div>
                `).join('') : '<div class="empty-state">No active reminders.</div>'}
            </div>
        `;
    }
    return panel.innerHTML ? panel : null;
}

// ─── Quick-reply chip bar ─────────────────────────────────────────────────────
function renderQuickReplies() {
    const bar = document.getElementById('quickReplies');
    if (!bar) return;
    bar.innerHTML = QUICK_REPLIES.map(qr => `
        <button class="chip" data-text="${qr.text}">${qr.label}</button>
    `).join('');
    
    // Add click listeners to chips
    bar.querySelectorAll('.chip').forEach(btn => {
        btn.addEventListener('click', () => {
            sendMessage(btn.getAttribute('data-text'));
        });
    });
}

function _hideQuickReplies() {
    const bar = document.getElementById('quickReplies');
    if (bar) bar.style.display = 'none';
}

// ─── Intent badge ─────────────────────────────────────────────────────────────
function _intentBadge(intent, confidence) {
    const icons = {
        recommend: '📚',
        audiobook: '🎧',
        comment: '💬',
        reminder: '⏰',
        greeting: '👋',
    };
    const icon = icons[intent] || '🤖';
    const pct = confidence != null ? `· ${(confidence * 100).toFixed(0)}%` : '';
    return `<span class="intent-badge">${icon} ${intent} ${pct}</span>`;
}

// ─── Core send ────────────────────────────────────────────────────────────────
async function sendMessage(text) {
    const inputEl = _input();
    const message = typeof text === 'string' ? text : (inputEl?.value.trim() ?? '');
    if (!message) return;

    // Show user bubble
    appendMessage(message, 'user');
    if (inputEl) inputEl.value = '';
    _hideQuickReplies();
    _setInputDisabled(true);
    showTyping();

    try {
        const res = await fetch(`${API_BASE}/chatbot`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                user_id: state.userId,
                book_id: state.bookId ?? undefined,
            }),
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err?.error?.message || `Server error ${res.status}`);
        }

        const data = await res.json();
        hideTyping();
        _handleResponse(data);
    } catch (err) {
        hideTyping();
        console.error('Chatbot error:', err);
        appendMessage(`❌ ${err.message || 'Could not reach the backend. Is the FastAPI server running?'}`, 'bot');
    } finally {
        _setInputDisabled(false);
        inputEl?.focus();
    }
}

// ─── Response handler ─────────────────────────────────────────────────────────
function _handleResponse(data) {
    const intent = data.intent || 'recommend';
    const confidence = data.confidence ?? null;
    const slots = data.slots || {};
    const message = data.message || data.answer || 'Here are some suggestions!';
    const books = data.books || data.recommendations || [];
    const action = data.action || null;
    const mlPowered = data.ml_powered ?? false;

    // ── Build bot bubble text ─────────────────────────────────────────────
    let replyText = message;
    // Append slot info if detected
    if (slots.mood || slots.genre) {
        const detected = [slots.mood, slots.genre].filter(Boolean).join(' + ');
        replyText += `\n\n_Detected: ${detected}_`;
    }

    // ── Intent badge line ─────────────────────────────────────────────────
    const badgeHtml = _intentBadge(intent, confidence);
    const mlText = mlPowered ? ' ML' : '';

    // Build the main bubble node
    const bubbleNode = document.createElement('div');
    bubbleNode.innerHTML = `
        <div class="bubble-header">${badgeHtml}${mlText}</div>
        <div class="bubble-content">${_formatText(replyText)}</div>
    `;

    // ── Attach extras ─────────────────────────────────────────────────────
    const bookEl = bookListEl(books);
    const actionEl = _actionEl(action);
    appendMessage(bubbleNode, 'bot', bookEl || actionEl);

    // If both books AND action (e.g., audiobook also returns books), append second
    if (bookEl && actionEl) {
        appendMessage(actionEl, 'bot');
    }

    // ── Context-aware follow-up chips ─────────────────────────────────────
    renderFollowUpChips(intent, slots);
}

// ─── Follow-up chips based on intent ─────────────────────────────────────────
function renderFollowUpChips(intent, slots) {
    const bar = document.getElementById('quickReplies');
    if (!bar) return;
    const followUps = {
        recommend: [
            { label: 'Try fantasy', text: 'Give me fantasy recommendations' },
            { label: 'Try psychology', text: 'Show me psychology books' },
            { label: 'As audiobook', text: 'I want to listen to a book' },
            { label: 'See comments', text: 'Show me the comments for this book' },
        ],
        audiobook: [
            { label: 'More books', text: 'Recommend me a book' },
            { label: 'Set reminder', text: 'Set a reading reminder for me' },
        ],
        comment: [
            { label: 'Get suggestions', text: 'Recommend me a book' },
            { label: 'Set reminder', text: 'Set a reading reminder' },
        ],
        reminder: [
            { label: 'Recommendations', text: 'Recommend me a book' },
            { label: 'View comments', text: 'Show me book comments' },
        ],
        greeting: QUICK_REPLIES.slice(0, 5),
    };
    const chips = followUps[intent] || QUICK_REPLIES.slice(0, 4);
    bar.innerHTML = chips.map(qr => `
        <button class="chip" data-text="${qr.text}">${qr.label}</button>
    `).join('');
    
    // Add click listeners
    bar.querySelectorAll('.chip').forEach(btn => {
        btn.addEventListener('click', () => {
            sendMessage(btn.getAttribute('data-text'));
        });
    });
    
    bar.style.display = 'flex';
}

// ─── Utilities ────────────────────────────────────────────────────────────────
function _setInputDisabled(disabled) {
    const inputEl = _input();
    const sendBtn = _send();
    if (inputEl) inputEl.disabled = disabled;
    if (sendBtn) sendBtn.disabled = disabled;
}

function _esc(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function _escAttr(str) {
    return String(str ?? '').replace(/'/g, "\'").replace(/"/g, '"');
}

function saveChatBook(id, title) {
    if (!id) return;
    const saved = JSON.parse(localStorage.getItem('savedBooks') || '[]');
    if (saved.some(b => b.id === id)) return;
    saved.push({ id, title, savedAt: new Date().toISOString() });
    localStorage.setItem('savedBooks', JSON.stringify(saved));
    // Visual feedback
    if (event?.target) {
        event.target.classList.add('saved');
        event.target.textContent = '✓ Saved';
    }
}

// ─── Handle Enter key in input ────────────────────────────────────────────────
function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

// ─── Init ─────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
    // Check if we're on a book detail page and pass book_id to chatbot
    const urlParams = new URLSearchParams(window.location.search);
    const bookId = urlParams.get('book');
    if (bookId) state.bookId = parseInt(bookId, 10);

    // Wire send button
    const sendBtn = _send();
    if (sendBtn) sendBtn.addEventListener('click', () => sendMessage());

    // Wire Enter key on input
    const inputEl = _input();
    if (inputEl) inputEl.addEventListener('keydown', handleKeyDown);

    // Render initial quick-reply chips
    renderQuickReplies();

    // Welcome message — matches ChatbotEngine greeting intent
    appendMessage(
        'Welcome to **DigiKitab AI** \n\n' +
        'I can help you:\n' +
        '• **Recommend** books by mood, genre, or a book you loved\n' +
        '• **Generate audiobooks** from Project Gutenberg\n' +
        '• **Show comments** and sentiment for any book\n' +
        '• **Set reading reminders** and track your progress\n\n' +
        'Type a message or tap a suggestion below',
        'bot'
    );
});