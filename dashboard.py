import sqlite3
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = Path(".atlas/atlas.db")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ATLAS Live Dashboard</title>
    <style>
        :root {
            --bg-color: #0f111a;
            --panel-bg: #1e2130;
            --text-color: #e0e6ed;
            --accent: #4ade80;
            --border: #333842;
            --error: #f87171;
            --system: #60a5fa;
            --agent: #a78bfa;
            --user: #fbbf24;
        }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 2rem;
            line-height: 1.5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--border);
        }
        h1 { margin: 0; color: var(--text-color); font-weight: 600; font-size: 1.8rem; }
        .status-dot {
            display: inline-block;
            width: 12px; height: 12px;
            background-color: var(--accent);
            border-radius: 50%;
            box-shadow: 0 0 10px var(--accent);
            animation: pulse 2s infinite;
            margin-right: 10px;
        }
        @keyframes pulse {
            0% { transform: scale(0.95); opacity: 0.8; }
            50% { transform: scale(1.05); opacity: 1; }
            100% { transform: scale(0.95); opacity: 0.8; }
        }
        
        .grid {
            display: grid;
            grid-template-columns: 350px 1fr;
            gap: 2rem;
            align-items: start;
        }
        
        .panel {
            background-color: var(--panel-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.5rem;
        }
        .panel h2 { margin-top: 0; font-size: 1.2rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }
        
        .task-list { list-style: none; padding: 0; margin: 0; }
        .task-item {
            padding: 1rem;
            border: 1px solid var(--border);
            border-radius: 6px;
            margin-bottom: 1rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        .task-item:hover { border-color: var(--accent); }
        .task-item.active { border-color: var(--accent); background: rgba(74, 222, 128, 0.1); }
        
        .task-id { font-family: monospace; font-size: 0.8rem; color: #888; }
        .task-request { margin: 0.5rem 0; font-weight: 500; }
        .task-meta { display: flex; justify-content: space-between; font-size: 0.85rem; color: #aaa; }
        
        .episodes {
            display: flex;
            flex-direction: column;
            gap: 1rem;
            max-height: 70vh;
            overflow-y: auto;
            padding-right: 1rem;
        }
        .episode {
            padding: 1rem;
            border-radius: 6px;
            border-left: 4px solid var(--border);
            background: rgba(0,0,0,0.2);
        }
        .episode.agent { border-left-color: var(--agent); }
        .episode.system { border-left-color: var(--system); }
        .episode.user { border-left-color: var(--user); }
        .episode.error { border-left-color: var(--error); }
        
        .ep-header {
            display: flex; justify-content: space-between;
            font-size: 0.85rem; margin-bottom: 0.5rem;
            color: #aaa;
        }
        .ep-role { font-weight: 600; text-transform: uppercase; letter-spacing: 1px; }
        .role-agent { color: var(--agent); }
        .role-system { color: var(--system); }
        .role-user { color: var(--user); }
        
        .ep-content { font-family: monospace; white-space: pre-wrap; font-size: 0.9rem; }
        .ep-tool { margin-top: 0.5rem; display: inline-block; padding: 0.2rem 0.5rem; background: #333; border-radius: 4px; font-size: 0.8rem; color: var(--accent); }
        
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: var(--bg-color); }
        ::-webkit-scrollbar-thumb { background: #444; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #555; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1><span class="status-dot"></span> ATLAS Execution Dashboard</h1>
            <div style="font-family: monospace; color: #888;">Auto-refresh: 2s</div>
        </header>
        
        <div class="grid">
            <div class="panel">
                <h2>Recent Tasks</h2>
                <ul class="task-list" id="taskList">
                    <li>Loading tasks...</li>
                </ul>
            </div>
            
            <div class="panel">
                <h2>Trace / Episodes</h2>
                <div class="episodes" id="episodeList">
                    Select a task to view its trace.
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentTaskId = null;
        
        async function fetchData() {
            try {
                const res = await fetch('/api/data');
                const data = await res.json();
                
                // Render Tasks
                const taskList = document.getElementById('taskList');
                if (data.tasks.length === 0) {
                    taskList.innerHTML = '<li>No tasks found.</li>';
                } else {
                    taskList.innerHTML = data.tasks.map(t => `
                        <li class="task-item ${t.id === currentTaskId ? 'active' : ''}" onclick="selectTask('${t.id}')">
                            <div class="task-id">${t.id.split('-').pop()}</div>
                            <div class="task-request">${t.request}</div>
                            <div class="task-meta">
                                <span>${t.source}</span>
                                <span>${t.created_ts.substring(11, 19)}</span>
                            </div>
                        </li>
                    `).join('');
                }
                
                // Auto-select latest task if none selected
                if (!currentTaskId && data.tasks.length > 0) {
                    selectTask(data.tasks[0].id);
                }
                
                // Render Episodes for selected task
                if (currentTaskId) {
                    const epList = document.getElementById('episodeList');
                    const episodes = data.episodes[currentTaskId] || [];
                    
                    if (episodes.length === 0) {
                        epList.innerHTML = '<div style="color:#888;">No episodes yet for this task.</div>';
                    } else {
                        epList.innerHTML = episodes.map(e => {
                            const isError = e.outcome === 'error';
                            const roleClass = e.role ? `role-${e.role.toLowerCase()}` : '';
                            const borderClass = isError ? 'error' : (e.role ? e.role.toLowerCase() : 'system');
                            
                            return `
                                <div class="episode ${borderClass}">
                                    <div class="ep-header">
                                        <span class="ep-role ${roleClass}">[Step ${e.step}] ${e.role || 'system'}</span>
                                        <span>${e.ts.substring(11, 19)}</span>
                                    </div>
                                    <div class="ep-content">${e.content}</div>
                                    ${e.tool ? `<div class="ep-tool">🔧 ${e.tool}</div>` : ''}
                                </div>
                            `;
                        }).join('');
                    }
                }
                
            } catch (err) {
                console.error("Failed to fetch data", err);
            }
        }
        
        function selectTask(id) {
            currentTaskId = id;
            fetchData();
        }
        
        // Initial fetch and poll
        fetchData();
        setInterval(fetchData, 2000);
    </script>
</body>
</html>
"""

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass # Silent logging
        
    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode('utf-8'))
            
        elif parsed.path == '/api/data':
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                
                # Fetch recent tasks
                cur.execute("SELECT id, correlation_id, source, request, created_ts FROM tasks ORDER BY created_ts DESC LIMIT 10")
                tasks = [dict(r) for r in cur.fetchall()]
                
                # Fetch episodes for these tasks via correlation_id
                episodes = {}
                for t in tasks:
                    cur.execute(
                        "SELECT step, role, content, tool, outcome, ts FROM episodes WHERE correlation_id = ? ORDER BY step ASC, ts ASC", 
                        (t['correlation_id'],)
                    )
                    episodes[t['id']] = [dict(r) for r in cur.fetchall()]
                    
                conn.close()
                
                payload = {
                    "tasks": tasks,
                    "episodes": episodes
                }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode('utf-8'))
                
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
                
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', 8080), DashboardHandler)
    print("ATLAS Dashboard running on http://127.0.0.1:8080")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
