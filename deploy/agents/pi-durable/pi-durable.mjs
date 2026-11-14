// Starts durable pi from the source checkout deploy/agents/pi-durable/setup.py keeps (PI_DURABLE_SRC, default ~/.pi/durable-src).
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const root = process.env.PI_DURABLE_SRC || join(homedir(), ".pi", "durable-src");
const src = join(root, "packages", "coding-agent", "src", "experimental");
// pi's resolver maps its workspace imports to the source files; it must load before the agent does.
await import(pathToFileURL(join(src, "source-resolver.ts")).href);
await import(pathToFileURL(join(src, "durable", "bridge-main.ts")).href);
