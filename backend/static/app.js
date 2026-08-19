const statusEl = document.getElementById("status");
const booksGrid = document.getElementById("books-grid");
const relatedGrid = document.getElementById("related-grid");
const detailPanel = document.getElementById("detail-panel");
const detailTitle = document.getElementById("detail-title");
const detailContent = document.getElementById("detail-content");
const discoverReply = document.getElementById("discover-reply");
const template = document.getElementById("book-card-template");

async function getJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function cardElement(book) {
  const fragment = template.content.cloneNode(true);
  const image = fragment.querySelector(".cover");
  image.src = book.thumbnail || "https://placehold.co/600x450/f0dfce/6d5d57?text=Book";
  image.alt = `${book.title} cover`;
  fragment.querySelector(".genre-pill").textContent = book.genre || "General";
  fragment.querySelector(".mood-pill").textContent = book.mood || "Curious";
  fragment.querySelector(".book-title").textContent = book.title;
  fragment.querySelector(".book-author").textContent = book.author;
  fragment.querySelector(".book-description").textContent = book.description || "No description available.";
  fragment.querySelector(".rating").textContent = `Rating ${book.rating}`;
  fragment.querySelector(".price").textContent = book.price > 0 ? `$${book.price}` : "Price unavailable";
  fragment.querySelector(".pages").textContent = `${book.pages} pages`;
  fragment.querySelector(".open-book").addEventListener("click", () => showBook(book.id));
  return fragment;
}

function renderBooks(target, items) {
  target.innerHTML = "";
  if (!items.length) {
    target.innerHTML = `<p class="muted">No books matched this search.</p>`;
    return;
  }
  const fragment = document.createDocumentFragment();
  items.forEach((book) => fragment.appendChild(cardElement(book)));
  target.appendChild(fragment);
}

async function loadFilterOptions() {
  const data = await getJson("/api/filter-options");
  for (const [id, values] of Object.entries({
    genre: data.genres,
    language: data.languages,
    mood: data.moods,
  })) {
    const select = document.getElementById(id);
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  }
}

async function loadBooks(params = new URLSearchParams({ limit: "12", min_rating: "3.5" })) {
  statusEl.textContent = "Loading recommendations...";
  const data = await getJson(`/api/books?${params.toString()}`);
  renderBooks(booksGrid, data.items);
  statusEl.textContent = `${data.items.length} books loaded`;
}

async function showBook(bookId) {
  const [book, related] = await Promise.all([
    getJson(`/api/books/${encodeURIComponent(bookId)}`),
    getJson("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_id: bookId, limit: 6 }),
    }),
  ]);

  detailTitle.textContent = book.title;
  detailContent.innerHTML = `
    <img class="detail-cover" src="${book.thumbnail || "https://placehold.co/480x640/f0dfce/6d5d57?text=Book"}" alt="${book.title} cover" />
    <div>
      <p class="eyebrow">${book.genre} • ${book.mood}</p>
      <h3>${book.title}</h3>
      <p class="book-author">${book.author}</p>
      <p>${book.description || "No description available."}</p>
      <div class="meta-row">
        <span>Rating ${book.rating}</span>
        <span>${book.ratings_count || 0} ratings</span>
        <span>${book.pages} pages</span>
        <span>${book.price > 0 ? `$${book.price}` : "Price unavailable"}</span>
        <span>${book.language}</span>
      </div>
    </div>
  `;
  renderBooks(relatedGrid, related.items);
  detailPanel.classList.remove("hidden");
  detailPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

document.getElementById("filters-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const params = new URLSearchParams();
  params.set("limit", "12");
  for (const [key, value] of form.entries()) {
    if (value !== "") {
      params.set(key, value);
    }
  }
  await loadBooks(params);
});

document.getElementById("discover-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = document.getElementById("prompt").value.trim();
  if (!prompt) {
    return;
  }
  statusEl.textContent = "Generating smart matches...";
  const data = await getJson("/api/discover", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
  discoverReply.textContent = data.reply;
  renderBooks(booksGrid, data.items);
  statusEl.textContent = `${data.items.length} ML-ranked matches`;
});

Promise.all([loadFilterOptions(), loadBooks()]).catch((error) => {
  statusEl.textContent = "Failed to load data";
  console.error(error);
});
