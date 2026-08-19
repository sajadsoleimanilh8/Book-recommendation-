// filter.js - نسخه کامل با تصاویر

const API_BASE = 'http://127.0.0.1:8000';
const DEFAULT_THUMBNAIL = 'https://via.placeholder.com/200x280?text=No+Cover';

let currentPage = 1;
let totalBooks = 0;
let booksPerPage = 24;

document.addEventListener('DOMContentLoaded', function() {
    loadBooks();
    
    const filterForm = document.getElementById('filterForm');
    if (filterForm) {
        filterForm.addEventListener('submit', function(e) {
            e.preventDefault();
            searchBooks();
        });
    }
    
    const resetBtn = document.getElementById('resetBtn');
    if (resetBtn) {
        resetBtn.addEventListener('click', resetFilters);
    }
});

async function loadBooks() {
    const container = document.getElementById('booksContainer');
    if (!container) return;
    
    container.innerHTML = '<div class="loading">Loading books...</div>';
    
    try {
        const response = await fetch(`${API_BASE}/api/books?limit=${booksPerPage}`);
        if (!response.ok) throw new Error('Failed to load');
        
        const data = await response.json();
        totalBooks = data.total || data.items.length;
        displayBooks(data.items);
        
    } catch (error) {
        container.innerHTML = '<div class="error">Failed to load books. Make sure backend is running on port 8000.</div>';
    }
}

async function searchBooks() {
    const container = document.getElementById('booksContainer');
    if (!container) return;
    
    container.innerHTML = '<div class="loading">Searching...</div>';
    
    const genre = document.getElementById('genre')?.value || '';
    const mood = document.getElementById('mood')?.value || '';
    const language = document.getElementById('language')?.value || '';
    
    let url = `${API_BASE}/api/books?limit=50`;
    if (genre) url += `&genre=${encodeURIComponent(genre)}`;
    if (mood) url += `&mood=${encodeURIComponent(mood)}`;
    if (language) url += `&language=${encodeURIComponent(language)}`;
    
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error('Search failed');
        
        const data = await response.json();
        displayBooks(data.items);
        
    } catch (error) {
        container.innerHTML = '<div class="error">Search failed. Please try again.</div>';
    }
}

function displayBooks(books) {
    const container = document.getElementById('booksContainer');
    if (!container) return;
    
    if (!books || books.length === 0) {
        container.innerHTML = '<div class="error">No books found.</div>';
        return;
    }
    
    container.innerHTML = books.map(book => {
        let thumbnail = book.thumbnail;
        if (!thumbnail || thumbnail === '' || thumbnail === 'null' || thumbnail === 'undefined') {
            thumbnail = DEFAULT_THUMBNAIL;
        }
        
        return `
            <div class="book-card" onclick="location.href='user.html?book=${book.id}'">
                <div class="book-cover">
                    <img src="${thumbnail}" alt="${escapeHtml(book.title)}" 
                         onerror="this.src='${DEFAULT_THUMBNAIL}'">
                </div>
                <h3>${escapeHtml(book.title)}</h3>
                <p>${escapeHtml(book.author)}</p>
                <div class="tags">
                    <span class="cover-tag">${escapeHtml(book.genre || 'General')}</span>
                    <span class="cover-tag">${book.pages || '?'} pgs</span>
                    <span class="cover-tag">★ ${book.rating || 'N/A'}</span>
                </div>
            </div>
        `;
    }).join('');
}

function resetFilters() {
    const fields = ['genre', 'mood', 'language', 'author', 'minRating', 'minPages'];
    fields.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    loadBooks();
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/[&<>]/g, function(m) {
        if (m === '&') return '&amp;';
        if (m === '<') return '&lt;';
        if (m === '>') return '&gt;';
        return m;
    });
}

window.resetFilters = resetFilters;