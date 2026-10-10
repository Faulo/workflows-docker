const { spawnSync } = require('node:child_process');
const path = require('node:path');
// Node actions receive GitHub's runtime cache credentials. Pass them directly
// to the build process without writing them to job outputs or logs.
const result = spawnSync('python', [
  path.resolve(__dirname, '../../../scripts/build_group.py'), 'windows-bake',
  '--plan', 'plan/plan.json', '--group', process.env.INPUT_GROUP,
  '--platform', process.env.INPUT_PLATFORM, '--publish', process.env.INPUT_PUBLISH,
  '--directory', process.env.INPUT_DIRECTORY,
], { stdio: 'inherit', env: process.env });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
