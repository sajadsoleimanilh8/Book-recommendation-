const express = require('express');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = 3000;
const BACKEND_PORT = 8000;

app.use(express.json());
app.use(express.static(path.join(__dirname, 'frontend')));

app.use('/api', async (req, res) => {
    try {
        const fetch = (await import('node-fetch')).default;
        const url = `http://127.0.0.1:${BACKEND_PORT}${req.url}`;
        
        const options = {
            method: req.method,
            headers: {
                'Content-Type': 'application/json',
            },
        };
        
        if (req.method !== 'GET' && req.body) {
            options.body = JSON.stringify(req.body);
        }
        
        const response = await fetch(url, options);
        const data = await response.json();
        res.status(response.status).json(data);
    } catch (error) {
        res.status(502).json({ error: 'Backend not running on port ' + BACKEND_PORT });
    }
});

app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'frontend', 'index.html'));
});

app.get('/filter.html', (req, res) => {
    res.sendFile(path.join(__dirname, 'frontend', 'filter.html'));
});

app.get('/user.html', (req, res) => {
    res.sendFile(path.join(__dirname, 'frontend', 'user.html'));
});

app.get('/chatbot.html', (req, res) => {
    res.sendFile(path.join(__dirname, 'frontend', 'chatbot.html'));
});

app.get('/Audiobook.html', (req, res) => {
    res.sendFile(path.join(__dirname, 'frontend', 'Audiobook.html'));
});

app.get('/questionnair.html', (req, res) => {
    res.sendFile(path.join(__dirname, 'frontend', 'questionnair.html'));
});

app.get('/css/:file', (req, res) => {
    res.sendFile(path.join(__dirname, 'frontend', 'css', req.params.file));
});

app.get('/js/:file', (req, res) => {
    res.sendFile(path.join(__dirname, 'frontend', 'js', req.params.file));
});

function startBackend() {
    console.log('🐍 Starting Python Backend (FastAPI)...');
    
    const backend = spawn('python', ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)], {
        cwd: path.join(__dirname, 'backend'),
        shell: true,
        stdio: 'pipe'
    });
    
    backend.stdout.on('data', (data) => {
        console.log(`[Backend] ${data.toString().trim()}`);
    });
    
    backend.stderr.on('data', (data) => {
        console.error(`[Backend] ${data.toString().trim()}`);
    });
    
    backend.on('close', (code) => {
        console.log(`Backend process exited with code ${code}`);
    });
    
    return backend;
}

const backendProcess = startBackend();

app.listen(PORT, () => {
    console.log(`
    ╔════════════════════════════════════════════════════════════╗
    ║                    DigiKitab Server Ready                   ║
    ╠════════════════════════════════════════════════════════════╣
    ║  🌐 Frontend:  http://localhost:${PORT}                     ║
    ║  🔧 Backend:   http://127.0.0.1:${BACKEND_PORT}              ║
    ║  📁 Frontend:  /frontend                                    ║
    ║  🐍 Backend:   /backend (FastAPI + Uvicorn)                 ║
    ╚════════════════════════════════════════════════════════════╝
    `);
});

process.on('SIGINT', () => {
    console.log('\n🛑 Shutting down...');
    backendProcess.kill();
    process.exit();
});