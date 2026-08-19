// questionnair.js - نسخه اصلاح شده

const API_BASE = 'http://127.0.0.1:8000';

let currentPage = 1;
let currentQuestions = [];
let userAnswers = {};

// منتظر بمان تا DOM کامل بارگذاری شود
document.addEventListener('DOMContentLoaded', function() {
    
    const quizForm = document.getElementById('quizForm');
    const nextBtn = document.getElementById('nextBtn');
    const prevBtn = document.getElementById('prevBtn');
    const submitBtn = document.getElementById('submitBtn');
    
    if (quizForm) {
        quizForm.addEventListener('submit', function(e) {
            e.preventDefault();
            submitQuiz();
        });
    }
    
    if (nextBtn) {
        nextBtn.addEventListener('click', nextQuestion);
    }
    
    if (prevBtn) {
        prevBtn.addEventListener('click', prevQuestion);
    }
    
    if (submitBtn) {
        submitBtn.addEventListener('click', submitQuiz);
    }
    
    // بارگذاری سوالات اولیه
    loadQuestions();
});

// تابع setLoading
function setLoading(isLoading, message = 'Loading...') {
    const container = document.getElementById('results');
    const submitBtn = document.getElementById('submitBtn');
    const nextBtn = document.getElementById('nextBtn');
    
    if (isLoading) {
        if (container) {
            container.innerHTML = `<div class="loading">${message}</div>`;
        }
        if (submitBtn) submitBtn.disabled = true;
        if (nextBtn) nextBtn.disabled = true;
    } else {
        if (submitBtn) submitBtn.disabled = false;
        if (nextBtn) nextBtn.disabled = false;
    }
}

// تابع showError
function showError(message) {
    const container = document.getElementById('results');
    if (container) {
        container.innerHTML = `<div class="error">${message}</div>`;
    }
    console.error(message);
}

// بارگذاری سوالات از بک‌اند
async function loadQuestions() {
    setLoading(true, 'Loading questions...');
    
    try {
        const response = await fetch(`${API_BASE}/api/questionnaire/options`);
        
        if (!response.ok) {
            // اگر بک‌اند endpoint خاصی نداره، از سوالات پیش‌فرض استفاده کن
            loadDefaultQuestions();
            return;
        }
        
        const data = await response.json();
        currentQuestions = data.questions || [];
        displayQuestion(0);
        
    } catch (error) {
        console.log('Using default questions');
        loadDefaultQuestions();
    } finally {
        setLoading(false);
    }
}

// سوالات پیش‌فرض
function loadDefaultQuestions() {
    currentQuestions = [
        { id: 'genre', text: 'What genre do you prefer?', options: ['Fantasy', 'Sci-Fi', 'Romance', 'Thriller', 'History', 'Self-Help', 'Any'] },
        { id: 'mood', text: 'What mood are you in?', options: ['Happy', 'Adventurous', 'Dark', 'Romantic', 'Motivational', 'Relaxing', 'Any'] },
        { id: 'pace', text: 'What reading pace do you prefer?', options: ['Fast', 'Slow', 'Any'] },
        { id: 'language', text: 'Preferred language?', options: ['English', 'Persian', 'French', 'German', 'Spanish', 'Any'] },
        { id: 'popularity', text: 'Popular or underrated?', options: ['Popular', 'Underrated', 'Any'] },
        { id: 'favorite_author', text: 'Favorite author (optional)', options: [] }
    ];
    
    displayQuestion(0);
}

// نمایش سوال جاری
function displayQuestion(index) {
    const optionsContainer = document.getElementById('options');
    const nextBtn = document.getElementById('nextBtn');
    const prevBtn = document.getElementById('prevBtn');
    const submitBtn = document.getElementById('submitBtn');
    const questionTitle = document.getElementById('questionTitle');
    
    if (!optionsContainer) return;
    
    const q = currentQuestions[index];
    if (!q) return;
    
    if (questionTitle) {
        questionTitle.innerText = q.text;
    }
    
    // نمایش گزینه‌ها
    if (q.options && q.options.length > 0) {
        let html = '';
        q.options.forEach(opt => {
            const checked = userAnswers[q.id] === opt ? 'checked' : '';
            html += `
                <label class="quiz-option">
                    <input type="radio" name="question" value="${escapeHtml(opt)}" ${checked}>
                    <span>${escapeHtml(opt)}</span>
                </label>
            `;
        });
        optionsContainer.innerHTML = html;
    } else {
        // برای سوالات متنی مثل favorite_author
        const savedValue = userAnswers[q.id] || '';
        optionsContainer.innerHTML = `
            <input type="text" id="textAnswer" class="input" placeholder="Enter your answer..." value="${escapeHtml(savedValue)}">
        `;
    }
    
    // بروزرسانی دکمه‌ها
    if (prevBtn) prevBtn.style.display = index === 0 ? 'none' : 'inline-block';
    if (nextBtn) nextBtn.style.display = index === currentQuestions.length - 1 ? 'none' : 'inline-block';
    if (submitBtn) submitBtn.style.display = index === currentQuestions.length - 1 ? 'inline-block' : 'none';
    
    // بروزرسانی پیشرفت
    updateProgress(index);
}

// رفتن به سوال بعدی
function nextQuestion() {
    saveCurrentAnswer();
    
    if (currentPage < currentQuestions.length - 1) {
        currentPage++;
        displayQuestion(currentPage);
    }
}

// رفتن به سوال قبلی
function prevQuestion() {
    saveCurrentAnswer();
    
    if (currentPage > 0) {
        currentPage--;
        displayQuestion(currentPage);
    }
}

// ذخیره پاسخ جاری
function saveCurrentAnswer() {
    const q = currentQuestions[currentPage];
    if (!q) return;
    
    const optionsContainer = document.getElementById('options');
    if (!optionsContainer) return;
    
    if (q.options && q.options.length > 0) {
        const selected = optionsContainer.querySelector('input[type="radio"]:checked');
        if (selected) {
            userAnswers[q.id] = selected.value;
        }
    } else {
        const textInput = document.getElementById('textAnswer');
        if (textInput) {
            const value = textInput.value.trim();
            if (value) {
                userAnswers[q.id] = value;
            }
        }
    }
}

// بروزرسانی نوار پیشرفت
function updateProgress(index) {
    const progressBar = document.getElementById('progressBar');
    const progressText = document.getElementById('progressText');
    
    if (progressBar) {
        const percent = ((index + 1) / currentQuestions.length) * 100;
        progressBar.style.width = `${percent}%`;
    }
    
    if (progressText) {
        progressText.innerText = `Question ${index + 1} of ${currentQuestions.length}`;
    }
}

// بروزرسانی خلاصه انتخاب‌ها
function updateSummary() {
    const summaryList = document.getElementById('summaryList');
    if (!summaryList) return;
    
    let html = '';
    for (const [key, value] of Object.entries(userAnswers)) {
        if (value) {
            html += `
                <div class="summary-item">
                    <strong>${escapeHtml(key)}:</strong> ${escapeHtml(value)}
                </div>
            `;
        }
    }
    
    if (html === '') {
        html = '<div class="muted">No choices yet. Answer the questions above.</div>';
    }
    
    summaryList.innerHTML = html;
}

// ارسال پرسشنامه و دریافت پیشنهادات
async function submitQuiz(event) {
    if (event) event.preventDefault();
    
    saveCurrentAnswer();
    
    // بررسی اینکه حداقل چند سوال پاسخ داده شده
    const answeredCount = Object.keys(userAnswers).filter(k => userAnswers[k]).length;
    if (answeredCount < 2) {
        showError('Please answer at least 2 questions to get recommendations.');
        return;
    }
    
    setLoading(true, 'Getting recommendations...');
    
    try {
        const response = await fetch(`${API_BASE}/api/questionnaire`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: localStorage.getItem('userId') || 'guest',
                genre: userAnswers.genre || null,
                mood: userAnswers.mood || null,
                pace: userAnswers.pace ? userAnswers.pace.toLowerCase() : null,
                language: userAnswers.language ? userAnswers.language.toLowerCase() : null,
                popularity: userAnswers.popularity ? userAnswers.popularity.toLowerCase() : null,
                favorite_author: userAnswers.favorite_author || null,
                top_k: 12
            })
        });
        
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        
        const data = await response.json();
        displayRecommendations(data.items || []);
        
    } catch (error) {
        console.error('Questionnaire error:', error);
        showError('Failed to get recommendations. Please make sure the backend is running.');
    } finally {
        setLoading(false);
    }
}

// نمایش پیشنهادات
function displayRecommendations(books) {
    const container = document.getElementById('results');
    const resultsSection = document.getElementById('resultsSection');
    
    if (!container) return;
    
    if (resultsSection) {
        resultsSection.style.display = 'block';
    }
    
    if (!books || books.length === 0) {
        container.innerHTML = '<div class="error">No recommendations found. Try different answers.</div>';
        return;
    }
    
    container.innerHTML = books.map(book => `
        <div class="book-card" onclick="location.href='user.html?book=${book.id}'">
            <h3>${escapeHtml(book.title)}</h3>
            <p>${escapeHtml(book.author)}</p>
            <div class="tags">
                <span class="cover-tag">${escapeHtml(book.genre || 'General')}</span>
                <span class="cover-tag">${book.page_count || book.pages || '?'} pages</span>
                <span class="cover-tag">★ ${book.average_rating || book.rating || 'N/A'}</span>
            </div>
            ${book.ml_score ? `<div class="match-score">Match: ${Math.round(book.ml_score * 100)}%</div>` : ''}
        </div>
    `).join('');
}

// ذخیره کتاب
function saveBook(bookId, title, author) {
    let saved = JSON.parse(localStorage.getItem('savedBooks') || '[]');
    if (!saved.some(b => b.id === bookId)) {
        saved.push({ id: bookId, title: title, author: author });
        localStorage.setItem('savedBooks', JSON.stringify(saved));
        alert(`"${title}" saved to your library!`);
    } else {
        alert('This book is already in your library.');
    }
}

// تابع escape برای امنیت
function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/[&<>]/g, function(m) {
        if (m === '&') return '&amp;';
        if (m === '<') return '&lt;';
        if (m === '>') return '&gt;';
        return m;
    });
}