const { execFileSync } = require('node:child_process');
const path = require('node:path');
execFileSync('pwsh', ['-NoProfile', '-File', path.resolve(__dirname, '../../../scripts/setup_windows_buildkit.ps1')], { stdio: 'inherit' });
