#!/usr/bin/env node

// Durable pi on the Claude bridge (pi-claude-bridge): Deck's `pi-durable`. Copied into pi's durable directory by
// Deck's setup, next to the patch that adds `OpenDurableOptions.setup` and `appendSystemPrompt`.

import { appendFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import {
	type Api,
	type AssistantMessageEventStream,
	type Context,
	createAssistantMessageEventStream,
	type Model,
	type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import { ROOT_CONVERSATION_ID } from "@earendil-works/pi-durable";
import { getAgentDir } from "../../config.ts";
import { createJiti } from "../../core/extensions/jiti-loader.ts";
import { loadExtensions } from "../../core/extensions/loader.ts";
import { VIRTUAL_MODULES } from "../../core/extensions/virtual-modules.ts";
import { loadPromptResources } from "./prompt.ts";
import { type DurableHost, type DurableViewSource, openDurable } from "./runtime.ts";
import { runDurableTui } from "./tui.ts";

const PROVIDER = "claude-bridge";
const REQUIRED_MODEL = "claude-opus-5-5";
/** Set to 1 to resolve each request's capture, log what Claude Code would get, and fail the request unsent. */
const CAPTURE_CHECK = process.env.PI_DURABLE_CAPTURE_CHECK === "1";
const DEBUG = process.env.CLAUDE_BRIDGE_DEBUG === "1";
/** After a restart (or a failed request) the request ends at a tool result no Claude Code query waits for. */
const CONTINUE_PROMPT =
	"[The request for this turn ended before the tool result above reached you (the agent restarted or the request failed). Continue the task from there.]";

type Stream = (model: Model<Api>, context: Context, options?: SimpleStreamOptions) => AssistantMessageEventStream;
type Capture = { assembledPrompt: string };
type Captures = { resolve(systemPrompt?: string): Capture | undefined };

function parseArgs(argv: readonly string[]) {
	let continueSession = false;
	const append: string[] = [];
	let message: string | undefined;
	for (let i = 0; i < argv.length; i++) {
		const arg = argv[i]!;
		if (arg === "--continue" || arg === "-c") continueSession = true;
		else if (arg === "--append-system-prompt") {
			const text = argv[++i];
			if (text === undefined) throw new Error("--append-system-prompt needs a value");
			append.push(text);
		} else if (arg.startsWith("-") || message !== undefined) throw new Error(`Unknown argument: ${arg}`);
		else message = arg;
	}
	// Joined like pi joins repeated flags.
	return { continueSession, appendSystemPrompt: append.length > 0 ? append.join("\n\n") : undefined, message };
}

/** A failed response that never reaches the bridge. */
function refuse(model: Model<Api>, errorMessage: string): AssistantMessageEventStream {
	const stream = createAssistantMessageEventStream();
	const cost = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 };
	const usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost };
	queueMicrotask(() => {
		stream.push({
			type: "error",
			reason: "error",
			error: {
				role: "assistant",
				content: [],
				api: model.api,
				provider: model.provider,
				model: model.id,
				usage,
				stopReason: "error",
				errorMessage,
				timestamp: Date.now(),
			},
		});
		stream.end();
	});
	return stream;
}

/**
 * Load the bridge with pi's extension loader and register its provider behind a gate. The bridge sends a request as
 * Claude Code subscription use only when the request's system prompt resolves to a prompt capture, which pi records at
 * `before_agent_start`; durable has no such event, so the gate records each request's exact prompt and refuses the
 * request when it does not resolve. Durable reports and ignores errors thrown by hooks, so the gate wraps the provider.
 */
async function setupBridge(host: DurableHost, appendSystemPrompt: string | undefined): Promise<void> {
	const dir = process.env.PI_DURABLE_BRIDGE ?? join(getAgentDir(), "npm", "node_modules", "pi-claude-bridge");
	if (!existsSync(join(dir, "package.json"))) {
		throw new Error(`pi-claude-bridge is not installed at ${dir} (install it with pi, or set PI_DURABLE_BRIDGE)`);
	}
	const manifest = JSON.parse(readFileSync(join(dir, "package.json"), "utf8")) as { pi?: { extensions?: string[] } };
	const entry = manifest.pi?.extensions?.[0];
	if (entry === undefined) throw new Error(`${dir} is not a pi extension package`);
	const loaded = await loadExtensions([resolve(dir, entry)], host.cwd);
	const bridge = loaded.extensions[0];
	if (bridge === undefined)
		throw new Error(loaded.errors.map((error) => error.error).join("\n") || "No bridge loaded");

	// The bridge's own modules, for its prompt key and projection; pure, so a second instance is the same code.
	const jiti = createJiti(import.meta.url, {
		moduleCache: false,
		virtualModules: VIRTUAL_MODULES,
		tsconfigPaths: true,
	});
	const src = join(dir, "src");
	const { toBridgeContext } = (await jiti.import(join(src, "transcript.ts"))) as {
		toBridgeContext(context: Context): Context;
	};
	const { projectPromptCapture } = (await jiti.import(join(src, "prompt-capture.ts"))) as {
		projectPromptCapture(capture: Capture, options: { skillReadTool: "mcp" | "none" }): string | undefined;
	};
	const { DEBUG_LOG_PATH } = (await jiti.import(join(src, "log-paths.ts"))) as { DEBUG_LOG_PATH: string };
	const captures = (globalThis as Record<symbol, Captures | undefined>)[Symbol.for("claude-bridge:promptCaptures")];
	if (captures === undefined) throw new Error("The bridge did not create its prompt captures; unsupported version");
	const log = (line: string) => {
		if (DEBUG) appendFileSync(DEBUG_LOG_PATH, `[${new Date().toISOString()}] [pi-durable] ${line}\n`);
	};
	// The bridge's handlers for these events are synchronous.
	const emit = (event: string, payload: unknown, ctx?: unknown) => {
		for (const handler of bridge.handlers.get(event) ?? []) void handler(payload, ctx);
	};

	/** Per pi session: its first history message, to see compaction replace the history. */
	const heads = new Map<string, string>();
	/** Sessions whose last bridge answer stopped at a tool call, so a Claude Code query waits for its result. */
	const parked = new Set<string>();
	/**
	 * Per session, the abort signal of its live Claude Code query. The bridge binds a query to the signal of the request
	 * that started it and keeps the query across tool rounds, like pi's run signal; durable aborts each request's signal
	 * when its invocation ends, which would kill the query at every tool call.
	 */
	const runs = new Map<string, AbortController>();
	const prepare = (context: Context, sessionId: string | undefined): Context => {
		const { systemPrompt, tools = [] } = toBridgeContext(context);
		if (!systemPrompt) throw new Error("claude-bridge: the request has no system prompt; not sending it");
		const selectedTools = tools.map((tool) => tool.name);
		const { contextFiles, skills } = loadPromptResources(host.settingsManager, host.cwd);
		const options = {
			cwd: host.cwd,
			selectedTools,
			appendSystemPrompt: appendSystemPrompt ?? "",
			contextFiles,
			skills,
		};
		emit("before_agent_start", {
			type: "before_agent_start",
			prompt: "",
			systemPrompt,
			systemPromptOptions: options,
		});
		const capture = captures.resolve(systemPrompt);
		if (capture?.assembledPrompt !== systemPrompt) {
			throw new Error(
				`claude-bridge: no prompt capture for this ${systemPrompt.length}-char system prompt; not sent`,
			);
		}
		log(
			`capture resolved: ${systemPrompt.length}-char prompt, append ${options.appendSystemPrompt.length} chars, ` +
				`${contextFiles.length} context files, ${skills.length} skills, tools ${selectedTools.join(",")}`,
		);
		if (CAPTURE_CHECK) {
			const sent = projectPromptCapture(capture, { skillReadTool: selectedTools.includes("read") ? "mcp" : "none" });
			log(`capture check: Claude Code would get its preset plus ${sent?.length ?? 0} chars:\n${sent ?? ""}`);
			throw new Error("PI_DURABLE_CAPTURE_CHECK: capture resolved; request not sent");
		}
		if (sessionId === undefined) return context;

		// Compaction replaced the history: the bridge must rebuild Claude Code's copy, as after pi's /compact.
		const history = context.messages.filter((message) => message.role !== "system");
		const head = JSON.stringify(history[0] ?? null);
		const previous = heads.get(sessionId);
		heads.set(sessionId, head);
		if (previous !== undefined && previous !== head) {
			const sessionManager = { getSessionId: () => sessionId };
			emit("session_compact", { type: "session_compact", reason: "durable", willRetry: false }, { sessionManager });
		}
		if (history.at(-1)?.role === "toolResult" && !parked.has(sessionId)) {
			log("no query waits for the tool result; continuing in a fresh query");
			const message = { role: "user" as const, content: CONTINUE_PROMPT, timestamp: Date.now() };
			return { ...context, messages: [...context.messages, message] };
		}
		return context;
	};

	for (const { name, config } of loaded.runtime.pendingProviderRegistrations) {
		const send = config.streamSimple as Stream | undefined;
		if (name !== PROVIDER || send === undefined) continue;
		const gated: Stream = (model, context, options) => {
			// pi's one-off summarizers (durable compaction) carry this marker; the bridge serves them in an isolated query.
			if (options?.cacheRetention === "none") return send(model, context, options);
			let request: Context;
			try {
				request = prepare(context, options?.sessionId);
			} catch (error) {
				return refuse(model, error instanceof Error ? error.message : String(error));
			}
			const sessionId = options?.sessionId;
			const signal = options?.signal;
			if (sessionId === undefined || signal === undefined) return send(model, request, options);
			let run = parked.has(sessionId) ? runs.get(sessionId) : undefined;
			if (run === undefined) {
				run = new AbortController();
				runs.set(sessionId, run);
			}
			// A request's abort reaches the query only while that request's response streams.
			const query = run;
			const abort = () => query.abort(signal.reason);
			if (signal.aborted) abort();
			else signal.addEventListener("abort", abort, { once: true });
			const stream = send(model, request, { ...options, signal: query.signal });
			void stream.result().then((message) => {
				signal.removeEventListener("abort", abort);
				if (message.stopReason === "toolUse") parked.add(sessionId);
				else parked.delete(sessionId);
			});
			return stream;
		};
		host.modelRuntime.registerProvider(name, { ...config, streamSimple: gated });
	}
	await host.modelRuntime.refresh({ allowNetwork: false });

	// Fail before a model is chosen: pi's default resolution falls back to any configured provider.
	const { modelRuntime, settingsManager } = host;
	if (modelRuntime.getModel(PROVIDER, REQUIRED_MODEL) === undefined) {
		throw new Error(`${PROVIDER} is not registered or does not list ${REQUIRED_MODEL}`);
	}
	const defaultModel = settingsManager.getDefaultModel();
	if (
		settingsManager.getDefaultProvider() !== PROVIDER ||
		defaultModel === undefined ||
		modelRuntime.getModel(PROVIDER, defaultModel) === undefined ||
		!modelRuntime.hasConfiguredAuth(PROVIDER)
	) {
		throw new Error(`pi's default model must be a ${PROVIDER} model (settings.json defaultProvider/defaultModel)`);
	}
}

/** A session name from its first message, as Deck's Pi extension (deploy/agents/pi-name.ts) makes it. */
function nameFrom(text: string): string {
	const line = text.trim().split("\n")[0]!.replace(/\s+/g, " ").trim();
	if (line.length <= 40) return line;
	const cut = line.slice(0, 40);
	return (cut.lastIndexOf(" ") > 20 ? cut.slice(0, cut.lastIndexOf(" ")) : cut) + "…";
}

/**
 * The terminal title pi sets, "π - <name> - <folder>", so Deck's tab shows a name. The name comes from the main
 * conversation's first message and is kept in the session folder, so `--continue` shows it at once.
 */
function nameTerminal(view: DurableViewSource): () => void {
	const { directory, cwd } = view.current().session;
	const file = join(directory, "deck-name.txt");
	let name = existsSync(file) ? readFileSync(file, "utf8").trim() : "";
	const title = () => process.stdout.write(`\x1b]0;π - ${name ? `${name} - ` : ""}${basename(cwd)}\x07`);
	title();
	return view.subscribe(() => {
		if (name) return;
		const first = view.current().conversations.find((c) => c.id === ROOT_CONVERSATION_ID)?.title;
		if (!first || first.startsWith("/")) return;
		name = nameFrom(first);
		writeFileSync(file, `${name}\n`);
		title();
	});
}

const args = parseArgs(process.argv.slice(2));
const durable = await openDurable({
	continueSession: args.continueSession,
	appendSystemPrompt: args.appendSystemPrompt,
	setup: (host) => setupBridge(host, args.appendSystemPrompt),
});
const unname = nameTerminal(durable.view);
try {
	if (args.message !== undefined) await durable.controller.submit(args.message, "followUp");
	await runDurableTui(durable.view, durable.controller, durable.settings);
} finally {
	unname();
	await durable.close();
}
