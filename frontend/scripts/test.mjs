import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { writeFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const result = await build({
  entryPoints: ["tests/presentation.test.tsx"],
  bundle: true,
  write: false,
  platform: "node",
  format: "cjs",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' },
});
const output = join(tmpdir(), `dolphinlab-ui-tests-${process.pid}.cjs`);
writeFileSync(output, result.outputFiles[0].text);
try {
  execFileSync(process.execPath, [output], { stdio: "inherit" });
} finally {
  unlinkSync(output);
}
