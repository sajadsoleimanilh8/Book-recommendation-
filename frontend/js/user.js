const API_BASE = 'http://localhost:8000';
const STORAGE_KEY = 'savedBooks';
const PROFILE_KEY = 'profileName';
const USER_ID = 'guest';

let books = [];
let selectedBookId = null;
let reader = { pages: [], index: 0 };

function getSavedBooks() {
  return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]').map(String);
}

function setSavedBooks(ids) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
}

function appendMiniChat(text, role = 'bot') {
  const wrap = document.getElementById('miniChatMessages');
  const div = document.createElement('div');
  div.className = `bubble ${role}`;
  div.textContent = text;
  wrap.appendChild(div);
  wrap.scrollTop = wrap.scrollHeight;
}

async function fetchBooks() {
  const res = await fetch(`${API_BASE}/books?limit=200`);
  const data = await res.json();
  books = data.items || [];
}

function renderSaved() {
  const ids = getSavedBooks();
  const items = books.filter((book) => ids.includes(String(book.id)));
  document.getElementById('savedCount').textContent = String(items.length);
  document.getElementById('savedBooks').innerHTML = items.map((book) => `
    <div class="list-item workspace-book-item ${String(book.id) === String(selectedBookId) ? 'selected-item' : ''}">
      <div>
        <strong>${book.title}</strong>
        <div>${book.author}</div>
        <div class="muted">${book.genre} | ${book.mood}</div>
      </div>
      <div class="actions">
        <button class="btn btn-primary" type="button" onclick="openBook('${encodeURIComponent(book.id)}')">Open</button>
        <button class="btn btn-secondary" type="button" onclick="removeBook('${encodeURIComponent(book.id)}')">Remove</button>
      </div>
    </div>`).join('') || '<div class="list-item">Save books from Home or Library to build your workspace.</div>';
}

function removeBook(id) {
  const bookId = decodeURIComponent(id);
  const next = getSavedBooks().filter((item) => item !== bookId);
  setSavedBooks(next);
  if (selectedBookId === bookId) {
    selectedBookId = next[0] || null;
  }
  renderSaved();
  if (selectedBookId) {
    openBook(selectedBookId);
  }
}

async function openBook(id) {
  selectedBookId = decodeURIComponent(id);
  const saved = getSavedBooks();
  if (!saved.includes(selectedBookId)) {
    saved.push(selectedBookId);
    setSavedBooks(saved);
  }
  const [bookRes, pageRes] = await Promise.all([
    fetch(`${API_BASE}/books/${encodeURIComponent(selectedBookId)}`),
    fetch(`${API_BASE}/books/${encodeURIComponent(selectedBookId)}/pages?page=1&page_size=24`)
  ]);
  if (!bookRes.ok || !pageRes.ok) throw new Error('Book request failed');
  const book = await bookRes.json();
  const pageData = await pageRes.json();
  reader.pages = (pageData.items || []).map((item) => item.content);
  reader.index = 0;
  document.getElementById('readerTitle').textContent = book.title;
  document.getElementById('readerMeta').textContent = `${book.author} | ${book.genre} | ${book.pages} pages | Rating ${book.rating || 'N/A'}`;
  document.getElementById('readerAudioLink').href = `./audiobook.html?book=${encodeURIComponent(selectedBookId)}`;
  document.getElementById('readerChatLink').href = `./chatbot.html?book=${encodeURIComponent(selectedBookId)}`;
  renderReaderPage();
  renderSaved();
  await Promise.all([loadComments(), loadReminders(), renderHistory()]);
  appendMiniChat(`Loaded ${book.title}. Ask for a recap, what to read next, or a reading plan.`, 'bot');
}

function renderReaderPage() {
  const page = reader.pages[reader.index] || 'No reader preview available.';
  document.getElementById('readerPage').textContent = page;
  document.getElementById('readerPageNum').textContent = reader.pages.length ? `Page ${reader.index + 1} of ${reader.pages.length}` : '';
  document.getElementById('readerPrev').disabled = reader.index <= 0;
  document.getElementById('readerNext').disabled = reader.index >= reader.pages.length - 1;
  const progress = reader.pages.length ? (reader.index + 1) / reader.pages.length : 0;
  if (selectedBookId) {
    fetch(`${API_BASE}/progress`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: USER_ID, book_id: selectedBookId, progress, page: reader.index + 1, source: 'reader' })
    }).catch(() => {});
  }
}

async function loadComments() {
  if (!selectedBookId) return;
  const res = await fetch(`${API_BASE}/books/${encodeURIComponent(selectedBookId)}/comments`);
  const data = await res.json();
  const items = data.items || [];
  document.getElementById('commentCount').textContent = String(items.length);
  document.getElementById('commentsList').innerHTML = items.map((item) => `
    <div class="list-item">
      <strong>${item.user_name}</strong>
      <div class="muted">${item.rating ? `Rating ${item.rating}/5` : 'No rating'} | ${new Date(item.created_at).toLocaleString()}</div>
      <div>${item.text}</div>
    </div>`).join('') || '<div class="list-item">No comments yet for this book.</div>';
}

async function submitComment(event) {
  event.preventDefault();
  if (!selectedBookId) {
    window.alert('Select a book first.');
    return;
  }
  const body = {
    user_name: document.getElementById('commentName').value.trim() || localStorage.getItem(PROFILE_KEY) || 'Guest',
    text: document.getElementById('commentText').value.trim(),
    rating: document.getElementById('commentRating').value ? Number(document.getElementById('commentRating').value) : null
  };
  if (!body.text) return;
  const res = await fetch(`${API_BASE}/books/${encodeURIComponent(selectedBookId)}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error('Comment request failed');
  document.getElementById('commentText').value = '';
  document.getElementById('commentRating').value = '';
  await loadComments();
}

async function loadReminders() {
  const res = await fetch(`${API_BASE}/reminders?user_id=${encodeURIComponent(USER_ID)}${selectedBookId ? `&book_id=${encodeURIComponent(selectedBookId)}` : ''}`);
  const data = await res.json();
  const items = data.items || [];
  document.getElementById('reminderCount').textContent = String(items.length);
  document.getElementById('remindersList').innerHTML = items.map((item) => `
    <div class="list-item">
      <strong>${item.book ? item.book.title : 'Book reminder'}</strong>
      <div class="muted">${item.remind_at ? new Date(item.remind_at).toLocaleString() : 'No time set'}</div>
      <div>${item.note || 'Reading reminder'}</div>
      <div class="actions top-gap">
        <button class="btn ${item.done ? 'btn-secondary' : 'btn-primary'}" type="button" onclick="toggleReminder('${item.id}', ${item.done ? 'false' : 'true'})">${item.done ? 'Mark Active' : 'Mark Done'}</button>
      </div>
    </div>`).join('') || '<div class="list-item">No reminders yet.</div>';
}

async function submitReminder(event) {
  event.preventDefault();
  if (!selectedBookId) {
    window.alert('Select a book first.');
    return;
  }
  const body = {
    user_id: USER_ID,
    book_id: selectedBookId,
    remind_at: document.getElementById('remindAt').value,
    note: document.getElementById('reminderNote').value.trim()
  };
  const res = await fetch(`${API_BASE}/reminders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error('Reminder request failed');
  document.getElementById('remindAt').value = '';
  document.getElementById('reminderNote').value = '';
  await loadReminders();
}

async function toggleReminder(id, done) {
  await fetch(`${API_BASE}/reminders/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ done })
  });
  await loadReminders();
}

async function renderHistory() {
  const res = await fetch(`${API_BASE}/progress?user_id=${encodeURIComponent(USER_ID)}`);
  const data = await res.json();
  const items = data.items || [];
  document.getElementById('historyList').innerHTML = items.map((item) => `
    <div class="list-item">
      <strong>${item.book ? item.book.title : 'Unknown book'}</strong>
      <div>${item.book ? item.book.author : ''}</div>
      <div class="muted">Progress ${Math.round((item.progress || 0) * 100)}% | Page ${item.page || 1}</div>
    </div>`).join('') || '<div class="list-item">No reading activity yet.</div>';
}

async function sendWorkspaceChat() {
  const input = document.getElementById('miniChatInput');
  const message = input.value.trim();
  if (!message) return;
  appendMiniChat(message, 'user');
  input.value = '';
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, book_id: selectedBookId, user_id: USER_ID })
  });
  if (!res.ok) throw new Error('Chat request failed');
  const data = await res.json();
  const titles = (data.items || []).slice(0, 3).map((book) => `${book.title} by ${book.author}`).join('; ');
  appendMiniChat(`${data.reply}${titles ? ` Suggested next: ${titles}.` : ''}`, 'bot');
}

function moveReader(step) {
  if (!reader.pages.length) return;
  const next = reader.index + step;
  if (next < 0 || next >= reader.pages.length) return;
  reader.index = next;
  renderReaderPage();
}

async function initUser() {
  try {
    await fetchBooks();
    const profileName = localStorage.getItem(PROFILE_KEY) || 'Reader';
    document.getElementById('profileName').textContent = profileName;
    document.getElementById('name').value = profileName;
    renderSaved();
    await renderHistory();

    const params = new URLSearchParams(window.location.search);
    const requestedBook = params.get('book') || getSavedBooks()[0];
    if (requestedBook) {
      await openBook(requestedBook);
    }

    document.getElementById('profileForm').addEventListener('submit', (event) => {
      event.preventDefault();
      const name = document.getElementById('name').value.trim() || 'Reader';
      localStorage.setItem(PROFILE_KEY, name);
      document.getElementById('profileName').textContent = name;
      document.getElementById('commentName').value = name;
    });
    document.getElementById('commentName').value = profileName;
    document.getElementById('commentForm').addEventListener('submit', submitComment);
    document.getElementById('reminderForm').addEventListener('submit', submitReminder);
    document.getElementById('readerPrev').addEventListener('click', () => moveReader(-1));
    document.getElementById('readerNext').addEventListener('click', () => moveReader(1));
  } catch (error) {
    console.error(error);
    document.getElementById('readerPage').textContent = 'Could not load the reading workspace. Start the backend first.';
  }
}

window.addEventListener('DOMContentLoaded', initUser);
window.openBook = openBook;
window.removeBook = removeBook;
window.toggleReminder = toggleReminder;
window.sendWorkspaceChat = sendWorkspaceChat;
