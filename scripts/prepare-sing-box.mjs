import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, readdir, rm, mkdir, writeFile, chmod } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";
import process from "node:process";

const version = process.env.GOYOU_SING_BOX_VERSION || "1.11.11";
const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const resourceDir = path.join(root, "src-tauri", "resources", "sing-box");

function targetForHost() {
  if (process.platform === "darwin" && process.arch === "x64") {
    return { name: "darwin-amd64", archive: "tar.gz", binary: "sing-box" };
  }
  if (process.platform === "darwin" && process.arch === "arm64") {
    return { name: "darwin-arm64", archive: "tar.gz", binary: "sing-box" };
  }
  if (process.platform === "win32" && process.arch === "x64") {
    return { name: "windows-amd64", archive: "zip", binary: "sing-box.exe" };
  }
  if (process.platform === "linux" && process.arch === "x64") {
    return { name: "linux-amd64", archive: "tar.gz", binary: "sing-box" };
  }
  throw new Error(`暂不支持为 ${process.platform}/${process.arch} 准备内置 sing-box`);
}

async function download(url, destination) {
  const response = await fetch(url, {
    redirect: "follow",
    signal: AbortSignal.timeout(120_000),
  });
  if (!response.ok) {
    throw new Error(`下载 sing-box 失败：HTTP ${response.status} (${url})`);
  }
  await writeFile(destination, Buffer.from(await response.arrayBuffer()));
}

async function findBinary(directory, filename) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const candidate = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      const found = await findBinary(candidate, filename);
      if (found) return found;
    } else if (entry.name === filename) {
      return candidate;
    }
  }
  return null;
}

const target = targetForHost();
const output = path.join(resourceDir, target.binary);
const existing = process.env.GOYOU_SING_BOX_PATH || process.env.PROXYSWITCH_SING_BOX_PATH;
if (existing && path.resolve(existing) !== path.resolve(output)) {
  console.log(`使用 GOYOU_SING_BOX_PATH，不下载内置 sing-box：${existing}`);
  process.exit(0);
}

await mkdir(resourceDir, { recursive: true });
try {
  const stat = await readFile(output).then(() => true).catch(() => false);
  if (stat) {
    console.log(`已存在内置 sing-box：${output}`);
    process.exit(0);
  }

  const archiveName = `sing-box-${version}-${target.name}.${target.archive}`;
  const url = `https://github.com/SagerNet/sing-box/releases/download/v${version}/${archiveName}`;
  const tempDir = await mkdtemp(path.join(os.tmpdir(), "goyou-sing-box-"));
  const archive = path.join(tempDir, archiveName);
  try {
    console.log(`下载 sing-box ${version}（${target.name}）...`);
    await download(url, archive);
    const unpacked = path.join(tempDir, "unpacked");
    await mkdir(unpacked);
    if (target.archive === "zip" && process.platform === "win32") {
      const powershellPath = archive.replaceAll("'", "''");
      const unpackedPath = unpacked.replaceAll("'", "''");
      execFileSync("powershell.exe", [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        `Expand-Archive -LiteralPath '${powershellPath}' -DestinationPath '${unpackedPath}' -Force`,
      ], { stdio: "inherit" });
    } else {
      execFileSync("tar", ["-xf", archive, "-C", unpacked], {
        stdio: "inherit",
      });
    }
    const extracted = await findBinary(unpacked, target.binary);
    if (!extracted) throw new Error(`发布包中未找到 ${target.binary}`);
    await rm(output, { force: true });
    const binary = await readFile(extracted);
    await writeFile(output, binary, { mode: 0o755 });
    if (process.platform !== "win32") await chmod(output, 0o755);
    console.log(`已准备内置 sing-box：${output}`);
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}
