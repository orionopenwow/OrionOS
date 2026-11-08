/**
 * Orion VS Code & Cursor Companion Extension
 * Connects directly to Orion Daemon via WebSocket (ws://localhost:9120)
 * Monitors active editor AST changes, diagnostics (compiler errors/warnings),
 * and provides 1-click code repairs.
 */

const vscode = require('vscode');

let socket = null;
let statusBarItem = null;
const ORION_WS = 'ws://localhost:9120';
const ORION_HTTP = 'http://127.0.0.1:9120';

/** Orion's per-start API key (apps/daemon/api_token.py writes it, owner-only). Read fresh on every connect,
 *  because it changes whenever Orion restarts. */
function daemonToken() {
  const fs = require('fs');
  const os = require('os');
  const path = require('path');
  const env = process.env;
  const home = os.homedir();
  const files = [env.ORION_TOKEN_FILE];
  if (process.platform !== 'win32') {
    files.push(path.join(env.XDG_RUNTIME_DIR || `/run/user/${process.getuid ? process.getuid() : ''}`, 'orion', 'daemon.token'));
  }
  files.push(path.join(env.ORION_DATA_DIR ? env.ORION_DATA_DIR.replace(/^~/, home) : path.join(home, '.orion'), 'daemon.token'));
  for (const f of files) {
    if (!f) continue;
    try {
      const t = fs.readFileSync(f, 'utf8').trim();
      if (t) return t;
    } catch (_) {
      /* not there */
    }
  }
  return '';
}

function authHeaders() {
  const t = daemonToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

function activate(context) {
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.text = '$(circle-outline) Orion';
  statusBarItem.tooltip = 'Orion Coding Companion';
  statusBarItem.command = 'orion.reconnect';
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  connect();

  // Listen to document changes & cursor selections
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor((editor) => {
      if (editor) sendEditorContext(editor);
    }),
    vscode.languages.onDidChangeDiagnostics(() => {
      sendDiagnostics();
    }),
    vscode.commands.registerCommand('orion.sendActiveFile', () => {
      const editor = vscode.window.activeTextEditor;
      if (editor) {
        sendEditorContext(editor, true);
        vscode.window.showInformationMessage('Code context dispatched to Orion HUD!');
      }
    }),
    vscode.commands.registerCommand('orion.reconnect', () => {
      connect();
    }),
    vscode.commands.registerCommand('orion.whyLine', () => {
      whyLine();
    })
  );
}

function connect() {
  const WebSocket = require('ws');
  if (socket) {
    try { socket.close(); } catch (_) {}
  }

  try {
    socket = new WebSocket(ORION_WS, { headers: authHeaders() });

    socket.on('open', () => {
      statusBarItem.text = '$(pass-filled) Orion Online';
      socket.send(JSON.stringify({
        event: 'CLIENT_HELLO',
        client: 'desktop',
        browser: 'vscode',
      }));
    });

    socket.on('message', (data) => {
      try {
        const msg = JSON.parse(data.toString());
        if (msg.event === 'ORCHESTRATOR_RESPONSE') {
          const codeAction = (msg.response.actions || []).find((a) => a.type === 'code_suggestion');
          if (codeAction && codeAction.payload && codeAction.payload.code) {
            vscode.window.showInformationMessage(
              `Orion Suggestion: ${msg.response.summary}`,
              'Apply Repair',
              'Dismiss'
            ).then((choice) => {
              if (choice === 'Apply Repair') {
                applyCodeSnippet(codeAction.payload.code);
              }
            });
          }
        }
      } catch (err) {
        console.error('Error handling Orion message', err);
      }
    });

    socket.on('close', () => {
      statusBarItem.text = '$(error) Orion Disconnected';
      setTimeout(connect, 5000);
    });

    socket.on('error', () => {
      statusBarItem.text = '$(error) Orion Offline';
    });
  } catch (err) {
    statusBarItem.text = '$(error) Orion Offline';
  }
}

function sendEditorContext(editor, force = false) {
  if (!socket || socket.readyState !== 1) return;
  const doc = editor.document;
  if (doc.isUntitled) return;

  const text = doc.getText();
  const selection = editor.selection;
  const selectedText = doc.getText(selection);

  socket.send(JSON.stringify({
    event: 'SCREEN_DELTA_DETECTED',
    timestamp: Date.now() / 1000,
    phash_diff: force ? 100 : 15,
    window_title: `${doc.fileName} - Visual Studio Code`,
    extracted_text: selectedText ? `// Selection:\n${selectedText}\n\n// Full file:\n${text.slice(0, 4000)}` : text.slice(0, 4000),
    focus_changed: true,
  }));
}

function sendDiagnostics() {
  if (!socket || socket.readyState !== 1) return;
  const editor = vscode.window.activeTextEditor;
  if (!editor) return;

  const diagnostics = vscode.languages.getDiagnostics(editor.document.uri);
  const errors = diagnostics.filter((d) => d.severity === vscode.DiagnosticSeverity.Error);
  if (errors.length > 0) {
    const errorText = errors.map((e) => `Line ${e.range.start.line + 1}: ${e.message}`).join('\n');
    socket.send(JSON.stringify({
      event: 'USER_PROMPT',
      text: `[Compiler Errors in ${editor.document.fileName}]\n${errorText}\n\nPlease inspect and suggest a fix.`,
    }));
  }
}

function applyCodeSnippet(code) {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return;
  editor.edit((editBuilder) => {
    if (!editor.selection.isEmpty) {
      editBuilder.replace(editor.selection, code);
    } else {
      editBuilder.insert(editor.selection.active, code);
    }
  });
}

// AI Blame: ask the daemon why the line under the cursor last changed (GET /blame on the local daemon).
function blameUrl(file, line) {
  return `${ORION_HTTP}/blame?file=${encodeURIComponent(file)}&line=${line}`;
}

function whyLine() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.isUntitled) {
    vscode.window.showInformationMessage('Open a saved file and put the cursor on a line first.');
    return;
  }
  const line = editor.selection.active.line + 1;
  const http = require('http');
  const req = http.get(blameUrl(editor.document.fileName, line), { timeout: 15000, headers: authHeaders() }, (res) => {
    let body = '';
    res.setEncoding('utf8');
    res.on('data', (chunk) => { body += chunk; });
    res.on('end', () => {
      try {
        const data = JSON.parse(body);
        if (data.error) {
          vscode.window.showWarningMessage(`Orion: ${data.error}`);
        } else {
          vscode.window.showInformationMessage(`Line ${line}: ${data.answer}`);
        }
      } catch (_) {
        vscode.window.showWarningMessage('Orion sent an answer it could not read.');
      }
    });
  });
  req.on('timeout', () => req.destroy(new Error('timeout')));
  req.on('error', () => {
    vscode.window.showWarningMessage('Orion is not running, so it cannot say why this line changed.');
  });
}

function deactivate() {
  if (socket) socket.close();
}

module.exports = { activate, deactivate, blameUrl };
