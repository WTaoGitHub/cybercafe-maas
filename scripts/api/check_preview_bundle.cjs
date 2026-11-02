// Run with the same parser module used by the VSCode preview plugin.
// Usage: node scripts/api/check_preview_bundle.cjs [parser-module-or-path]
const path = require('node:path');
const fs = require('node:fs');
const expectedPaths = Object.keys(JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../docs/api/openapi.json'), 'utf8')).paths).sort();
const parser = require(process.argv[2] || '@apidevtools/json-schema-ref-parser');
(async () => {
  for (const filename of ['openapi.yaml', 'openapi.json']) {
    const spec = await parser.bundle(path.resolve(__dirname, '../../docs/api', filename));
    const serialized = JSON.stringify(spec); // Same message payload as OpenAPI Preview.
    if (!serialized.length || JSON.stringify(Object.keys(spec.paths).sort()) !== JSON.stringify(expectedPaths)) throw new Error('Incomplete bundled document');
    process.stdout.write(`${filename}: preview bundle and serialization passed\n`);
  }
})().catch(error => { process.stderr.write(`${error.name}: ${error.message}\n`); process.exitCode = 1; });
