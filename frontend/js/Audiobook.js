const API_BASE = "http://localhost:8000";
let books = [];
let audioEl = null;


async function initAudio() {
  try {
    const res = await fetch(`${API_BASE}/api/books?limit=150`);
    const data = await res.json();
    books = data.items || [];
    const select = document.getElementById("bookSelect");
    select.innerHTML = books
      .map(b => `<option value="${b.id}">${b.title} — ${b.author}</option>`)
      .join("");
    const params = new URLSearchParams(location.search);
    const id = params.get("book");
    if (id) select.value = id;
    renderBook();
  } catch (err) {
    console.error(err);
    document.getElementById("audioStatus").textContent =
      "Backend not running. Start FastAPI first.";
  }
}


function renderBook() {
  const id = Number(document.getElementById("bookSelect").value);
  const b = books.find(x => Number(x.id) === id);
  if (!b) return;
  document.getElementById("audioMeta").innerHTML = `
    <div class="list-item"><strong>${b.title}</strong><div>${b.author}</div></div>
    <div class="list-item">Genre: ${b.genre}</div>
    <div class="list-item">Pages: ${b.pages || "N/A"}</div>
    <div class="list-item">
      Estimated audio length: ${Math.max(2, Math.round((b.pages || 120) / 35))} hrs
    </div>
    <div class="list-item">
      Audiobook: ${b.audiobook ? "Available" : "On-demand generation"}
    </div>
  `;
}


async function generateAudio() {
  const voice = document.getElementById("voice").value;
  const id = Number(document.getElementById("bookSelect").value);
  try {
    const book = books.find(b => b.id === id);
    if (!book) return;
    const res = await fetch(`${API_BASE}/api/audiobook/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        book_name: book.title,
        output_file: `audiobook_${id}.mp3`,
        language: "en"
      })
    });
    const data = await res.json();
    document.getElementById("audioStatus").textContent =
      data.ok
        ? `🎧 Generated audiobook for "${book.title}" with ${voice}`
        : `⚠️ ${data.error || "Generation failed"}`;
    
   
    const sampleUrl =
      "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3";
    document.getElementById("playerWrap").innerHTML = `
      <div class="player">
        <audio id="audioEl" preload="metadata">
          <source src="${sampleUrl}" type="audio/mpeg" />
        </audio>
        <div class="actions" style="margin-top:10px">
          <button id="btnPlay">Play</button>
          <button id="btnPause">Pause</button>
          <label>
            Speed
            <select id="speedSel">
              <option value="0.75">0.75x</option>
              <option value="1" selected>1x</option>
              <option value="1.25">1.25x</option>
              <option value="1.5">1.5x</option>
              <option value="2">2x</option>
            </select>
          </label>
        </div>
        <div style="margin-top:12px">
          <input id="progressBar" type="range" min="0" max="100" value="0" />
          <div id="timeLabel">0:00 / 0:00</div>
        </div>
      </div>
    `;
    setupPlayer(id);
  } catch (err) {
    console.error(err);
    document.getElementById("audioStatus").textContent =
      "Audiobook generation failed.";
  }
}


function setupPlayer(bookId) {
  audioEl = document.getElementById("audioEl");
  const btnPlay = document.getElementById("btnPlay");
  const btnPause = document.getElementById("btnPause");
  const speedSel = document.getElementById("speedSel");
  const progressBar = document.getElementById("progressBar");
  const timeLabel = document.getElementById("timeLabel");
  
  const fmt = (sec) => {
    if (!isFinite(sec)) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  };

  btnPlay.onclick = () => audioEl.play();
  btnPause.onclick = () => audioEl.pause();
  
  speedSel.onchange = () => {
    audioEl.playbackRate = Number(speedSel.value);
  };

  audioEl.ontimeupdate = async () => {
    const dur = audioEl.duration || 0;
    const pct = dur ? (audioEl.currentTime / dur) * 100 : 0;
    progressBar.value = pct;
    timeLabel.textContent = `${fmt(audioEl.currentTime)} / ${fmt(dur)}`;
    
    // progress sync to backend (lightweight)
    if (dur > 0) {
      const progress = audioEl.currentTime / dur;
      try {
        await fetch(`${API_BASE}/progress`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            user_id: "guest",
            book_id: bookId,
            progress
          })
        });
      } catch (e) {}
    }
  };

  progressBar.oninput = () => {
    const dur = audioEl.duration || 0;
    if (!dur) return;
    audioEl.currentTime = (progressBar.value / 100) * dur;
  };
}


if (typeof window !== 'undefined') {
  window.addEventListener("DOMContentLoaded", initAudio);
}