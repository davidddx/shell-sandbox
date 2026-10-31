const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const pty = require('node-pty');
const path = require('path');

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

// Configuration Caps
const PORT = 3000;
const MAX_SESSIONS = 15; 
let activeSessions = 0;

// Serve your terminal website HTML/JS frontend from a "public" folder
app.use(express.static(path.join(__dirname, 'public')));

wss.on('connection', (ws) => {
	// 1. Enforce Server Capacity Cap
	if (activeSessions >= MAX_SESSIONS) {
		ws.send("\r\n\x1b[31mServer is at capacity. Please try again later.\x1b[0m\r\n");
		ws.close();
		return;
	}

	activeSessions++;
	console.log(`User connected. Active sessions: ${activeSessions}/${MAX_SESSIONS}`);

	// 2. Spawn the Ephemeral Container
	// This connects Node directly to a secure, limited Docker instance
	const shell = pty.spawn('docker', [
		'run', '-it', '--rm',
		'--network', 'none',
		'--read-only',
		'--tmpfs', '/home/sandboxuser:rw,noexec,nosuid,size=10m,uid=1000,gid=1000',
		'--tmpfs', '/tmp:rw,noexec,nosuid,size=5m',
		'--memory', '64m',
		'--cpus', '.25',
		'--pids-limit', '15',
		'terminal-sandbox' // Must match your built Docker image tag
	], {
		name: 'xterm-color',
		cols: 80,
		rows: 24,
		cwd: process.env.HOME,
		env: process.env
	});

	// 3. Setup Idle & Max Lifetime Absolute Timeouts
	let idleTimer;
	const IDLE_TIMEOUT_MS = 3 * 60 * 1000; // 3 Minutes of walking away
	const MAX_LIFETIME_MS = 10 * 60 * 1000; // 10 Minutes absolute max session length

	const resetIdleTimer = () => {
		clearTimeout(idleTimer);
		idleTimer = setTimeout(() => {
			ws.send("\r\n\x1b[33mSession closed due to inactivity.\x1b[0m\r\n");
			ws.close();
		}, IDLE_TIMEOUT_MS);
	};

	// Start the clock
	resetIdleTimer();

	const maxLifetimeTimer = setTimeout(() => {
		ws.send("\r\n\x1b[33mMaximum session time reached.\x1b[0m\r\n");
		ws.close();
	}, MAX_LIFETIME_MS);

	// 4. Two-Way Data Piping
	// Incoming data from Browser (keystrokes) -> sent to Docker
	ws.on('message', (message) => {
		resetIdleTimer(); // User is typing, reset their idle clock
		shell.write(message.toString());
	});

	// Outgoing data from Docker -> sent to Browser
	shell.onData((data) => {
		try {
			ws.send(data);
		} catch (ex) {
			// Handle race conditions where socket closes while data flushes
		}
	});

	// 5. Automated Tearing Down & Vaporization
	let clean = false
	const cleanup = () => {
		clearTimeout(idleTimer);
		clearTimeout(maxLifetimeTimer);
		if(clean) {
			return
		}
		try {
			shell.kill(); // Kills the process, triggering Docker --rm to delete container instantly
		} catch (e) {}

		activeSessions--;
		clean = true
		console.log(`User disconnected. Active sessions: ${activeSessions}/${MAX_SESSIONS}`);
	};

	ws.on('close', cleanup);
	shell.onExit(cleanup);
});

server.listen(PORT, () => {
	console.log(`Secure Terminal Express Engine running on http://localhost:${PORT}`);
});
