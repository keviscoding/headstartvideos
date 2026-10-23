// Starts this Studio folder's `studio` tool server (cr_studio_tools.py) for Claude Code, from the very first session:
// the download's .mcp.json names this file, so the tools are there before Studio has ever run. Once Studio has run,
// cr_claude_code.configure() points .mcp.json straight at Studio's own Python instead.
//
// Python: Studio's .venv when it exists, else this computer's (python3 on a Mac, py -3 or python on Windows).
// No Python yet: a one-tool server whose setup_check says Studio needs installing (Claude Code can do it).
'use strict'
const { spawn, spawnSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const readline = require('readline')

const HERE = __dirname
const WINDOWS = process.platform === 'win32'
const TOOLS = path.join(HERE, 'cr_studio_tools.py')

function works(command, args) {
  try {
    const run = spawnSync(command, [...args, '-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'],
      { timeout: 15000, windowsHide: true, stdio: 'ignore' })
    return run.status === 0
  } catch (error) {
    return false
  }
}

function python() {
  const venv = WINDOWS ? path.join(HERE, '.venv', 'Scripts', 'python.exe') : path.join(HERE, '.venv', 'bin', 'python')
  if (fs.existsSync(venv)) return [venv, []]
  const choices = WINDOWS ? [['py', ['-3']], ['python', []]] : [['python3', []], ['python', []]]
  return choices.find(([command, args]) => works(command, args)) || null
}

function serveAlone(message) {
  const send = m => process.stdout.write(JSON.stringify(m) + '\n')
  readline.createInterface({ input: process.stdin }).on('line', line => {
    let m
    try { m = JSON.parse(line) } catch (error) { return }
    if (m.id === undefined || m.id === null) return
    const params = m.params || {}
    if (m.method === 'initialize') {
      send({ jsonrpc: '2.0', id: m.id, result: { protocolVersion: params.protocolVersion || '2025-06-18',
        capabilities: { tools: { listChanged: false } }, serverInfo: { name: 'channelrecipe-studio', version: '1.0.0' } } })
    } else if (m.method === 'tools/list') {
      send({ jsonrpc: '2.0', id: m.id, result: { tools: [{ name: 'setup_check',
        description: 'Is ChannelRecipe Studio installed and running? Free. Call this first.',
        inputSchema: { type: 'object', properties: {} } }] } })
    } else if (m.method === 'tools/call') {
      send({ jsonrpc: '2.0', id: m.id, result: { content: [{ type: 'text',
        text: JSON.stringify({ running: false, installed: false, message }) }], isError: false } })
    } else if (m.method === 'ping') {
      send({ jsonrpc: '2.0', id: m.id, result: {} })
    } else {
      send({ jsonrpc: '2.0', id: m.id, error: { code: -32601, message: 'Method not found' } })
    }
  })
}

const found = python()
if (!found) {
  serveAlone('Studio is not installed on this computer yet (Python is missing). Ask Claude Code here to install ' +
    'ChannelRecipe Studio: it follows the steps in CLAUDE.md, then start Studio with its launcher and open a new ' +
    'Claude Code session in this folder so Studio\'s tools load.')
} else {
  const [command, args] = found
  const child = spawn(command, [...args, TOOLS], { cwd: HERE, stdio: 'inherit', windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' } })
  child.on('exit', code => process.exit(code === null ? 0 : code))
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal))
}
